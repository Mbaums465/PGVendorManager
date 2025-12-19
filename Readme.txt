===============================
PROJECT GORGON VENDOR TRACKER
===============================

A desktop application designed to help players of the MMORPG Project Gorgon keep track of vendor reset timers, available council, and transaction history across multiple characters.



This tool provides a clear, visual interface to manage which vendors are ready for you to sell to, how much council they have left, and when they will reset.

--------------------------------------------------------------------------------

FEATURES
--------

* MULTI-CHARACTER SUPPORT: Easily add and switch between all your characters. Vendor lists are saved per character.

* VENDOR TRACKING: Add an unlimited number of vendors, tracking their name, zone, current council, and maximum council.

* COUNTDOWN TIMERS: Each vendor has a live countdown timer showing exactly when their inventory and council will reset.

* VISUAL STATUS INDICATORS:
    - Color-Coded Borders: Vendor cards change color from red (long time to reset) to green (ready to reset), giving you an at-a-glance overview.
    - Pulsing Animation: Vendors that are empty, ready to reset, and not muted will pulse, making them easy to spot.
    - Gray Background: Vendors with zero council are grayed out.

* ENHANCED SEARCH: The filter uses "AND" logic, allowing you to search for multiple terms at once. For example, "weap fae" will only show vendors in the "Fae Realm" tagged with "Weapons".

* TRANSACTION LOGGING: Automatically logs vendor creation, deletions, resets, and council updates.

* HISTORY VIEWER: A dedicated window to review your transaction history and see a summary of council earned per day over a custom timeframe.

--------------------------------------------------------------------------------

INSTALLATION & USAGE (FOR WINDOWS)
----------------------------------

Getting started is easy! The included "VendorMe.bat" file handles everything for you.

1. Download the Files: Place "VendorMe.bat", "playerlog_reader.py" and "PGVendorTracker.py" in the same folder.

2. Run the Launcher: Double-click "VendorMe.bat".

That's it! The first time you run it, the script will automatically:
* Check if you have Python installed.
* If you don't, it will download and install it for you. This may take a few minutes.
* Launch the Vendor Tracker application.

After the first time, you can just double-click "VendorMe.bat" to start the program directly.

--------------------------------------------------------------------------------

MANUAL INSTALLATION (FOR MACOS, LINUX, OR ADVANCED USERS)
---------------------------------------------------------

If you aren't on Windows or prefer to run the script manually:

1. Install Python: Ensure you have Python 3.6 or newer installed on your system.

2. Save the Code: Save the main Python script as "PGVendorTracker.py".

3. Run from Terminal: Open a terminal or command prompt, navigate to the directory where you saved the file, and run the following command:
   
   python PGVendorTracker.py

4. Automatic Setup: The first time you run the script, it will automatically create a "character_data" folder and a "vendors.db" database file inside it to store all your information.

--------------------------------------------------------------------------------

HOW TO USE THE APP
------------------

CHARACTERS
* Add a Character: Click the "Add New Character" button and enter your character's name. The vendors from the "Default" profile will be copied to your new character to get you started.
* Switch Characters: Use the dropdown menu at the top-left to switch between your character profiles.

VENDORS
* Add a Vendor: Click "Add New Vendor".
    - Fill in the Name, Zone, and starting Council (in 'K', so '50' means 50,000).
    - Set the "Time until reset". The default is 6 days, 23 hours, 59 minutes.
    - Assign Categories to make searching easier.
* Update a Vendor: Click the "Update" button on any vendor card.
    - You can adjust the remaining council, update the reset timer, change categories, or mute/unmute the vendor.
* Reset a Vendor: In the Update window, click "Reset Now" to immediately reset a vendor's council to its maximum and restart the 7-day timer.
* Delete a Vendor: Click the "Delete" button on a vendor card. This action cannot be undone.

FILTERING AND VIEWING
* Search: Type into the "Filter" box at the top. The list will update in real-time. You can use multiple words (e.g., "jewelry serbule").
* Muted Vendors: Check the "Show Muted" box to see vendors you have hidden from the main list.

TRANSACTIONS
* Click "View Transactions" to open the history window. Here you can see a detailed log of all changes and a summary of your council earnings by day.

