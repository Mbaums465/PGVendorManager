import tkinter as tk
from tkinter import messagebox, Toplevel, Label, Entry, Button, Scrollbar, Canvas, OptionMenu, StringVar, simpledialog, Checkbutton, BooleanVar
import json
from datetime import datetime, timedelta
import os

# Base directory for all character files
DATA_DIR = 'character_data'

class Vendor:
    """Represents a vendor with their details and reset time."""
    def __init__(self, name, zone, council_left, last_reset, reset_maximum=0, categories=None):
        self.name = name
        self.zone = zone
        self.council_left = council_left

        # Ensure last_reset is stored as datetime
        if isinstance(last_reset, str):
            self.last_reset = datetime.fromisoformat(last_reset)
        elif isinstance(last_reset, datetime):
            self.last_reset = last_reset
        else:
            raise ValueError("last_reset must be str or datetime")

        # Set the initial reset_maximum to the maximum of the provided values
        self.reset_maximum = max(council_left, reset_maximum)
        self.categories = categories or []  # list[str]

    def to_dict(self):
        """Converts the vendor object to a dictionary for JSON saving."""
        return {
            "name": self.name,
            "zone": self.zone,
            "council_left": self.council_left,
            "last_reset": self.last_reset.isoformat(),
            "reset_maximum": self.reset_maximum,
            "categories": self.categories
        }

    @staticmethod
    def from_dict(vendor_dict):
        """Creates a vendor object from a dictionary."""
        return Vendor(
            vendor_dict['name'],
            vendor_dict['zone'],
            vendor_dict.get('council_left', 0),
            vendor_dict['last_reset'],
            vendor_dict.get('reset_maximum', 0),
            vendor_dict.get('categories', [])
        )
    
    @property
    def next_reset(self):
        """Calculates the next reset time (7 days after the last reset)."""
        return self.last_reset + timedelta(days=7)

def load_vendors(character_name):
    """Loads vendor data for a specific character from the JSON file."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    
    file_path = os.path.join(DATA_DIR, f"{character_name}_vendors.json")
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            return [Vendor.from_dict(v) for v in data]
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_vendors(vendors, character_name):
    """Saves vendor data for a specific character to the JSON file."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    file_path = os.path.join(DATA_DIR, f"{character_name}_vendors.json")
    vendor_list_of_dicts = [v.to_dict() for v in vendors]
    with open(file_path, 'w') as f:
        json.dump(vendor_list_of_dicts, f, indent=4)

