import tkinter as tk
from tkinter import messagebox, Toplevel, Label, Entry, Button, OptionMenu, StringVar, simpledialog, Checkbutton, BooleanVar, filedialog, ttk
import sqlite3
import json
import re
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Optional, Dict, Tuple, Set, NamedTuple
from dataclasses import dataclass

# Import the standardized reader
from playerlog_reader import PlayerLogReader

# ---------------------
# Constants & Config
# ---------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'character_data')
DATABASE_PATH = os.path.join(DATA_DIR, 'vendors_auto.db')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
DEFAULT_LOG_PATH = os.path.expandvars(r'C:\Users\%USERNAME%\AppData\LocalLow\Elder Game\Project Gorgon\Player.log')
DEFAULT_CHARACTER = 'Default'

# Limits
INVALID_MAX_COUNCIL = 2147483647
AUTO_SCAN_INTERVAL_MS = 2000
TIMER_UPDATE_MS = 1000
PULSE_UPDATE_MS = 100

# UI Colors
COLOR_RESET_READY = "green"
COLOR_EMPTY_BG = "lightgrey"
COLOR_NORMAL_BG = "white"

# Categories
VENDOR_CATEGORIES = ["Jewelry", "Armor", "Weapons", "Scrolls", "Misc"]

# Regex Patterns
PATTERNS = {
    'login': re.compile(r'Vivox - LoginAsync\(([A-Za-z][A-Za-z0-9_]*)\)'),
    'area': re.compile(r'Initializing area! \(\d+\): Area(\w+)'),
    'interact': re.compile(r'LocalPlayer: ProcessStartInteraction\((\d+),.*?,.*?,.*?,\s*"?(NPC_[^",\)\s]+)"?[\),]'),
    'screen': re.compile(r'LocalPlayer: ProcessVendorScreen\((\d+), ([^,]+), (\d+), (\d+), (\d+),'),
    'update': re.compile(r'LocalPlayer: ProcessVendorUpdateAvailableGold\((\d+), (\d+), (\d+)\)?')
}

# ---------------------
# Data Classes
# ---------------------
class Transaction(NamedTuple):
    """Named tuple for clearer transaction field access."""
    id: int
    character_name: str
    vendor_name: str
    npc_id: int
    transaction_type: str
    council_before: int
    council_after: int
    council_change: int
    timestamp: str
    notes: str

# ---------------------
# Helpers
# ---------------------
def format_number(value) -> str:
    """Format number with K/M suffixes."""
    try:
        val = int(value)
    except (ValueError, TypeError):
        return str(value)
    
    if val == 0: return "0"
    if abs(val) >= 1_000_000: return f"{val / 1_000_000:.1f}M"
    if abs(val) >= 1_000: return f"{val // 1000}K"
    return str(val)

def calculate_border_color(vendor, min_time, max_time) -> str:
    """Calculate border color based on reset time percentile."""
    if vendor.is_ready: return COLOR_RESET_READY
    
    time_diff = (vendor.next_reset - datetime.now()).total_seconds()
    if max_time > min_time:
        ratio = (time_diff - min_time) / (max_time - min_time)
    else:
        ratio = 1.0
    
    # Red (far) to Green (close)
    r = int(255 * (1 - ratio))
    g = int(255 * ratio)
    return f"#{r:02x}{g:02x}00"

def clean_npc_name(raw: str) -> str:
    return raw[4:] if raw.startswith('NPC_') else raw

def validate_council(left: int, max_c: int) -> Tuple[int, int]:
    return (0, 0) if max_c >= INVALID_MAX_COUNCIL else (left, max_c)

