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

# ---------------------
# Configuration
# ---------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'character_data')
DATABASE_PATH = os.path.join(DATA_DIR, 'vendors.db')
DEFAULT_CHARACTER = 'Default'
MAX_TOTAL_MINUTES = 6 * 24 * 60 + 23 * 60 + 59  # 6d 23h 59m

# ---------------------
# Database Setup
# ---------------------
def _ensure_data_dir():
    try:
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
    except OSError as e:
        print(f"Error creating data directory: {e}")
        messagebox.showerror("Error", f"Could not create data directory: {e}")

def init_database():
    """Initialize SQLite database and create vendors and transactions tables if they don't exist."""
    _ensure_data_dir()
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
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
            
            # New transactions table
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
            
            # Index for faster queries
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_transactions_lookup 
                ON transactions(character_name, vendor_name, timestamp)
            ''')
            
            conn.commit()
    except sqlite3.Error as e:
        print(f"Error initializing database: {e}")
        messagebox.showerror("Error", f"Could not initialize database: {e}")

def log_transaction(character_name, vendor_name, transaction_type, council_before, council_after, notes=None):
    """Log a transaction to the database."""
    try:
        council_change = council_after - council_before
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO transactions (
                    character_name, vendor_name, transaction_type,
                    council_before, council_after, council_change,
                    timestamp, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                character_name,
                vendor_name,
                transaction_type,
                council_before,
                council_after,
                council_change,
                datetime.now().isoformat(),
                notes
            ))
            conn.commit()
    except sqlite3.Error as e:
        print(f"Error logging transaction: {e}")

def get_council_earned(character_name, vendor_name=None, days=7):
    """Get total council earned (spent) in the last N days for a character or specific vendor.
    Earned = how much was spent from vendors (reset_maximum - current_council)."""
    try:
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            if vendor_name:
                # For a specific vendor, sum all negative changes (purchases) and positive resets
                cursor.execute('''
                    SELECT SUM(ABS(council_change)) FROM transactions
                    WHERE character_name = ? AND vendor_name = ?
                    AND timestamp >= ? AND transaction_type IN ('purchase', 'adjustment')
                    AND council_change < 0
                ''', (character_name, vendor_name, cutoff_date))
                result = cursor.fetchone()
                spent = result[0] if result[0] is not None else 0
                
                # Add the current amount spent (max - current) if no transactions exist
                cursor.execute('''
                    SELECT council_left, reset_maximum FROM vendors
                    WHERE character_name = ? AND name = ?
                ''', (character_name, vendor_name))
                vendor_row = cursor.fetchone()
                if vendor_row:
                    council_left, reset_maximum = vendor_row
                    current_spent = reset_maximum - council_left
                    # Only add current spent if there are no recent transactions
                    cursor.execute('''
                        SELECT COUNT(*) FROM transactions
                        WHERE character_name = ? AND vendor_name = ?
                        AND timestamp >= ?
                    ''', (character_name, vendor_name, cutoff_date))
                    if cursor.fetchone()[0] == 0:
                        spent += current_spent
                
                return spent
            else:
                # For all vendors, calculate total spent
                total_earned = 0
                cursor.execute('''
                    SELECT name, council_left, reset_maximum FROM vendors
                    WHERE character_name = ?
                ''', (character_name,))
                vendors = cursor.fetchall()
                
                for vendor_name, council_left, reset_maximum in vendors:
                    # Get transaction-based spending
                    cursor.execute('''
                        SELECT SUM(ABS(council_change)) FROM transactions
                        WHERE character_name = ? AND vendor_name = ?
                        AND timestamp >= ? AND transaction_type IN ('purchase', 'adjustment')
                        AND council_change < 0
                    ''', (character_name, vendor_name, cutoff_date))
                    result = cursor.fetchone()
                    spent = result[0] if result[0] is not None else 0
                    
                    # Add current spent amount if no recent transactions
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
    except sqlite3.Error as e:
        print(f"Error getting council earned: {e}")
        return 0

def get_transactions(character_name, vendor_name=None, start_date=None, end_date=None):
    """Query transactions within a specific timeframe."""
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
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

def migrate_json_to_sqlite():
    """Migrate existing JSON files to SQLite database."""
    _ensure_data_dir()
    try:
        files = [f for f in os.listdir(DATA_DIR) if f.endswith('_vendors.json')]
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            for json_file in files:
                character_name = json_file.replace('_vendors.json', '')
                file_path = os.path.join(DATA_DIR, json_file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            for vendor_data in data:
                                cursor.execute('''
                                    INSERT OR REPLACE INTO vendors (
                                        character_name, name, zone, council_left,
                                        last_reset, reset_maximum, categories, muted
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (
                                    character_name,
                                    vendor_data.get("name", ""),
                                    vendor_data.get("zone", ""),
                                    int(vendor_data.get("council_left", 0)),
                                    vendor_data.get("last_reset", datetime.now().isoformat()),
                                    int(vendor_data.get("reset_maximum", 0)),
                                    json.dumps(vendor_data.get("categories", [])),
                                    bool(vendor_data.get("muted", False))
                                ))
                    conn.commit()
                    backup_path = file_path + f'.backup_{int(time.time())}'
                    os.rename(file_path, backup_path)
                    print(f"Migrated {json_file} to SQLite and renamed to {os.path.basename(backup_path)}")
                except (json.JSONDecodeError, IOError, sqlite3.Error) as e:
                    print(f"Error migrating {json_file}: {e}")
                except OSError as e:
                    print(f"Error renaming {json_file}: {e}")
    except OSError as e:
        print(f"Error accessing data directory: {e}")