def format_number(value):
    """Formats a number into K or M format using Excel-like formatting."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    else:
        return f"{value // 1000}K"

def _clamp_reset_inputs(days, hours, minutes):
    """
    Clamp user inputs so 'time until reset' stays under 7 days.
    Enforce: 0 <= days <= 6, 0 <= hours <= 23, 0 <= minutes <= 59.
    """
    try:
        d = max(0, min(6, int(days or 0)))
        h = max(0, min(23, int(hours or 0)))
        m = max(0, min(59, int(minutes or 0)))
    except ValueError:
        # Fallback to zeros if parsing fails
        d, h, m = 0, 0, 0
    return d, h, m

def calculate_last_reset(days, hours, minutes):
    """Calculates the last reset time based on current time and time until reset."""
    d, h, m = _clamp_reset_inputs(days, hours, minutes)
    time_until_reset = timedelta(days=d, hours=h, minutes=m)
    time_since_last_reset = timedelta(days=7) - time_until_reset
    return datetime.now() - time_since_last_reset  # return datetime, not str

class VendorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Vendor Reset Manager")
        self.geometry("800x500")

        self.character_files = self.get_character_files()
        self.characters = [f.replace('_vendors.json', '') for f in self.character_files]
        if not self.characters:
            self.current_character = "Default"
            self.characters.append("Default")
        else:
            self.current_character = self.characters[0]

        self.vendors = load_vendors(self.current_character)

        self.create_widgets()
        self.update_vendor_list()

    def get_character_files(self):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
            return []
        return [f for f in os.listdir(DATA_DIR) if f.endswith('_vendors.json')]

    def create_widgets(self):
        # Character selection and top panel
        char_frame = tk.Frame(self)
        char_frame.pack(fill=tk.X, padx=10, pady=5)
        
        Label(char_frame, text="Character:").pack(side=tk.LEFT)
        self.char_var = StringVar(self)
        self.char_var.set(self.current_character)
        self.char_var.trace("w", self.on_char_change)

        self.char_menu = OptionMenu(char_frame, self.char_var, *self.characters)
        self.char_menu.pack(side=tk.LEFT, padx=5)

        add_char_button = Button(char_frame, text="Add New", command=self.add_new_character)
        add_char_button.pack(side=tk.LEFT, padx=5)

        # Filter bar
        filter_frame = tk.Frame(self)
        filter_frame.pack(fill=tk.X, padx=10, pady=5)

        Label(filter_frame, text="Filter:").pack(side=tk.LEFT)
        self.filter_var = StringVar()
        self.filter_var.trace("w", lambda *args: self.update_vendor_list())
        filter_entry = Entry(filter_frame, textvariable=self.filter_var)
        filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # Top info panel
        top_frame = tk.Frame(self, bg="lightgrey", relief="groove", bd=2)
        top_frame.pack(fill=tk.X, padx=10, pady=5)

        self.total_council_label = Label(top_frame, text="Current Vendor Council Pool: 0K", bg="lightgrey", font=("Helvetica", 10))
        self.total_council_label.pack(side=tk.LEFT, padx=10, pady=5)
        
        self.total_max_label = Label(top_frame, text="Total Vendor Cash: 0K", bg="lightgrey", font=("Helvetica", 10))
        self.total_max_label.pack(side=tk.LEFT, padx=10, pady=5)

        self.next_reset_label = Label(top_frame, text="Time until next reset: --", bg="lightgrey", font=("Helvetica", 10))
        self.next_reset_label.pack(side=tk.LEFT, padx=10, pady=5)
        
        # Frame for action buttons
        button_frame = tk.Frame(self)
        button_frame.pack(pady=5)

        add_vendor_button = Button(button_frame, text="Add New Vendor", command=self.open_add_vendor_window)
        add_vendor_button.pack(side=tk.LEFT, padx=5)

        # Vendor list frame
        self.vendor_frame = tk.Frame(self)
        self.vendor_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

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
        
        # Bind mouse wheel events for scrolling
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        def _bind_to_mousewheel(event):
            self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        def _unbind_from_mousewheel(event):
            self.canvas.unbind_all("<MouseWheel>")
        
        # Bind mouse wheel when entering the main window area
        self.bind('<Enter>', _bind_to_mousewheel)
        self.bind('<Leave>', _unbind_from_mousewheel)
        self.canvas.bind('<Enter>', _bind_to_mousewheel)
        self.canvas.bind('<Leave>', _unbind_from_mousewheel)
        
        self.after(1000, self.update_timers)
        self.update_total_values()
    
    def on_char_change(self, *args):
        self.current_character = self.char_var.get()
        self.vendors = load_vendors(self.current_character)
        self.update_vendor_list()
        self.update_total_values()

    def add_new_character(self):
        new_char = simpledialog.askstring("New Character", "Enter new character name:")
        if new_char:
            if not new_char.isalnum():
                messagebox.showerror("Error", "Character name must be alphanumeric.", parent=self)
                return
            if new_char in self.characters:
                messagebox.showerror("Error", "Character already exists.", parent=self)
            else:
                self.characters.append(new_char)
                if 'Default' in self.characters and new_char != 'Default':
                    default_vendors = load_vendors('Default')
                    save_vendors(default_vendors, new_char)
                
                self.char_var.set(new_char)
                self.update_char_menu()

    def update_char_menu(self):
        menu = self.char_menu["menu"]
        menu.delete(0, "end")
        for char in sorted(self.characters):
            menu.add_command(label=char, command=tk._setit(self.char_var, char))
        
    def update_total_values(self):
        total_council = sum(v.council_left for v in self.vendors)
        total_maximum = sum(v.reset_maximum for v in self.vendors)
        
        self.total_council_label.config(text=f"Current Vendor Council Pool: {format_number(total_council)}")
        self.total_max_label.config(text=f"Total Vendor Cash: {format_number(total_maximum)}")
        
    def update_timers(self):
        # Update each vendor's visible time label
        for widget in self.scrollable_frame.winfo_children():
            if hasattr(widget, 'vendor_name'):
                vendor_name = widget.vendor_name
                vendor = next((v for v in self.vendors if v.name == vendor_name), None)
                if vendor:
                    time_diff = vendor.next_reset - datetime.now()
                    if time_diff.total_seconds() > 0:
                        days = time_diff.days
                        hours = time_diff.seconds // 3600
                        minutes = (time_diff.seconds % 3600) // 60
                        time_left_str = f"{days} days, {hours}h, {minutes}m"
                    else:
                        time_left_str = "RESET PENDING!"
                    
                    # Find the time_label widget. It's inside: parent_frame -> vendor_frame -> info_frame -> time_label (last)
                    time_label = widget.winfo_children()[0].winfo_children()[0].winfo_children()[-1]
                    time_label.config(text=f"Time until reset: {time_left_str}")

        # Update the global "next reset" timer
        if self.vendors:
            next_reset = min(v.next_reset for v in self.vendors)
            time_diff = next_reset - datetime.now()
            if time_diff.total_seconds() > 0:
                days = time_diff.days
                hours = time_diff.seconds // 3600
                minutes = (time_diff.seconds % 3600) // 60
                reset_str = f"{days} days, {hours}h, {minutes}m"
            else:
                reset_str = "RESET PENDING!"
            self.next_reset_label.config(text=f"Time until next reset: {reset_str}")
        else:
            self.next_reset_label.config(text="Time until next reset: --")
        
        self.after(1000, self.update_timers)
        
    # --- Clustering Logic ---
    def _group_vendors_by_reset_time(self, vendors):
        """Groups vendors into clusters based on significant time gaps between resets."""
        if not vendors:
            return []

        clusters = []
        current_cluster = [vendors[0]]
        
        # Natural break threshold between clusters
        TIME_GAP_THRESHOLD = timedelta(hours=1)
        
        for i in range(1, len(vendors)):
            time_diff = vendors[i].next_reset - vendors[i-1].next_reset
            
            if time_diff > TIME_GAP_THRESHOLD:
                clusters.append(current_cluster)
                current_cluster = [vendors[i]]
            else:
                current_cluster.append(vendors[i])

        if current_cluster:
            clusters.append(current_cluster)

        return clusters

    # --- Updated vendor list renderer (preserves clustering & coloring) ---
    def update_vendor_list(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        query = self.filter_var.get().lower().strip()
        filtered_vendors = []
        for v in self.vendors:
            search_blob = f"{v.name} {v.zone} {' '.join(v.categories)}".lower()
            if query in search_blob:
                filtered_vendors.append(v)
                
        sorted_vendors = sorted(filtered_vendors, key=lambda x: x.next_reset)
        
        clusters = self._group_vendors_by_reset_time(sorted_vendors)
        
        total_clusters = len(clusters)
        for i, cluster in enumerate(clusters):
            border_color = None
            if total_clusters >= 3:
                if i == 0:
                    border_color = "#32CD32"  # A light green for the soonest cluster
                elif i == total_clusters - 1:
                    border_color = "#8B0000"  # A dark red for the last cluster
                    
            for vendor in cluster:
                # Background color rules
                bg_color = "SystemButtonFace"
                if vendor.council_left == 0:
                    bg_color = "#D3D3D3"     # greyed out when empty
                elif (vendor.next_reset - datetime.now()).total_seconds() <= 0:
                    bg_color = "#90EE90"     # light green when reset pending
                
                # Optional colored border for cluster edges
                parent_frame = tk.Frame(self.scrollable_frame, bg=border_color or "", bd=5 if border_color else 0)
                parent_frame.pack(fill=tk.X, pady=5)
                parent_frame.vendor_name = vendor.name

                vendor_frame = tk.Frame(parent_frame, bd=2, relief="groove", padx=5, pady=5, bg=bg_color)
                vendor_frame.pack(fill=tk.X, expand=True)

                info_frame = tk.Frame(vendor_frame, bg=bg_color)
                info_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
                
                name_label = Label(info_frame, text=f"{vendor.name} ({vendor.zone})", font=("Helvetica", 12, "bold"), bg=bg_color)
                name_label.pack(anchor="w")

                council_label = Label(info_frame, text=f"Council left: {format_number(vendor.council_left)}", bg=bg_color)
                council_label.pack(anchor="w")
                
                if vendor.reset_maximum > 0:
                    max_label = Label(info_frame, text=f"Reset maximum: {format_number(vendor.reset_maximum)}", bg=bg_color)
                    max_label.pack(anchor="w")
                
                if vendor.categories:
                    cat_label = Label(info_frame, text="Categories: " + ", ".join(vendor.categories), bg=bg_color)
                    cat_label.pack(anchor="w")

                time_label = Label(info_frame, text="", fg="red", bg=bg_color) 
                time_label.pack(anchor="w")
                
                button_frame = tk.Frame(vendor_frame, bg=bg_color)
                button_frame.pack(side=tk.RIGHT)
                
                edit_button = Button(button_frame, text="Update", command=lambda v=vendor: self.open_update_vendor_window(v))
                edit_button.pack(pady=2, padx=5)

                delete_button = Button(button_frame, text="Delete", command=lambda v=vendor: self.delete_vendor(v))
                delete_button.pack(pady=2, padx=5)
                
    # --- Add Vendor (same behavior as v6; includes category selection vertical list) ---
    def open_add_vendor_window(self):
        add_window = Toplevel(self)
        add_window.title("Add New Vendor")
        
        Label(add_window, text="Vendor Name:").pack(padx=10, pady=5)
        name_entry = Entry(add_window)
        name_entry.pack(padx=10, pady=5)
        
        Label(add_window, text="Vendor Zone:").pack(padx=10, pady=5)
        zone_entry = Entry(add_window)
        zone_entry.pack(padx=10, pady=5)
        
        Label(add_window, text="Council left (in K):").pack(padx=10, pady=5)
        council_entry = Entry(add_window)
        council_entry.pack(padx=10, pady=5)

        time_frame = tk.Frame(add_window)
        time_frame.pack(pady=10)
        Label(time_frame, text="Time until reset:").pack(side=tk.LEFT)
        Label(time_frame, text="Days:").pack(side=tk.LEFT)
        days_entry = Entry(time_frame, width=5)
        days_entry.pack(side=tk.LEFT, padx=2)
        Label(time_frame, text="Hours:").pack(side=tk.LEFT)
        hours_entry = Entry(time_frame, width=5)
        hours_entry.pack(side=tk.LEFT, padx=2)
        Label(time_frame, text="Minutes:").pack(side=tk.LEFT)
        minutes_entry = Entry(time_frame, width=5)
        minutes_entry.pack(side=tk.LEFT, padx=2)

        categories = ["Jewelry", "Armor", "Weapons", "Scrolls", "Misc"]
        cat_vars = {c: BooleanVar() for c in categories}
        Label(add_window, text="Categories:").pack(pady=5)
        for c in categories:
            cb = Checkbutton(add_window, text=c, variable=cat_vars[c])
            cb.pack(anchor="w")
        
        def add_and_save():
            name = name_entry.get().strip()
            zone = zone_entry.get().strip()
            
            try:
                council_input = float(council_entry.get() or 0)
                council = int(council_input * 1000)
                d_raw = days_entry.get()
                h_raw = hours_entry.get()
                m_raw = minutes_entry.get()
            except ValueError:
                messagebox.showerror("Error", "Council, Days, Hours, and Minutes must be numbers.", parent=add_window)
                return

            if not name:
                messagebox.showerror("Error", "Vendor name cannot be empty.", parent=add_window)
                return
            
            # Clamp time inputs for safety
            d, h, m = _clamp_reset_inputs(d_raw, h_raw, m_raw)

            last_reset = calculate_last_reset(d, h, m)
            reset_maximum = council
            selected_cats = [c for c, var in cat_vars.items() if var.get()]

            new_vendor = Vendor(name, zone, council, last_reset, reset_maximum, selected_cats)
            self.vendors.append(new_vendor)
            save_vendors(self.vendors, self.current_character)
            self.update_vendor_list()
            self.update_total_values()
            messagebox.showinfo("Success", f"Vendor '{name}' added.", parent=add_window)
            add_window.destroy()
        
        add_button = Button(add_window, text="Add", command=add_and_save)
        add_button.pack(pady=10)

    # --- Update Vendor (modified per your request) ---
    def open_update_vendor_window(self, vendor):
        update_window = Toplevel(self)
        update_window.title(f"Update {vendor.name}")
        
        Label(update_window, text=f"Updating {vendor.name} from {vendor.zone}").pack(padx=10, pady=5)

        Label(update_window, text="New Council left (in K):").pack(padx=10, pady=5)
        council_entry = Entry(update_window)
        council_entry.insert(0, str(vendor.council_left // 1000))
        council_entry.pack(padx=10, pady=5)

        # Prefill time until next reset
        time_diff = vendor.next_reset - datetime.now()
        initial_days = max(0, time_diff.days)
        initial_hours = max(0, time_diff.seconds // 3600)
        initial_minutes = max(0, (time_diff.seconds % 3600) // 60)
        
        time_frame = tk.Frame(update_window)
        time_frame.pack(pady=10)
        
        Label(time_frame, text="Update reset time:").pack(side=tk.LEFT)
        Label(time_frame, text="Days:").pack(side=tk.LEFT)
        days_entry = Entry(time_frame, width=5)
        days_entry.insert(0, str(initial_days))
        days_entry.pack(side=tk.LEFT, padx=2)
        
        Label(time_frame, text="Hours:").pack(side=tk.LEFT)
        hours_entry = Entry(time_frame, width=5)
        hours_entry.insert(0, str(initial_hours))
        hours_entry.pack(side=tk.LEFT, padx=2)
        
        Label(time_frame, text="Minutes:").pack(side=tk.LEFT)
        minutes_entry = Entry(time_frame, width=5)
        minutes_entry.insert(0, str(initial_minutes))
        minutes_entry.pack(side=tk.LEFT, padx=2)

        # Categories laid out as 2 rows × 3 columns with a Custom checkbox+entry in the 6th cell
        categories = ["Jewelry", "Armor", "Weapons", "Scrolls", "Misc"]
        Label(update_window, text="Categories:").pack(pady=5)

        cat_frame = tk.Frame(update_window)
        cat_frame.pack(pady=5)

        cat_vars = {c: BooleanVar(value=(c in vendor.categories)) for c in categories}

        # Place the 5 fixed categories in a 2x3 grid (first 5 slots)
        for i, c in enumerate(categories):
            r, col = divmod(i, 3)
            cb = Checkbutton(cat_frame, text=c, variable=cat_vars[c])
            cb.grid(row=r, column=col, sticky="w", padx=5, pady=2)

        # 6th slot (row=1, col=2): Custom checkbox + inline entry inside a subframe
        custom_wrap = tk.Frame(cat_frame)
        custom_wrap.grid(row=1, column=2, sticky="w", padx=5, pady=2)

        custom_var = BooleanVar(value=False)
        cb_custom = Checkbutton(custom_wrap, text="Custom:", variable=custom_var)
        cb_custom.pack(side=tk.LEFT)

        custom_entry = Entry(custom_wrap, width=12)
        custom_entry.pack(side=tk.LEFT, padx=4)

        def reset_now():
            if messagebox.askyesno("Confirm Reset", f"Are you sure you want to reset {vendor.name}?", parent=update_window):
                vendor.last_reset = datetime.now()
                if vendor.reset_maximum > 0:
                    vendor.council_left = vendor.reset_maximum
                save_vendors(self.vendors, self.current_character)
                self.update_vendor_list()
                self.update_total_values()
                messagebox.showinfo("Success", f"Vendor '{vendor.name}' has been reset.", parent=update_window)
                update_window.destroy()
        
        def update_vendor():
            # Council
            try:
                council_input = float(council_entry.get() or 0)
                new_council = int(council_input * 1000)
            except ValueError:
                messagebox.showerror("Error", "Council must be a number.", parent=update_window)
                return

            # Time (clamped)
            d, h, m = _clamp_reset_inputs(days_entry.get(), hours_entry.get(), minutes_entry.get())

            # Apply updates
            vendor.council_left = new_council
            if new_council > vendor.reset_maximum:
                vendor.reset_maximum = new_council
            
            vendor.last_reset = calculate_last_reset(d, h, m)

            selected_cats = [c for c, var in cat_vars.items() if var.get()]

            if custom_var.get():
                custom_val = custom_entry.get().strip()
                if custom_val:
                    selected_cats.append(custom_val)

            # De-duplicate while preserving order
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
        
        # Buttons on the SAME LINE: Reset Now (left), Update (right)
        button_line = tk.Frame(update_window)
        button_line.pack(pady=10, fill=tk.X)

        reset_button = Button(button_line, text="Reset Now", command=reset_now, fg="red")
        reset_button.pack(side=tk.LEFT, padx=10)

        update_button = Button(button_line, text="Update", command=update_vendor)
        update_button.pack(side=tk.RIGHT, padx=10)

    def delete_vendor(self, vendor_to_delete):
        if messagebox.askyesno("Delete Vendor", f"Are you sure you want to delete {vendor_to_delete.name}?", parent=self):
            self.vendors = [v for v in self.vendors if v.name != vendor_to_delete.name]
            save_vendors(self.vendors, self.current_character)
            self.update_vendor_list()
            self.update_total_values()
            messagebox.showinfo("Deleted", f"{vendor_to_delete.name} has been deleted.", parent=self)

if __name__ == "__main__":
    app = VendorApp()
    app.protocol("WM_DELETE_WINDOW", lambda: (save_vendors(app.vendors, app.current_character), app.destroy()))
    app.mainloop()
