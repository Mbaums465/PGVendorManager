import tkinter as tk
from tkinter import messagebox, Toplevel, Label, Entry, Button, Scrollbar, Canvas, OptionMenu, StringVar, simpledialog, Checkbutton, BooleanVar
import json
from datetime import datetime, timedelta
import os
import sys

# ---------------------
# Configuration
# ---------------------
# Use absolute path to avoid issues when running from different directories
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'character_data')
DEFAULT_CHARACTER = 'Default'
MAX_TOTAL_MINUTES = 6 * 24 * 60 + 23 * 60 + 59  # 6d 23h 59m

# ---------------------
# Vendor model
# ---------------------
class Vendor:
    def __init__(self, name, zone, council_left, last_reset, reset_maximum=0, categories=None):
        self.name = name
        self.zone = zone
        self.council_left = int(council_left)
        
        # Improved last_reset parsing with better error handling
        if isinstance(last_reset, str):
            try:
                self.last_reset = datetime.fromisoformat(last_reset)
            except ValueError:
                try:
                    # fallback: if it's stored as timestamp string
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

    def to_dict(self):
        return {
            "name": self.name,
            "zone": self.zone,
            "council_left": int(self.council_left),
            "last_reset": self.last_reset.isoformat(),
            "reset_maximum": int(self.reset_maximum),
            "categories": self.categories
        }

    @staticmethod
    def from_dict(d):
        return Vendor(
            d.get("name", ""),
            d.get("zone", ""),
            d.get("council_left", 0),
            d.get("last_reset", datetime.now().isoformat()),
            d.get("reset_maximum", 0),
            d.get("categories", [])
        )

    @property
    def next_reset(self):
        return self.last_reset + timedelta(days=7)


# ---------------------
# Persistence
# ---------------------
def _ensure_data_dir():
    try:
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
    except OSError as e:
        print(f"Error creating data directory: {e}")
        messagebox.showerror("Error", f"Could not create data directory: {e}")