# ---------------------
# Vendor model
# ---------------------
class Vendor:
    def __init__(self, name, zone, council_left, last_reset, reset_maximum=0, categories=None, muted=False):
        self.name = name
        self.zone = zone
        self.council_left = int(council_left)
        
        if isinstance(last_reset, str):
            try:
                self.last_reset = datetime.fromisoformat(last_reset)
            except ValueError:
                try:
                    self.last_reset = datetime.fromtimestamp(float(last_reset))
                except (ValueError, OverflowError):
                    print(f"Warning: Invalid last_reset format for {name}, using current time")
                    self.last_reset = datetime.now()
        elif isinstance(last_reset, datetime):
            self.last_reset = last_reset
        else:
            print(f"Warning: Unknown last_reset type for {name}, using current time")
            self.last_reset = datetime.now()

        self.reset_maximum = int(reset_maximum)
        self.categories = categories or []
        self.muted = bool(muted)

    def to_dict(self):
        return {
            "name": self.name,
            "zone": self.zone,
            "council_left": int(self.council_left),
            "last_reset": self.last_reset.isoformat(),
            "reset_maximum": int(self.reset_maximum),
            "categories": self.categories,
            "muted": self.muted
        }

    @staticmethod
    def from_dict(d):
        return Vendor(
            d.get("name", ""),
            d.get("zone", ""),
            d.get("council_left", 0),
            d.get("last_reset", datetime.now().isoformat()),
            d.get("reset_maximum", 0),
            d.get("categories", []),
            d.get("muted", False)
        )

    @property
    def next_reset(self):
        return self.last_reset + timedelta(days=7)

    @property
    def is_ready_to_reset(self):
        return datetime.now() >= self.next_reset

    @property
    def is_empty(self):
        return self.council_left == 0

# ---------------------
# Persistence
# ---------------------
def save_vendors(vendors, character_name):
    """Save vendors for character_name to SQLite."""
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
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
        print(f"Error saving vendors: {e}")
        messagebox.showerror("Error", f"Could not save vendors: {e}")

def load_vendors(character_name):
    """Load vendors for character_name from SQLite."""
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT name, zone, council_left, last_reset, reset_maximum, categories, muted FROM vendors WHERE character_name = ?', (character_name,))
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
        print(f"Error loading vendors: {e}")
        messagebox.showerror("Error", f"Could not load vendors for {character_name}: {e}")
        return []

def get_all_characters():
    """Retrieve all unique character names from the database."""
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT DISTINCT character_name FROM vendors')
            characters = [row[0] for row in cursor.fetchall()]
            if DEFAULT_CHARACTER not in characters:
                characters.append(DEFAULT_CHARACTER)
            return sorted(characters)
    except sqlite3.Error as e:
        print(f"Error fetching characters: {e}")
        return [DEFAULT_CHARACTER]

# ---------------------
# Helpers
# ---------------------
def format_number(value):
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

def _clamp_reset_inputs(days, hours, minutes, override_max_time=False):
    try:
        d = max(0, int(days or 0))
        h = max(0, int(hours or 0))
        m = max(0, int(minutes or 0))
    except (ValueError, TypeError):
        return 0, 0, 0

    if not override_max_time:
        total_minutes = d * 24 * 60 + h * 60 + m
        if total_minutes > MAX_TOTAL_MINUTES:
            total_minutes = MAX_TOTAL_MINUTES
        d, remainder = divmod(total_minutes, 24 * 60)
        h, m = divmod(remainder, 60)
    else:
        h = min(h, 23)
        m = min(m, 59)
    return int(d), int(h), int(m)

def calculate_last_reset(days, hours, minutes, override_max_time=False):
    d, h, m = _clamp_reset_inputs(days, hours, minutes, override_max_time)
    time_until_reset = timedelta(days=d, hours=h, minutes=m)
    if not override_max_time:
        time_since_last_reset = timedelta(days=7) - time_until_reset
        return datetime.now() - time_since_last_reset
    else:
        return datetime.now() + time_until_reset - timedelta(days=7)

