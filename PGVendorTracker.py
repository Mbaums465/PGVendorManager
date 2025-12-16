import tkinter as tk
from tkinter import messagebox, Toplevel, Label, Entry, Button, Scrollbar, Canvas, OptionMenu, StringVar, simpledialog, Checkbutton, BooleanVar, filedialog
from tkinter import ttk
import sqlite3
import json
import re
from datetime import datetime, timedelta
from collections import defaultdict
import os
import sys
import time
from typing import List, Optional, Dict, Tuple, Set
from dataclasses import dataclass

# ---------------------
# Constants
# ---------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'character_data')
DATABASE_PATH = os.path.join(DATA_DIR, 'vendors_auto.db')
DEFAULT_CHARACTER = 'Default'
MAX_DAYS = 6
MAX_HOURS = 23
MAX_MINUTES = 59
MAX_TOTAL_MINUTES = MAX_DAYS * 24 * 60 + MAX_HOURS * 60 + MAX_MINUTES

# Invalid council maximum threshold (2^31 - 1, likely overflow/invalid value)
INVALID_MAX_COUNCIL = 2147483647

# Default Player.log path (Windows)
DEFAULT_LOG_PATH = os.path.expandvars(r'C:\Users\%USERNAME%\AppData\LocalLow\Elder Game\Project Gorgon\Player.log')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')

# UI Constants
VENDOR_CATEGORIES = ["Jewelry", "Armor", "Weapons", "Scrolls", "Misc"]
PULSE_FRAME_MAX = 120
PULSE_CYCLE_DIVISOR = 30
TIMER_UPDATE_MS = 1000
PULSE_UPDATE_MS = 100
AUTO_SCAN_INTERVAL_MS = 2000  # Check for new log data every 2 seconds

# Colors
COLOR_EMPTY_BG = "lightgrey"
COLOR_NORMAL_BG = "white"
COLOR_RESET_READY = "green"

# Regex patterns for log parsing
PATTERN_LOGIN = re.compile(r'Vivox - LoginAsync\(([A-Za-z][A-Za-z0-9_]*)\)')
PATTERN_AREA = re.compile(r'Initializing area! \(\d+\): Area(\w+)')
PATTERN_INTERACTION = re.compile(r'LocalPlayer: ProcessStartInteraction\((\d+),.*?,.*?,.*?, (NPC_[^,]+),')
PATTERN_VENDOR_SCREEN = re.compile(r'LocalPlayer: ProcessVendorScreen\((\d+), ([^,]+), (\d+), (\d+), (\d+),')
PATTERN_VENDOR_UPDATE_GOLD = re.compile(r'LocalPlayer: ProcessVendorUpdateAvailableGold\((\d+), (\d+), (\d+),')


# ---------------------
# Data Classes
# ---------------------
@dataclass
class TimeUntilReset:
    """Wrapper for time until reset calculations."""
    days: int
    hours: int
    minutes: int
    
    @classmethod
    def from_timedelta(cls, td: timedelta) -> 'TimeUntilReset':
        """Create from timedelta object."""
        total_seconds = max(0, int(td.total_seconds()))
        days = total_seconds // (24 * 3600)
        remainder = total_seconds % (24 * 3600)
        hours = remainder // 3600
        minutes = (remainder % 3600) // 60
        return cls(days, hours, minutes)
    
    @classmethod
    def from_inputs(cls, days: int, hours: int, minutes: int, override_max: bool = False) -> 'TimeUntilReset':
        """Create from user inputs with optional clamping."""
        d = max(0, int(days or 0))
        h = max(0, int(hours or 0))
        m = max(0, int(minutes or 0))
        
        if not override_max:
            total_minutes = d * 24 * 60 + h * 60 + m
            if total_minutes > MAX_TOTAL_MINUTES:
                total_minutes = MAX_TOTAL_MINUTES
            d, remainder = divmod(total_minutes, 24 * 60)
            h, m = divmod(remainder, 60)
        else:
            h = min(h, 23)
            m = min(m, 59)
        
        return cls(int(d), int(h), int(m))
    
    @classmethod
    def from_reset_timestamp_ms(cls, reset_timestamp_ms: int) -> 'TimeUntilReset':
        """Create from Unix millisecond timestamp of reset time."""
        if reset_timestamp_ms == 0:
            # Vendor just reset, full 7 days
            return cls(6, 23, 59)
        
        reset_time = datetime.fromtimestamp(reset_timestamp_ms / 1000.0)
        now = datetime.now()
        td = reset_time - now
        
        if td.total_seconds() <= 0:
            return cls(0, 0, 0)
        
        return cls.from_timedelta(td)
    
    def to_timedelta(self) -> timedelta:
        """Convert to timedelta."""
        return timedelta(days=self.days, hours=self.hours, minutes=self.minutes)
    
    def to_string(self) -> str:
        """Format as string."""
        return f"{self.days}d {self.hours}h {self.minutes}m"
    
    def calculate_last_reset(self, override_max: bool = False) -> datetime:
        """Calculate when the last reset occurred."""
        time_until_reset = self.to_timedelta()
        if not override_max:
            time_since_last_reset = timedelta(days=7) - time_until_reset
            return datetime.now() - time_since_last_reset
        else:
            return datetime.now() + time_until_reset - timedelta(days=7)


@dataclass
class ScanResult:
    """Results from scanning the Player.log file."""
    characters_found: Set[str]
    npc_mappings: Dict[int, str]  # NPC_ID -> clean name (without NPC_ prefix)
    npc_zones: Dict[int, str]  # NPC_ID -> zone name
    vendor_data: Dict[str, Dict[int, Tuple[int, int, int]]]  # character -> {npc_id: (council_left, reset_ts_ms, max_council)}
    errors: List[str]


# ---------------------
# Log Watcher (Incremental Scanner)
# ---------------------
class LogWatcher:
    """Efficiently watches Player.log for new vendor data by reading only new lines."""
    
    def __init__(self, log_path: str, on_update_callback):
        self.log_path = log_path
        self.on_update_callback = on_update_callback
        
        # File tracking state
        self.last_position = 0
        self.last_size = 0
        self.last_modified = 0
        
        # Parsing state (persistent across reads)
        self.current_character: Optional[str] = None
        self.current_zone: str = "Unknown"
        self.current_vendor_npc_id: Optional[int] = None
        
        # Accumulated data
        self.characters_found: Set[str] = set()
        self.npc_mappings: Dict[int, str] = {}
        self.npc_zones: Dict[int, str] = {}
        self.vendor_data: Dict[str, Dict[int, Tuple[int, int, int]]] = {}
        
        # Track what changed in last scan
        self.last_scan_updates: List[Tuple[str, int, str]] = []  # (character, npc_id, npc_name)
    
    def check_for_updates(self) -> bool:
        """
        Check for new content in the log file.
        Returns True if new vendor data was found.
        """
        if not os.path.exists(self.log_path):
            return False
        
        try:
            stat = os.stat(self.log_path)
            current_size = stat.st_size
            current_modified = stat.st_mtime
            
            # Check if file was rotated/truncated (size decreased)
            if current_size < self.last_size:
                # File was reset, start from beginning
                self.last_position = 0
                self.last_size = 0
            
            # Check if there's new content
            if current_size <= self.last_position:
                return False
            
            # Read only the new content
            updates_found = self._read_new_content()
            
            self.last_size = current_size
            self.last_modified = current_modified
            
            return updates_found
            
        except (OSError, IOError) as e:
            # File might be locked by the game, try again later
            return False
    
    def _read_new_content(self) -> bool:
        """Read new lines from the log file. Returns True if vendor data was updated."""
        self.last_scan_updates.clear()
        updates_found = False
        
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Seek to last known position
                f.seek(self.last_position)
                
                for line in f:
                    if self._process_line(line):
                        updates_found = True
                
                # Update position for next read
                self.last_position = f.tell()
            
            return updates_found
            
        except (OSError, IOError):
            return False
    
    def _process_line(self, line: str) -> bool:
        """Process a single log line. Returns True if vendor data was updated."""
        # Check for character login
        login_match = PATTERN_LOGIN.search(line)
        if login_match:
            char_name = login_match.group(1)
            if not char_name.isdigit():
                self.current_character = char_name
                self.characters_found.add(char_name)
                if char_name not in self.vendor_data:
                    self.vendor_data[char_name] = {}
            return False
        
        # Check for area/zone change
        area_match = PATTERN_AREA.search(line)
        if area_match:
            self.current_zone = area_match.group(1)
            return False
        
        # Check for NPC interaction
        interaction_match = PATTERN_INTERACTION.search(line)
        if interaction_match:
            npc_id = int(interaction_match.group(1))
            npc_name_raw = interaction_match.group(2)
            npc_name = npc_name_raw[4:] if npc_name_raw.startswith('NPC_') else npc_name_raw
            self.npc_mappings[npc_id] = npc_name
            
            # VendorFox exception
            if npc_name == 'VendorFox':
                self.npc_zones[npc_id] = 'Anywhere'
            else:
                self.npc_zones[npc_id] = self.current_zone
            return False
        
        # Check for vendor screen (opens vendor)
        vendor_match = PATTERN_VENDOR_SCREEN.search(line)
        if vendor_match and self.current_character:
            npc_id = int(vendor_match.group(1))
            council_left = int(vendor_match.group(3))
            reset_ts_ms = int(vendor_match.group(4))
            max_council = int(vendor_match.group(5))
            
            # Check for invalid max_council value and replace both with 0
            if max_council >= INVALID_MAX_COUNCIL:
                max_council = 0
                council_left = 0
            
            self.vendor_data[self.current_character][npc_id] = (council_left, reset_ts_ms, max_council)
            self.current_vendor_npc_id = npc_id
            
            npc_name = self.npc_mappings.get(npc_id, f"Unknown_{npc_id}")
            self.last_scan_updates.append((self.current_character, npc_id, npc_name))
            return True
        
        # Check for vendor gold update (after selling)
        update_match = PATTERN_VENDOR_UPDATE_GOLD.search(line)
        if update_match and self.current_character and self.current_vendor_npc_id is not None:
            council_left = int(update_match.group(1))
            reset_ts_ms = int(update_match.group(2))
            max_council = int(update_match.group(3))
            
            # Check for invalid max_council value and replace both with 0
            if max_council >= INVALID_MAX_COUNCIL:
                max_council = 0
                council_left = 0
            
            self.vendor_data[self.current_character][self.current_vendor_npc_id] = (council_left, reset_ts_ms, max_council)
            
            npc_name = self.npc_mappings.get(self.current_vendor_npc_id, f"Unknown_{self.current_vendor_npc_id}")
            self.last_scan_updates.append((self.current_character, self.current_vendor_npc_id, npc_name))
            return True
        
        return False
    
    def get_scan_result(self) -> ScanResult:
        """Get current accumulated data as a ScanResult."""
        return ScanResult(
            self.characters_found.copy(),
            self.npc_mappings.copy(),
            self.npc_zones.copy(),
            {char: vendors.copy() for char, vendors in self.vendor_data.items()},
            []
        )
    
    def reset(self):
        """Reset the watcher to re-read from the beginning."""
        self.last_position = 0
        self.last_size = 0
        self.current_character = None
        self.current_zone = "Unknown"
        self.current_vendor_npc_id = None