def character_file_path(character_name):
    # Sanitize filename to prevent path issues
    safe_name = "".join(c for c in character_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    return os.path.join(DATA_DIR, f"{safe_name}_vendors.json")

def save_vendors(vendors, character_name):
    """Save vendors for character_name. Keep format as a list (backwards-compatible)."""
    try:
        _ensure_data_dir()
        file_path = character_file_path(character_name)
        vendor_list = [v.to_dict() for v in vendors]
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(vendor_list, f, indent=4, ensure_ascii=False)
    except (OSError, IOError) as e:
        print(f"Error saving vendors: {e}")
        messagebox.showerror("Error", f"Could not save vendors: {e}")

def load_vendors(character_name):
    """
    Load vendors for character_name with improved error handling.
    """
    _ensure_data_dir()
    file_path = character_file_path(character_name)
    
    if not os.path.exists(file_path):
        return []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # if file is a list of dicts
        if isinstance(data, list):
            vendors = []
            for vendor_data in data:
                try:
                    vendors.append(Vendor.from_dict(vendor_data))
                except Exception as e:
                    print(f"Error loading vendor {vendor_data.get('name', 'Unknown')}: {e}")
            return vendors
            
        # if file is a dict with "vendors"
        if isinstance(data, dict):
            vendors_blob = data.get("vendors") or data.get("vendor_list") or []
            if isinstance(vendors_blob, list):
                vendors = []
                for vendor_data in vendors_blob:
                    try:
                        vendors.append(Vendor.from_dict(vendor_data))
                    except Exception as e:
                        print(f"Error loading vendor {vendor_data.get('name', 'Unknown')}: {e}")
                return vendors
        
        print(f"Warning: Unexpected file format in {file_path}")
        return []
        
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON file {file_path}: {e}")
        messagebox.showerror("Error", f"Could not parse vendor file for {character_name}: {e}")
        return []
    except (OSError, IOError) as e:
        print(f"Error reading file {file_path}: {e}")
        messagebox.showerror("Error", f"Could not read vendor file for {character_name}: {e}")
        return []


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
    """Clamp user inputs to reasonable bounds. If not override, clamp to <= 6d 23h 59m."""
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
        # still keep hours/minutes in normal ranges
        h = min(h, 23)
        m = min(m, 59)
    return int(d), int(h), int(m)

def calculate_last_reset(days, hours, minutes, override_max_time=False):
    """Return the last_reset datetime given time until next reset (d,h,m)."""
    d, h, m = _clamp_reset_inputs(days, hours, minutes, override_max_time)
    time_until_reset = timedelta(days=d, hours=h, minutes=m)
    if not override_max_time:
        # "time since last reset" = 7 days - time_until_reset
        time_since_last_reset = timedelta(days=7) - time_until_reset
        return datetime.now() - time_since_last_reset
    else:
        # allow >7d and compute accordingly
        return datetime.now() + time_until_reset - timedelta(days=7)


# ---------------------
# GUI Application
# ---------------------
class VendorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Vendor Reset Manager")
        self.geometry("900x600")

        # Initialize vendors list
        self.vendors = []
        
        # Load characters with error handling
        try:
            _ensure_data_dir()
            files = [f for f in os.listdir(DATA_DIR) if f.endswith('_vendors.json')]
            self.characters = sorted(list({f.replace('_vendors.json', '') for f in files}))
        except OSError:
            self.characters = []
            
        if DEFAULT_CHARACTER not in self.characters:
            self.characters.insert(0, DEFAULT_CHARACTER)
        if not self.characters:
            self.characters = [DEFAULT_CHARACTER]
        self.current_character = self.characters[0]

        self.vendors = load_vendors(self.current_character)

        self.create_widgets()
        self.update_vendor_list()
        self.update_total_values()
        
        # Start timer updates
        self.timer_running = True
        self.after(1000, self.update_timers)

    def create_widgets(self):
        # Top: character selection and filter
        top = tk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)

        Label(top, text="Character:").pack(side=tk.LEFT)
        self.char_var = StringVar(value=self.current_character)
        self.char_var.trace("w", self.on_char_change)
        self.char_menu = OptionMenu(top, self.char_var, *self.characters)
        self.char_menu.pack(side=tk.LEFT, padx=6)

        Button(top, text="Add New Character", command=self.add_new_character).pack(side=tk.LEFT, padx=6)

        Label(top, text="Filter:").pack(side=tk.LEFT, padx=(12,4))
        self.filter_var = StringVar()
        self.filter_var.trace("w", lambda *a: self.update_vendor_list())
        Entry(top, textvariable=self.filter_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        # Info bar
        info = tk.Frame(self, bg="lightgrey", relief="raised", bd=1)
        info.pack(fill=tk.X, padx=8, pady=6)
        self.total_council_label = Label(info, text="Current Vendor Council Pool: 0K", bg="lightgrey")
        self.total_council_label.pack(side=tk.LEFT, padx=8, pady=6)
        self.total_max_label = Label(info, text="Total Vendor Cash: 0K", bg="lightgrey")
        self.total_max_label.pack(side=tk.LEFT, padx=8, pady=6)
        self.next_reset_label = Label(info, text="Time until next reset: --", bg="lightgrey")
        self.next_reset_label.pack(side=tk.LEFT, padx=8, pady=6)

        # Buttons
        btns = tk.Frame(self)
        btns.pack(fill=tk.X, padx=8, pady=4)
        Button(btns, text="Add New Vendor", command=self.open_add_vendor_window).pack(side=tk.LEFT, padx=4)

        # Vendor list with scrolling
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

        # Improved mouse wheel binding for cross-platform compatibility
        def _on_mousewheel(event):
            try:
                # Windows
                if hasattr(event, 'delta'):
                    self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                # Linux
                elif event.num == 4:
                    self.canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    self.canvas.yview_scroll(1, "units")
            except Exception as e:
                print(f"Mouse wheel error: {e}")
        
        # Bind for different platforms
        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)  # Windows
        self.canvas.bind_all("<Button-4>", _on_mousewheel)    # Linux
        self.canvas.bind_all("<Button-5>", _on_mousewheel)    # Linux

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
        
        # More flexible character name validation
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
        
        # If Default existed, copy default vendors into new char
        default_path = character_file_path(DEFAULT_CHARACTER)
        if os.path.exists(default_path):
            try:
                default_vendors = load_vendors(DEFAULT_CHARACTER)
                save_vendors(default_vendors, safe_name)
            except Exception as e:
                print(f"Error copying default vendors: {e}")

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
            total_council = sum(v.council_left for v in self.vendors)
            total_maximum = sum(v.reset_maximum for v in self.vendors)
            self.total_council_label.config(text=f"Current Vendor Council Pool: {format_number(total_council)}")
            self.total_max_label.config(text=f"Total Vendor Cash: {format_number(total_maximum)}")
        except Exception as e:
            print(f"Error updating total values: {e}")

    def update_timers(self):
        if not self.timer_running:
            return
            
        try:
            # Update each vendor's time label if present
            for widget in self.scrollable_frame.winfo_children():
                if hasattr(widget, 'vendor_name') and hasattr(widget, 'time_label'):
                    vname = widget.vendor_name
                    vendor = next((x for x in self.vendors if x.name == vname), None)
                    if vendor and widget.time_label.winfo_exists():
                        time_diff = vendor.next_reset - datetime.now()
                        if time_diff.total_seconds() > 0:
                            days = time_diff.days
                            hours = time_diff.seconds // 3600
                            minutes = (time_diff.seconds % 3600) // 60
                            time_str = f"{days} days, {hours}h, {minutes}m"
                        else:
                            time_str = "RESET PENDING!"
                        widget.time_label.config(text=f"Time until reset: {time_str}")

            # Global next reset
            if self.vendors:
                next_reset = min(v.next_reset for v in self.vendors)
                td = next_reset - datetime.now()
                if td.total_seconds() > 0:
                    days = td.days
                    hours = td.seconds // 3600
                    minutes = (td.seconds % 3600) // 60
                    reset_str = f"{days} days, {hours}h, {minutes}m"
                else:
                    reset_str = "RESET PENDING!"
                self.next_reset_label.config(text=f"Time until next reset: {reset_str}")
            else:
                self.next_reset_label.config(text="Time until next reset: --")
                
        except Exception as e:
            print(f"Error updating timers: {e}")
        
        # Schedule next update
        self.after(1000, self.update_timers)

    def _group_vendors_by_reset_time(self, vendors):
        if not vendors:
            return []
        clusters = []
        current = [vendors[0]]
        THRESH = timedelta(hours=1)
        for i in range(1, len(vendors)):
            td = vendors[i].next_reset - vendors[i-1].next_reset
            if td > THRESH:
                clusters.append(current)
                current = [vendors[i]]
            else:
                current.append(vendors[i])
        if current:
            clusters.append(current)
        return clusters

    def update_vendor_list(self):
        try:
            # Clear existing widgets
            for w in self.scrollable_frame.winfo_children():
                w.destroy()

            query = self.filter_var.get().lower().strip()
            filtered = []
            for v in self.vendors:
                blob = f"{v.name} {v.zone} {' '.join(v.categories)}".lower()
                if query in blob:
                    filtered.append(v)

            sorted_vendors = sorted(filtered, key=lambda x: x.next_reset)
            clusters = self._group_vendors_by_reset_time(sorted_vendors)
            total_clusters = len(clusters)

            for i, cluster in enumerate(clusters):
                border_color = None
                if total_clusters >= 3:
                    if i == 0:
                        border_color = "#32CD32"
                    elif i == total_clusters - 1:
                        border_color = "#8B0000"

                for vendor in cluster:
                    bg = "SystemButtonFace"
                    if vendor.council_left == 0:
                        bg = "#D3D3D3"
                    elif (vendor.next_reset - datetime.now()).total_seconds() <= 0:
                        bg = "#90EE90"

                    parent = tk.Frame(self.scrollable_frame, bg=border_color or "", bd=5 if border_color else 0)
                    parent.pack(fill=tk.X, pady=5)
                    parent.vendor_name = vendor.name

                    vf = tk.Frame(parent, bd=2, relief="groove", padx=5, pady=5, bg=bg)
                    vf.pack(fill=tk.X, expand=True)

                    info = tk.Frame(vf, bg=bg)
                    info.pack(side=tk.LEFT, fill=tk.X, expand=True)

                    Label(info, text=f"{vendor.name} ({vendor.zone})", font=("Helvetica", 12, "bold"), bg=bg).pack(anchor="w")
                    Label(info, text=f"Council left: {format_number(vendor.council_left)}", bg=bg).pack(anchor="w")
                    if vendor.reset_maximum > 0:
                        Label(info, text=f"Reset maximum: {format_number(vendor.reset_maximum)}", bg=bg).pack(anchor="w")
                    if vendor.categories:
                        Label(info, text="Categories: " + ", ".join(vendor.categories), bg=bg).pack(anchor="w")

                    time_label = Label(info, text="", fg="red", bg=bg)
                    time_label.pack(anchor="w")

                    # Attach time_label to parent so update_timers can find it
                    parent.time_label = time_label

                    btns = tk.Frame(vf, bg=bg)
                    btns.pack(side=tk.RIGHT)
                    Button(btns, text="Update", command=lambda v=vendor: self.open_update_vendor_window(v)).pack(padx=5, pady=2)
                    Button(btns, text="Delete", command=lambda v=vendor: self.delete_vendor(v)).pack(padx=5, pady=2)
        except Exception as e:
            print(f"Error updating vendor list: {e}")
            messagebox.showerror("Error", f"Could not update vendor list: {e}")

    def delete_vendor(self, vendor_to_delete):
        try:
            if messagebox.askyesno("Delete Vendor", f"Are you sure you want to delete {vendor_to_delete.name}?", parent=self):
                self.vendors = [v for v in self.vendors if v.name != vendor_to_delete.name]
                save_vendors(self.vendors, self.current_character)
                self.update_vendor_list()
                self.update_total_values()
                messagebox.showinfo("Deleted", f"{vendor_to_delete.name} has been deleted.", parent=self)
        except Exception as e:
            print(f"Error deleting vendor: {e}")
            messagebox.showerror("Error", f"Could not delete vendor: {e}")

    # ---------------------
    # Add Vendor Window
    # ---------------------
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

        # Time inputs
        time_frame = tk.Frame(add_window)
        time_frame.pack(padx=10, pady=8, anchor="w", fill=tk.X)
        Label(time_frame, text="Time until reset:").pack(side=tk.LEFT)
        Label(time_frame, text="Days:").pack(side=tk.LEFT, padx=(8,0))
        days_entry = Entry(time_frame, width=5)
        days_entry.pack(side=tk.LEFT, padx=2)
        Label(time_frame, text="Hours:").pack(side=tk.LEFT, padx=(8,0))
        hours_entry = Entry(time_frame, width=5)
        hours_entry.pack(side=tk.LEFT, padx=2)
        Label(time_frame, text="Minutes:").pack(side=tk.LEFT, padx=(8,0))
        minutes_entry = Entry(time_frame, width=5)
        minutes_entry.pack(side=tk.LEFT, padx=2)

        # Categories & override row
        cat_override_row = tk.Frame(add_window)
        cat_override_row.pack(padx=10, pady=6, anchor="w", fill=tk.X)

        # Override on the left
        max_time_override_var = BooleanVar(value=False)
        override_frame = tk.Frame(cat_override_row)
        override_frame.pack(side=tk.LEFT, padx=(0,12), anchor="n")
        Checkbutton(override_frame, text="Max-Time-Override", variable=max_time_override_var).pack(anchor="n")

        # Categories area
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

        # Custom slot in row=1, col=2
        custom_wrap = tk.Frame(cat_frame)
        custom_wrap.grid(row=1, column=2, sticky="w", padx=8, pady=4)
        custom_var = BooleanVar(value=False)
        cb_custom = Checkbutton(custom_wrap, text="Custom:", variable=custom_var)
        cb_custom.pack(side=tk.LEFT)
        custom_entry = Entry(custom_wrap, width=18)
        custom_entry.pack(side=tk.LEFT, padx=4)

        # Buttons
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

                # Raw time values
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

                selected_cats = [c for c, var in cat_vars.items() if var.get()]
                if custom_var.get():
                    cv = custom_entry.get().strip()
                    if cv:
                        selected_cats.append(cv)

                # Dedupe preserve order
                seen = set()
                final_cats = []
                for c in selected_cats:
                    if c not in seen:
                        seen.add(c)
                        final_cats.append(c)

                new_vendor = Vendor(name, zone, council, last_reset, reset_maximum, final_cats)
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

    # ---------------------
    # Update Vendor Window
    # ---------------------
    def open_update_vendor_window(self, vendor):
        update_window = Toplevel(self)
        update_window.title(f"Update {vendor.name}")
        update_window.geometry("640x360")

        Label(update_window, text=f"Updating {vendor.name} ({vendor.zone})").pack(padx=10, pady=(8,2), anchor="w")

        Label(update_window, text="New Council left (in K):").pack(padx=10, anchor="w")
        council_entry = Entry(update_window)
        council_entry.insert(0, str(vendor.council_left // 1000))
        council_entry.pack(padx=10, fill=tk.X)

        # Prefill time until next reset
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

        # Categories & override row (same layout as add)
        cat_override_row = tk.Frame(update_window)
        cat_override_row.pack(padx=10, pady=6, anchor="w", fill=tk.X)

        max_time_override_var = BooleanVar(value=False)
        override_frame = tk.Frame(cat_override_row)
        override_frame.pack(side=tk.LEFT, padx=(0,12), anchor="n")
        Checkbutton(override_frame, text="Max-Time-Override", variable=max_time_override_var).pack(anchor="n")

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

        # If vendor has custom categories not in fixed list, prefill custom
        custom_items = [c for c in vendor.categories if c not in categories]
        if custom_items:
            custom_var.set(True)
            custom_entry.insert(0, ", ".join(custom_items))

        # Buttons
        button_line = tk.Frame(update_window)
        button_line.pack(padx=10, pady=10, fill=tk.X)

        def reset_now():
            try:
                if messagebox.askyesno("Confirm Reset", f"Are you sure you want to reset {vendor.name}?", parent=update_window):
                    vendor.last_reset = datetime.now()
                    if vendor.reset_maximum > 0:
                        vendor.council_left = vendor.reset_maximum
                    save_vendors(self.vendors, self.current_character)
                    self.update_vendor_list()
                    self.update_total_values()
                    messagebox.showinfo("Success", f"Vendor '{vendor.name}' has been reset.", parent=update_window)
                    update_window.destroy()
            except Exception as e:
                print(f"Error resetting vendor: {e}")
                messagebox.showerror("Error", f"Could not reset vendor: {e}", parent=update_window)

        def update_vendor_action():
            try:
                try:
                    council_input = float(council_entry.get() or 0)
                    new_council = int(council_input * 1000)
                except (ValueError, TypeError):
                    messagebox.showerror("Error", "Council must be numeric (K).", parent=update_window)
                    return

                # Raw time inputs
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

                selected_cats = [c for c, var in cat_vars.items() if var.get()]
                if custom_var.get():
                    cv = custom_entry.get().strip()
                    if cv:
                        extras = [x.strip() for x in cv.split(",") if x.strip()]
                        selected_cats.extend(extras)

                # Dedupe preserve order
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

    def on_closing(self):
        """Handle application closing."""
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
        # Ensure saving on close
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