# ---------------------
# GUI Application
# ---------------------
class VendorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Vendor Reset Manager")
        self.geometry("900x600")

        init_database()
        migrate_json_to_sqlite()

        self.vendors = []
        
        self.characters = get_all_characters()
        self.current_character = self.characters[0] if self.characters else DEFAULT_CHARACTER

        self.vendors = load_vendors(self.current_character)

        self.pulse_frame = 0
        self.pulse_widgets = []

        self.flash_phase = False

        self.create_widgets()
        self.update_vendor_list()
        self.update_total_values()
        
        self.timer_running = True
        self.after(1000, self.update_timers)
        self.after(100, self.update_pulse_animation)

    def create_widgets(self):
        top = tk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)

        Label(top, text="Character:").pack(side=tk.LEFT)
        self.char_var = StringVar(value=self.current_character)
        self.char_var.trace("w", self.on_char_change)
        self.char_menu = OptionMenu(top, self.char_var, *self.characters)
        self.char_menu.pack(side=tk.LEFT, padx=6)

        Button(top, text="Add New Character", command=self.add_new_character).pack(side=tk.LEFT, padx=6)
        Button(top, text="View Transactions", command=self.open_transactions_window).pack(side=tk.LEFT, padx=6)

        Label(top, text="Filter:").pack(side=tk.LEFT, padx=(12,4))
        self.filter_var = StringVar()
        self.filter_var.trace("w", lambda *a: self.update_vendor_list())
        Entry(top, textvariable=self.filter_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        self.show_muted_var = BooleanVar(value=False)
        self.show_muted_var.trace("w", lambda *a: self.update_vendor_list())
        Checkbutton(top, text="Show Muted", variable=self.show_muted_var).pack(side=tk.LEFT, padx=6)

        info = tk.Frame(self, bg="lightgrey", relief="raised", bd=1)
        info.pack(fill=tk.X, padx=8, pady=6)
        self.total_council_label = Label(info, text="Current Vendor Council Pool: 0K", bg="lightgrey")
        self.total_council_label.pack(side=tk.LEFT, padx=8, pady=6)
        self.total_max_label = Label(info, text="Total Vendor Cash: 0K", bg="lightgrey")
        self.total_max_label.pack(side=tk.LEFT, padx=8, pady=6)
        self.earned_7d_label = Label(info, text="Council earned (7d): 0K", bg="lightgrey")
        self.earned_7d_label.pack(side=tk.LEFT, padx=8, pady=6)

        btns = tk.Frame(self)
        btns.pack(fill=tk.X, padx=8, pady=4)
        Button(btns, text="Add New Vendor", command=self.open_add_vendor_window).pack(side=tk.LEFT, padx=4)

        self.vendor_frame = tk.Frame(self)
        self.vendor_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.canvas = Canvas(self.vendor_frame)
        self.scrollbar = Scrollbar(self.vendor_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            try:
                if hasattr(event, 'delta'):
                    self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                elif event.num == 4:
                    self.canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    self.canvas.yview_scroll(1, "units")
            except Exception as e:
                print(f"Mouse wheel error: {e}")
        
        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self.canvas.bind_all("<Button-4>", _on_mousewheel)
        self.canvas.bind_all("<Button-5>", _on_mousewheel)

    def open_transactions_window(self):
        """Open window to view and query transactions."""
        trans_window = Toplevel(self)
        trans_window.title("Transaction History")
        trans_window.geometry("800x600")

        controls = tk.Frame(trans_window)
        controls.pack(fill=tk.X, padx=10, pady=10)

        Label(controls, text="Days to show:").pack(side=tk.LEFT, padx=5)
        days_var = StringVar(value="7")
        days_entry = Entry(controls, textvariable=days_var, width=5)
        days_entry.pack(side=tk.LEFT, padx=5)

        # Create notebook for tabs
        notebook = ttk.Notebook(trans_window)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tab 1: Transaction List
        trans_tab = tk.Frame(notebook)
        notebook.add(trans_tab, text="Transaction List")

        trans_canvas = Canvas(trans_tab)
        trans_scrollbar = Scrollbar(trans_tab, orient="vertical", command=trans_canvas.yview)
        trans_frame = tk.Frame(trans_canvas)

        trans_frame.bind("<Configure>", lambda e: trans_canvas.configure(scrollregion=trans_canvas.bbox("all")))
        trans_canvas.create_window((0, 0), window=trans_frame, anchor="nw")
        trans_canvas.configure(yscrollcommand=trans_scrollbar.set)

        trans_canvas.pack(side="left", fill="both", expand=True)
        trans_scrollbar.pack(side="right", fill="y")

        # Tab 2: Daily Earnings
        daily_tab = tk.Frame(notebook)
        notebook.add(daily_tab, text="Daily Earnings")

        daily_canvas = Canvas(daily_tab)
        daily_scrollbar = Scrollbar(daily_tab, orient="vertical", command=daily_canvas.yview)
        daily_frame = tk.Frame(daily_canvas)

        daily_frame.bind("<Configure>", lambda e: daily_canvas.configure(scrollregion=daily_canvas.bbox("all")))
        daily_canvas.create_window((0, 0), window=daily_frame, anchor="nw")
        daily_canvas.configure(yscrollcommand=daily_scrollbar.set)

        daily_canvas.pack(side="left", fill="both", expand=True)
        daily_scrollbar.pack(side="right", fill="y")

        def refresh_transactions():
            try:
                days = int(days_var.get())
                start_date = datetime.now() - timedelta(days=days)
                transactions = get_transactions(self.current_character, start_date=start_date)
                
                # Clear transaction list tab
                for widget in trans_frame.winfo_children():
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
                    
                    label = Label(trans_frame, text=trans_text, fg=color, anchor="w")
                    label.pack(fill=tk.X, padx=5, pady=2)
                
                summary = Label(trans_frame, text=f"\nTotal Council Earned: {format_number(total_earned)}", 
                               font=("Arial", 10, "bold"))
                summary.pack(fill=tk.X, padx=5, pady=10)
                
                # Clear daily earnings tab
                for widget in daily_frame.winfo_children():
                    widget.destroy()
                
                # Calculate daily earnings
                daily_earnings = defaultdict(int)
                for trans in transactions:
                    trans_id, char, vendor, trans_type, before, after, change, timestamp, notes = trans
                    # Only count purchases/spending as "earned"
                    if change < 0:
                        dt = datetime.fromisoformat(timestamp)
                        date_key = dt.date()
                        daily_earnings[date_key] += abs(change)
                
                # Create list of all dates in range (most recent first)
                all_dates = []
                for i in range(days):
                    date = (datetime.now() - timedelta(days=i)).date()
                    all_dates.append(date)
                
                # Display daily earnings
                Label(daily_frame, text="Daily Council Earned", font=("Arial", 12, "bold")).grid(row=0, column=0, columnspan=2, padx=5, pady=10, sticky="w")
                
                # Column headers
                Label(daily_frame, text="Date", font=("Arial", 10, "bold"), anchor="w", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=1, column=0, sticky="ew")
                Label(daily_frame, text="Council Earned", font=("Arial", 10, "bold"), anchor="e", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=1, column=1, sticky="ew")
                
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
                    
                    Label(daily_frame, text=date_text, fg=color, anchor="w", font=("Arial", 10), relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=0, sticky="ew")
                    Label(daily_frame, text=council_text, fg=color, anchor="e", font=("Arial", 10), relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=1, sticky="ew")
                    row += 1
                
                # Total row
                Label(daily_frame, text="Total:", font=("Arial", 10, "bold"), anchor="w", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=0, sticky="ew")
                Label(daily_frame, text=f"{format_number(total_daily)}", font=("Arial", 10, "bold"), anchor="e", relief="solid", borderwidth=1, padx=5, pady=3).grid(row=row, column=1, sticky="ew")
                
            except ValueError:
                messagebox.showerror("Error", "Days must be a number", parent=trans_window)

        Button(controls, text="Refresh", command=refresh_transactions).pack(side=tk.LEFT, padx=5)

        refresh_transactions()

    def on_char_change(self, *args):
        try:
            self.current_character = self.char_var.get()
            self.vendors = load_vendors(self.current_character)
            self.update_vendor_list()
            self.update_total_values()
        except Exception as e:
            print(f"Error changing character: {e}")
            messagebox.showerror("Error", f"Could not switch to character: {e}")

    def add_new_character(self):
        name = simpledialog.askstring("New Character", "Enter new character name:", parent=self)
        if not name:
            return
        if not name.strip():
            messagebox.showerror("Error", "Character name cannot be empty.", parent=self)
            return
        
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
        if not safe_name:
            messagebox.showerror("Error", "Character name must contain alphanumeric characters.", parent=self)
            return
            
        if safe_name in self.characters:
            messagebox.showerror("Error", "Character already exists.", parent=self)
            return
            
        self.characters.append(safe_name)
        self.characters.sort()
        self.char_var.set(safe_name)
        self.update_char_menu()
        
        default_vendors = load_vendors(DEFAULT_CHARACTER)
        if default_vendors:
            save_vendors(default_vendors, safe_name)

    def update_char_menu(self):
        try:
            menu = self.char_menu["menu"]
            menu.delete(0, "end")
            for c in sorted(self.characters):
                menu.add_command(label=c, command=tk._setit(self.char_var, c))
        except Exception as e:
            print(f"Error updating character menu: {e}")

    def update_total_values(self):
        try:
            unmuted_vendors = [v for v in self.vendors if not v.muted]
            total_council = sum(v.council_left for v in unmuted_vendors)
            total_maximum = sum(v.reset_maximum for v in unmuted_vendors)
            total_earned_7d = get_council_earned(self.current_character, days=7)
            
            self.total_council_label.config(text=f"Current Vendor Council Pool: {format_number(total_council)}")
            self.total_max_label.config(text=f"Total Vendor Cash: {format_number(total_maximum)}")
            self.earned_7d_label.config(text=f"Council earned (7d): {format_number(total_earned_7d)}")
        except Exception as e:
            print(f"Error updating total values: {e}")

    def update_pulse_animation(self):
        if not self.timer_running:
            return
            
        try:
            self.pulse_frame = (self.pulse_frame + 1) % 120
            import math
            pulse_ratio = (1 + math.sin(self.pulse_frame * math.pi / 30)) / 2
            r = int(255 - (255 - 50) * pulse_ratio)
            g = int(255 - (255 - 205) * pulse_ratio)
            b = int(0 + 50 * pulse_ratio)
            pulse_color = f"#{r:02x}{g:02x}{b:02x}"
            
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
                except Exception as e:
                    print(f"Error updating widget color: {e}")
                    widgets_to_remove.append(widget_info)
            
            for widget_info in widgets_to_remove:
                self.pulse_widgets.remove(widget_info)
                        
        except Exception as e:
            print(f"Error updating pulse animation: {e}")
        
        self.after(100, self.update_pulse_animation)

    def update_timers(self):
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
                            days = time_diff.days
                            hours = time_diff.seconds // 3600
                            minutes = (time_diff.seconds % 3600) // 60
                            time_str = f"{days}d {hours}h {minutes}m"
                            widget.time_label.config(text=time_str, font=("Arial", 10, "normal"))
                        else:
                            time_str = "RESET PENDING!"
                            font_style = "bold" if self.flash_phase else "normal"
                            widget.time_label.config(text=time_str, font=("Arial", 10, font_style))
            self.flash_phase = not self.flash_phase
        except Exception as e:
            print(f"Error updating timers: {e}")
        
        self.after(1000, self.update_timers)

    def update_vendor_list(self):
        try:
            for widget in self.scrollable_frame.winfo_children():
                widget.destroy()
            self.pulse_widgets.clear()

            filter_text = self.filter_var.get().lower()
            show_muted = self.show_muted_var.get()

            displayed_vendors = []
            for vendor in self.vendors:
                if not show_muted and vendor.muted:
                    continue
                if filter_text and not (filter_text in vendor.name.lower() or filter_text in vendor.zone.lower() or any(filter_text in c.lower() for c in vendor.categories)):
                    continue
                displayed_vendors.append(vendor)

            not_ready = [v for v in displayed_vendors if not v.is_ready_to_reset]
            if not_ready:
                times = [(v.next_reset - datetime.now()).total_seconds() for v in not_ready]
                max_time = max(times)
                min_time = min(times)
            else:
                max_time = min_time = 0

            displayed_vendors.sort(key=lambda v: v.next_reset)

            for vendor in displayed_vendors:
                time_diff = vendor.next_reset - datetime.now()
                should_pulse = vendor.is_empty and vendor.is_ready_to_reset and not vendor.muted

                if vendor.is_ready_to_reset:
                    border_color = "green"
                else:
                    t = time_diff.total_seconds()
                    if max_time > min_time:
                        ratio = (t - min_time) / (max_time - min_time)
                    else:
                        ratio = 1.0
                    r = int(255 * (1 - ratio))
                    g = int(255 * ratio)
                    b = 0
                    border_color = f"#{r:02x}{g:02x}{b:02x}"

                if vendor.is_empty and not vendor.is_ready_to_reset:
                    bg_color = "lightgrey"
                else:
                    bg_color = "white"

                outer = tk.Frame(self.scrollable_frame)
                outer.pack(fill=tk.X, padx=4, pady=4)
                outer.config(bg=border_color)
                outer.vendor_name = vendor.name

                vf = tk.Frame(outer, bg=bg_color)
                vf.pack(padx=2, pady=2, fill=tk.X)

                info = tk.Frame(vf, bg=bg_color)
                info.pack(fill=tk.X, padx=4, pady=2)

                left_info = tk.Frame(info, bg=bg_color)
                left_info.pack(side=tk.LEFT, anchor="w")

                name_label = Label(left_info, text=f"{vendor.name} ({vendor.zone})", bg=bg_color, font=("Arial", 10, "bold"))
                name_label.pack(anchor="w")

                council_str = f"Council: {format_number(vendor.council_left)}"
                if vendor.reset_maximum > 0:
                    council_str += f" / Max: {format_number(vendor.reset_maximum)}"
                council_label = Label(left_info, text=council_str, bg=bg_color)
                council_label.pack(anchor="w")

                # Show time until reset (reverting from earned display)
                time_diff = vendor.next_reset - datetime.now()
                if time_diff.total_seconds() > 0:
                    days = time_diff.days
                    hours = time_diff.seconds // 3600
                    minutes = (time_diff.seconds % 3600) // 60
                    time_str = f"{days}d {hours}h {minutes}m"
                else:
                    time_str = "RESET PENDING!"
                time_label = Label(info, text=time_str, bg=bg_color)
                time_label.pack(side=tk.RIGHT, anchor="e")
                outer.time_label = time_label

                btns = tk.Frame(vf, bg=bg_color)
                btns.pack(fill=tk.X, padx=4, pady=2)
                Button(btns, text="Update", command=lambda v=vendor: self.open_update_vendor_window(v)).pack(side=tk.LEFT, padx=5, pady=2)
                Button(btns, text="Delete", command=lambda v=vendor: self.delete_vendor(v), fg="red").pack(side=tk.LEFT, padx=5, pady=2)
                mute_text = "Mute" if not vendor.muted else "Unmute"
                Button(btns, text=mute_text, command=lambda v=vendor: self.toggle_mute_vendor(v)).pack(side=tk.LEFT, padx=5, pady=2)

                if should_pulse:
                    pulse_widgets = [vf, info, left_info, name_label, council_label, time_label, btns]
                    for widget in pulse_widgets:
                        self.pulse_widgets.append({
                            'widget': widget,
                            'vendor_name': vendor.name
                        })

            self.canvas.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
                        
        except Exception as e:
            print(f"Error updating vendor list: {e}")
            messagebox.showerror("Error", f"Could not update vendor list: {e}")

    def toggle_mute_vendor(self, vendor):
        try:
            vendor.muted = not vendor.muted
            save_vendors(self.vendors, self.current_character)
            self.update_vendor_list()
            self.update_total_values()
            status = "muted" if vendor.muted else "unmuted"
            messagebox.showinfo("Success", f"{vendor.name} has been {status}.", parent=self)
        except Exception as e:
            print(f"Error toggling mute for vendor: {e}")
            messagebox.showerror("Error", f"Could not toggle mute for vendor: {e}")

    def delete_vendor(self, vendor_to_delete):
        try:
            if messagebox.askyesno("Delete Vendor", f"Are you sure you want to delete {vendor_to_delete.name}?", parent=self):
                log_transaction(
                    self.current_character,
                    vendor_to_delete.name,
                    'deletion',
                    vendor_to_delete.council_left,
                    0,
                    f"Vendor deleted with {vendor_to_delete.council_left} council remaining"
                )
                
                self.vendors = [v for v in self.vendors if v.name != vendor_to_delete.name]
                save_vendors(self.vendors, self.current_character)
                self.update_vendor_list()
                self.update_total_values()
                messagebox.showinfo("Deleted", f"{vendor_to_delete.name} has been deleted.", parent=self)
        except Exception as e:
            print(f"Error deleting vendor: {e}")
            messagebox.showerror("Error", f"Could not delete vendor: {e}")

    def open_add_vendor_window(self):
        add_window = Toplevel(self)
        add_window.title("Add New Vendor")
        add_window.geometry("640x360")

        Label(add_window, text="Vendor Name:").pack(padx=10, pady=(8,2), anchor="w")
        name_entry = Entry(add_window)
        name_entry.pack(padx=10, fill=tk.X)

        Label(add_window, text="Vendor Zone:").pack(padx=10, pady=(8,2), anchor="w")
        zone_entry = Entry(add_window)
        zone_entry.pack(padx=10, fill=tk.X)

        Label(add_window, text="Council left (in K):").pack(padx=10, pady=(8,2), anchor="w")
        council_entry = Entry(add_window)
        council_entry.pack(padx=10, fill=tk.X)

        time_frame = tk.Frame(add_window)
        time_frame.pack(padx=10, pady=8, anchor="w", fill=tk.X)
        Label(time_frame, text="Time until reset:").pack(side=tk.LEFT)
        Label(time_frame, text="Days:").pack(side=tk.LEFT, padx=(8,0))
        days_entry = Entry(time_frame, width=5)
        days_entry.insert(0, '6')
        days_entry.pack(side=tk.LEFT, padx=2)
        Label(time_frame, text="Hours:").pack(side=tk.LEFT, padx=(8,0))
        hours_entry = Entry(time_frame, width=5)
        hours_entry.insert(0, '23')
        hours_entry.pack(side=tk.LEFT, padx=2)
        Label(time_frame, text="Minutes:").pack(side=tk.LEFT, padx=(8,0))
        minutes_entry = Entry(time_frame, width=5)
        minutes_entry.insert(0, '59')
        minutes_entry.pack(side=tk.LEFT, padx=2)

        cat_override_row = tk.Frame(add_window)
        cat_override_row.pack(padx=10, pady=6, anchor="w", fill=tk.X)

        left_options = tk.Frame(cat_override_row)
        left_options.pack(side=tk.LEFT, padx=(0,12), anchor="n")
        
        max_time_override_var = BooleanVar(value=False)
        Checkbutton(left_options, text="Max-Time-Override", variable=max_time_override_var).pack(anchor="w")
        
        muted_var = BooleanVar(value=False)
        Checkbutton(left_options, text="Start Muted", variable=muted_var).pack(anchor="w")

        cat_area_frame = tk.Frame(cat_override_row)
        cat_area_frame.pack(side=tk.LEFT, anchor="n")
        Label(cat_area_frame, text="Categories:").pack(anchor="w")
        cat_frame = tk.Frame(cat_area_frame)
        cat_frame.pack(anchor="w", pady=2)

        categories = ["Jewelry", "Armor", "Weapons", "Scrolls", "Misc"]
        cat_vars = {c: BooleanVar() for c in categories}
        for i, c in enumerate(categories):
            r, col = divmod(i, 3)
            cb = Checkbutton(cat_frame, text=c, variable=cat_vars[c])
            cb.grid(row=r, column=col, sticky="w", padx=8, pady=4)

        custom_wrap = tk.Frame(cat_frame)
        custom_wrap.grid(row=1, column=2, sticky="w", padx=8, pady=4)
        custom_var = BooleanVar(value=False)
        cb_custom = Checkbutton(custom_wrap, text="Custom:", variable=custom_var)
        cb_custom.pack(side=tk.LEFT)
        custom_entry = Entry(custom_wrap, width=18)
        custom_entry.pack(side=tk.LEFT, padx=4)

        button_line = tk.Frame(add_window)
        button_line.pack(padx=10, pady=10, fill=tk.X)

        def add_and_save():
            try:
                name = name_entry.get().strip()
                zone = zone_entry.get().strip()
                if not name:
                    messagebox.showerror("Error", "Vendor name cannot be empty.", parent=add_window)
                    return
                try:
                    council_input = float(council_entry.get() or 0)
                    council = int(council_input * 1000)
                except (ValueError, TypeError):
                    messagebox.showerror("Error", "Council must be numeric (K).", parent=add_window)
                    return

                try:
                    d_raw = int(days_entry.get() or 0)
                    h_raw = int(hours_entry.get() or 0)
                    m_raw = int(minutes_entry.get() or 0)
                except (ValueError, TypeError):
                    messagebox.showerror("Error", "Days, Hours, Minutes must be integers.", parent=add_window)
                    return

                override_flag = max_time_override_var.get()
                total_minutes = d_raw * 24 * 60 + h_raw * 60 + m_raw
                if total_minutes > MAX_TOTAL_MINUTES and not override_flag:
                    messagebox.showerror("Error", "Reset time cannot exceed 6d 23h 59m unless Max-Time-Override is checked.", parent=add_window)
                    return

                d, h, m = _clamp_reset_inputs(d_raw, h_raw, m_raw, override_flag)
                last_reset = calculate_last_reset(d, h, m, override_flag)
                reset_maximum = council
                is_muted = muted_var.get()

                selected_cats = [c for c, var in cat_vars.items() if var.get()]
                if custom_var.get():
                    cv = custom_entry.get().strip()
                    if cv:
                        selected_cats.append(cv)

                seen = set()
                final_cats = []
                for c in selected_cats:
                    if c not in seen:
                        seen.add(c)
                        final_cats.append(c)

                new_vendor = Vendor(name, zone, council, last_reset, reset_maximum, final_cats, is_muted)
                
                log_transaction(
                    self.current_character,
                    name,
                    'creation',
                    0,
                    council,
                    f"Vendor created with initial council: {format_number(council)}"
                )
                
                self.vendors.append(new_vendor)
                save_vendors(self.vendors, self.current_character)
                self.update_vendor_list()
                self.update_total_values()
                messagebox.showinfo("Success", f"Vendor '{name}' added.", parent=add_window)
                add_window.destroy()
            except Exception as e:
                print(f"Error adding vendor: {e}")
                messagebox.showerror("Error", f"Could not add vendor: {e}", parent=add_window)

        add_button = Button(button_line, text="Add", command=add_and_save)
        add_button.pack(side=tk.RIGHT, padx=6)
        cancel_button = Button(button_line, text="Cancel", command=add_window.destroy)
        cancel_button.pack(side=tk.RIGHT)

    def open_update_vendor_window(self, vendor):
        update_window = Toplevel(self)
        update_window.title(f"Update {vendor.name}")
        update_window.geometry("640x400")

        Label(update_window, text=f"Updating {vendor.name} ({vendor.zone})").pack(padx=10, pady=(8,2), anchor="w")

        Label(update_window, text="New Council left (in K):").pack(padx=10, anchor="w")
        council_entry = Entry(update_window)
        council_entry.insert(0, str(vendor.council_left // 1000))
        council_entry.pack(padx=10, fill=tk.X)

        try:
            time_diff = vendor.next_reset - datetime.now()
            init_days = max(0, time_diff.days)
            init_hours = max(0, time_diff.seconds // 3600)
            init_minutes = max(0, (time_diff.seconds % 3600) // 60)
        except Exception as e:
            print(f"Error calculating time diff: {e}")
            init_days = init_hours = init_minutes = 0

        time_frame = tk.Frame(update_window)
        time_frame.pack(padx=10, pady=8, anchor="w", fill=tk.X)
        Label(time_frame, text="Update reset time:").pack(side=tk.LEFT)
        Label(time_frame, text="Days:").pack(side=tk.LEFT, padx=(8,0))
        days_entry = Entry(time_frame, width=5)
        days_entry.insert(0, str(init_days))
        days_entry.pack(side=tk.LEFT, padx=2)
        Label(time_frame, text="Hours:").pack(side=tk.LEFT, padx=(8,0))
        hours_entry = Entry(time_frame, width=5)
        hours_entry.insert(0, str(init_hours))
        hours_entry.pack(side=tk.LEFT, padx=2)
        Label(time_frame, text="Minutes:").pack(side=tk.LEFT, padx=(8,0))
        minutes_entry = Entry(time_frame, width=5)
        minutes_entry.insert(0, str(init_minutes))
        minutes_entry.pack(side=tk.LEFT, padx=2)

        cat_override_row = tk.Frame(update_window)
        cat_override_row.pack(padx=10, pady=6, anchor="w", fill=tk.X)

        left_options = tk.Frame(cat_override_row)
        left_options.pack(side=tk.LEFT, padx=(0,12), anchor="n")
        
        max_time_override_var = BooleanVar(value=False)
        Checkbutton(left_options, text="Max-Time-Override", variable=max_time_override_var).pack(anchor="w")
        
        muted_var = BooleanVar(value=vendor.muted)
        Checkbutton(left_options, text="Muted", variable=muted_var).pack(anchor="w")

        cat_area_frame = tk.Frame(cat_override_row)
        cat_area_frame.pack(side=tk.LEFT, anchor="n")
        Label(cat_area_frame, text="Categories:").pack(anchor="w")
        cat_frame = tk.Frame(cat_area_frame)
        cat_frame.pack(anchor="w", pady=2)

        categories = ["Jewelry", "Armor", "Weapons", "Scrolls", "Misc"]
        cat_vars = {c: BooleanVar(value=(c in vendor.categories)) for c in categories}
        for i, c in enumerate(categories):
            r, col = divmod(i, 3)
            cb = Checkbutton(cat_frame, text=c, variable=cat_vars[c])
            cb.grid(row=r, column=col, sticky="w", padx=8, pady=4)

        custom_wrap = tk.Frame(cat_frame)
        custom_wrap.grid(row=1, column=2, sticky="w", padx=8, pady=4)
        custom_var = BooleanVar(value=False)
        cb_custom = Checkbutton(custom_wrap, text="Custom:", variable=custom_var)
        cb_custom.pack(side=tk.LEFT)
        custom_entry = Entry(custom_wrap, width=18)
        custom_entry.pack(side=tk.LEFT, padx=4)

        custom_items = [c for c in vendor.categories if c not in categories]
        if custom_items:
            custom_var.set(True)
            custom_entry.insert(0, ", ".join(custom_items))

        button_line = tk.Frame(update_window)
        button_line.pack(padx=10, pady=10, fill=tk.X)

        def reset_now():
            try:
                if messagebox.askyesno("Confirm Reset", f"Are you sure you want to reset {vendor.name}?", parent=update_window):
                    old_council = vendor.council_left
                    vendor.last_reset = datetime.now()
                    if vendor.reset_maximum > 0:
                        vendor.council_left = vendor.reset_maximum
                    
                    log_transaction(
                        self.current_character,
                        vendor.name,
                        'reset',
                        old_council,
                        vendor.council_left,
                        f"Manual reset from {format_number(old_council)} to {format_number(vendor.council_left)}"
                    )
                    
                    save_vendors(self.vendors, self.current_character)
                    self.update_vendor_list()
                    self.update_total_values()
                    messagebox.showinfo("Success", f"Vendor '{vendor.name}' has been reset.", parent=update_window)
                    time_diff = vendor.next_reset - datetime.now()
                    new_days = max(0, time_diff.days)
                    new_hours = max(0, time_diff.seconds // 3600)
                    new_minutes = max(0, (time_diff.seconds % 3600) // 60)
                    
                    days_entry.delete(0, tk.END)
                    days_entry.insert(0, str(new_days))
                    hours_entry.delete(0, tk.END)
                    hours_entry.insert(0, str(new_hours))
                    minutes_entry.delete(0, tk.END)
                    minutes_entry.insert(0, str(new_minutes))
                    
                    council_entry.delete(0, tk.END)
                    council_entry.insert(0, str(vendor.council_left // 1000))
            except Exception as e:
                print(f"Error resetting vendor: {e}")
                messagebox.showerror("Error", f"Could not reset vendor: {e}", parent=update_window)

        def update_vendor_action():
            try:
                old_council = vendor.council_left
                
                try:
                    council_input = float(council_entry.get() or 0)
                    new_council = int(council_input * 1000)
                except (ValueError, TypeError):
                    messagebox.showerror("Error", "Council must be numeric (K).", parent=update_window)
                    return

                try:
                    d_raw = int(days_entry.get() or 0)
                    h_raw = int(hours_entry.get() or 0)
                    m_raw = int(minutes_entry.get() or 0)
                except (ValueError, TypeError):
                    messagebox.showerror("Error", "Days/Hours/Minutes must be integers.", parent=update_window)
                    return

                override_flag = max_time_override_var.get()
                total_minutes = d_raw * 24 * 60 + h_raw * 60 + m_raw
                if total_minutes > MAX_TOTAL_MINUTES and not override_flag:
                    messagebox.showerror("Error", "Reset time cannot exceed 6d 23h 59m unless Max-Time-Override is checked.", parent=update_window)
                    return

                d, h, m = _clamp_reset_inputs(d_raw, h_raw, m_raw, override_flag)
                vendor.council_left = new_council
                if new_council > vendor.reset_maximum:
                    vendor.reset_maximum = new_council
                vendor.last_reset = calculate_last_reset(d, h, m, override_flag)
                vendor.muted = muted_var.get()

                if old_council != new_council:
                    transaction_type = 'purchase' if new_council < old_council else 'adjustment'
                    log_transaction(
                        self.current_character,
                        vendor.name,
                        transaction_type,
                        old_council,
                        new_council,
                        f"Manual update: {format_number(old_council)} → {format_number(new_council)}"
                    )

                selected_cats = [c for c, var in cat_vars.items() if var.get()]
                if custom_var.get():
                    cv = custom_entry.get().strip()
                    if cv:
                        extras = [x.strip() for x in cv.split(",") if x.strip()]
                        selected_cats.extend(extras)

                seen = set()
                final_cats = []
                for c in selected_cats:
                    if c not in seen:
                        seen.add(c)
                        final_cats.append(c)

                vendor.categories = final_cats
                save_vendors(self.vendors, self.current_character)
                self.update_vendor_list()
                self.update_total_values()
                messagebox.showinfo("Success", f"Vendor '{vendor.name}' updated.", parent=update_window)
                update_window.destroy()
            except Exception as e:
                print(f"Error updating vendor: {e}")
                messagebox.showerror("Error", f"Could not update vendor: {e}", parent=update_window)

        reset_button = Button(button_line, text="Reset Now", command=reset_now, fg="red")
        reset_button.pack(side=tk.LEFT, padx=6)
        update_button = Button(button_line, text="Update", command=update_vendor_action)
        update_button.pack(side=tk.RIGHT, padx=6)
        close_button = Button(button_line, text="Close", command=update_window.destroy)
        close_button.pack(side=tk.RIGHT)

    def on_closing(self):
        try:
            self.timer_running = False
            save_vendors(self.vendors, self.current_character)
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
        app.protocol("WM_DELETE_WINDOW", app.on_closing)
        app.mainloop()
    except Exception as e:
        print(f"Fatal error: {e}")
        if 'app' in locals():
            try:
                app.destroy()
            except:
                pass
        sys.exit(1)
