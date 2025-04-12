# Standard library imports
import datetime
import os
import re
import subprocess
import tempfile
import threading
import time

# Third-party imports
import gi
import gettext

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

# Application constants
DEFAULT_WINDOW_SIZE = (450, 400)
DEFAULT_NTP_SERVER = "pool.ntp.org"
UI_MARGIN_SMALL = 5
UI_MARGIN_STANDARD = 10
CSS_STYLE = b"""
    .blue-button { background: #3584e4; color: white; }
    .red-button { background: #e43e35; color: white; }
"""

# Uses the previously defined constant CSS_STYLE
css_provider = Gtk.CssProvider()
css_provider.load_from_data(CSS_STYLE)
Gtk.StyleContext.add_provider_for_screen(
    Gdk.Screen.get_default(),
    css_provider,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
)

# Translation support - Implementação melhorada
lang_translations = gettext.translation(
    "comm-xfce-datetime", localedir="/usr/share/locale", fallback=True
)
lang_translations.install()
# define _ shortcut for translations
_ = lang_translations.gettext


class DateTimeApp(Gtk.Window):  # Alterado para Gtk.Window
    def __init__(self):
        """Initialize the Date and Time Settings application."""
        super().__init__(title=_("Date and Time Settings"))
        self.set_default_size(*DEFAULT_WINDOW_SIZE)
        self.set_icon_name("time")

        # Initialize application state
        self.selected_timezone = None
        self.search_text = ""
        self.timezone_info_cache = {}  # Cache for timezone info

        # Create main layout container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_box)

        # Create and add tab container
        self.notebook = Gtk.Notebook()
        main_box.pack_start(self.notebook, True, True, 0)

        # Create tab pages
        self.create_date_time_tab()
        self.create_timezone_tab()
        self.create_system_tab()

        # Add status area at the bottom
        self._create_status_area(main_box)

        # Add button bar at the bottom
        self._create_button_bar(main_box)

        # Populate timezone list
        self.populate_timezone_list()

    def _create_status_area(self, main_box):
        """Create and add the status area to the main box."""
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_SMALL)
        status_box.set_margin_start(UI_MARGIN_STANDARD)
        status_box.set_margin_end(UI_MARGIN_STANDARD)
        status_box.set_margin_top(UI_MARGIN_SMALL)
        status_box.set_margin_bottom(UI_MARGIN_STANDARD)

        # Current timezone label
        self.current_tz_label = Gtk.Label()
        self.update_current_timezone_label()
        self.current_tz_label.set_xalign(0)
        status_box.pack_start(self.current_tz_label, False, False, 0)

        # Status Label
        self.status_label = Gtk.Label()
        self.status_label.set_markup("<i>" + _("Status: Ready") + "</i>")
        self.status_label.set_xalign(0)
        status_box.pack_start(self.status_label, False, False, 0)

        main_box.pack_start(status_box, False, False, 0)

    def _create_button_bar(self, main_box):
        """Create and add the button bar to the main box."""
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=UI_MARGIN_STANDARD)
        button_box.set_halign(Gtk.Align.END)
        button_box.set_margin_start(UI_MARGIN_STANDARD)
        button_box.set_margin_end(UI_MARGIN_STANDARD)
        button_box.set_margin_top(UI_MARGIN_SMALL)
        button_box.set_margin_bottom(UI_MARGIN_STANDARD)

        # Cancel button
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.get_style_context().add_class("red-button")
        cancel_button.connect("clicked", self.on_cancel_clicked)

        # Synchronize button
        sync_button = Gtk.Button(label=_("Synchronize"))
        sync_button.connect("clicked", self.on_sync_clicked)

        # Apply button
        apply_button = Gtk.Button(label=_("Apply"))
        apply_button.get_style_context().add_class("blue-button")
        apply_button.connect("clicked", self.on_apply_clicked)

        # Add buttons to button box
        button_box.pack_start(cancel_button, False, False, 0)
        button_box.pack_start(sync_button, False, False, 0)
        button_box.pack_start(apply_button, False, False, 0)

        main_box.pack_start(button_box, False, False, 0)

    def create_date_time_tab(self):
        """Create the Date & Time tab"""
        date_time_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        date_time_box.set_margin_start(10)
        date_time_box.set_margin_end(10)
        date_time_box.set_margin_top(10)
        date_time_box.set_margin_bottom(10)

        # Date selection
        date_frame = Gtk.Frame(label=_("Date"))
        date_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        date_box.set_margin_start(10)
        date_box.set_margin_end(10)
        date_box.set_margin_top(10)
        date_box.set_margin_bottom(10)

        self.calendar = Gtk.Calendar()
        date_box.pack_start(self.calendar, True, True, 0)  # GTK3
        date_frame.add(date_box)  # GTK3
        date_time_box.pack_start(date_frame, True, True, 0)  # GTK3

        # Time adjustment
        time_frame = Gtk.Frame(label=_("Time"))
        time_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        time_box.set_halign(Gtk.Align.CENTER)
        time_box.set_margin_start(10)
        time_box.set_margin_end(10)
        time_box.set_margin_top(10)
        time_box.set_margin_bottom(10)

        self.hour_spinner = Gtk.SpinButton.new_with_range(0, 23, 1)
        self.minute_spinner = Gtk.SpinButton.new_with_range(0, 59, 1)
        self.second_spinner = Gtk.SpinButton.new_with_range(0, 59, 1)
        self.set_initial_time()

        time_box.pack_start(Gtk.Label(label=_("Hour:")), False, False, 0)  # GTK3
        time_box.pack_start(self.hour_spinner, False, False, 0)
        time_box.pack_start(Gtk.Label(label=_("Minute:")), False, False, 0)
        time_box.pack_start(self.minute_spinner, False, False, 0)
        time_box.pack_start(Gtk.Label(label=_("Second:")), False, False, 0)
        time_box.pack_start(self.second_spinner, False, False, 0)

        time_frame.add(time_box)  # GTK3
        date_time_box.pack_start(time_frame, False, False, 0)  # GTK3

        # Add the tab
        tab_label = Gtk.Label(label=_("Date & Time"))
        self.notebook.append_page(date_time_box, tab_label)

    def create_timezone_tab(self):
        """Create the Timezone tab"""
        tz_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        tz_box.set_margin_start(10)
        tz_box.set_margin_end(10)
        tz_box.set_margin_top(10)
        tz_box.set_margin_bottom(10)

        # Search entry
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        search_label = Gtk.Label(label=_("Search:"))
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.connect("search-changed", self.on_search_changed)
        # Configuração para expandir o search entry
        self.search_entry.set_hexpand(True)
        search_box.pack_start(search_label, False, False, 0)  # GTK3
        search_box.pack_start(self.search_entry, True, True, 0)
        tz_box.pack_start(search_box, False, False, 0)  # GTK3

        # Create scrollable list for timezones
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_min_content_height(200)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Create list box for timezone items
        self.timezone_list = Gtk.ListBox()
        self.timezone_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.timezone_list.connect("row-selected", self.on_timezone_selected)
        scrolled_window.add(self.timezone_list)  # GTK3
        tz_box.pack_start(scrolled_window, True, True, 0)  # GTK3

        # Current selection
        self.selection_label = Gtk.Label()
        self.selection_label.set_markup("<b>" + _("Selected:") + "</b> " + _("None"))
        self.selection_label.set_xalign(0)
        tz_box.pack_start(self.selection_label, False, False, 0)  # GTK3

        # Add the tab
        tab_label = Gtk.Label(label=_("Timezone"))
        self.notebook.append_page(tz_box, tab_label)

    def create_system_tab(self):
        """Create the System tab"""
        system_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        system_box.set_margin_start(10)
        system_box.set_margin_end(10)
        system_box.set_margin_top(10)
        system_box.set_margin_bottom(10)

        # Network time sync frame
        sync_frame = Gtk.Frame(label=_("Time Synchronization"))
        sync_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sync_box.set_margin_start(10)
        sync_box.set_margin_end(10)
        sync_box.set_margin_top(10)
        sync_box.set_margin_bottom(10)

        # NTP checkbox
        self.ntp_checkbox = Gtk.CheckButton(
            label=_("Enable Network Time Synchronization")
        )
        self.ntp_checkbox.set_active(self.is_ntp_enabled())
        self.ntp_toggle_lock = False
        self.ntp_checkbox.connect("toggled", self.on_ntp_toggled)
        sync_box.pack_start(self.ntp_checkbox, False, False, 0)  # GTK3

        # NTP server info (example only, not functional)
        server_label = Gtk.Label(label=_("NTP Servers:"))
        server_label.set_xalign(0)
        server_label.set_margin_top(5)
        sync_box.pack_start(server_label, False, False, 0)  # GTK3

        server_entry = Gtk.Entry()
        server_entry.set_text("pool.ntp.org")
        server_entry.set_sensitive(False)  # Read-only example
        sync_box.pack_start(server_entry, False, False, 0)  # GTK3

        note_label = Gtk.Label()
        note_label.set_markup("<i>" + _("Note: NTP servers are configured in /etc/ntp.conf") + "</i>")
        note_label.set_xalign(0)
        note_label.set_margin_top(5)
        sync_box.pack_start(note_label, False, False, 0)  # GTK3

        sync_frame.add(sync_box)  # GTK3
        system_box.pack_start(sync_frame, False, False, 0)  # GTK3

        # Hardware clock frame
        hw_frame = Gtk.Frame(label=_("Hardware Clock"))
        hw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hw_box.set_margin_start(10)
        hw_box.set_margin_end(10)
        hw_box.set_margin_top(10)
        hw_box.set_margin_bottom(10)

        self.hw_utc_radio = Gtk.RadioButton(label=_("Hardware clock uses UTC"))
        self.hw_local_radio = Gtk.RadioButton.new_with_label_from_widget(
            self.hw_utc_radio, _("Hardware clock uses local time")
        )

        # Set initial state based on system setting
        self.hw_utc_radio.set_active(self.is_hw_clock_utc())
        self.hw_local_radio.set_active(not self.is_hw_clock_utc())

        hw_box.pack_start(self.hw_utc_radio, False, False, 0)  # GTK3
        hw_box.pack_start(self.hw_local_radio, False, False, 0)  # GTK3

        hw_frame.add(hw_box)  # GTK3
        system_box.pack_start(hw_frame, False, False, 0)  # GTK3

        # Add the tab
        tab_label = Gtk.Label(label=_("System"))
        self.notebook.append_page(system_box, tab_label)

    def create_timezone_row(self, city, country, region_path, timezone, utc_offset):
        """Create a stylized row for the timezone list"""
        # Main row container
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        row.set_margin_start(5)
        row.set_margin_end(5)
        row.set_margin_top(5)
        row.set_margin_bottom(5)

        # Left side: City and region info
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)

        # City and country
        city_label = Gtk.Label()
        city_label.set_markup(f"<span weight='bold'>{city}</span> {country}")
        city_label.set_xalign(0)
        info_box.pack_start(city_label, False, False, 0)  # GTK3

        # Region path and UTC
        region_label = Gtk.Label()
        region_label.set_markup(f"<span foreground='#cccccc' size='small'>{region_path} • {utc_offset}</span>")
        region_label.set_xalign(0)
        info_box.pack_start(region_label, False, False, 0)  # GTK3

        row.pack_start(info_box, True, True, 0)  # GTK3

        # Right side: Current time in that timezone
        local_time = self.get_time_in_timezone(timezone)
        time_label = Gtk.Label()
        time_label.set_markup(f"<span foreground='#cccccc' size='small'>{local_time}</span>")
        row.pack_start(time_label, False, False, 0)  # GTK3

        # Create the list row
        list_row = Gtk.ListBoxRow()
        list_row.add(row)  # GTK3

        # Store timezone data
        list_row.timezone = timezone
        list_row.city = city
        list_row.country = country
        list_row.utc_offset = utc_offset

        return list_row

    def get_time_in_timezone(self, timezone):
        """Get the current time in the specified timezone"""
        try:
            # Use env to set TZ environment variable properly
            result = subprocess.run(
                ["env", f"TZ={timezone}", "date", "+%a %H:%M"],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def get_timezone_utc_offset(self, timezone):
        """Get the UTC offset for a timezone."""
        # Check if we have this info cached
        if timezone in self.timezone_info_cache:
            return self.timezone_info_cache[timezone]

        try:
            # Use env to set TZ environment variable properly
            result = subprocess.run(
                ["env", f"TZ={timezone}", "date", "+%z"],
                capture_output=True, text=True, check=True
            )
            offset_raw = result.stdout.strip()

            # Parse offset properly
            if offset_raw and len(offset_raw) >= 5:  # Format should be +HHMM or -HHMM
                sign = offset_raw[0]
                hours = int(offset_raw[1:3])
                minutes = int(offset_raw[3:5])
                
                # Format the offset string
                if minutes == 0:
                    offset_str = f"UTC{sign}{hours}"
                else:
                    offset_str = f"UTC{sign}{hours}:{minutes:02d}"

                # Cache the result
                self.timezone_info_cache[timezone] = offset_str
                return offset_str
        except Exception as e:
            print(f"Error getting timezone offset for {timezone}: {e}")

        # Default fallback if we can't determine
        return "UTC"

    def populate_timezone_list(self):
        """Populate the timezone list with available timezones."""
        try:
            # Get available timezones
            result = subprocess.run(
                ["timedatectl", "list-timezones"],
                capture_output=True, text=True, check=True
            )
            all_timezones = result.stdout.splitlines()

            # Process and map common country codes for better display
            country_mapping = self.create_country_mapping()

            # Process each timezone
            for timezone in sorted(all_timezones):
                parts = timezone.split('/')

                if len(parts) >= 2:
                    region = parts[0]
                    city_raw = parts[-1]

                    # Format the city name
                    city = city_raw.replace('_', ' ')

                    # Try to determine country from region or mapping
                    country = country_mapping.get(city_raw, "")

                    # Get UTC offset
                    utc_offset = self.get_timezone_utc_offset(timezone)

                    # Create the region path for display
                    region_path = f"{region}/{city_raw}"

                    # Create and add row to list
                    row = self.create_timezone_row(city, country, region_path, timezone, utc_offset)
                    self.timezone_list.add(row)  # GTK3

            # Show all rows
            self.timezone_list.show_all()  # GTK3

            # Update list based on initial search (empty)
            self.filter_timezone_list()

        except Exception as e:
            self.show_message_dialog(
                Gtk.MessageType.ERROR,
                _("Error loading timezone data: ") + str(e)
            )

    def create_country_mapping(self):
        """Create a mapping of common cities to their countries"""
        return {
            "Sao_Paulo": _("Brazil"),
            "New_York": _("United States"),
            "Los_Angeles": _("United States"),
            "Chicago": _("United States"),
            "Toronto": _("Canada"),
            "Mexico_City": _("Mexico"),
            "London": _("United Kingdom"),
            "Paris": _("France"),
            "Berlin": _("Germany"),
            "Rome": _("Italy"),
            "Madrid": _("Spain"),
            "Moscow": _("Russia"),
            "Cairo": _("Egypt"),
            "Johannesburg": _("South Africa"),
            "Dubai": _("UAE"),
            "Mumbai": _("India"),
            "Tokyo": _("Japan"),
            "Shanghai": _("China"),
            "Seoul": _("South Korea"),
            "Singapore": _("Singapore"),
            "Sydney": _("Australia"),
            "Auckland": _("New Zealand"),
            # Add more as needed
        }

    def on_search_changed(self, entry):
        """Filter the timezone list based on search text"""
        self.search_text = entry.get_text().strip().lower()
        self.filter_timezone_list()

    def filter_timezone_list(self):
        """Show/hide rows based on search text in GTK3"""
        for row in self.timezone_list.get_children():
            if not hasattr(row, 'timezone'):
                continue

            # Get the data from the row
            city = row.city.lower() if hasattr(row, 'city') else ""
            country = row.country.lower() if hasattr(row, 'country') else ""
            timezone = row.timezone.lower() if hasattr(row, 'timezone') else ""

            # Check if the search text is in any of the fields
            visible = (
                self.search_text in city or
                self.search_text in country or
                self.search_text in timezone
            )

            row.set_visible(visible)  # GTK3

    def on_timezone_selected(self, list_box, row):
        """Handle timezone selection from the list"""
        if row:
            timezone = row.timezone
            city = row.city
            country = row.country
            utc_offset = row.utc_offset

            self.selected_timezone = timezone
            self.selection_label.set_markup(
                f"<b>{_('Selected:')}</b> {timezone} ({utc_offset})"
            )
            self.status_label.set_markup(
                f"<i>{_('Status:')}</i> {_('Selected')} {city}, {country}"
            )
        else:
            self.selected_timezone = None
            self.selection_label.set_markup(
                f"<b>{_('Selected:')}</b> {_('None')}"
            )

    def update_current_timezone_label(self):
        """Update the label showing current timezone."""
        try:
            result = subprocess.run(
                ["timedatectl", "status"],
                capture_output=True, text=True, check=True
            )
            tz_line = None
            timezone = None

            for line in result.stdout.splitlines():
                if "Time zone:" in line:
                    tz_line = line.strip()
                    # Extract timezone
                    match = re.search(r'Time zone: ([^\s]+)', tz_line)
                    if match:
                        timezone = match.group(1)
                    break

            if tz_line:
                # Get UTC offset
                utc_offset = ""
                if timezone:
                    utc_offset = self.get_timezone_utc_offset(timezone)

                # Enhanced display with local time and UTC offset
                now = datetime.datetime.now()
                local_time = now.strftime("%H:%M:%S")
                self.current_tz_label.set_markup(
                    f"<b>{_('Current:')}</b> {timezone} {utc_offset} ({_('Local time:')} {local_time})"
                )
            else:
                self.current_tz_label.set_markup(f"<b>{_('Current:')}</b> {_('Unknown')}")
        except Exception:
            self.current_tz_label.set_markup(f"<b>{_('Current:')}</b> {_('Error getting timezone')}")

    def is_ntp_enabled(self):
        """Check if automatic synchronization service is active."""
        try:
            result = subprocess.run(
                ["timedatectl", "show", "--property=NTP"],
                capture_output=True, text=True, check=True
            )
            return "NTP=yes" in result.stdout
        except subprocess.CalledProcessError:
            return False

    def is_hw_clock_utc(self):
        """Check if hardware clock uses UTC."""
        try:
            result = subprocess.run(
                ["timedatectl", "show", "--property=LocalRTC"],
                capture_output=True, text=True, check=True
            )
            # LocalRTC=yes means hardware clock uses local time, not UTC
            return "LocalRTC=no" in result.stdout
        except subprocess.CalledProcessError:
            return True  # Default to UTC

    def on_ntp_toggled(self, button):
        """Enable or disable automatic synchronization"""
        if self.ntp_toggle_lock:  # Avoid loop
            return

        self.ntp_toggle_lock = True

        new_state = "true" if button.get_active() else "false"
        if new_state == "true":
            msg = _("Network time synchronization enabled.")
        else:
            msg = _("Network time synchronization disabled.")

        try:
            # Execute command with administrative privileges
            self.run_privileged_commands([
                ["timedatectl", "set-ntp", new_state]
            ])
            self.status_label.set_markup("<i>" + _("Status:") + "</i> " + msg)
        except Exception as e:
            GLib.idle_add(
                self.show_message_dialog,
                Gtk.MessageType.ERROR,
                str(e)
            )
            button.set_active(not button.get_active())
        finally:
            self.ntp_toggle_lock = False

    def set_initial_time(self):
        """Set initial time based on the system's current time."""
        now = datetime.datetime.now()
        self.hour_spinner.set_value(now.hour)
        self.minute_spinner.set_value(now.minute)
        self.second_spinner.set_value(now.second)

    def on_apply_clicked(self, button):
        """Confirmation before applying settings."""
        try:
            # Get selected values
            year, month, day = self.calendar.get_date()
            month = month + 1  # Month starts at 0
            hour = self.hour_spinner.get_value_as_int()
            minute = self.minute_spinner.get_value_as_int()
            second = self.second_spinner.get_value_as_int()

            # Check if we have a selected timezone
            if not self.selected_timezone:
                # Select current tab to guide user
                self.notebook.set_current_page(1)  # Timezone tab
                raise ValueError(_("No timezone selected. Please select a timezone from the list."))

            timezone = self.selected_timezone

            # Get UTC offset for display
            utc_offset = self.get_timezone_utc_offset(timezone)

            # Check hardware clock setting
            use_utc = self.hw_utc_radio.get_active()

            date_str = f"{year}-{month:02}-{day:02}"
            time_str = f"{hour:02}:{minute:02}:{second:02}"
            date_str_inverted = f"{day:02}/{month:02}/{year}"

            # Confirmation message
            confirm_msg = _(
                "The following settings will be applied:\n\n"
                "Date: {}\n"
                "Time: {}\n"
                "Timezone: {} ({})\n"
                "Hardware clock: {}\n\n"
                "Do you want to continue?"
            ).format(
                date_str_inverted,
                time_str,
                timezone,
                utc_offset,
                _("UTC") if use_utc else _("Local time")
            )

            # Create dialog - Adaptado para GTK3
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                message_format=confirm_msg
            )

            # Connect signal to capture user response
            dialog.connect(
                "response", self.on_confirm_response,
                date_str, time_str, timezone, use_utc
            )
            dialog.show_all()  # GTK3

        except Exception as e:
            self.show_message_dialog(Gtk.MessageType.ERROR, str(e))

    def _apply_timezone_to_session(self, timezone):
        """Apply timezone to current session and update environment."""
        # Apply TZ to current session
        os.environ['TZ'] = timezone
        time.tzset()

        # Update session environment using dbus for all applications
        try:
            self.run_command([
                "dbus-send", "--session", "--dest=org.freedesktop.DBus",
                "--type=method_call", "--print-reply", "/org/freedesktop/DBus",
                "org.freedesktop.DBus.UpdateActivationEnvironment",
                f"array:string:TZ={timezone}"
            ])
        except Exception as e:
            print(f"Warning: Failed to update session environment: {e}")

        # Export TZ to XDG runtime dir to ensure new applications have the setting
        runtime_dir = os.environ.get('XDG_RUNTIME_DIR', f"/run/user/{os.getuid()}")
        if os.path.exists(runtime_dir):
            os.makedirs(f"{runtime_dir}/environment.d", exist_ok=True)
            try:
                with open(f"{runtime_dir}/environment.d/50-timezone.conf", "w") as f:
                    f.write(f"TZ={timezone}\n")
            except Exception as e:
                print(f"Warning: Failed to write timezone to runtime directory: {e}")

    def _prepare_timezone_commands(self, timezone, date_str, time_str, use_utc):
        """Prepare commands for system timezone changes."""
        commands = []

        # Add command to set hardware clock mode
        local_rtc = "false" if use_utc else "true"
        commands.append(["timedatectl", "set-local-rtc", local_rtc])

        # Add command to set timezone
        commands.append(["timedatectl", "set-timezone", timezone])

        # Add command to set date/time if NTP is disabled
        if not self.ntp_checkbox.get_active():
            commands.append(["timedatectl", "set-time", f"{date_str} {time_str}"])

        return commands

    def on_confirm_response(
        self, dialog, response,
        date_str, time_str, timezone, use_utc
    ):
        """Apply settings if user confirms in the dialog."""
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            try:
                # Show progress dialog
                progress_dialog = Gtk.MessageDialog(
                    parent=self,
                    flags=Gtk.DialogFlags.MODAL,
                    type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.NONE,
                    message_format=_("Applying settings...")
                )
                progress_dialog.show_all()

                # Process all events to show dialog
                while Gtk.events_pending():
                    Gtk.main_iteration()

                # Prepare and execute privileged commands
                commands = self._prepare_timezone_commands(timezone, date_str, time_str, use_utc)
                self.run_privileged_commands(commands)

                # Apply timezone to session environment
                self._apply_timezone_to_session(timezone)

                # Close progress dialog
                progress_dialog.destroy()

                self.update_current_timezone_label()
                self.status_label.set_markup("<i>" + _("Status:") + "</i> " + _("Settings applied successfully!"))

                # Show success message with important information
                self.show_message_dialog(
                    Gtk.MessageType.INFO,
                    _("Settings have been applied successfully!\n\n"
                    "The new timezone is now active for system services and new applications. "
                    "Some running applications may need to be restarted to use the new timezone settings.")
                )
            except Exception as e:
                self.show_message_dialog(Gtk.MessageType.ERROR, str(e))

    def on_cancel_clicked(self, button):
        """Close the application without making any changes."""
        Gtk.main_quit()  # GTK3

    def _get_ntp_sync_command(self):
        """
        Determine the appropriate NTP synchronization command for the system.

        Returns:
            list: Command to execute for NTP synchronization
        """
        # Try to detect which time synchronization system is available
        try:
            # Check for systemd-timesyncd
            result = subprocess.run(
                ["systemctl", "status", "systemd-timesyncd"],
                capture_output=True, text=True
            )
            if "active" in result.stdout:
                return ["systemctl", "restart", "systemd-timesyncd"]
        except Exception:
            pass

        try:
            # Check for chronyd
            result = subprocess.run(
                ["systemctl", "status", "chronyd"],
                capture_output=True, text=True
            )
            if "active" in result.stdout:
                return ["chronyc", "makestep"]
        except Exception:
            pass

        # Default to ntpd if available
        return ["ntpd", "-gq"]

    def on_sync_clicked(self, button):
        """Synchronize time with NTP servers and display a message."""
        button.set_sensitive(False)  # Disable button during synchronization
        self.status_label.set_markup(
            "<i>" + _("Status:") + "</i> " + _("Please wait, synchronizing...")
        )

        def sync_thread():
            try:
                # Get appropriate NTP sync command for this system
                sync_command = self._get_ntp_sync_command()

                # Use privileged commands function to execute NTP sync
                self.run_privileged_commands([sync_command])

                GLib.idle_add(
                    lambda: self.status_label.set_markup("<i>" + _("Status:") + "</i> " + _("Synchronization completed successfully!"))
                )
                GLib.idle_add(self.set_initial_time)
                GLib.idle_add(self.update_current_timezone_label)
            except Exception as e:
                GLib.idle_add(
                    self.show_message_dialog,
                    Gtk.MessageType.ERROR,
                    str(e)
                )
                GLib.idle_add(
                    lambda: self.status_label.set_markup("<i>" + _("Status:") + "</i> " + _("Synchronization failed."))
                )
            finally:
                GLib.idle_add(lambda: button.set_sensitive(True))

        threading.Thread(target=sync_thread, daemon=True).start()

    def run_command(self, command):
        """Run a command in the shell with better error handling"""
        try:
            # Use check=True to raise an exception in case of error
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else str(e)
            # Check if it's an authorization error
            if "polkit" in error_msg.lower() or "authentication" in error_msg.lower():
                raise RuntimeError(_("Permission denied. Please provide administrator password when prompted."))
            else:
                raise RuntimeError(f"{_('Command failed')}: {error_msg}")

    def show_message_dialog(self, message_type, message):
        """Display a dialog with a message."""
        # Adaptado para GTK3
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            type=message_type,
            buttons=Gtk.ButtonsType.OK,
            message_format=message
        )
        dialog.connect("response", lambda d, r: d.destroy())  # GTK3
        dialog.show_all()  # GTK3

    def create_temp_script(self, commands):
        """
        Creates a temporary Python script that executes the provided commands with privileges.

        Args:
            commands: List of lists, where each inner list is a command to be executed

        Returns:
            str: Path to the created temporary script
        """
        # Create a temporary file with correct permissions
        fd, script_path = tempfile.mkstemp(suffix='.py', prefix='datetime_')

        try:
            with os.fdopen(fd, 'w') as f:
                # Write shebang and imports
                f.write("#!/usr/bin/env python3\n")
                f.write("import os, sys, subprocess\n\n")

                # Check for root privileges
                f.write("if os.geteuid() != 0:\n")
                f.write("    print('This script must be run as root', file=sys.stderr)\n")
                f.write("    sys.exit(1)\n\n")

                # Command execution function
                f.write("def run_command(cmd):\n")
                f.write("    try:\n")
                f.write("        subprocess.run(cmd, check=True)\n")
                f.write("        print(f'Successfully executed: {\" \".join(cmd)}')\n")
                f.write("        return True\n")
                f.write("    except subprocess.CalledProcessError as e:\n")
                f.write("        print(f'Error executing {\" \".join(cmd)}: {e}', file=sys.stderr)\n")
                f.write("        return False\n\n")

                # Setup success tracking
                f.write("success = True\n\n")

                # Add each command
                for cmd in commands:
                    cmd_str = str(cmd).replace("'", "\"")
                    f.write(f"success = run_command({cmd_str}) and success\n")

                # Exit with appropriate status
                f.write("\nsys.exit(0 if success else 1)\n")

            # Make the script executable
            os.chmod(script_path, 0o755)
            return script_path

        except Exception as e:
            # Clean up in case of error
            try:
                os.unlink(script_path)
            except Exception:
                pass
            raise RuntimeError(f"Failed to create temporary script: {e}")

    def _create_temp_script_inline(self, commands):
        """Fallback method to create script without external template."""
        fd, script_path = tempfile.mkstemp(suffix='.py', prefix='datetime_')

        with os.fdopen(fd, 'w') as f:
            f.write("#!/usr/bin/env python3\n")
            f.write("import os\n")
            f.write("import sys\n")
            f.write("import subprocess\n\n")

            # Check if running as root
            f.write("if os.geteuid() != 0:\n")
            f.write("    print('This script must be run as root', file=sys.stderr)\n")
            f.write("    sys.exit(1)\n\n")

            # Function to execute commands with error checking
            f.write("def run_command(cmd):\n")
            f.write("    try:\n")
            f.write("        subprocess.run(cmd, check=True)\n")
            f.write("        print(f'Successfully executed: {\" \".join(cmd)}')\n")
            f.write("        return True\n")
            f.write("    except subprocess.CalledProcessError as e:\n")
            f.write("        print(f'Error executing {\" \".join(cmd)}: {e}', file=sys.stderr)\n")
            f.write("        return False\n\n")

            # Add all commands to the script
            f.write("# Execute all privileged commands\n")
            f.write("success = True\n")

            for cmd in commands:
                cmd_str = str(cmd).replace("'", "\"")
                f.write(f"success = run_command({cmd_str}) and success\n")

            f.write("\nsys.exit(0 if success else 1)\n")

        # Make the script executable
        os.chmod(script_path, 0o755)
        return script_path

    def run_privileged_commands(self, commands):
        """
        Execute multiple commands with administrator privileges using a single authentication.

        This function creates a temporary script containing all the specified commands,
        then executes it with administrative privileges using pkexec. This approach
        ensures the user is only prompted for a password once, regardless of how many
        privileged operations need to be performed.

        Args:
            commands: List of lists, where each inner list is a command to be executed
                    Example: [["timedatectl", "set-timezone", "America/Sao_Paulo"],
                            ["timedatectl", "set-time", "2023-01-01 12:00:00"]]

        Returns:
            bool: True if all commands were executed successfully

        Raises:
            RuntimeError: If authentication fails or command execution fails
        """
        script_path = None

        try:
            # Create temporary script
            script_path = self.create_temp_script(commands)

            # Execute the script with pkexec (single authentication)
            result = subprocess.run(
                ["pkexec", script_path],
                capture_output=True,
                text=True,
                check=True
            )

            # Check if there were any errors in the output
            if "Error executing" in result.stdout:
                self.status_label.set_markup(
                    "<i>" + _("Status:") + "</i> " +
                    _("Some commands failed. Check system logs for details.")
                )

            return True

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else str(e)

            if "polkit" in error_msg.lower() or "authentication" in error_msg.lower():
                raise RuntimeError(_("Permission denied. Please provide administrator password when prompted."))
            else:
                raise RuntimeError(f"{_('Command failed')}: {error_msg}")

        finally:
            # Remove the temporary script regardless of the outcome
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except OSError as e:
                    print(f"Warning: Failed to remove temporary script: {e}")


# Adaptado para GTK3 - modelo de aplicativo mais simples
if __name__ == "__main__":
    win = DateTimeApp()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