# ---------------------
# Log Scanner (Full File Scan)
# ---------------------
class PlayerLogScanner:
    """Scans Player.log to extract vendor information."""
    
    def __init__(self, log_path: str):
        self.log_path = log_path
    
    def scan(self) -> ScanResult:
        """Scan the log file and extract vendor data."""
        characters_found: Set[str] = set()
        npc_mappings: Dict[int, str] = {}
        npc_zones: Dict[int, str] = {}
        vendor_data: Dict[str, Dict[int, Tuple[int, int, int]]] = {}
        errors: List[str] = []
        
        current_character: Optional[str] = None
        current_zone: str = "Unknown"
        current_vendor_npc_id: Optional[int] = None  # Track the currently open vendor
        
        if not os.path.exists(self.log_path):
            errors.append(f"Log file not found: {self.log_path}")
            return ScanResult(characters_found, npc_mappings, npc_zones, vendor_data, errors)
        
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        # Check for character login
                        login_match = PATTERN_LOGIN.search(line)
                        if login_match:
                            char_name = login_match.group(1)
                            # Skip numeric IDs (like Steam IDs)
                            if not char_name.isdigit():
                                current_character = char_name
                                characters_found.add(char_name)
                                if char_name not in vendor_data:
                                    vendor_data[char_name] = {}
                            continue
                        
                        # Check for area/zone change
                        area_match = PATTERN_AREA.search(line)
                        if area_match:
                            current_zone = area_match.group(1)
                            continue
                        
                        # Check for NPC interaction (mapping ID to name and zone)
                        interaction_match = PATTERN_INTERACTION.search(line)
                        if interaction_match:
                            npc_id = int(interaction_match.group(1))
                            npc_name_raw = interaction_match.group(2)
                            # Remove NPC_ prefix if present
                            if npc_name_raw.startswith('NPC_'):
                                npc_name = npc_name_raw[4:]
                            else:
                                npc_name = npc_name_raw
                            npc_mappings[npc_id] = npc_name
                            # Associate zone with this NPC (VendorFox is special - can be anywhere)
                            if npc_name == 'VendorFox':
                                npc_zones[npc_id] = 'Anywhere'
                            else:
                                npc_zones[npc_id] = current_zone
                            continue
                        
                        # Check for vendor screen (council data) - this opens a vendor
                        vendor_match = PATTERN_VENDOR_SCREEN.search(line)
                        if vendor_match and current_character:
                            npc_id = int(vendor_match.group(1))
                            # group(2) is category like SoulMates - not used
                            council_left = int(vendor_match.group(3))
                            reset_ts_ms = int(vendor_match.group(4))
                            max_council = int(vendor_match.group(5))
                            
                            # Check for invalid max_council value and replace both with 0
                            if max_council >= INVALID_MAX_COUNCIL:
                                max_council = 0
                                council_left = 0
                            
                            # Store vendor data for current character
                            vendor_data[current_character][npc_id] = (council_left, reset_ts_ms, max_council)
                            
                            # Track this as the currently open vendor
                            current_vendor_npc_id = npc_id
                            continue
                        
                        # Check for vendor gold update (after selling items)
                        update_match = PATTERN_VENDOR_UPDATE_GOLD.search(line)
                        if update_match and current_character and current_vendor_npc_id is not None:
                            council_left = int(update_match.group(1))
                            reset_ts_ms = int(update_match.group(2))
                            max_council = int(update_match.group(3))
                            
                            # Check for invalid max_council value and replace both with 0
                            if max_council >= INVALID_MAX_COUNCIL:
                                max_council = 0
                                council_left = 0
                            
                            # Update the currently open vendor's data
                            vendor_data[current_character][current_vendor_npc_id] = (council_left, reset_ts_ms, max_council)
                            continue
                    
                    except Exception as e:
                        errors.append(f"Line {line_num}: {str(e)}")
        
        except Exception as e:
            errors.append(f"Error reading log file: {str(e)}")
        
        return ScanResult(characters_found, npc_mappings, npc_zones, vendor_data, errors)


