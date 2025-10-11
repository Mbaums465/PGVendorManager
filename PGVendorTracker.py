import tkinter as tk
from tkinter import messagebox, Toplevel, Label, Entry, Button, Scrollbar, Canvas, OptionMenu, StringVar, simpledialog, Checkbutton, BooleanVar
from tkinter import ttk
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict
import os
import sys
import time
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass

# ---------------------
# Constants
# ---------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'character_data')
DATABASE_PATH = os.path.join(DATA_DIR, 'vendors.db')
DEFAULT_CHARACTER = 'Default'
MAX_DAYS = 6
MAX_HOURS = 23
MAX_MINUTES = 59
MAX_TOTAL_MINUTES = MAX_DAYS * 24 * 60 + MAX_HOURS * 60 + MAX_MINUTES

# UI Constants
VENDOR_CATEGORIES = ["Jewelry", "Armor", "Weapons", "Scrolls", "Misc"]
PULSE_FRAME_MAX = 120
PULSE_CYCLE_DIVISOR = 30
TIMER_UPDATE_MS = 1000
PULSE_UPDATE_MS = 100

# Colors
COLOR_EMPTY_BG = "lightgrey"
COLOR_NORMAL_BG = "white"
COLOR_RESET_READY = "green"


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
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS vendors (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        character_name TEXT NOT NULL,
                        name TEXT NOT NULL,
                        zone TEXT NOT NULL,
                        council_left INTEGER NOT NULL,
                        last_reset TEXT NOT NULL,
                        reset_maximum INTEGER NOT NULL,
                        categories TEXT NOT NULL,
                        muted BOOLEAN NOT NULL,
                        UNIQUE(character_name, name)
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        character_name TEXT NOT NULL,
                        vendor_name TEXT NOT NULL,
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
                
                conn.commit()
        except sqlite3.Error as e:
            raise RuntimeError(f"Could not initialize database: {e}")
    
    def save_vendors(self, vendors: List['Vendor'], character_name: str):
        """Save vendors for a character."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM vendors WHERE character_name = ?', (character_name,))
                for vendor in vendors:
                    cursor.execute('''
                        INSERT INTO vendors (
                            character_name, name, zone, council_left,
                            last_reset, reset_maximum, categories, muted
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        character_name,
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
                    SELECT name, zone, council_left, last_reset, 
                           reset_maximum, categories, muted 
                    FROM vendors WHERE character_name = ?
                ''', (character_name,))
                rows = cursor.fetchall()
                
                vendors = []
                for row in rows:
                    try:
                        vendor = Vendor(
                            name=row[0],
                            zone=row[1],
                            council_left=row[2],
                            last_reset=row[3],
                            reset_maximum=row[4],
                            categories=json.loads(row[5]),
                            muted=row[6]
                        )
                        vendors.append(vendor)
                    except (ValueError, json.JSONDecodeError) as e:
                        print(f"Error loading vendor {row[0]}: {e}")
                
                return vendors
        except sqlite3.Error as e:
            raise RuntimeError(f"Could not load vendors for {character_name}: {e}")
    
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
                       council_after: int, notes: Optional[str] = None):
        """Log a transaction."""
        try:
            council_change = council_after - council_before
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO transactions (
                        character_name, vendor_name, transaction_type,
                        council_before, council_after, council_change,
                        timestamp, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    character_name, vendor_name, transaction_type,
                    council_before, council_after, council_change,
                    datetime.now().isoformat(), notes
                ))
                conn.commit()
        except sqlite3.Error as e:
            print(f"Error logging transaction: {e}")
    
    def get_council_earned(self, character_name: str, 
                          vendor_name: Optional[str] = None, 
                          days: int = 7) -> int:
        """Get total council earned in the last N days."""
        try:
            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                if vendor_name:
                    return self._get_vendor_earned(cursor, character_name, vendor_name, cutoff_date)
                else:
                    return self._get_all_vendors_earned(cursor, character_name, cutoff_date)
        except sqlite3.Error as e:
            print(f"Error getting council earned: {e}")
            return 0
    
    def _get_vendor_earned(self, cursor, character_name: str, 
                          vendor_name: str, cutoff_date: str) -> int:
        """Calculate earned for a specific vendor."""
        cursor.execute('''
            SELECT SUM(ABS(council_change)) FROM transactions
            WHERE character_name = ? AND vendor_name = ?
            AND timestamp >= ? AND transaction_type IN ('purchase', 'adjustment')
            AND council_change < 0
        ''', (character_name, vendor_name, cutoff_date))
        result = cursor.fetchone()
        spent = result[0] if result[0] is not None else 0
        
        cursor.execute('''
            SELECT council_left, reset_maximum FROM vendors
            WHERE character_name = ? AND name = ?
        ''', (character_name, vendor_name))
        vendor_row = cursor.fetchone()
        
        if vendor_row:
            council_left, reset_maximum = vendor_row
            current_spent = reset_maximum - council_left
            
            cursor.execute('''
                SELECT COUNT(*) FROM transactions
                WHERE character_name = ? AND vendor_name = ?
                AND timestamp >= ?
            ''', (character_name, vendor_name, cutoff_date))
            
            if cursor.fetchone()[0] == 0:
                spent += current_spent
        
        return spent
    
    def _get_all_vendors_earned(self, cursor, character_name: str, cutoff_date: str) -> int:
        """Calculate earned for all vendors."""
        total_earned = 0
        cursor.execute('''
            SELECT name, council_left, reset_maximum FROM vendors
            WHERE character_name = ?
        ''', (character_name,))
        vendors = cursor.fetchall()
        
        for vendor_name, council_left, reset_maximum in vendors:
            cursor.execute('''
                SELECT SUM(ABS(council_change)) FROM transactions
                WHERE character_name = ? AND vendor_name = ?
                AND timestamp >= ? AND transaction_type IN ('purchase', 'adjustment')
                AND council_change < 0
            ''', (character_name, vendor_name, cutoff_date))
            result = cursor.fetchone()
            spent = result[0] if result[0] is not None else 0
            
            current_spent = reset_maximum - council_left
            cursor.execute('''
                SELECT COUNT(*) FROM transactions
                WHERE character_name = ? AND vendor_name = ?
                AND timestamp >= ?
            ''', (character_name, vendor_name, cutoff_date))
            
            if cursor.fetchone()[0] == 0:
                spent += current_spent
            
            total_earned += spent
        
        return total_earned
    
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
# Vendor Model
# ---------------------
class Vendor:
    """Vendor model with business logic."""
    
    def __init__(self, name: str, zone: str, council_left: int, 
                 last_reset, reset_maximum: int = 0, 
                 categories: Optional[List[str]] = None, 
                 muted: bool = False):
        self.name = name
        self.zone = zone
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
        """Check if vendor has no council left."""
        return self.council_left == 0
    
    @property
    def time_until_reset(self) -> TimeUntilReset:
        """Get time until next reset."""
        return TimeUntilReset.from_timedelta(self.next_reset - datetime.now())
    
    def matches_filter(self, filter_text: str) -> bool:
        """Check if vendor matches filter text."""
        if not filter_text:
            return True
        
        filter_lower = filter_text.lower()
        return (filter_lower in self.name.lower() or 
                filter_lower in self.zone.lower() or
                any(filter_lower in c.lower() for c in self.categories))


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


# ---------------------
# UI Components
# ---------------------
class ScrollableFrame(tk.Frame):
    """Reusable scrollable frame component."""
    
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.canvas = Canvas(self)
        self.scrollbar = Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas)
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        self._setup_mousewheel()
    
    def _setup_mousewheel(self):
        """Setup mousewheel scrolling."""
        def on_mousewheel(event):
            try:
                if hasattr(event, 'delta'):
                    self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                elif event.num == 4:
                    self.canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    self.canvas.yview_scroll(1, "units")
            except Exception as e:
                print(f"Mouse wheel error: {e}")
        
        self.canvas.bind_all("<MouseWheel>", on_mousewheel)
        self.canvas.bind_all("<Button-4>", on_mousewheel)
        self.canvas.bind_all("<Button-5>", on_mousewheel)


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
        
        # Days
        Label(time_frame, text="Days:").pack(side=tk.LEFT, padx=(8,0))
        self.days_entry = Entry(time_frame, width=5)
        self.days_entry.pack(side=tk.LEFT, padx=2)
        
        # Hours
        Label(time_frame, text="Hours:").pack(side=tk.LEFT, padx=(8,0))
        self.hours_entry = Entry(time_frame, width=5)
        self.hours_entry.pack(side=tk.LEFT, padx=2)
        
        # Minutes
        Label(time_frame, text="Minutes:").pack(side=tk.LEFT, padx=(8,0))
        self.minutes_entry = Entry(time_frame, width=5)
        self.minutes_entry.pack(side=tk.LEFT, padx=2)
        
        # Set initial values
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
        
        # Left options
        left_options = tk.Frame(cat_override_row)
        left_options.pack(side=tk.LEFT, padx=(0,12), anchor="n")
        
        Checkbutton(left_options, text="Max-Time-Override", 
                   variable=self.max_time_override_var).pack(anchor="w")
        
        mute_text = "Muted" if self.is_update else "Start Muted"
        Checkbutton(left_options, text=mute_text, 
                   variable=self.muted_var).pack(anchor="w")
        
        # Categories
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
        
        # Custom category
        custom_wrap = tk.Frame(cat_frame)
        custom_wrap.grid(row=1, column=2, sticky="w", padx=8, pady=4)
        cb_custom = Checkbutton(custom_wrap, text="Custom:", variable=self.custom_var)
        cb_custom.pack(side=tk.LEFT)
        self.custom_entry = Entry(custom_wrap, width=18)
        self.custom_entry.pack(side=tk.LEFT, padx=4)
        
        # Populate custom categories
        if self.vendor:
            custom_items = [c for c in self.vendor.categories if c not in VENDOR_CATEGORIES]
            if custom_items:
                self.custom_var.set(True)
                self.custom_entry.insert(0, ", ".join(custom_items))
    
    def get_values(self) -> Dict:
        """Extract and validate form values."""
        values = {}
        
        # Name and zone (for add mode)
        if not self.is_update:
            values['name'] = self.name_entry.get().strip()
            values['zone'] = self.zone_entry.get().strip()
            
            if not values['name']:
                raise ValueError("Vendor name cannot be empty.")
        
        # Council
        try:
            council_input = float(self.council_entry.get() or 0)
            values['council'] = int(council_input * 1000)
        except (ValueError, TypeError):
            raise ValueError("Council must be numeric (K).")
        
        # Time inputs
        try:
            values['days'] = int(self.days_entry.get() or 0)
            values['hours'] = int(self.hours_entry.get() or 0)
            values['minutes'] = int(self.minutes_entry.get() or 0)
        except (ValueError, TypeError):
            raise ValueError("Days, Hours, Minutes must be integers.")
        
        # Validate time
        override_flag = self.max_time_override_var.get()
        total_minutes = values['days'] * 24 * 60 + values['hours'] * 60 + values['minutes']
        if total_minutes > MAX_TOTAL_MINUTES and not override_flag:
            raise ValueError(f"Reset time cannot exceed {MAX_DAYS}d {MAX_HOURS}h {MAX_MINUTES}m unless Max-Time-Override is checked.")
        
        values['override_max_time'] = override_flag
        values['muted'] = self.muted_var.get()
        
        # Categories
        selected_cats = [c for c, var in self.cat_vars.items() if var.get()]
        if self.custom_var.get():
            cv = self.custom_entry.get().strip()
            if cv:
                extras = [x.strip() for x in cv.split(",") if x.strip()]
                selected_cats.extend(extras)
        
        # Remove duplicates while preserving order
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
        
        # Outer frame with colored border
        self.outer_frame = tk.Frame(self.parent, bg=border_color)
        self.outer_frame.pack(fill=tk.X, padx=4, pady=4)
        self.outer_frame.vendor_name = self.vendor.name
        
        # Inner frame
        vf = tk.Frame(self.outer_frame, bg=bg_color)
        vf.pack(padx=2, pady=2, fill=tk.X)
        
        # Info section
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
        
        # Time label
        time_str = self._get_time_string()
        self.time_label = Label(info, text=time_str, bg=bg_color)
        self.time_label.pack(side=tk.RIGHT, anchor="e")
        self.outer_frame.time_label = self.time_label
        
        # Buttons
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
        
        # Track widgets for pulse animation
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
        # Controls
        controls = tk.Frame(self.window)
        controls.pack(fill=tk.X, padx=10, pady=10)
        
        Label(controls, text="Days to show:").pack(side=tk.LEFT, padx=5)
        days_entry = Entry(controls, textvariable=self.days_var, width=5)
        days_entry.pack(side=tk.LEFT, padx=5)
        Button(controls, text="Refresh", command=self.refresh_transactions).pack(side=tk.LEFT, padx=5)
        
        # Notebook with tabs
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Transaction list tab
        trans_tab = ScrollableFrame(notebook)
        notebook.add(trans_tab, text="Transaction List")
        self.trans_frame = trans_tab.scrollable_frame
        
        # Daily earnings tab
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
            trans_id, char, vendor, trans_type, before, after, change, timestamp, notes = trans
            if change > 0:
                total_earned += change
            
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
        
        # Calculate daily earnings
        daily_earnings = defaultdict(int)
        for trans in transactions:
            trans_id, char, vendor, trans_type, before, after, change, timestamp, notes = trans
            if change < 0:
                dt = datetime.fromisoformat(timestamp)
                date_key = dt.date()
                daily_earnings[date_key] += abs(change)
        
        # Create date list
        all_dates = [(datetime.now() - timedelta(days=i)).date() for i in range(days)]
        
        # Headers
        Label(self.daily_frame, text="Daily Council Earned", 
              font=("Arial", 12, "bold")).grid(row=0, column=0, columnspan=2, padx=5, pady=10, sticky="w")
        
        Label(self.daily_frame, text="Date", font=("Arial", 10, "bold"), 
              anchor="w", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=1, column=0, sticky="ew")
        Label(self.daily_frame, text="Council Earned", font=("Arial", 10, "bold"), 
              anchor="e", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=1, column=1, sticky="ew")
        
        # Data rows
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
        
        # Total row
        Label(self.daily_frame, text="Total:", font=("Arial", 10, "bold"), 
              anchor="w", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=0, sticky="ew")
        Label(self.daily_frame, text=f"{format_number(total_daily)}", 
              font=("Arial", 10, "bold"), anchor="e", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=1, sticky="ew")


# ---------------------
# Main Application
# ---------------------
class VendorApp(tk.Tk):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.title("Vendor Reset Manager")
        self.geometry("900x600")
        
        # Initialize database
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
        
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def _create_ui(self):
        """Create the main UI."""
        self._create_top_bar()
        self._create_info_bar()
        self._create_button_bar()
        self._create_vendor_list()
        
        self.update_vendor_list()
        self.update_total_values()
    
    def _create_top_bar(self):
        """Create top bar with character selection and filters."""
        top = tk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)
        
        Label(top, text="Character:").pack(side=tk.LEFT)
        self.char_var = StringVar(value=self.current_character)
        self.char_var.trace("w", self._on_char_change)
        self.char_menu = OptionMenu(top, self.char_var, *self.characters)
        self.char_menu.pack(side=tk.LEFT, padx=6)
        
        Button(top, text="Add New Character", command=self._add_new_character).pack(side=tk.LEFT, padx=6)
        Button(top, text="View Transactions", command=self._open_transactions_window).pack(side=tk.LEFT, padx=6)
        
        Label(top, text="Filter:").pack(side=tk.LEFT, padx=(12,4))
        self.filter_var = StringVar()
        self.filter_var.trace("w", lambda *a: self.update_vendor_list())
        Entry(top, textvariable=self.filter_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        
        self.show_muted_var = BooleanVar(value=False)
        self.show_muted_var.trace("w", lambda *a: self.update_vendor_list())
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
        
        # Copy default vendors
        default_vendors = self.db.load_vendors(DEFAULT_CHARACTER)
        if default_vendors:
            self.db.save_vendors(default_vendors, safe_name)
    
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
    
    def _open_add_vendor_window(self):
        """Open window to add a new vendor."""
        add_window = Toplevel(self)
        add_window.title("Add New Vendor")
        add_window.geometry("640x360")
        
        form = VendorForm(add_window)
        form_frame = form.create_form()
        form_frame.pack(fill=tk.BOTH, expand=True)
        
        # Buttons
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
                    values['name'], values['zone'], values['council'],
                    last_reset, values['council'], values['categories'], 
                    values['muted']
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
        update_window.geometry("640x400")
        
        form = VendorForm(update_window, vendor)
        form_frame = form.create_form()
        form_frame.pack(fill=tk.BOTH, expand=True)
        
        # Buttons
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
                    f"Manual reset from {format_number(old_council)} to {format_number(vendor.council_left)}"
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
                        f"Manual update: {format_number(old_council)} → {format_number(values['council'])}"
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
                f"Vendor deleted with {vendor.council_left} council remaining"
            )
            
            self.vendors = [v for v in self.vendors if v.name != vendor.name]
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
        # Clear existing
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.pulse_widgets.clear()
        
        # Filter vendors
        filter_text = self.filter_var.get().lower()
        show_muted = self.show_muted_var.get()
        
        displayed_vendors = [
            v for v in self.vendors
            if (show_muted or not v.muted) and v.matches_filter(filter_text)
        ]
        
        # Calculate time range for color scaling
        not_ready = [v for v in displayed_vendors if not v.is_ready_to_reset]
        if not_ready:
            times = [(v.next_reset - datetime.now()).total_seconds() for v in not_ready]
            max_time = max(times)
            min_time = min(times)
        else:
            max_time = min_time = 0
        
        # Sort by reset time
        displayed_vendors.sort(key=lambda v: v.next_reset)
        
        # Create cards
        callbacks = {
            'update': self._open_update_vendor_window,
            'delete': self._delete_vendor,
            'toggle_mute': self._toggle_mute_vendor
        }
        
        for vendor in displayed_vendors:
            card = VendorCard(self.scrollable_frame, vendor, min_time, max_time, callbacks)
            
            # Track for pulse animation
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