# ---------------------
# Data Structures
# ---------------------
@dataclass
class TimeUntilReset:
    days: int
    hours: int
    minutes: int

    @classmethod
    def from_timedelta(cls, td: timedelta):
        s = max(0, int(td.total_seconds()))
        d, r = divmod(s, 86400)
        h, m = divmod(r, 3600)
        return cls(d, h, m // 60)

    def to_string(self): return f"{self.days}d {self.hours}h {self.minutes}m"

# ---------------------
# Logic Core
# ---------------------
class VendorLogParser:
    """Unified logic for parsing log lines."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.chars = set()
        self.names = {}
        self.zones = {}
        self.data = defaultdict(dict)
        self.cur_char = None
        self.cur_zone = "Unknown"
        self.cur_vendor = None
        self.updates = []
        self.errors = []

    def parse_lines(self, lines: List[str]) -> bool:
        found_update = False
        self.updates.clear()
        
        for line in lines:
            try:
                if m := PATTERNS['login'].search(line):
                    if not m.group(1).isdigit():
                        self.cur_char = m.group(1)
                        self.chars.add(self.cur_char)
                
                elif m := PATTERNS['area'].search(line):
                    self.cur_zone = m.group(1)
                
                elif m := PATTERNS['interact'].search(line):
                    nid, name = int(m.group(1)), clean_npc_name(m.group(2))
                    self.names[nid] = name
                    self.zones[nid] = 'Anywhere' if name == 'VendorFox' else self.cur_zone

                elif (m := PATTERNS['screen'].search(line)) and self.cur_char:
                    self._process_vendor_data(m, is_screen=True)
                    found_update = True
                
                elif (m := PATTERNS['update'].search(line)) and self.cur_char and self.cur_vendor:
                    self._process_vendor_data(m, is_screen=False)
                    found_update = True
            except Exception:
                pass 
        
        return found_update

    def _process_vendor_data(self, match, is_screen):
        if is_screen:
            nid, left, ts, max_c = int(match.group(1)), int(match.group(3)), int(match.group(4)), int(match.group(5))
            self.cur_vendor = nid
        else:
            nid = self.cur_vendor
            left, ts, max_c = int(match.group(1)), int(match.group(2)), int(match.group(3))

        left, max_c = validate_council(left, max_c)
        self.data[self.cur_char][nid] = (left, ts, max_c)
        
        name = self.names.get(nid, f"Unknown_{nid}")
        self.updates.append((self.cur_char, nid, name))

class VendorDatabase:
    def __init__(self, db_path):
        self.path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _conn(self): return sqlite3.connect(self.path)

    def _init_db(self):
        with self._conn() as conn:
            c = conn.cursor()
            c.execute('CREATE TABLE IF NOT EXISTS npc_mappings (npc_id INTEGER PRIMARY KEY, npc_name TEXT, zone TEXT, last_updated TEXT)')
            c.execute('''CREATE TABLE IF NOT EXISTS vendors (id INTEGER PRIMARY KEY, character_name TEXT, npc_id INTEGER, 
                         npc_name TEXT, zone TEXT, council_left INTEGER, last_reset TEXT, reset_maximum INTEGER, 
                         categories TEXT, muted BOOLEAN, UNIQUE(character_name, npc_name, zone))''')
            c.execute('''CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, character_name TEXT, vendor_name TEXT, 
                         npc_id INTEGER, transaction_type TEXT, council_before INTEGER, council_after INTEGER, 
                         council_change INTEGER, timestamp TEXT, notes TEXT)''')
            self._migrate(conn)

    def _migrate(self, conn):
        rows = conn.execute('SELECT character_name, npc_name, zone, count(*) as c FROM vendors GROUP BY 1,2,3 HAVING c > 1').fetchall()
        for char, name, zone, _ in rows:
            dups = conn.execute('SELECT id, npc_id, council_left, last_reset, reset_maximum FROM vendors WHERE character_name=? AND npc_name=? AND zone=? ORDER BY last_reset DESC', (char, name, zone)).fetchall()
            if len(dups) > 1:
                keep = dups[0]
                best_max = max(d[4] for d in dups)
                conn.execute('UPDATE vendors SET npc_id=?, reset_maximum=? WHERE id=?', (max(d[1] for d in dups), best_max, keep[0]))
                for d in dups[1:]: conn.execute('DELETE FROM vendors WHERE id=?', (d[0],))
        conn.commit()

    def save_mapping(self, nid, name, zone):
        with self._conn() as c: 
            c.execute('INSERT OR REPLACE INTO npc_mappings VALUES (?,?,?,?)', (nid, name, zone, datetime.now().isoformat()))

    def get_zone(self, nid):
        row = self._conn().execute('SELECT zone FROM npc_mappings WHERE npc_id=?', (nid,)).fetchone()
        return row[0] if row else 'Unknown'

    def save_vendors(self, vendors, char):
        with self._conn() as conn:
            conn.execute('DELETE FROM vendors WHERE character_name=?', (char,))
            conn.executemany('INSERT INTO vendors (character_name, npc_id, npc_name, zone, council_left, last_reset, reset_maximum, categories, muted) VALUES (?,?,?,?,?,?,?,?,?)',
                             [(char, v.npc_id, v.name, v.zone, v.council_left, v.last_reset.isoformat(), v.reset_maximum, json.dumps(v.categories), v.muted) for v in vendors])

    def load_vendors(self, char):
        rows = self._conn().execute('SELECT npc_id, npc_name, zone, council_left, last_reset, reset_maximum, categories, muted FROM vendors WHERE character_name=?', (char,)).fetchall()
        return [Vendor(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6]), r[7]) for r in rows]

    def log_trans(self, char, v_name, v_id, t_type, before, after, notes=""):
        change = after - before
        with self._conn() as c:
            c.execute('INSERT INTO transactions (character_name, vendor_name, npc_id, transaction_type, council_before, council_after, council_change, timestamp, notes) VALUES (?,?,?,?,?,?,?,?,?)',
                      (char, v_name, v_id, t_type, before, after, change, datetime.now().isoformat(), notes))

    def get_trans(self, char, days=7) -> List[Transaction]:
        """Get transactions as named tuples for clearer field access."""
        date = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self._conn().execute(
            'SELECT * FROM transactions WHERE character_name=? AND timestamp>=? ORDER BY timestamp DESC', 
            (char, date)
        ).fetchall()
        return [Transaction(*r) for r in rows]

    def get_earned(self, char, days=7):
        date = (datetime.now() - timedelta(days=days)).isoformat()
        res = self._conn().execute(
            "SELECT COALESCE(SUM(ABS(council_change)), 0) FROM transactions "
            "WHERE character_name=? AND timestamp>=? AND council_change < 0 AND transaction_type != 'deletion'", 
            (char, date)
        ).fetchone()
        return res[0]

    def get_chars(self):
        chars = [r[0] for r in self._conn().execute('SELECT DISTINCT character_name FROM vendors').fetchall()]
        return sorted(list(set(chars) | {DEFAULT_CHARACTER}))

class Vendor:
    def __init__(self, npc_id, name, zone, council, last_reset, reset_max, categories=None, muted=False):
        self.npc_id = npc_id
        self.name = name
        self.zone = zone
        self.reset_maximum = int(reset_max) if int(reset_max) < INVALID_MAX_COUNCIL else 0
        self.council_left = int(council) if self.reset_maximum else 0
        self.categories = categories or []
        self.muted = bool(muted)
        
        if isinstance(last_reset, str):
            try: self.last_reset = datetime.fromisoformat(last_reset)
            except: self.last_reset = datetime.now()
        else: self.last_reset = last_reset or datetime.now()

    @property
    def next_reset(self): return self.last_reset + timedelta(days=7)
    @property
    def is_ready(self): return datetime.now() >= self.next_reset
    @property
    def is_empty(self): return self.council_left < 1000
    @property
    def time_until(self): return TimeUntilReset.from_timedelta(self.next_reset - datetime.now())
    
    @classmethod
    def from_scan(cls, nid, name, zone, left, ts, max_c):
        lr = datetime.now() if ts == 0 else datetime.fromtimestamp(ts/1000.0) - timedelta(days=7)
        return cls(nid, name, zone, left, lr, max_c)

# ---------------------
# UI Classes
# ---------------------
class ScrollableFrame(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.cv = tk.Canvas(self)
        self.sb = tk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.frm = tk.Frame(self.cv)
        
        self.frm.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.create_window((0, 0), window=self.frm, anchor="nw")
        self.cv.configure(yscrollcommand=self.sb.set)
        
        self.cv.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        
        self.bind('<Enter>', lambda e: self.cv.bind_all("<MouseWheel>", self._scroll))
        self.bind('<Leave>', lambda e: self.cv.unbind_all("<MouseWheel>"))

    def _scroll(self, e):
        self.cv.yview_scroll(int(-1*(e.delta/120)), "units")

class VendorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Vendor Reset Manager")
        self.geometry("600x700")
        
        # Data & Tools
        self.settings = self._load_settings()
        self.db = VendorDatabase(DATABASE_PATH)
        self.parser = VendorLogParser()
        self.reader = None 
        
        # State
        self.chars = self.db.get_chars()
        self.cur_char = self.chars[0]
        self.vendors = self.db.load_vendors(self.cur_char)
        self.pulse_frame = 0
        self.scanning = False
        self.flash_state = False
        self._pulsing_widgets = []
        self._time_labels = [] 
        
        self._build_ui()
        self._update_list()
        self._update_totals()
        self._anim_loop()
        self._timer_loop()

    def _load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f: return {**{'log_path': DEFAULT_LOG_PATH}, **json.load(f)}
        return {'log_path': DEFAULT_LOG_PATH}

    def _build_ui(self):
        # Top Bar
        top = tk.Frame(self)
        top.pack(fill=tk.X, padx=5, pady=5)
        
        self.cv = StringVar(value=self.cur_char)
        self.cv.trace_add("write", lambda *a: self._set_char(self.cv.get()))
        
        tk.Label(top, text="Char:").pack(side=tk.LEFT)
        self.om = OptionMenu(top, self.cv, *self.chars)
        self.om.pack(side=tk.LEFT, padx=5)
        
        tk.Button(top, text="View Transactions", command=self._show_history).pack(side=tk.LEFT)
        self.show_muted = BooleanVar(value=False)
        tk.Checkbutton(top, text="Show Muted", variable=self.show_muted, command=self._update_list).pack(side=tk.LEFT, padx=5)

        # Search
        sf = tk.Frame(self)
        sf.pack(fill=tk.X, padx=5)
        tk.Label(sf, text="Search:").pack(side=tk.LEFT)
        self.search = StringVar()
        self.search.trace_add("write", lambda *a: self._update_list())
        tk.Entry(sf, textvariable=self.search).pack(fill=tk.X, padx=5)

        # Info Header
        inf = tk.Frame(self, bg="lightgrey", relief="raised", bd=1)
        inf.pack(fill=tk.X, padx=5, pady=5)
        self.lbl_pool = tk.Label(inf, bg="lightgrey")
        self.lbl_pool.pack(side=tk.LEFT, padx=8, pady=6)
        
        self.lbl_max = tk.Label(inf, bg="lightgrey")
        self.lbl_max.pack(side=tk.LEFT, padx=8, pady=6)
        
        self.lbl_earned = tk.Label(inf, bg="lightgrey")
        self.lbl_earned.pack(side=tk.LEFT, padx=8, pady=6)

        # Actions
        act = tk.Frame(self)
        act.pack(fill=tk.X, padx=5)
        tk.Button(act, text="Add New Vendor", command=self._add_vendor).pack(side=tk.LEFT, padx=4)
        
        self.btn_scan = tk.Button(act, text="▶ Start Auto-Scan", bg="#28a745", fg="white", 
                                  font=("Arial", 10, "bold"), command=self._toggle_scan)
        self.btn_scan.pack(side=tk.LEFT, padx=10)
        
        self.lbl_scan = tk.Label(act, text="Auto-scan: OFF", fg="gray")
        self.lbl_scan.pack(side=tk.LEFT, padx=4)
        
        tk.Button(act, text="⚙ Settings", command=self._settings).pack(side=tk.RIGHT, padx=4)

        # List
        self.v_frame = ScrollableFrame(self)
        self.v_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ---------------------
    # Logic
    # ---------------------
    def _set_char(self, name):
        self.cur_char = name
        self.vendors = self.db.load_vendors(name)
        self._update_list()
        self._update_totals()

    def _update_totals(self):
        active = [v for v in self.vendors if not v.muted]
        pool = sum(v.council_left for v in active)
        total_max = sum(v.reset_maximum for v in active)
        earned = self.db.get_earned(self.cur_char)
        
        self.lbl_pool.config(text=f"Current Vendor Council Pool: {format_number(pool)}")
        self.lbl_max.config(text=f"Total Vendor Cash: {format_number(total_max)}")
        self.lbl_earned.config(text=f"Council earned (7d): {format_number(earned)}")

    def _update_list(self):
        self._pulsing_widgets = []
        self._time_labels = []
        for w in self.v_frame.frm.winfo_children(): w.destroy()
        
        term = self.search.get().lower()
        flt = lambda v: term in (v.name + v.zone + " ".join(v.categories)).lower()
        
        visible = [v for v in self.vendors if (self.show_muted.get() or not v.muted) and flt(v)]
        visible.sort(key=lambda v: v.next_reset)
        
        not_ready = [v for v in visible if not v.is_ready]
        if not_ready:
            times = [(v.next_reset - datetime.now()).total_seconds() for v in not_ready]
            min_time, max_time = min(times), max(times)
        else:
            min_time, max_time = 0, 0
        
        for v in visible:
            self._draw_card(v, min_time, max_time)

    def _draw_card(self, v: Vendor, min_t, max_t):
        border_col = calculate_border_color(v, min_t, max_t)
        bg = COLOR_EMPTY_BG if (v.is_empty and not v.is_ready) else COLOR_NORMAL_BG
        
        out = tk.Frame(self.v_frame.frm, bg=border_col)
        out.pack(fill=tk.X, padx=4, pady=4)
        
        p = tk.Frame(out, bg=bg)
        p.pack(fill=tk.X, padx=2, pady=2)
        
        inf = tk.Frame(p, bg=bg)
        inf.pack(fill=tk.X, padx=4, pady=2)
        
        left = tk.Frame(inf, bg=bg)
        left.pack(side=tk.LEFT, anchor="w")
        tk.Label(left, text=f"{v.name} ({v.zone})", font=("Arial", 10, "bold"), bg=bg).pack(anchor="w")
        
        c_str = f"Council: {format_number(v.council_left)}"
        if v.reset_maximum > 0: c_str += f" / Max: {format_number(v.reset_maximum)}"
        tk.Label(left, text=c_str, bg=bg).pack(anchor="w")
        
        t_lbl = tk.Label(inf, bg=bg)
        t_lbl.pack(side=tk.RIGHT, anchor="e")
        
        self._time_labels.append((t_lbl, v))
        self._update_time_label(t_lbl, v)

        btns = tk.Frame(p, bg=bg)
        btns.pack(fill=tk.X, padx=4, pady=2)
        tk.Button(btns, text="Update", command=lambda: self._edit_vendor(v)).pack(side=tk.LEFT, padx=5)
        tk.Button(btns, text="Delete", fg="red", command=lambda: self._del_vendor(v)).pack(side=tk.LEFT, padx=5)
        tk.Button(btns, text="Unmute" if v.muted else "Mute", command=lambda: self._toggle_mute(v)).pack(side=tk.LEFT, padx=5)

        if v.is_ready and v.is_empty and not v.muted:
            self._pulsing_widgets.append(p)

    def _update_time_label(self, lbl, v):
        if v.is_ready:
            lbl.config(text="RESET PENDING!", font=("Arial", 10, "bold" if self.flash_state else "normal"))
        else:
            lbl.config(text=v.time_until.to_string(), font=("Arial", 10, "normal"))

    # ---------------------
    # Loops
    # ---------------------
    def _timer_loop(self):
        self.flash_state = not self.flash_state
        valid_labels = []
        for lbl, v in self._time_labels:
            try:
                if lbl.winfo_exists():
                    self._update_time_label(lbl, v)
                    valid_labels.append((lbl, v))
            except: pass
        self._time_labels = valid_labels
        self.after(TIMER_UPDATE_MS, self._timer_loop)

    def _anim_loop(self):
        self.pulse_frame = (self.pulse_frame + 1) % 120
        import math
        ratio = (1 + math.sin(self.pulse_frame * math.pi / 30)) / 2
        r = int(255 - (205) * ratio)
        g = int(255 - (50) * ratio)
        b = int(50 * ratio)
        col = f"#{r:02x}{g:02x}{b:02x}"
        
        valid_widgets = []
        for w in self._pulsing_widgets:
            try:
                if w.winfo_exists():
                    w.config(bg=col)
                    valid_widgets.append(w)
            except: pass
        self._pulsing_widgets = valid_widgets
        self.after(PULSE_UPDATE_MS, self._anim_loop)

    # ---------------------
    # Scanning
    # ---------------------
    def _toggle_scan(self):
        self.scanning = not self.scanning
        if self.scanning:
            path = self.settings.get('log_path')
            if not os.path.exists(path):
                messagebox.showerror("Error", "Log file not found.")
                self.scanning = False
                return
            
            self.reader = PlayerLogReader(path)
            self.reader.reset() 
            
            self.btn_scan.config(text="⏹ Stop Auto-Scan", bg="#dc3545")
            self.lbl_scan.config(text="Scanning...", fg="blue")
            self._scan_tick()
        else:
            self.btn_scan.config(text="▶ Start Auto-Scan", bg="#28a745")
            self.lbl_scan.config(text="Auto-scan: OFF", fg="gray")

    def _scan_tick(self):
        if not self.scanning: return
        
        try:
            if self.reader.has_new_content():
                lines = self.reader.read_new_lines()
                if self.parser.parse_lines(lines):
                    self._apply_updates(self.parser.updates)
                    self.lbl_scan.config(text=f"Updated {len(self.parser.updates)} vendors", fg="green")
        except Exception as e:
            print(f"Scan error: {e}")
        
        self.after(AUTO_SCAN_INTERVAL_MS, self._scan_tick)

    def _apply_updates(self, updates):
        chars_updated = set()
        for char, nid, name in updates:
            chars_updated.add(char)
            vendors = self.db.load_vendors(char)
            existing = {(v.name, v.zone): v for v in vendors}
            
            d_left, d_ts, d_max = self.parser.data[char][nid]
            zone = 'Anywhere' if name == 'VendorFox' else self.parser.zones.get(nid, self.db.get_zone(nid))
            self.db.save_mapping(nid, name, zone)
            
            key = (name, zone)
            if key in existing:
                v = existing[key]
                if v.council_left != d_left:
                    self.db.log_trans(char, name, nid, 'auto', v.council_left, d_left)
                    v.council_left = d_left
                    v.reset_maximum = max(v.reset_maximum, d_max)
                    if d_ts == 0 or d_left > v.council_left:
                         v.last_reset = datetime.now() if d_ts == 0 else datetime.fromtimestamp(d_ts/1000.0) - timedelta(days=7)
            else:
                vendors.append(Vendor.from_scan(nid, name, zone, d_left, d_ts, d_max))
                self.db.log_trans(char, name, nid, 'create', 0, d_left)
            
            self.db.save_vendors(vendors, char)
            
        if self.cur_char in chars_updated:
            self._set_char(self.cur_char)

    # ---------------------
    # Windows & Actions
    # ---------------------
    def _edit_vendor(self, v):
        win = Toplevel(self)
        win.title(f"Update {v.name}")
        win.geometry("400x550")

        # Name & Zone
        tk.Label(win, text="Name:").pack(anchor="w", padx=10, pady=(10,0))
        e_name = tk.Entry(win); e_name.insert(0, v.name); e_name.pack(fill="x", padx=10)
        
        tk.Label(win, text="Zone:").pack(anchor="w", padx=10, pady=(5,0))
        e_zone = tk.Entry(win); e_zone.insert(0, v.zone); e_zone.pack(fill="x", padx=10)

        # Council
        tk.Label(win, text="Council (K):").pack(anchor="w", padx=10, pady=(5,0))
        e_council = tk.Entry(win); e_council.insert(0, str(v.council_left // 1000)); e_council.pack(fill="x", padx=10)

        # Time
        tk.Label(win, text="Reset In (Days/Hours/Minutes):").pack(anchor="w", padx=10, pady=(10,0))
        f_time = tk.Frame(win)
        f_time.pack(fill="x", padx=10)
        
        rem = v.time_until
        e_d = tk.Entry(f_time, width=5); e_d.insert(0, str(rem.days)); e_d.pack(side="left", padx=2)
        e_h = tk.Entry(f_time, width=5); e_h.insert(0, str(rem.hours)); e_h.pack(side="left", padx=2)
        e_m = tk.Entry(f_time, width=5); e_m.insert(0, str(rem.minutes)); e_m.pack(side="left", padx=2)

        # Categories
        tk.Label(win, text="Buys Categories:").pack(anchor="w", padx=10, pady=(10,0))
        
        cat_vars = {}
        for cat in VENDOR_CATEGORIES:
            var = BooleanVar(value=(cat in v.categories))
            cat_vars[cat] = var
            tk.Checkbutton(win, text=cat, variable=var).pack(anchor="w", padx=20)

        # Custom Category
        tk.Label(win, text="Custom Category (comma sep):").pack(anchor="w", padx=10, pady=(5,0))
        e_custom = tk.Entry(win)
        custom_cats = [c for c in v.categories if c not in VENDOR_CATEGORIES]
        e_custom.insert(0, ", ".join(custom_cats))
        e_custom.pack(fill="x", padx=10)

        # Buttons
        btn_frame = tk.Frame(win)
        btn_frame.pack(fill="x", padx=10, pady=20)

        def save():
            try:
                # Basic info
                v.name = e_name.get()
                v.zone = e_zone.get()
                
                # Council
                old_c = v.council_left
                new_c = int(float(e_council.get()) * 1000)
                
                # Time update (manual override)
                try:
                    d, h, m = int(e_d.get()), int(e_h.get()), int(e_m.get())
                    # If inputs differ from current, user wants to change time
                    delta = timedelta(days=d, hours=h, minutes=m)
                    # Next reset is Now + delta. So Last Reset is (Now + delta) - 7d
                    v.last_reset = (datetime.now() + delta) - timedelta(days=7)
                except: pass

                # Categories
                new_cats = [cat for cat, var in cat_vars.items() if var.get()]
                custom_str = e_custom.get().strip()
                if custom_str:
                    new_cats.extend([c.strip() for c in custom_str.split(',') if c.strip()])
                v.categories = new_cats

                v.council_left = new_c
                if new_c > v.reset_maximum: v.reset_maximum = new_c

                self.db.save_vendors(self.vendors, self.cur_char)
                self.db.save_mapping(v.npc_id, v.name, v.zone)
                self.db.log_trans(self.cur_char, v.name, v.npc_id, 'edit', old_c, new_c)
                
                self._update_list()
                self._update_totals()
                win.destroy()
            except ValueError: messagebox.showerror("Error", "Invalid number format")

        tk.Button(btn_frame, text="Save", command=save).pack(side="right")
        tk.Button(btn_frame, text="Reset Now", fg="red", command=lambda: [
            setattr(v, 'last_reset', datetime.now()), 
            setattr(v, 'council_left', v.reset_maximum), 
            e_council.delete(0, 'end'), e_council.insert(0, str(v.reset_maximum//1000)),
            e_d.delete(0, 'end'), e_d.insert(0, "6"),
            e_h.delete(0, 'end'), e_h.insert(0, "23"),
            e_m.delete(0, 'end'), e_m.insert(0, "59")
        ]).pack(side="left")

    def _del_vendor(self, v):
        if messagebox.askyesno("Delete", f"Delete {v.name}?"):
            self.vendors.remove(v)
            self.db.save_vendors(self.vendors, self.cur_char)
            self.db.log_trans(self.cur_char, v.name, v.npc_id, 'deletion', v.council_left, 0)
            self._update_list()
            self._update_totals()

    def _add_vendor(self):
        win = Toplevel(self)
        tk.Label(win, text="Name:").pack(); en = tk.Entry(win); en.pack()
        tk.Label(win, text="Zone:").pack(); ez = tk.Entry(win); ez.pack()
        tk.Label(win, text="Council (K):").pack(); ec = tk.Entry(win); ec.pack()
        
        def do_add():
            try:
                c = int(float(ec.get()) * 1000)
                self.vendors.append(Vendor(0, en.get(), ez.get(), c, datetime.now(), c))
                self.db.save_vendors(self.vendors, self.cur_char)
                self._update_list()
                self._update_totals()
                win.destroy()
            except: pass
            
        tk.Button(win, text="Add", command=do_add).pack()

    def _toggle_mute(self, v):
        v.muted = not v.muted
        self.db.save_vendors(self.vendors, self.cur_char)
        self._update_list()
        self._update_totals()

    def _show_history(self):
        """Display transaction history with TreeView for daily earnings."""
        win = Toplevel(self)
        win.title("Transaction History")
        win.geometry("700x500")

        # Controls (Top)
        ctl = tk.Frame(win)
        ctl.pack(fill='x', padx=10, pady=10)
        
        tk.Label(ctl, text="Days to show:").pack(side="left")
        e_days = tk.Entry(ctl, width=5)
        e_days.insert(0, "7")
        e_days.pack(side="left", padx=5)
        
        # Notebook (Main)
        nb = ttk.Notebook(win)
        nb.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        
        # --- Daily Earnings Tab with TreeView ---
        f_daily = tk.Frame(nb)
        nb.add(f_daily, text="Daily Earnings")
        
        # TreeView with columns
        columns = ("date", "vendors", "earned")
        tree_daily = ttk.Treeview(f_daily, columns=columns, show="tree headings", selectmode="browse")
        
        # Column configuration
        tree_daily.heading("#0", text="", anchor="w")
        tree_daily.heading("date", text="Date", anchor="w")
        tree_daily.heading("vendors", text="Vendors", anchor="center")
        tree_daily.heading("earned", text="Earned", anchor="e")
        
        tree_daily.column("#0", width=30, stretch=False)
        tree_daily.column("date", width=120, anchor="w")
        tree_daily.column("vendors", width=80, anchor="center")
        tree_daily.column("earned", width=120, anchor="e")
        
        # Scrollbar for daily tree
        sb_daily = ttk.Scrollbar(f_daily, orient="vertical", command=tree_daily.yview)
        tree_daily.configure(yscrollcommand=sb_daily.set)
        
        tree_daily.pack(side="left", fill="both", expand=True)
        sb_daily.pack(side="right", fill="y")
        
        # Style for total row
        style = ttk.Style()
        style.configure("Total.Treeview", font=("Arial", 10, "bold"))
        
        # --- Transactions Tab with TreeView ---
        f_trans = tk.Frame(nb)
        nb.add(f_trans, text="All Transactions")
        
        trans_columns = ("timestamp", "vendor", "type", "change", "before", "after")
        tree_trans = ttk.Treeview(f_trans, columns=trans_columns, show="headings", selectmode="browse")
        
        tree_trans.heading("timestamp", text="Time", anchor="w")
        tree_trans.heading("vendor", text="Vendor", anchor="w")
        tree_trans.heading("type", text="Type", anchor="w")
        tree_trans.heading("change", text="Change", anchor="e")
        tree_trans.heading("before", text="Before", anchor="e")
        tree_trans.heading("after", text="After", anchor="e")
        
        tree_trans.column("timestamp", width=130, anchor="w")
        tree_trans.column("vendor", width=120, anchor="w")
        tree_trans.column("type", width=80, anchor="w")
        tree_trans.column("change", width=80, anchor="e")
        tree_trans.column("before", width=80, anchor="e")
        tree_trans.column("after", width=80, anchor="e")
        
        sb_trans = ttk.Scrollbar(f_trans, orient="vertical", command=tree_trans.yview)
        tree_trans.configure(yscrollcommand=sb_trans.set)
        
        tree_trans.pack(side="left", fill="both", expand=True)
        sb_trans.pack(side="right", fill="y")
        
        # Tag for coloring
        tree_trans.tag_configure("earning", foreground="green")
        tree_trans.tag_configure("spending", foreground="red")

        def refresh():
            try:
                days = int(e_days.get())
            except ValueError:
                return
            
            # Clear both trees
            for item in tree_daily.get_children():
                tree_daily.delete(item)
            for item in tree_trans.get_children():
                tree_trans.delete(item)
            
            trans = self.db.get_trans(self.cur_char, days)
            
            # --- Build Daily Earnings Data ---
            # Group by date, then by vendor
            daily_data: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
            daily_totals: Dict[str, int] = defaultdict(int)
            grand_total = 0
            
            for t in trans:
                # Earnings: council_change < 0 and not a deletion
                if t.transaction_type != 'deletion' and t.council_change < 0:
                    date_str = t.timestamp[:10]
                    amt = abs(t.council_change)
                    daily_data[date_str][t.vendor_name] += amt
                    daily_totals[date_str] += amt
                    grand_total += amt
            
            # Sort dates newest first
            sorted_dates = sorted(daily_data.keys(), reverse=True)
            
            # Populate daily tree
            for date_str in sorted_dates:
                vendor_earnings = daily_data[date_str]
                num_vendors = len(vendor_earnings)
                day_total = daily_totals[date_str]
                
                # Parent row for the day
                day_id = tree_daily.insert(
                    "", "end",
                    values=(date_str, f"{num_vendors} vendor{'s' if num_vendors != 1 else ''}", format_number(day_total)),
                    open=False
                )
                
                # Child rows for each vendor (sorted by earnings desc)
                for vendor, amt in sorted(vendor_earnings.items(), key=lambda x: -x[1]):
                    tree_daily.insert(
                        day_id, "end",
                        values=("", vendor, format_number(amt))
                    )
            
            # Add separator and total row
            tree_daily.insert("", "end", values=("─" * 15, "─" * 10, "─" * 10))
            tree_daily.insert(
                "", "end",
                values=("TOTAL", f"{len(sorted_dates)} day{'s' if len(sorted_dates) != 1 else ''}", format_number(grand_total)),
                tags=("total",)
            )
            
            # Style the total row
            tree_daily.tag_configure("total", font=("Arial", 10, "bold"))
            
            # --- Populate Transactions Tab ---
            for t in trans:
                tag = "earning" if t.council_change < 0 else "spending"
                change_str = format_number(t.council_change)
                if t.council_change < 0:
                    change_str = f"-{format_number(abs(t.council_change))}"
                elif t.council_change > 0:
                    change_str = f"+{format_number(t.council_change)}"
                
                tree_trans.insert(
                    "", "end",
                    values=(
                        t.timestamp[:16],
                        t.vendor_name,
                        t.transaction_type,
                        change_str,
                        format_number(t.council_before),
                        format_number(t.council_after)
                    ),
                    tags=(tag,)
                )

        tk.Button(ctl, text="Refresh", command=refresh).pack(side="left", padx=10)
        
        # Export button
        def export_csv():
            try:
                days = int(e_days.get())
            except ValueError:
                return
            
            filepath = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv")],
                title="Export Transactions"
            )
            if not filepath:
                return
            
            trans = self.db.get_trans(self.cur_char, days)
            with open(filepath, 'w') as f:
                f.write("timestamp,vendor,type,change,before,after\n")
                for t in trans:
                    f.write(f"{t.timestamp},{t.vendor_name},{t.transaction_type},{t.council_change},{t.council_before},{t.council_after}\n")
            messagebox.showinfo("Export", f"Exported {len(trans)} transactions to {filepath}")
        
        tk.Button(ctl, text="Export CSV", command=export_csv).pack(side="left")
        
        refresh()  # Initial load

    def _settings(self):
        win = Toplevel(self)
        tk.Label(win, text="Log Path:").pack()
        e = tk.Entry(win, width=50); e.insert(0, self.settings['log_path']); e.pack()
        def save():
            with open(SETTINGS_FILE, 'w') as f: json.dump({'log_path': e.get()}, f)
            self.settings['log_path'] = e.get()
            win.destroy()
        tk.Button(win, text="Save", command=save).pack()
        tk.Button(win, text="Find...", command=lambda: e.insert(0, filedialog.askopenfilename() or "")).pack()

if __name__ == "__main__":
    VendorApp().mainloop()