# ---------------------
# Database Layer
# ---------------------
class VendorDatabase:
    """Handles all database operations."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_data_dir()
        self.init_database()
    
    def _ensure_data_dir(self):
        """Ensure data directory exists."""
        data_dir = os.path.dirname(self.db_path)
        try:
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)
        except OSError as e:
            raise RuntimeError(f"Could not create data directory: {e}")
    
    def init_database(self):
        """Initialize database tables."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # NPC ID to Name mapping table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS npc_mappings (
                        npc_id INTEGER PRIMARY KEY,
                        npc_name TEXT NOT NULL,
                        zone TEXT DEFAULT '',
                        last_updated TEXT NOT NULL
                    )
                ''')
                
                # Vendors table (per character) - identified by name+zone, not npc_id
                # npc_id is stored for reference but not as unique key since the game
                # assigns different IDs to the same NPC in different sessions
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS vendors (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        character_name TEXT NOT NULL,
                        npc_id INTEGER NOT NULL,
                        npc_name TEXT NOT NULL,
                        zone TEXT NOT NULL,
                        council_left INTEGER NOT NULL,
                        last_reset TEXT NOT NULL,
                        reset_maximum INTEGER NOT NULL,
                        categories TEXT NOT NULL,
                        muted BOOLEAN NOT NULL,
                        UNIQUE(character_name, npc_name, zone)
                    )
                ''')
                
                # Transactions table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        character_name TEXT NOT NULL,
                        vendor_name TEXT NOT NULL,
                        npc_id INTEGER,
                        transaction_type TEXT NOT NULL,
                        council_before INTEGER NOT NULL,
                        council_after INTEGER NOT NULL,
                        council_change INTEGER NOT NULL,
                        timestamp TEXT NOT NULL,
                        notes TEXT
                    )
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_transactions_lookup 
                    ON transactions(character_name, vendor_name, timestamp)
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_vendors_character 
                    ON vendors(character_name)
                ''')
                
                # Check if we need to migrate from old schema (npc_id unique) to new (name+zone unique)
                self._migrate_to_name_zone_unique(conn)
                
                conn.commit()
        except sqlite3.Error as e:
            raise RuntimeError(f"Could not initialize database: {e}")
    
    def _migrate_to_name_zone_unique(self, conn):
        """
        Migrate from old schema (UNIQUE on npc_id) to new schema (UNIQUE on name+zone).
        This consolidates duplicate vendors that have the same name and zone but different npc_ids.
        """
        cursor = conn.cursor()
        
        # Check if migration is needed by looking for duplicates
        cursor.execute('''
            SELECT character_name, npc_name, zone, COUNT(*) as cnt
            FROM vendors
            GROUP BY character_name, npc_name, zone
            HAVING cnt > 1
        ''')
        duplicates = cursor.fetchall()
        
        if not duplicates:
            return  # No migration needed
        
        print(f"Migrating database: consolidating {len(duplicates)} duplicate vendor groups...")
        
        for char_name, npc_name, zone, count in duplicates:
            # Get all duplicate entries for this vendor
            cursor.execute('''
                SELECT id, npc_id, council_left, last_reset, reset_maximum, categories, muted
                FROM vendors
                WHERE character_name = ? AND npc_name = ? AND zone = ?
                ORDER BY last_reset DESC
            ''', (char_name, npc_name, zone))
            rows = cursor.fetchall()
            
            if len(rows) <= 1:
                continue
            
            # Keep the entry with the most recent last_reset, but use highest reset_maximum
            # and most recent council_left
            keep_id = rows[0][0]
            keep_npc_id = rows[0][1]
            best_council_left = rows[0][2]
            best_last_reset = rows[0][3]
            best_reset_maximum = max(row[4] for row in rows)
            keep_categories = rows[0][5]
            keep_muted = rows[0][6]
            
            # Find most recent npc_id (highest is usually most recent)
            best_npc_id = max(row[1] for row in rows)
            
            # Update the kept row with best values
            cursor.execute('''
                UPDATE vendors
                SET npc_id = ?, council_left = ?, reset_maximum = ?
                WHERE id = ?
            ''', (best_npc_id, best_council_left, best_reset_maximum, keep_id))
            
            # Delete the duplicate rows
            ids_to_delete = [row[0] for row in rows if row[0] != keep_id]
            for del_id in ids_to_delete:
                cursor.execute('DELETE FROM vendors WHERE id = ?', (del_id,))
            
            print(f"  Consolidated {count} entries for {npc_name} ({zone}) -> kept npc_id {best_npc_id}")
        
        conn.commit()
        print("Migration complete.")
    
    def save_npc_mapping(self, npc_id: int, npc_name: str, zone: str = ''):
        """Save or update NPC ID to name mapping."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO npc_mappings (npc_id, npc_name, zone, last_updated)
                    VALUES (?, ?, ?, ?)
                ''', (npc_id, npc_name, zone, datetime.now().isoformat()))
                conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving NPC mapping: {e}")
    
    def get_npc_name(self, npc_id: int) -> Optional[str]:
        """Get NPC name from ID."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT npc_name FROM npc_mappings WHERE npc_id = ?', (npc_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except sqlite3.Error as e:
            print(f"Error getting NPC name: {e}")
            return None
    
    def get_npc_zone(self, npc_id: int) -> str:
        """Get NPC zone from ID."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT zone FROM npc_mappings WHERE npc_id = ?', (npc_id,))
                row = cursor.fetchone()
                return row[0] if row and row[0] else 'Unknown'
        except sqlite3.Error as e:
            print(f"Error getting NPC zone: {e}")
            return 'Unknown'
    
    def update_npc_zone(self, npc_id: int, zone: str):
        """Update zone for an NPC."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE npc_mappings SET zone = ?, last_updated = ? WHERE npc_id = ?
                ''', (zone, datetime.now().isoformat(), npc_id))
                conn.commit()
        except sqlite3.Error as e:
            print(f"Error updating NPC zone: {e}")
    
    def save_vendors(self, vendors: List['Vendor'], character_name: str):
        """Save vendors for a character."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM vendors WHERE character_name = ?', (character_name,))
                for vendor in vendors:
                    cursor.execute('''
                        INSERT INTO vendors (
                            character_name, npc_id, npc_name, zone, council_left,
                            last_reset, reset_maximum, categories, muted
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        character_name,
                        vendor.npc_id,
                        vendor.name,
                        vendor.zone,
                        vendor.council_left,
                        vendor.last_reset.isoformat(),
                        vendor.reset_maximum,
                        json.dumps(vendor.categories),
                        vendor.muted
                    ))
                conn.commit()
        except sqlite3.Error as e:
            raise RuntimeError(f"Could not save vendors: {e}")
    
    def load_vendors(self, character_name: str) -> List['Vendor']:
        """Load vendors for a character."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT npc_id, npc_name, zone, council_left, last_reset, 
                           reset_maximum, categories, muted 
                    FROM vendors WHERE character_name = ?
                ''', (character_name,))
                rows = cursor.fetchall()
                
                vendors = []
                for row in rows:
                    try:
                        vendor = Vendor(
                            npc_id=row[0],
                            name=row[1],
                            zone=row[2],
                            council_left=row[3],
                            last_reset=row[4],
                            reset_maximum=row[5],
                            categories=json.loads(row[6]),
                            muted=row[7]
                        )
                        vendors.append(vendor)
                    except (ValueError, json.JSONDecodeError) as e:
                        print(f"Error loading vendor {row[1]}: {e}")
                
                return vendors
        except sqlite3.Error as e:
            raise RuntimeError(f"Could not load vendors for {character_name}: {e}")
    
    def get_vendor_by_npc_id(self, character_name: str, npc_id: int) -> Optional['Vendor']:
        """Get a specific vendor by NPC ID (legacy method, prefer get_vendor_by_name_zone)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT npc_id, npc_name, zone, council_left, last_reset, 
                           reset_maximum, categories, muted 
                    FROM vendors WHERE character_name = ? AND npc_id = ?
                ''', (character_name, npc_id))
                row = cursor.fetchone()
                
                if row:
                    return Vendor(
                        npc_id=row[0],
                        name=row[1],
                        zone=row[2],
                        council_left=row[3],
                        last_reset=row[4],
                        reset_maximum=row[5],
                        categories=json.loads(row[6]),
                        muted=row[7]
                    )
                return None
        except sqlite3.Error as e:
            print(f"Error getting vendor: {e}")
            return None
    
    def get_vendor_by_name_zone(self, character_name: str, npc_name: str, zone: str) -> Optional['Vendor']:
        """Get a specific vendor by name and zone (the canonical lookup method)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT npc_id, npc_name, zone, council_left, last_reset, 
                           reset_maximum, categories, muted 
                    FROM vendors WHERE character_name = ? AND npc_name = ? AND zone = ?
                ''', (character_name, npc_name, zone))
                row = cursor.fetchone()
                
                if row:
                    return Vendor(
                        npc_id=row[0],
                        name=row[1],
                        zone=row[2],
                        council_left=row[3],
                        last_reset=row[4],
                        reset_maximum=row[5],
                        categories=json.loads(row[6]),
                        muted=row[7]
                    )
                return None
        except sqlite3.Error as e:
            print(f"Error getting vendor by name/zone: {e}")
            return None
    
    def get_all_characters(self) -> List[str]:
        """Get all unique character names."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT DISTINCT character_name FROM vendors')
                characters = [row[0] for row in cursor.fetchall()]
                if DEFAULT_CHARACTER not in characters:
                    characters.append(DEFAULT_CHARACTER)
                return sorted(characters)
        except sqlite3.Error as e:
            print(f"Error fetching characters: {e}")
            return [DEFAULT_CHARACTER]
    
    def log_transaction(self, character_name: str, vendor_name: str, 
                        transaction_type: str, council_before: int, 
                        council_after: int, notes: Optional[str] = None,
                        npc_id: Optional[int] = None):
        """Log a transaction."""
        try:
            council_change = council_after - council_before
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO transactions (
                        character_name, vendor_name, npc_id, transaction_type,
                        council_before, council_after, council_change,
                        timestamp, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    character_name, vendor_name, npc_id, transaction_type,
                    council_before, council_after, council_change,
                    datetime.now().isoformat(), notes
                ))
                conn.commit()
        except sqlite3.Error as e:
            print(f"Error logging transaction: {e}")
    
    def get_council_earned(self, character_name: str, 
                           vendor_name: Optional[str] = None, 
                           days: int = 7) -> int:
        """
        Get total council earned in the last N days by summing negative 
        council_change values from transactions (negative = you sold to vendor = earned).
        This matches the logic used in the Daily Earnings tab of Transaction History.
        """
        try:
            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                if vendor_name:
                    cursor.execute('''
                        SELECT COALESCE(SUM(ABS(council_change)), 0) FROM transactions
                        WHERE character_name = ? AND vendor_name = ?
                        AND timestamp >= ?
                        AND council_change < 0
                        AND transaction_type != 'deletion'
                    ''', (character_name, vendor_name, cutoff_date))
                else:
                    cursor.execute('''
                        SELECT COALESCE(SUM(ABS(council_change)), 0) FROM transactions
                        WHERE character_name = ?
                        AND timestamp >= ?
                        AND council_change < 0
                        AND transaction_type != 'deletion'
                    ''', (character_name, cutoff_date))
                
                return cursor.fetchone()[0]
        except sqlite3.Error as e:
            print(f"Error getting council earned: {e}")
            return 0
    
                                                              
                                                                      
                                                     
                          
                                                             
                                                        
                                                                                                
                                  
                                                        
                                  
                                                         
        
                          
                                                           
                                                     
                                           
                                      
        
                      
                                                    
                                                        
            
                              
                                                 
                                                            
                                  
                                                            
            
                                         
                                      
        
                    
    
                                                                                            
                                               
                        
                          
                                                                     
                                    
                               
                                   
        
                                                                
                              
                                                                 
                                                            
                                                                                                    
                                      
                                                            
                                      
                                                             
            
                                                        
                              
                                                 
                                                            
                                  
                                                            
            
                                         
                                      
            
                                 
        
                           
    
    def get_transactions(self, character_name: str, 
                         vendor_name: Optional[str] = None,
                         start_date: Optional[datetime] = None,
                         end_date: Optional[datetime] = None) -> List[Tuple]:
        """Query transactions within a timeframe."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                query = 'SELECT * FROM transactions WHERE character_name = ?'
                params = [character_name]
                
                if vendor_name:
                    query += ' AND vendor_name = ?'
                    params.append(vendor_name)
                
                if start_date:
                    query += ' AND timestamp >= ?'
                    params.append(start_date.isoformat() if isinstance(start_date, datetime) else start_date)
                
                if end_date:
                    query += ' AND timestamp <= ?'
                    params.append(end_date.isoformat() if isinstance(end_date, datetime) else end_date)
                
                query += ' ORDER BY timestamp DESC'
                
                cursor.execute(query, params)
                return cursor.fetchall()
        except sqlite3.Error as e:
            print(f"Error querying transactions: {e}")
            return []


# ---------------------
# Settings Manager
# ---------------------
class SettingsManager:
    """Manages application settings."""
    
    def __init__(self, settings_path: str):
        self.settings_path = settings_path
        self.settings = self._load_settings()
    
    def _load_settings(self) -> Dict:
        """Load settings from file."""
        default_settings = {
            'log_path': DEFAULT_LOG_PATH
        }
        
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, 'r') as f:
                    loaded = json.load(f)
                    default_settings.update(loaded)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error loading settings: {e}")
        
        return default_settings
    
    def save_settings(self):
        """Save settings to file."""
        try:
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            with open(self.settings_path, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except IOError as e:
            print(f"Error saving settings: {e}")
    
    def get(self, key: str, default=None):
        """Get a setting value."""
        return self.settings.get(key, default)
    
    def set(self, key: str, value):
        """Set a setting value."""
        self.settings[key] = value
        self.save_settings()


# ---------------------
# Vendor Model
# ---------------------
class Vendor:
    """Vendor model with business logic."""
    
    def __init__(self, name: str, zone: str, council_left: int, 
                 last_reset, reset_maximum: int = 0, 
                 categories: Optional[List[str]] = None, 
                 muted: bool = False,
                 npc_id: int = 0):
        self.npc_id = npc_id
        self.name = name
        self.zone = zone
        
        # Handle invalid maximum values - if reset_maximum is invalid, set both to 0
        if reset_maximum >= INVALID_MAX_COUNCIL:
            reset_maximum = 0
            council_left = 0
        
        self.council_left = int(council_left)
        self.last_reset = self._parse_last_reset(last_reset, name)
        self.reset_maximum = int(reset_maximum)
        self.categories = categories or []
        self.muted = bool(muted)
    
    @staticmethod
    def _parse_last_reset(last_reset, vendor_name: str) -> datetime:
        """Parse last_reset into datetime."""
        if isinstance(last_reset, str):
            try:
                return datetime.fromisoformat(last_reset)
            except ValueError:
                try:
                    return datetime.fromtimestamp(float(last_reset))
                except (ValueError, OverflowError):
                    print(f"Warning: Invalid last_reset for {vendor_name}, using current time")
                    return datetime.now()
        elif isinstance(last_reset, datetime):
            return last_reset
        else:
            print(f"Warning: Unknown last_reset type for {vendor_name}, using current time")
            return datetime.now()
    
    @classmethod
    def from_scan_data(cls, npc_id: int, npc_name: str, zone: str,
                       council_left: int, reset_ts_ms: int, max_council: int) -> 'Vendor':
        """Create a Vendor from scanned log data."""
        # Check for invalid max_council and replace both values with 0
        if max_council >= INVALID_MAX_COUNCIL:
            max_council = 0
            council_left = 0
        
        if reset_ts_ms == 0:
            # Vendor at full, assume just reset (or within 7 days)
            last_reset = datetime.now()
        else:
            # Calculate last reset from the reset timestamp
            reset_time = datetime.fromtimestamp(reset_ts_ms / 1000.0)
            last_reset = reset_time - timedelta(days=7)
        
        return cls(
            npc_id=npc_id,
            name=npc_name,
            zone=zone,
            council_left=council_left,
            last_reset=last_reset,
            reset_maximum=max_council,
            categories=[],
            muted=False
        )
    
    @property
    def next_reset(self) -> datetime:
        """Calculate next reset time."""
        return self.last_reset + timedelta(days=7)
    
    @property
    def is_ready_to_reset(self) -> bool:
        """Check if vendor is ready to reset."""
        return datetime.now() >= self.next_reset
    
    @property
    def is_empty(self) -> bool:
        """Check if vendor has no council left (under 1k treated as empty)."""
        return self.council_left < 1000
    
    @property
    def time_until_reset(self) -> TimeUntilReset:
        """Get time until next reset."""
        return TimeUntilReset.from_timedelta(self.next_reset - datetime.now())
    
    def matches_filter(self, filter_text: str) -> bool:
        """
        Check if vendor matches filter text.
        Supports multi-term searching with AND logic.
        """
        if not filter_text:
            return True

        search_terms = filter_text.lower().split()
        vendor_info = " ".join([
            self.name.lower(),
            self.zone.lower(),
            *map(str.lower, self.categories)
        ])

        return all(term in vendor_info for term in search_terms)


# ---------------------
# Utility Functions
# ---------------------
def format_number(value) -> str:
    """Format number with K/M suffixes."""
    try:
        value = int(value)
    except (ValueError, TypeError):
        return str(value)
    
    if value == 0:
        return "0"
    elif abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    elif abs(value) >= 1_000:
        return f"{value // 1000}K"
    else:
        return str(value)


def calculate_border_color(vendor: Vendor, min_time: float, max_time: float) -> str:
    """Calculate border color based on reset time."""
    if vendor.is_ready_to_reset:
        return COLOR_RESET_READY
    
    time_diff_seconds = (vendor.next_reset - datetime.now()).total_seconds()
    
    if max_time > min_time:
        ratio = (time_diff_seconds - min_time) / (max_time - min_time)
    else:
        ratio = 1.0
    
    r = int(255 * (1 - ratio))
    g = int(255 * ratio)
    b = 0
    return f"#{r:02x}{g:02x}{b:02x}"


def calculate_pulse_color(frame: int) -> str:
    """Calculate pulsing color for ready vendors."""
    import math
    pulse_ratio = (1 + math.sin(frame * math.pi / PULSE_CYCLE_DIVISOR)) / 2
    r = int(255 - (255 - 50) * pulse_ratio)
    g = int(255 - (255 - 205) * pulse_ratio)
    b = int(0 + 50 * pulse_ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def format_reset_countdown(reset_ts_ms: int) -> str:
    """Format reset timestamp as countdown string."""
    if reset_ts_ms == 0:
        return "Full (recently reset)"
    
    reset_time = datetime.fromtimestamp(reset_ts_ms / 1000.0)
    now = datetime.now()
    td = reset_time - now
    
    if td.total_seconds() <= 0:
        return "Ready to reset!"
    
    time_obj = TimeUntilReset.from_timedelta(td)
    return f"Resets in: {time_obj.to_string()}"


# ---------------------
# UI Components
# ---------------------
class ScrollableFrame(tk.Frame):
    """
    A reusable scrollable frame component that handles mousewheel scrolling reliably
    by only binding scroll events when the mouse is over the frame.
    """
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        self.canvas = tk.Canvas(self)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.bind('<Enter>', self._bind_mousewheel)
        self.bind('<Leave>', self._unbind_mousewheel)

    def _on_mousewheel(self, event):
        """Handle mousewheel scrolling, compatible with Windows, macOS, and Linux."""
        try:
            if hasattr(event, 'delta'):
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")
        except tk.TclError:
            pass

    def _bind_mousewheel(self, event):
        """Bind mousewheel events when the mouse enters the frame."""
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>", self._on_mousewheel)
        self.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        """Unbind mousewheel events when the mouse leaves the frame."""
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")


class VendorForm:
    """Reusable form for adding/updating vendors."""
    
    def __init__(self, parent, vendor: Optional[Vendor] = None):
        self.parent = parent
        self.vendor = vendor
        self.is_update = vendor is not None
        
        self.name_entry = None
        self.zone_entry = None
        self.council_entry = None
        self.days_entry = None
        self.hours_entry = None
        self.minutes_entry = None
        self.max_time_override_var = BooleanVar(value=False)
        self.muted_var = BooleanVar(value=vendor.muted if vendor else False)
        self.cat_vars = {}
        self.custom_var = BooleanVar(value=False)
        self.custom_entry = None
    
    def create_form(self) -> tk.Frame:
        """Create and return the form frame."""
        form_frame = tk.Frame(self.parent)
        
        if not self.is_update:
            self._create_name_zone_fields(form_frame)
        else:
            Label(form_frame, text=f"Updating {self.vendor.name} ({self.vendor.zone})").pack(
                padx=10, pady=(8,2), anchor="w"
            )
            if self.vendor.npc_id:
                Label(form_frame, text=f"NPC ID: {self.vendor.npc_id}", fg="gray").pack(
                    padx=10, pady=(0,2), anchor="w"
                )
        
        self._create_council_field(form_frame)
        self._create_time_fields(form_frame)
        self._create_options_and_categories(form_frame)
        
        return form_frame
    
    def _create_name_zone_fields(self, parent):
        """Create name and zone entry fields."""
        Label(parent, text="Vendor Name:").pack(padx=10, pady=(8,2), anchor="w")
        self.name_entry = Entry(parent)
        self.name_entry.pack(padx=10, fill=tk.X)
        
        Label(parent, text="Vendor Zone:").pack(padx=10, pady=(8,2), anchor="w")
        self.zone_entry = Entry(parent)
        self.zone_entry.pack(padx=10, fill=tk.X)
    
    def _create_council_field(self, parent):
        """Create council entry field."""
        label_text = "New Council left (in K):" if self.is_update else "Council left (in K):"
        Label(parent, text=label_text).pack(padx=10, anchor="w")
        self.council_entry = Entry(parent)
        if self.vendor:
            self.council_entry.insert(0, str(self.vendor.council_left // 1000))
        self.council_entry.pack(padx=10, fill=tk.X)
    
    def _create_time_fields(self, parent):
        """Create time input fields."""
        time_frame = tk.Frame(parent)
        time_frame.pack(padx=10, pady=8, anchor="w", fill=tk.X)
        
        label_text = "Update reset time:" if self.is_update else "Time until reset:"
        Label(time_frame, text=label_text).pack(side=tk.LEFT)
        
        Label(time_frame, text="Days:").pack(side=tk.LEFT, padx=(8,0))
        self.days_entry = Entry(time_frame, width=5)
        self.days_entry.pack(side=tk.LEFT, padx=2)
        
        Label(time_frame, text="Hours:").pack(side=tk.LEFT, padx=(8,0))
        self.hours_entry = Entry(time_frame, width=5)
        self.hours_entry.pack(side=tk.LEFT, padx=2)
        
        Label(time_frame, text="Minutes:").pack(side=tk.LEFT, padx=(8,0))
        self.minutes_entry = Entry(time_frame, width=5)
        self.minutes_entry.pack(side=tk.LEFT, padx=2)
        
        if self.vendor:
            time_obj = self.vendor.time_until_reset
            self.days_entry.insert(0, str(max(0, time_obj.days)))
            self.hours_entry.insert(0, str(max(0, time_obj.hours)))
            self.minutes_entry.insert(0, str(max(0, time_obj.minutes)))
        else:
            self.days_entry.insert(0, '6')
            self.hours_entry.insert(0, '23')
            self.minutes_entry.insert(0, '59')
    
    def _create_options_and_categories(self, parent):
        """Create options checkboxes and category selection."""
        cat_override_row = tk.Frame(parent)
        cat_override_row.pack(padx=10, pady=6, anchor="w", fill=tk.X)
        
        left_options = tk.Frame(cat_override_row)
        left_options.pack(side=tk.LEFT, padx=(0,12), anchor="n")
        
        Checkbutton(left_options, text="Max-Time-Override", 
                    variable=self.max_time_override_var).pack(anchor="w")
        
        mute_text = "Muted" if self.is_update else "Start Muted"
        Checkbutton(left_options, text=mute_text, 
                    variable=self.muted_var).pack(anchor="w")
        
        cat_area_frame = tk.Frame(cat_override_row)
        cat_area_frame.pack(side=tk.LEFT, anchor="n")
        Label(cat_area_frame, text="Categories:").pack(anchor="w")
        cat_frame = tk.Frame(cat_area_frame)
        cat_frame.pack(anchor="w", pady=2)
        
        vendor_cats = self.vendor.categories if self.vendor else []
        self.cat_vars = {c: BooleanVar(value=(c in vendor_cats)) for c in VENDOR_CATEGORIES}
        
        for i, c in enumerate(VENDOR_CATEGORIES):
            r, col = divmod(i, 3)
            cb = Checkbutton(cat_frame, text=c, variable=self.cat_vars[c])
            cb.grid(row=r, column=col, sticky="w", padx=8, pady=4)
        
        custom_wrap = tk.Frame(cat_frame)
        custom_wrap.grid(row=1, column=2, sticky="w", padx=8, pady=4)
        cb_custom = Checkbutton(custom_wrap, text="Custom:", variable=self.custom_var)
        cb_custom.pack(side=tk.LEFT)
        self.custom_entry = Entry(custom_wrap, width=18)
        self.custom_entry.pack(side=tk.LEFT, padx=4)
        
        if self.vendor:
            custom_items = [c for c in self.vendor.categories if c not in VENDOR_CATEGORIES]
            if custom_items:
                self.custom_var.set(True)
                self.custom_entry.insert(0, ", ".join(custom_items))
    
    def get_values(self) -> Dict:
        """Extract and validate form values."""
        values = {}
        
        if not self.is_update:
            values['name'] = self.name_entry.get().strip()
            values['zone'] = self.zone_entry.get().strip()
            
            if not values['name']:
                raise ValueError("Vendor name cannot be empty.")
        
        try:
            council_input = float(self.council_entry.get() or 0)
            values['council'] = int(council_input * 1000)
        except (ValueError, TypeError):
            raise ValueError("Council must be numeric (K).")
        
        try:
            values['days'] = int(self.days_entry.get() or 0)
            values['hours'] = int(self.hours_entry.get() or 0)
            values['minutes'] = int(self.minutes_entry.get() or 0)
        except (ValueError, TypeError):
            raise ValueError("Days, Hours, Minutes must be integers.")
        
        override_flag = self.max_time_override_var.get()
        total_minutes = values['days'] * 24 * 60 + values['hours'] * 60 + values['minutes']
        if total_minutes > MAX_TOTAL_MINUTES and not override_flag:
            raise ValueError(f"Reset time cannot exceed {MAX_DAYS}d {MAX_HOURS}h {MAX_MINUTES}m unless Max-Time-Override is checked.")
        
        values['override_max_time'] = override_flag
        values['muted'] = self.muted_var.get()
        
        selected_cats = [c for c, var in self.cat_vars.items() if var.get()]
        if self.custom_var.get():
            cv = self.custom_entry.get().strip()
            if cv:
                extras = [x.strip() for x in cv.split(",") if x.strip()]
                selected_cats.extend(extras)
        
        seen = set()
        final_cats = []
        for c in selected_cats:
            if c not in seen:
                seen.add(c)
                final_cats.append(c)
        
        values['categories'] = final_cats
        
        return values


class VendorCard:
    """Display card for a single vendor."""
    
    def __init__(self, parent: tk.Frame, vendor: Vendor, 
                 min_time: float, max_time: float,
                 callbacks: Dict):
        self.parent = parent
        self.vendor = vendor
        self.callbacks = callbacks
        
        self.outer_frame = None
        self.time_label = None
        self.widgets_for_pulse = []
        
        self._create_card(min_time, max_time)
    
    def _create_card(self, min_time: float, max_time: float):
        """Create the vendor card UI."""
        border_color = calculate_border_color(self.vendor, min_time, max_time)
        bg_color = COLOR_EMPTY_BG if (self.vendor.is_empty and not self.vendor.is_ready_to_reset) else COLOR_NORMAL_BG
        
        self.outer_frame = tk.Frame(self.parent, bg=border_color)
        self.outer_frame.pack(fill=tk.X, padx=4, pady=4)
        self.outer_frame.vendor_name = self.vendor.name
        
        vf = tk.Frame(self.outer_frame, bg=bg_color)
        vf.pack(padx=2, pady=2, fill=tk.X)
        
        info = tk.Frame(vf, bg=bg_color)
        info.pack(fill=tk.X, padx=4, pady=2)
        
        left_info = tk.Frame(info, bg=bg_color)
        left_info.pack(side=tk.LEFT, anchor="w")
        
        name_label = Label(left_info, 
                           text=f"{self.vendor.name} ({self.vendor.zone})", 
                           bg=bg_color, font=("Arial", 10, "bold"))
        name_label.pack(anchor="w")
        
        council_str = f"Council: {format_number(self.vendor.council_left)}"
        if self.vendor.reset_maximum > 0:
            council_str += f" / Max: {format_number(self.vendor.reset_maximum)}"
        council_label = Label(left_info, text=council_str, bg=bg_color)
        council_label.pack(anchor="w")
        
        time_str = self._get_time_string()
        self.time_label = Label(info, text=time_str, bg=bg_color)
        self.time_label.pack(side=tk.RIGHT, anchor="e")
        self.outer_frame.time_label = self.time_label
        
        btns = tk.Frame(vf, bg=bg_color)
        btns.pack(fill=tk.X, padx=4, pady=2)
        
        Button(btns, text="Update", 
               command=lambda: self.callbacks['update'](self.vendor)).pack(side=tk.LEFT, padx=5, pady=2)
        Button(btns, text="Delete", 
               command=lambda: self.callbacks['delete'](self.vendor), 
               fg="red").pack(side=tk.LEFT, padx=5, pady=2)
        
        mute_text = "Unmute" if self.vendor.muted else "Mute"
        Button(btns, text=mute_text, 
               command=lambda: self.callbacks['toggle_mute'](self.vendor)).pack(side=tk.LEFT, padx=5, pady=2)
        
        if self.vendor.is_empty and self.vendor.is_ready_to_reset and not self.vendor.muted:
            self.widgets_for_pulse = [vf, info, left_info, name_label, council_label, self.time_label, btns]
    
    def _get_time_string(self) -> str:
        """Get formatted time string."""
        time_obj = self.vendor.time_until_reset
        if time_obj.days >= 0 and time_obj.hours >= 0 and time_obj.minutes >= 0:
            return time_obj.to_string()
        else:
            return "RESET PENDING!"


# ---------------------
# Transaction Window
# ---------------------
class TransactionWindow:
    """Window for viewing transaction history."""
    
    def __init__(self, parent, db: VendorDatabase, character_name: str):
        self.parent = parent
        self.db = db
        self.character_name = character_name
        
        self.window = Toplevel(parent)
        self.window.title("Transaction History")
        self.window.geometry("800x600")
        
        self.days_var = StringVar(value="7")
        self.trans_frame = None
        self.daily_frame = None
        
        self._create_ui()
        self.refresh_transactions()
    
    def _create_ui(self):
        """Create the transaction window UI."""
        controls = tk.Frame(self.window)
        controls.pack(fill=tk.X, padx=10, pady=10)
        
        Label(controls, text="Days to show:").pack(side=tk.LEFT, padx=5)
        days_entry = Entry(controls, textvariable=self.days_var, width=5)
        days_entry.pack(side=tk.LEFT, padx=5)
        Button(controls, text="Refresh", command=self.refresh_transactions).pack(side=tk.LEFT, padx=5)
        
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        trans_tab = ScrollableFrame(notebook)
        notebook.add(trans_tab, text="Transaction List")
        self.trans_frame = trans_tab.scrollable_frame
        
        daily_tab = ScrollableFrame(notebook)
        notebook.add(daily_tab, text="Daily Earnings")
        self.daily_frame = daily_tab.scrollable_frame
    
    def refresh_transactions(self):
        """Refresh transaction displays."""
        try:
            days = int(self.days_var.get())
            start_date = datetime.now() - timedelta(days=days)
            transactions = self.db.get_transactions(self.character_name, start_date=start_date)
            
            self._display_transaction_list(transactions)
            self._display_daily_earnings(transactions, days)
        except ValueError:
            messagebox.showerror("Error", "Days must be a number", parent=self.window)
    
    def _display_transaction_list(self, transactions: List[Tuple]):
        """Display transaction list."""
        for widget in self.trans_frame.winfo_children():
            widget.destroy()
        
        total_earned = 0
        for trans in transactions:
            # Updated to handle npc_id column
            trans_id, char, vendor, npc_id, trans_type, before, after, change, timestamp, notes = trans
            # Count negative changes as earned (sold to vendor), but exclude deletions
            if change < 0 and trans_type != 'deletion':
                total_earned += abs(change)
            
            color = "green" if change > 0 else "red" if change < 0 else "black"
            dt = datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M")
            
            trans_text = f"{dt} | {vendor} | {trans_type.upper()} | {format_number(change)} council"
            if notes:
                trans_text += f" | {notes}"
            
            label = Label(self.trans_frame, text=trans_text, fg=color, anchor="w")
            label.pack(fill=tk.X, padx=5, pady=2)
        
        summary = Label(self.trans_frame, 
                          text=f"\nTotal Council Earned: {format_number(total_earned)}", 
                          font=("Arial", 10, "bold"))
        summary.pack(fill=tk.X, padx=5, pady=10)
    
    def _display_daily_earnings(self, transactions: List[Tuple], days: int):
        """Display daily earnings grid."""
        for widget in self.daily_frame.winfo_children():
            widget.destroy()
        
        daily_earnings = defaultdict(int)
        for trans in transactions:
            trans_id, char, vendor, npc_id, trans_type, before, after, change, timestamp, notes = trans
            # Exclude deletions from daily earnings
            if change < 0 and trans_type != 'deletion':
                dt = datetime.fromisoformat(timestamp)
                date_key = dt.date()
                daily_earnings[date_key] += abs(change)
        
        all_dates = [(datetime.now() - timedelta(days=i)).date() for i in range(days)]
        
        Label(self.daily_frame, text="Daily Council Earned", 
              font=("Arial", 12, "bold")).grid(row=0, column=0, columnspan=2, padx=5, pady=10, sticky="w")
        
        Label(self.daily_frame, text="Date", font=("Arial", 10, "bold"), 
              anchor="w", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=1, column=0, sticky="ew")
        Label(self.daily_frame, text="Council Earned", font=("Arial", 10, "bold"), 
              anchor="e", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=1, column=1, sticky="ew")
        
        total_daily = 0
        row = 2
        for date in all_dates:
            earned = daily_earnings.get(date, 0)
            total_daily += earned
            
            weekday = date.strftime("%A")
            date_str = date.strftime("%Y-%m-%d")
            color = "green" if earned > 0 else "gray"
            date_text = f"{weekday}, {date_str}"
            council_text = format_number(earned)
            
            Label(self.daily_frame, text=date_text, fg=color, anchor="w", 
                  font=("Arial", 10), relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=0, sticky="ew")
            Label(self.daily_frame, text=council_text, fg=color, anchor="e", 
                  font=("Arial", 10), relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=1, sticky="ew")
            row += 1
        
        Label(self.daily_frame, text="Total:", font=("Arial", 10, "bold"), 
              anchor="w", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=0, sticky="ew")
        Label(self.daily_frame, text=f"{format_number(total_daily)}", 
              font=("Arial", 10, "bold"), anchor="e", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=1, sticky="ew")


# ---------------------
# Scan Results Window
# ---------------------
class ScanResultsWindow:
    """Window for displaying and selecting scan results."""
    
    def __init__(self, parent, scan_result: ScanResult, db: VendorDatabase, 
                 current_character: str, on_import_callback):
        self.parent = parent
        self.scan_result = scan_result
        self.db = db
        self.current_character = current_character
        self.on_import_callback = on_import_callback
        
        self.window = Toplevel(parent)
        self.window.title("Scan Results")
        self.window.geometry("900x600")
        
        self.char_var = StringVar()
        self.vendor_vars = {}  # npc_id -> BooleanVar
        
        self._create_ui()
    
    def _create_ui(self):
        """Create the scan results UI."""
        # Top section - character selection
        top_frame = tk.Frame(self.window)
        top_frame.pack(fill=tk.X, padx=10, pady=10)
        
        Label(top_frame, text="Characters found in log:", font=("Arial", 10, "bold")).pack(anchor="w")
        
        chars_found = sorted(self.scan_result.characters_found)
        if not chars_found:
            Label(top_frame, text="No characters found in log file.", fg="red").pack(anchor="w")
            return
        
        # Character selector
        char_frame = tk.Frame(top_frame)
        char_frame.pack(fill=tk.X, pady=5)
        Label(char_frame, text="Import data for character:").pack(side=tk.LEFT)
        
        # Default to current character if found, otherwise first found
        default_char = self.current_character if self.current_character in chars_found else chars_found[0]
        self.char_var.set(default_char)
        
        char_menu = OptionMenu(char_frame, self.char_var, *chars_found, command=self._on_char_select)
        char_menu.pack(side=tk.LEFT, padx=5)
        
        # Stats
        stats_text = f"Found {len(self.scan_result.npc_mappings)} NPCs, {len(chars_found)} characters"
        Label(top_frame, text=stats_text, fg="gray").pack(anchor="w")
        
        # Vendor list
        Label(self.window, text="Vendors with data:", font=("Arial", 10, "bold")).pack(padx=10, anchor="w")
        
        list_frame = ScrollableFrame(self.window)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.vendor_list_frame = list_frame.scrollable_frame
        
        self._populate_vendor_list()
        
        # Buttons
        btn_frame = tk.Frame(self.window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        Button(btn_frame, text="Select All", command=self._select_all).pack(side=tk.LEFT, padx=5)
        Button(btn_frame, text="Deselect All", command=self._deselect_all).pack(side=tk.LEFT, padx=5)
        Button(btn_frame, text="Import Selected", command=self._import_selected, 
               bg="green", fg="white").pack(side=tk.RIGHT, padx=5)
        Button(btn_frame, text="Cancel", command=self.window.destroy).pack(side=tk.RIGHT, padx=5)
        
        # Errors section
        if self.scan_result.errors:
            error_frame = tk.Frame(self.window)
            error_frame.pack(fill=tk.X, padx=10, pady=5)
            Label(error_frame, text=f"Warnings ({len(self.scan_result.errors)}):", fg="orange").pack(anchor="w")
            error_text = "; ".join(self.scan_result.errors[:5])
            if len(self.scan_result.errors) > 5:
                error_text += f"... and {len(self.scan_result.errors) - 5} more"
            Label(error_frame, text=error_text, fg="gray", wraplength=850).pack(anchor="w")
    
    def _populate_vendor_list(self):
        """Populate the vendor list for selected character."""
        for widget in self.vendor_list_frame.winfo_children():
            widget.destroy()
        self.vendor_vars.clear()
        
        char = self.char_var.get()
        if char not in self.scan_result.vendor_data:
            Label(self.vendor_list_frame, text="No vendor data for this character.").pack(anchor="w")
            return
        
        vendor_data = self.scan_result.vendor_data[char]
        
        # Sort by NPC name
        sorted_vendors = []
        for npc_id, (council_left, reset_ts_ms, max_council) in vendor_data.items():
            npc_name = self.scan_result.npc_mappings.get(npc_id, f"Unknown_{npc_id}")
            zone = self.scan_result.npc_zones.get(npc_id, "Unknown")
            sorted_vendors.append((npc_name, npc_id, council_left, reset_ts_ms, max_council, zone))
        sorted_vendors.sort(key=lambda x: x[0])
        
        for npc_name, npc_id, council_left, reset_ts_ms, max_council, zone in sorted_vendors:
            row_frame = tk.Frame(self.vendor_list_frame)
            row_frame.pack(fill=tk.X, pady=2)
            
            var = BooleanVar(value=True)
            self.vendor_vars[npc_id] = var
            
            cb = Checkbutton(row_frame, variable=var)
            cb.pack(side=tk.LEFT)
            
            # Vendor info with zone
            info_text = f"{npc_name} ({zone})"
            Label(row_frame, text=info_text, font=("Arial", 10, "bold"), width=30, anchor="w").pack(side=tk.LEFT)
            
            council_text = f"Council: {format_number(council_left)} / {format_number(max_council)}"
            Label(row_frame, text=council_text, width=20, anchor="w").pack(side=tk.LEFT)
            
            reset_text = format_reset_countdown(reset_ts_ms)
            Label(row_frame, text=reset_text, fg="blue", width=25, anchor="w").pack(side=tk.LEFT)
    
    def _on_char_select(self, *args):
        """Handle character selection change."""
        self._populate_vendor_list()
    
    def _select_all(self):
        """Select all vendors."""
        for var in self.vendor_vars.values():
            var.set(True)
    
    def _deselect_all(self):
        """Deselect all vendors."""
        for var in self.vendor_vars.values():
            var.set(False)
    
    def _import_selected(self):
        """Import selected vendors."""
        char = self.char_var.get()
        selected_ids = [npc_id for npc_id, var in self.vendor_vars.items() if var.get()]
        
        if not selected_ids:
            messagebox.showwarning("No Selection", "Please select at least one vendor to import.", parent=self.window)
            return
        
        # Save NPC mappings and zones first
        for npc_id, npc_name in self.scan_result.npc_mappings.items():
            zone = self.scan_result.npc_zones.get(npc_id, '')
            self.db.save_npc_mapping(npc_id, npc_name, zone)
        
        # Import vendor data
        imported = 0
        updated = 0
        
        for npc_id in selected_ids:
            if npc_id not in self.scan_result.vendor_data.get(char, {}):
                continue
            
            council_left, reset_ts_ms, max_council = self.scan_result.vendor_data[char][npc_id]
            npc_name = self.scan_result.npc_mappings.get(npc_id, f"Unknown_{npc_id}")
            zone = self.scan_result.npc_zones.get(npc_id, self.db.get_npc_zone(npc_id))
            
            # Check if vendor exists
            existing = self.db.get_vendor_by_npc_id(char, npc_id)
            
            if existing:
                # Update existing vendor
                old_council = existing.council_left
                
                # Calculate last reset from timestamp
                if reset_ts_ms == 0:
                    existing.last_reset = datetime.now()
                else:
                    reset_time = datetime.fromtimestamp(reset_ts_ms / 1000.0)
                    existing.last_reset = reset_time - timedelta(days=7)
                
                existing.council_left = council_left
                if max_council > existing.reset_maximum:
                    existing.reset_maximum = max_council
                
                # Update zone if we have new zone data
                if zone and zone != 'Unknown':
                    existing.zone = zone
                
                # Log transaction if council changed
                if old_council != council_left:
                    self.db.log_transaction(
                        char, npc_name, 'scan_update',
                        old_council, council_left,
                        f"Auto-scan update: {format_number(old_council)} → {format_number(council_left)}",
                        npc_id
                    )
                
                updated += 1
            else:
                # Create new vendor
                imported += 1
        
        # Trigger callback to refresh
        self.on_import_callback(char, selected_ids, self.scan_result)
        
        messagebox.showinfo(
            "Import Complete", 
            f"Imported {imported} new vendors, updated {updated} existing vendors for {char}.",
            parent=self.window
        )
        self.window.destroy()


# ---------------------
# Settings Window
# ---------------------
class SettingsWindow:
    """Window for application settings."""
    
    def __init__(self, parent, settings: SettingsManager, on_save_callback):
        self.parent = parent
        self.settings = settings
        self.on_save_callback = on_save_callback
        
        self.window = Toplevel(parent)
        self.window.title("Settings")
        self.window.geometry("600x200")
        
        self.log_path_var = StringVar(value=settings.get('log_path', DEFAULT_LOG_PATH))
        
        self._create_ui()
    
    def _create_ui(self):
        """Create settings UI."""
        # Log path
        path_frame = tk.Frame(self.window)
        path_frame.pack(fill=tk.X, padx=10, pady=10)
        
        Label(path_frame, text="Player.log Path:", font=("Arial", 10, "bold")).pack(anchor="w")
        
        entry_frame = tk.Frame(path_frame)
        entry_frame.pack(fill=tk.X, pady=5)
        
        path_entry = Entry(entry_frame, textvariable=self.log_path_var, width=60)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        Button(entry_frame, text="Browse...", command=self._browse_path).pack(side=tk.LEFT, padx=5)
        
        Label(path_frame, text="Default: " + DEFAULT_LOG_PATH, fg="gray", wraplength=550).pack(anchor="w")
        
        # Buttons
        btn_frame = tk.Frame(self.window)
        btn_frame.pack(fill=tk.X, padx=10, pady=20)
        
        Button(btn_frame, text="Save", command=self._save, bg="green", fg="white").pack(side=tk.RIGHT, padx=5)
        Button(btn_frame, text="Cancel", command=self.window.destroy).pack(side=tk.RIGHT, padx=5)
        Button(btn_frame, text="Reset to Default", command=self._reset_default).pack(side=tk.LEFT, padx=5)
    
    def _browse_path(self):
        """Open file browser for log path."""
        initial_dir = os.path.dirname(self.log_path_var.get())
        if not os.path.exists(initial_dir):
            initial_dir = os.path.expanduser("~")
        
        path = filedialog.askopenfilename(
            parent=self.window,
            title="Select Player.log",
            initialdir=initial_dir,
            filetypes=[("Log files", "*.log"), ("All files", "*.*")]
        )
        
        if path:
            self.log_path_var.set(path)
    
    def _reset_default(self):
        """Reset to default path."""
        self.log_path_var.set(DEFAULT_LOG_PATH)
    
    def _save(self):
        """Save settings."""
        self.settings.set('log_path', self.log_path_var.get())
        self.on_save_callback()
        messagebox.showinfo("Settings Saved", "Settings have been saved.", parent=self.window)
        self.window.destroy()


# ---------------------
# Main Application
# ---------------------
class VendorApp(tk.Tk):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.title("Vendor Reset Manager (Auto-Scan)")
        self.geometry("600x650")
        
        # Initialize settings and database
        self.settings = SettingsManager(SETTINGS_FILE)
        self.db = VendorDatabase(DATABASE_PATH)
        
        # State
        self.vendors = []
        self.characters = self.db.get_all_characters()
        self.current_character = self.characters[0] if self.characters else DEFAULT_CHARACTER
        self.vendors = self.db.load_vendors(self.current_character)
        
        # Animation state
        self.pulse_frame = 0
        self.pulse_widgets = []
        self.flash_phase = False
        self.timer_running = True
        
        # Auto-scan state
        self.auto_scan_enabled = False
        self.log_watcher: Optional[LogWatcher] = None
        self.auto_scan_status_label = None
        self.auto_scan_btn = None
        self.last_update_time = None
        
        # UI elements
        self.char_var = None
        self.char_menu = None
        self.filter_var = None
        self.show_muted_var = None
        self.scrollable_frame = None
        self.total_council_label = None
        self.total_max_label = None
        self.earned_7d_label = None
        
        self._create_ui()
        self._start_animations()
        self._init_auto_scan()
        
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def _create_ui(self):
        """Create the main UI."""
        self._create_top_bar()
        self._create_search_bar()
        self._create_info_bar()
        self._create_button_bar()
        self._create_vendor_list()
        
        self.update_vendor_list()
        self.update_total_values()
    
    def _create_search_bar(self):
        """Create search/filter bar on its own line."""
        search_frame = tk.Frame(self)
        search_frame.pack(fill=tk.X, padx=8, pady=4)
        
        Label(search_frame, text="Search:", font=("Arial", 10)).pack(side=tk.LEFT)
        self.filter_var = StringVar()
        self.filter_var.trace_add("write", lambda *a: self.update_vendor_list())
        filter_entry = Entry(search_frame, textvariable=self.filter_var, font=("Arial", 11))
        filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        # Multiple bindings to ensure filter updates reliably
        filter_entry.bind("<KeyRelease>", lambda e: self.update_vendor_list())
        filter_entry.bind("<Key>", lambda e: self.after(10, self.update_vendor_list))
    
    def _create_top_bar(self):
        """Create top bar with character selection."""
        top = tk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)
        
        Label(top, text="Character:").pack(side=tk.LEFT)
        self.char_var = StringVar(value=self.current_character)
        self.char_var.trace_add("write", self._on_char_change)
        self.char_menu = OptionMenu(top, self.char_var, *self.characters)
        self.char_menu.pack(side=tk.LEFT, padx=6)
        
        Button(top, text="View Transactions", command=self._open_transactions_window).pack(side=tk.LEFT, padx=6)
        
        self.show_muted_var = BooleanVar(value=False)
        self.show_muted_var.trace_add("write", lambda *a: self.update_vendor_list())
        Checkbutton(top, text="Show Muted", variable=self.show_muted_var).pack(side=tk.LEFT, padx=6)
    
    def _create_info_bar(self):
        """Create info bar with totals."""
        info = tk.Frame(self, bg="lightgrey", relief="raised", bd=1)
        info.pack(fill=tk.X, padx=8, pady=6)
        
        self.total_council_label = Label(info, text="Current Vendor Council Pool: 0K", bg="lightgrey")
        self.total_council_label.pack(side=tk.LEFT, padx=8, pady=6)
        
        self.total_max_label = Label(info, text="Total Vendor Cash: 0K", bg="lightgrey")
        self.total_max_label.pack(side=tk.LEFT, padx=8, pady=6)
        
        self.earned_7d_label = Label(info, text="Council earned (7d): 0K", bg="lightgrey")
        self.earned_7d_label.pack(side=tk.LEFT, padx=8, pady=6)
    
    def _create_button_bar(self):
        """Create button bar."""
        btns = tk.Frame(self)
        btns.pack(fill=tk.X, padx=8, pady=4)
        
        Button(btns, text="Add New Vendor", command=self._open_add_vendor_window).pack(side=tk.LEFT, padx=4)
        
        # Auto-scan toggle button
        self.auto_scan_btn = Button(btns, text="▶ Start Auto-Scan", command=self._toggle_auto_scan,
                                    bg="#28a745", fg="white", font=("Arial", 10, "bold"))
        self.auto_scan_btn.pack(side=tk.LEFT, padx=10)
        
        # Auto-scan status label
        self.auto_scan_status_label = Label(btns, text="Auto-scan: OFF", fg="gray")
        self.auto_scan_status_label.pack(side=tk.LEFT, padx=4)
        
        # Settings button
        Button(btns, text="⚙ Settings", command=self._open_settings_window).pack(side=tk.RIGHT, padx=4)
    
    def _init_auto_scan(self):
        """Initialize the auto-scan system."""
        log_path = self.settings.get('log_path', DEFAULT_LOG_PATH)
        self.log_watcher = LogWatcher(log_path, self._on_auto_scan_update)
        
        # Do an initial scan to populate the watcher's state
        if os.path.exists(log_path):
            self.log_watcher.check_for_updates()
    
    def _toggle_auto_scan(self):
        """Toggle auto-scan on/off."""
        self.auto_scan_enabled = not self.auto_scan_enabled
        
        if self.auto_scan_enabled:
            # Check if log file exists
            log_path = self.settings.get('log_path', DEFAULT_LOG_PATH)
            if not os.path.exists(log_path):
                messagebox.showwarning(
                    "Log File Not Found",
                    f"Cannot start auto-scan.\nPlayer.log not found at:\n{log_path}\n\nPlease check Settings.",
                    parent=self
                )
                self.auto_scan_enabled = False
                return
            
            # Reinitialize watcher with current path (starts from beginning)
            self.log_watcher = LogWatcher(log_path, self._on_auto_scan_update)
            
            # Update UI to show scanning
            self.auto_scan_btn.config(text="⏹ Stop Auto-Scan", bg="#dc3545")
            self.auto_scan_status_label.config(text="Scanning...", fg="blue")
            self.update()
            
            # Do initial full scan and import everything
            self.log_watcher.check_for_updates()
            self._import_all_from_watcher()
            
            self.auto_scan_status_label.config(text="Auto-scan: ACTIVE", fg="green")
            self._auto_scan_tick()
        else:
            self.auto_scan_btn.config(text="▶ Start Auto-Scan", bg="#28a745")
            self.auto_scan_status_label.config(text="Auto-scan: OFF", fg="gray")
    
    def _import_all_from_watcher(self):
        """Import all data from the watcher (full import on start)."""
        if not self.log_watcher:
            return
        
        scan_result = self.log_watcher.get_scan_result()
        
        if not scan_result.characters_found:
            return
        
        # Import all NPC mappings and zones
        for npc_id, npc_name in scan_result.npc_mappings.items():
            zone = scan_result.npc_zones.get(npc_id, 'Unknown')
            self.db.save_npc_mapping(npc_id, npc_name, zone)
        
        # Import vendor data for all characters
        for character in scan_result.characters_found:
            if character not in self.characters:
                self.characters.append(character)
            
            if character not in scan_result.vendor_data:
                continue
            
            char_vendors = self.db.load_vendors(character)
            # Create lookup by name+zone (the canonical identifier)
            existing_by_name_zone = {(v.name, v.zone): v for v in char_vendors}
            
            for npc_id, (council_left, reset_ts_ms, max_council) in scan_result.vendor_data[character].items():
                npc_name = scan_result.npc_mappings.get(npc_id, f"Unknown_{npc_id}")
                zone = scan_result.npc_zones.get(npc_id, 'Unknown')
                
                # VendorFox is special - always use 'Anywhere' as zone
                if npc_name == 'VendorFox':
                    zone = 'Anywhere'
                
                # Look up by name+zone (canonical identifier)
                key = (npc_name, zone)
                
                if key in existing_by_name_zone:
                    # Update existing vendor
                    vendor = existing_by_name_zone[key]
                    old_council = vendor.council_left
                    
                    if reset_ts_ms == 0:
                        vendor.last_reset = datetime.now()
                    else:
                        reset_time = datetime.fromtimestamp(reset_ts_ms / 1000.0)
                        vendor.last_reset = reset_time - timedelta(days=7)
                    
                    vendor.council_left = council_left
                    vendor.npc_id = npc_id  # Update to latest NPC ID
                    if max_council > vendor.reset_maximum:
                        vendor.reset_maximum = max_council
                    
                    if old_council != council_left:
                        self.db.log_transaction(
                            character, npc_name, 'auto_scan',
                            old_council, council_left,
                            f"Auto-scan: {format_number(old_council)} → {format_number(council_left)}",
                            npc_id
                        )
                else:
                    # Create new vendor
                    new_vendor = Vendor.from_scan_data(
                        npc_id, npc_name, zone,
                        council_left, reset_ts_ms, max_council
                    )
                    char_vendors.append(new_vendor)
                    existing_by_name_zone[key] = new_vendor  # Track by name+zone
                    
                    self.db.log_transaction(
                        character, npc_name, 'creation',
                        0, council_left,
                        f"Auto-scan import: {format_number(council_left)} council",
                        npc_id
                    )
            
            self.db.save_vendors(char_vendors, character)
        
        # Update character menu and refresh UI
        self.characters.sort()
        self._update_char_menu()
        self.vendors = self.db.load_vendors(self.current_character)
        self.update_vendor_list()
        self.update_total_values()
    
    def _auto_scan_tick(self):
        """Periodic check for new log data."""
        if not self.auto_scan_enabled or not self.timer_running:
            return
        
        try:
            if self.log_watcher and self.log_watcher.check_for_updates():
                self._apply_watcher_updates()
        except Exception as e:
            print(f"Auto-scan error: {e}")
        
        # Schedule next tick
        self.after(AUTO_SCAN_INTERVAL_MS, self._auto_scan_tick)
    
    def _apply_watcher_updates(self):
        """Apply updates from the log watcher to the database and UI."""
        if not self.log_watcher:
            return
        
        updates = self.log_watcher.last_scan_updates
        if not updates:
            return
        
        # Get current data from watcher
        scan_result = self.log_watcher.get_scan_result()
        
        # Track which characters were updated
        updated_characters = set()
        
        for character, npc_id, npc_name in updates:
            updated_characters.add(character)
            
            # Ensure character exists in our list
            if character not in self.characters:
                self.characters.append(character)
                self.characters.sort()
                self._update_char_menu()
            
            # Save NPC mapping and zone
            zone = scan_result.npc_zones.get(npc_id, 'Unknown')
            self.db.save_npc_mapping(npc_id, npc_name, zone)
            
            # Get vendor data
            if character in scan_result.vendor_data and npc_id in scan_result.vendor_data[character]:
                council_left, reset_ts_ms, max_council = scan_result.vendor_data[character][npc_id]
                
                # Load vendors for this character
                char_vendors = self.db.load_vendors(character)
                
                # VendorFox is special - always use 'Anywhere' as zone
                if npc_name == 'VendorFox':
                    zone = 'Anywhere'
                
                # Look up by name+zone (canonical identifier)
                existing = next((v for v in char_vendors if v.name == npc_name and v.zone == zone), None)
                
                if existing:
                    # Update existing vendor
                    old_council = existing.council_left
                    
                    if reset_ts_ms == 0:
                        existing.last_reset = datetime.now()
                    else:
                        reset_time = datetime.fromtimestamp(reset_ts_ms / 1000.0)
                        existing.last_reset = reset_time - timedelta(days=7)
                    
                    existing.council_left = council_left
                    existing.npc_id = npc_id  # Update to latest NPC ID
                    if max_council > existing.reset_maximum:
                        existing.reset_maximum = max_council
                    # Note: zone is part of the vendor's identity now, don't update it
                    
                    # Log transaction if council changed
                    if old_council != council_left:
                        self.db.log_transaction(
                            character, npc_name, 'auto_scan',
                            old_council, council_left,
                            f"Auto-scan: {format_number(old_council)} → {format_number(council_left)}",
                            npc_id
                        )
                else:
                    # Create new vendor
                    new_vendor = Vendor.from_scan_data(
                        npc_id, npc_name, zone,
                        council_left, reset_ts_ms, max_council
                    )
                    char_vendors.append(new_vendor)
                    
                    self.db.log_transaction(
                        character, npc_name, 'creation',
                        0, council_left,
                        f"Auto-scan import: {format_number(council_left)} council",
                        npc_id
                    )
                
                # Save vendors for this character
                self.db.save_vendors(char_vendors, character)
        
        # Update UI if current character was affected
        if self.current_character in updated_characters:
            self.vendors = self.db.load_vendors(self.current_character)
            self.update_vendor_list()
            self.update_total_values()
        
        # Update status with last update time
        self.last_update_time = datetime.now()
        update_names = [name for _, _, name in updates]
        status_text = f"Auto-scan: Updated {', '.join(update_names[:3])}"
        if len(update_names) > 3:
            status_text += f" +{len(update_names)-3} more"
        status_text += f" ({self.last_update_time.strftime('%H:%M:%S')})"
        self.auto_scan_status_label.config(text=status_text, fg="green")
    
    def _on_auto_scan_update(self, character: str, npc_id: int, npc_name: str):
        """Callback when auto-scan finds new data (not currently used, updates batched)."""
        pass
    
    def _create_vendor_list(self):
        """Create scrollable vendor list."""
        vendor_frame = ScrollableFrame(self)
        vendor_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.scrollable_frame = vendor_frame.scrollable_frame
    
    def _start_animations(self):
        """Start animation timers."""
        self.after(TIMER_UPDATE_MS, self._update_timers)
        self.after(PULSE_UPDATE_MS, self._update_pulse_animation)
    
    def _on_char_change(self, *args):
        """Handle character change."""
        try:
            self.current_character = self.char_var.get()
            self.vendors = self.db.load_vendors(self.current_character)
            self.update_vendor_list()
            self.update_total_values()
        except Exception as e:
            self._show_error(f"Could not switch to character: {e}")
    
    def _add_new_character(self):
        """Add a new character."""
        name = simpledialog.askstring("New Character", "Enter new character name:", parent=self)
        if not name or not name.strip():
            return
        
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
        if not safe_name:
            self._show_error("Character name must contain alphanumeric characters.")
            return
        
        if safe_name in self.characters:
            self._show_error("Character already exists.")
            return
        
        self.characters.append(safe_name)
        self.characters.sort()
        self.char_var.set(safe_name)
        self._update_char_menu()
    
    def _update_char_menu(self):
        """Update character menu."""
        try:
            menu = self.char_menu["menu"]
            menu.delete(0, "end")
            for c in sorted(self.characters):
                menu.add_command(label=c, command=tk._setit(self.char_var, c))
        except Exception as e:
            print(f"Error updating character menu: {e}")
    
    def _open_transactions_window(self):
        """Open transaction history window."""
        TransactionWindow(self, self.db, self.current_character)
    
    def _open_settings_window(self):
        """Open settings window."""
        SettingsWindow(self, self.settings, lambda: None)
    
    def _scan_player_log(self):
        """Scan Player.log file for vendor data."""
        log_path = self.settings.get('log_path', DEFAULT_LOG_PATH)
        
        if not os.path.exists(log_path):
            response = messagebox.askyesno(
                "Log File Not Found",
                f"Player.log not found at:\n{log_path}\n\nWould you like to browse for it?",
                parent=self
            )
            if response:
                path = filedialog.askopenfilename(
                    parent=self,
                    title="Select Player.log",
                    filetypes=[("Log files", "*.log"), ("All files", "*.*")]
                )
                if path:
                    self.settings.set('log_path', path)
                    log_path = path
                else:
                    return
            else:
                return
        
        # Show scanning message
        self.config(cursor="wait")
        self.update()
        
        try:
            scanner = PlayerLogScanner(log_path)
            result = scanner.scan()
            
            self.config(cursor="")
            
            if not result.characters_found and not result.vendor_data:
                messagebox.showinfo(
                    "Scan Complete",
                    "No vendor data found in the log file.\n\nMake sure you've interacted with vendors in-game.",
                    parent=self
                )
                return
            
            # Show results window
            ScanResultsWindow(
                self, result, self.db, self.current_character,
                self._on_scan_import
            )
        
        except Exception as e:
            self.config(cursor="")
            self._show_error(f"Error scanning log file: {e}")
    
    def _on_scan_import(self, character: str, npc_ids: List[int], scan_result: ScanResult):
        """Handle import from scan results."""
        # Build vendor list for character
        vendors = self.db.load_vendors(character)
        existing_ids = {v.npc_id for v in vendors}
        
        for npc_id in npc_ids:
            if npc_id not in scan_result.vendor_data.get(character, {}):
                continue
            
            council_left, reset_ts_ms, max_council = scan_result.vendor_data[character][npc_id]
            npc_name = scan_result.npc_mappings.get(npc_id, f"Unknown_{npc_id}")
            zone = scan_result.npc_zones.get(npc_id, self.db.get_npc_zone(npc_id))
            if not zone or zone == 'Unknown':
                zone = 'Unknown'
            
            if npc_id in existing_ids:
                # Update existing
                for vendor in vendors:
                    if vendor.npc_id == npc_id:
                        old_council = vendor.council_left
                        
                        if reset_ts_ms == 0:
                            vendor.last_reset = datetime.now()
                        else:
                            reset_time = datetime.fromtimestamp(reset_ts_ms / 1000.0)
                            vendor.last_reset = reset_time - timedelta(days=7)
                        
                        vendor.council_left = council_left
                        if max_council > vendor.reset_maximum:
                            vendor.reset_maximum = max_council
                        
                        # Update zone if we have better data
                        if zone and zone != 'Unknown':
                            vendor.zone = zone
                        break
            else:
                # Create new vendor
                new_vendor = Vendor.from_scan_data(
                    npc_id, npc_name, zone,
                    council_left, reset_ts_ms, max_council
                )
                vendors.append(new_vendor)
                
                self.db.log_transaction(
                    character, npc_name, 'creation',
                    0, council_left,
                    f"Auto-scan import: {format_number(council_left)} council",
                    npc_id
                )
        
        # Save and refresh
        self.db.save_vendors(vendors, character)
        
        # Update character list if new
        if character not in self.characters:
            self.characters.append(character)
            self.characters.sort()
            self._update_char_menu()
        
        # Switch to imported character
        if character != self.current_character:
            self.char_var.set(character)
        else:
            self.vendors = vendors
            self.update_vendor_list()
            self.update_total_values()
    
    def _open_add_vendor_window(self):
        """Open window to add a new vendor."""
        add_window = Toplevel(self)
        add_window.title("Add New Vendor")
        add_window.geometry("640x360")
        
        form = VendorForm(add_window)
        form_frame = form.create_form()
        form_frame.pack(fill=tk.BOTH, expand=True)
        
        button_line = tk.Frame(add_window)
        button_line.pack(padx=10, pady=10, fill=tk.X)
        
        def add_and_save():
            try:
                values = form.get_values()
                
                time_obj = TimeUntilReset.from_inputs(
                    values['days'], values['hours'], values['minutes'], 
                    values['override_max_time']
                )
                last_reset = time_obj.calculate_last_reset(values['override_max_time'])
                
                new_vendor = Vendor(
                    name=values['name'],
                    zone=values['zone'],
                    council_left=values['council'],
                    last_reset=last_reset,
                    reset_maximum=values['council'],
                    categories=values['categories'],
                    muted=values['muted'],
                    npc_id=0
                )
                
                self.db.log_transaction(
                    self.current_character, values['name'], 'creation',
                    0, values['council'],
                    f"Vendor created with initial council: {format_number(values['council'])}"
                )
                
                self.vendors.append(new_vendor)
                self.db.save_vendors(self.vendors, self.current_character)
                self.update_vendor_list()
                self.update_total_values()
                
                messagebox.showinfo("Success", f"Vendor '{values['name']}' added.", parent=add_window)
                add_window.destroy()
            except ValueError as e:
                self._show_error(str(e), add_window)
            except Exception as e:
                self._show_error(f"Could not add vendor: {e}", add_window)
        
        Button(button_line, text="Add", command=add_and_save).pack(side=tk.RIGHT, padx=6)
        Button(button_line, text="Cancel", command=add_window.destroy).pack(side=tk.RIGHT)
    
    def _open_update_vendor_window(self, vendor: Vendor):
        """Open window to update a vendor."""
        update_window = Toplevel(self)
        update_window.title(f"Update {vendor.name}")
        update_window.geometry("640x450")
        
        form = VendorForm(update_window, vendor)
        form_frame = form.create_form()
        form_frame.pack(fill=tk.BOTH, expand=True)
        
        # Zone update field for scanned vendors
        if vendor.npc_id:
            zone_frame = tk.Frame(update_window)
            zone_frame.pack(padx=10, fill=tk.X)
            Label(zone_frame, text="Update Zone:").pack(side=tk.LEFT)
            zone_entry = Entry(zone_frame, width=30)
            zone_entry.insert(0, vendor.zone)
            zone_entry.pack(side=tk.LEFT, padx=5)
            
            def save_zone():
                new_zone = zone_entry.get().strip()
                if new_zone:
                    vendor.zone = new_zone
                    self.db.update_npc_zone(vendor.npc_id, new_zone)
                    messagebox.showinfo("Zone Updated", f"Zone updated to: {new_zone}", parent=update_window)
            
            Button(zone_frame, text="Save Zone", command=save_zone).pack(side=tk.LEFT, padx=5)
        
        button_line = tk.Frame(update_window)
        button_line.pack(padx=10, pady=10, fill=tk.X)
        
        def reset_now():
            if messagebox.askyesno("Confirm Reset", f"Are you sure you want to reset {vendor.name}?", parent=update_window):
                old_council = vendor.council_left
                vendor.last_reset = datetime.now()
                if vendor.reset_maximum > 0:
                    vendor.council_left = vendor.reset_maximum
                
                self.db.log_transaction(
                    self.current_character, vendor.name, 'reset',
                    old_council, vendor.council_left,
                    f"Manual reset from {format_number(old_council)} to {format_number(vendor.council_left)}",
                    vendor.npc_id
                )
                
                self.db.save_vendors(self.vendors, self.current_character)
                self.update_vendor_list()
                self.update_total_values()
                messagebox.showinfo("Success", f"Vendor '{vendor.name}' has been reset.", parent=update_window)
                update_window.destroy()
        
        def update_vendor_action():
            try:
                old_council = vendor.council_left
                values = form.get_values()
                
                time_obj = TimeUntilReset.from_inputs(
                    values['days'], values['hours'], values['minutes'],
                    values['override_max_time']
                )
                
                vendor.council_left = values['council']
                if values['council'] > vendor.reset_maximum:
                    vendor.reset_maximum = values['council']
                vendor.last_reset = time_obj.calculate_last_reset(values['override_max_time'])
                vendor.muted = values['muted']
                vendor.categories = values['categories']
                
                if old_council != values['council']:
                    transaction_type = 'purchase' if values['council'] < old_council else 'adjustment'
                    self.db.log_transaction(
                        self.current_character, vendor.name, transaction_type,
                        old_council, values['council'],
                        f"Manual update: {format_number(old_council)} → {format_number(values['council'])}",
                        vendor.npc_id
                    )
                
                self.db.save_vendors(self.vendors, self.current_character)
                self.update_vendor_list()
                self.update_total_values()
                messagebox.showinfo("Success", f"Vendor '{vendor.name}' updated.", parent=update_window)
                update_window.destroy()
            except ValueError as e:
                self._show_error(str(e), update_window)
            except Exception as e:
                self._show_error(f"Could not update vendor: {e}", update_window)
        
        Button(button_line, text="Reset Now", command=reset_now, fg="red").pack(side=tk.LEFT, padx=6)
        Button(button_line, text="Update", command=update_vendor_action).pack(side=tk.RIGHT, padx=6)
        Button(button_line, text="Close", command=update_window.destroy).pack(side=tk.RIGHT)
    
    def _delete_vendor(self, vendor: Vendor):
        """Delete a vendor."""
        if messagebox.askyesno("Delete Vendor", f"Are you sure you want to delete {vendor.name}?", parent=self):
            self.db.log_transaction(
                self.current_character, vendor.name, 'deletion',
                vendor.council_left, 0,
                f"Vendor deleted with {vendor.council_left} council remaining",
                vendor.npc_id
            )
            
            # Delete by name+zone (the canonical identifier)
            self.vendors = [v for v in self.vendors if not (v.name == vendor.name and v.zone == vendor.zone)]
            self.db.save_vendors(self.vendors, self.current_character)
            self.update_vendor_list()
            self.update_total_values()
            messagebox.showinfo("Deleted", f"{vendor.name} has been deleted.", parent=self)
    
    def _toggle_mute_vendor(self, vendor: Vendor):
        """Toggle vendor mute status."""
        vendor.muted = not vendor.muted
        self.db.save_vendors(self.vendors, self.current_character)
        self.update_vendor_list()
        self.update_total_values()
        status = "muted" if vendor.muted else "unmuted"
        messagebox.showinfo("Success", f"{vendor.name} has been {status}.", parent=self)
    
    def update_vendor_list(self):
        """Update the vendor list display."""
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.pulse_widgets.clear()
        
        # Safety check - filter_var might not be initialized yet
        if self.filter_var is None:
            filter_text = ""
        else:
            filter_text = self.filter_var.get()
        
        if self.show_muted_var is None:
            show_muted = False
        else:
            show_muted = self.show_muted_var.get()
        
        displayed_vendors = [
            v for v in self.vendors
            if (show_muted or not v.muted) and v.matches_filter(filter_text)
        ]
        
        not_ready = [v for v in displayed_vendors if not v.is_ready_to_reset]
        if not_ready:
            times = [(v.next_reset - datetime.now()).total_seconds() for v in not_ready]
            max_time = max(times)
            min_time = min(times)
        else:
            max_time = min_time = 0
        
        displayed_vendors.sort(key=lambda v: v.next_reset)
        
        callbacks = {
            'update': self._open_update_vendor_window,
            'delete': self._delete_vendor,
            'toggle_mute': self._toggle_mute_vendor
        }
        
        for vendor in displayed_vendors:
            card = VendorCard(self.scrollable_frame, vendor, min_time, max_time, callbacks)
            
            if card.widgets_for_pulse:
                for widget in card.widgets_for_pulse:
                    self.pulse_widgets.append({
                        'widget': widget,
                        'vendor_name': vendor.name
                    })
    
    def update_total_values(self):
        """Update the total values display."""
        try:
            unmuted_vendors = [v for v in self.vendors if not v.muted]
            total_council = sum(v.council_left for v in unmuted_vendors)
            total_maximum = sum(v.reset_maximum for v in unmuted_vendors)
            total_earned_7d = self.db.get_council_earned(self.current_character, days=7)
            
            self.total_council_label.config(text=f"Current Vendor Council Pool: {format_number(total_council)}")
            self.total_max_label.config(text=f"Total Vendor Cash: {format_number(total_maximum)}")
            self.earned_7d_label.config(text=f"Council earned (7d): {format_number(total_earned_7d)}")
        except Exception as e:
            print(f"Error updating total values: {e}")
    
    def _update_pulse_animation(self):
        """Update pulse animation for ready vendors."""
        if not self.timer_running:
            return
        
        try:
            self.pulse_frame = (self.pulse_frame + 1) % PULSE_FRAME_MAX
            pulse_color = calculate_pulse_color(self.pulse_frame)
            
            widgets_to_remove = []
            for widget_info in self.pulse_widgets:
                widget = widget_info['widget']
                try:
                    if widget.winfo_exists():
                        widget.config(bg=pulse_color)
                    else:
                        widgets_to_remove.append(widget_info)
                except tk.TclError:
                    widgets_to_remove.append(widget_info)
            
            for widget_info in widgets_to_remove:
                self.pulse_widgets.remove(widget_info)
        except Exception as e:
            print(f"Error updating pulse animation: {e}")
        
        self.after(PULSE_UPDATE_MS, self._update_pulse_animation)
    
    def _update_timers(self):
        """Update timer displays."""
        if not self.timer_running:
            return
        
        try:
            now = datetime.now()
            for widget in self.scrollable_frame.winfo_children():
                if hasattr(widget, 'time_label') and widget.time_label.winfo_exists():
                    vname = widget.vendor_name
                    vendor = next((x for x in self.vendors if x.name == vname), None)
                    if vendor:
                        time_diff = vendor.next_reset - now
                        if time_diff.total_seconds() > 0:
                            time_obj = TimeUntilReset.from_timedelta(time_diff)
                            widget.time_label.config(text=time_obj.to_string(), font=("Arial", 10, "normal"))
                        else:
                            time_str = "RESET PENDING!"
                            font_style = "bold" if self.flash_phase else "normal"
                            widget.time_label.config(text=time_str, font=("Arial", 10, font_style))
            
            self.flash_phase = not self.flash_phase
        except Exception as e:
            print(f"Error updating timers: {e}")
        
        self.after(TIMER_UPDATE_MS, self._update_timers)
    
    def _show_error(self, message: str, parent=None):
        """Show error message."""
        if parent is None:
            parent = self
        messagebox.showerror("Error", message, parent=parent)
    
    def on_closing(self):
        """Handle window close."""
        try:
            self.timer_running = False
            self.auto_scan_enabled = False
            self.db.save_vendors(self.vendors, self.current_character)
        except Exception as e:
            print(f"Error saving on close: {e}")
        finally:
            self.destroy()


# ---------------------
# Launch
# ---------------------
if __name__ == "__main__":
    try:
        app = VendorApp()
        app.mainloop()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
