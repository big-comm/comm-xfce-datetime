# Standard library imports
import datetime
import gettext
import json
import locale
import os
import shutil
import subprocess
import sys
import time
import unicodedata
import zoneinfo
from zoneinfo import ZoneInfo

# Third-party imports
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

# Application constants
APP_NAME = "comm-xfce-datetime"
DEFAULT_WINDOW_SIZE = (450, 400)
UI_MARGIN_SMALL = 5
UI_MARGIN_STANDARD = 10
ROW_TIME_REFRESH_SECONDS = 30
SYNC_POLL_ATTEMPTS = 15
# pkexec exit codes when the authentication is dismissed or refused
PKEXEC_AUTH_ERRORS = (126, 127)

# Root helper that applies the changes, run through pkexec
APPLY_HELPER = "/usr/lib/comm-xfce-datetime/apply-settings"

# Data sources
ZONE_TAB = "/usr/share/zoneinfo/zone.tab"
ISO3166_TAB = "/usr/share/zoneinfo/iso3166.tab"
ISO_CODES_JSON = "/usr/share/iso-codes/json/iso_3166-1.json"
TIMESYNCD_CONF = "/etc/systemd/timesyncd.conf"

# D-Bus services (systemd-timedated and systemd-timesyncd)
TIMEDATE_BUS_NAME = "org.freedesktop.timedate1"
TIMEDATE_OBJECT_PATH = "/org/freedesktop/timedate1"
TIMESYNC_BUS_NAME = "org.freedesktop.timesync1"
TIMESYNC_OBJECT_PATH = "/org/freedesktop/timesync1"
TIMESYNC_INTERFACE = "org.freedesktop.timesync1.Manager"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
DBUS_ERROR_PREFIX = "GDBus.Error:"
# Index of the destination timestamp inside timesyncd's NTPMessage property
NTP_MESSAGE_DEST_TIMESTAMP = 11

# Use the user's locale for weekday names and date formats
try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    pass

# Translation support
lang_translations = gettext.translation(
    APP_NAME, localedir="/usr/share/locale", fallback=True
)
_ = lang_translations.gettext

# Country names are translated by the iso-codes package
country_translations = gettext.translation(
    "iso_3166-1", localedir="/usr/share/locale", fallback=True
)

escape = GLib.markup_escape_text


def set_margins(widget, margin):
    """Apply the same margin to all sides of a widget."""
    widget.set_margin_start(margin)
    widget.set_margin_end(margin)
    widget.set_margin_top(margin)
    widget.set_margin_bottom(margin)


def fold_text(text):
    """Lowercase text and strip accents so searches ignore them."""
    normalized = unicodedata.normalize("NFKD", text.replace("_", " "))
    return "".join(c for c in normalized if not unicodedata.combining(c)).casefold()


def format_utc_offset(offset):
    """Format a timedelta as UTC+H or UTC+H:MM."""
    total_minutes = int(offset.total_seconds()) // 60
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    if minutes == 0:
        return f"UTC{sign}{hours}"
    return f"UTC{sign}{hours}:{minutes:02d}"


def utc_offset_of(timezone):
    """Return the current UTC offset label of a timezone."""
    try:
        return format_utc_offset(datetime.datetime.now(ZoneInfo(timezone)).utcoffset())
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return ""


def load_timezones():
    """Return the timezones known by the system, falling back to Python's tzdata."""
    try:
        result = subprocess.run(
            ["timedatectl", "list-timezones", "--no-pager"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            tz for tz in zoneinfo.available_timezones()
            if not tz.startswith(("posix/", "right/"))
        )


def load_zone_countries():
    """Map each timezone to the translated name of its country."""
    countries = {}
    try:
        with open(ISO3166_TAB, encoding="utf-8") as f:
            for line in f:
                if not line.startswith("#") and "\t" in line:
                    code, name = line.rstrip("\n").split("\t", 1)
                    countries[code] = name
    except OSError:
        pass

    try:
        with open(ISO_CODES_JSON, encoding="utf-8") as f:
            for entry in json.load(f)["3166-1"]:
                name = entry.get("common_name", entry["name"])
                countries[entry["alpha_2"]] = country_translations.gettext(name)
    except (OSError, ValueError, KeyError):
        pass

    zone_countries = {}
    try:
        with open(ZONE_TAB, encoding="utf-8") as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if not line.startswith("#") and len(fields) >= 3:
                    zone_countries[fields[2]] = countries.get(fields[0], fields[0])
    except OSError:
        pass
    return zone_countries


def split_dbus_error(error):
    """Split a GDBus error into (remote error name, human readable message)."""
    message = error.message
    if message.startswith(DBUS_ERROR_PREFIX):
        name, _sep, text = message[len(DBUS_ERROR_PREFIX):].partition(": ")
        return name, text
    return "", message


class TimedateService:
    """Reads the clock settings from systemd and applies changes through a root helper.

    Polkit keeps authorizations per action, so calling timedated directly would ask
    for the password once for each kind of change. The helper needs a single one.
    """

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def get_properties(self):
        """Return timedated's properties (Timezone, LocalRTC, NTP, CanNTP...)."""
        reply = self.bus.call_sync(
            TIMEDATE_BUS_NAME, TIMEDATE_OBJECT_PATH, PROPERTIES_INTERFACE, "GetAll",
            GLib.Variant("(s)", (TIMEDATE_BUS_NAME,)), GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE, -1, None
        )
        return reply.unpack()[0]

    def apply(self, args, callback):
        """Run the helper with pkexec; callback receives None or an error message."""
        try:
            process = Gio.Subprocess.new(
                ["pkexec", APPLY_HELPER, *args],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE
            )
        except GLib.Error as e:
            callback(f"{_('Command failed')}: {e.message}")
            return

        def on_finished(process, result):
            try:
                _ok, _stdout, stderr = process.communicate_utf8_finish(result)
            except GLib.Error as e:
                callback(f"{_('Command failed')}: {e.message}")
                return
            if process.get_if_exited() and process.get_exit_status() == 0:
                callback(None)
            elif process.get_if_exited() and process.get_exit_status() in PKEXEC_AUTH_ERRORS:
                callback(_("Permission denied. Please provide administrator password when prompted."))
            else:
                callback(f"{_('Command failed')}: {(stderr or '').strip()}")

        process.communicate_utf8_async(None, None, on_finished)

    def get_timesync_properties(self, callback):
        """Read timesyncd's properties without starting it; callback gets a dict or None."""
        def on_finished(bus, result):
            try:
                callback(bus.call_finish(result).unpack()[0])
            except GLib.Error:
                callback(None)

        self.bus.call(
            TIMESYNC_BUS_NAME, TIMESYNC_OBJECT_PATH, PROPERTIES_INTERFACE, "GetAll",
            GLib.Variant("(s)", (TIMESYNC_INTERFACE,)), GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NO_AUTO_START, -1, None, on_finished
        )


class TimezoneRow(Gtk.ListBoxRow):
    """List row showing a timezone, its country, UTC offset and current time."""

    def __init__(self, timezone, country):
        super().__init__()
        self.timezone = timezone
        self.city = timezone.rsplit("/", 1)[-1].replace("_", " ")
        self.country = country
        self.search_key = fold_text(f"{self.city} {country} {timezone}")
        self.zone = ZoneInfo(timezone)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=UI_MARGIN_SMALL)
        set_margins(row, UI_MARGIN_SMALL)

        # Left side: city, country, region path and UTC offset
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)

        city_label = Gtk.Label()
        city_label.set_markup(f"<b>{escape(self.city)}</b> {escape(country)}")
        city_label.set_xalign(0)
        info_box.pack_start(city_label, False, False, 0)

        self.region_label = Gtk.Label()
        self.region_label.set_xalign(0)
        self.region_label.get_style_context().add_class("dim-label")
        info_box.pack_start(self.region_label, False, False, 0)

        row.pack_start(info_box, True, True, 0)

        # Right side: current time in that timezone
        self.time_label = Gtk.Label()
        self.time_label.get_style_context().add_class("dim-label")
        row.pack_start(self.time_label, False, False, 0)

        self.add(row)
        self.refresh(datetime.datetime.now(datetime.timezone.utc))

    def refresh(self, now_utc):
        """Update the UTC offset and local time shown in the row."""
        local = now_utc.astimezone(self.zone)
        self.utc_offset = format_utc_offset(local.utcoffset())
        self.region_label.set_markup(
            f"<small>{escape(self.timezone)} • {self.utc_offset}</small>"
        )
        self.time_label.set_markup(f"<small>{escape(local.strftime('%a %H:%M'))}</small>")


class DateTimeApp(Gtk.Window):
    def __init__(self, timedate):
        """Initialize the Date and Time Settings application."""
        super().__init__(title=_("Date and Time Settings"))
        self.set_default_size(*DEFAULT_WINDOW_SIZE)
        self.set_icon_name("time")

        # Initialize application state
        self.timedate = timedate
        self.state = timedate.get_properties()
        self.selected_timezone = self.state["Timezone"]
        self.search_text = ""
        self.time_dirty = False  # User edited the date/time fields
        self.updating_widgets = False  # Ignore signals caused by our own updates
        self.busy = False
        self.clock_ticks = 0

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

        # Add status area and button bar at the bottom
        self._create_status_area(main_box)
        self._create_button_bar(main_box)

        self.populate_timezone_list()
        self._load_state_into_widgets()
        self._update_clock()
        GLib.timeout_add_seconds(1, self._on_clock_tick)

    def _create_status_area(self, main_box):
        """Create and add the status area to the main box."""
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_SMALL)
        set_margins(status_box, UI_MARGIN_STANDARD)
        status_box.set_margin_top(UI_MARGIN_SMALL)

        # Current timezone label
        self.current_tz_label = Gtk.Label()
        self.current_tz_label.set_xalign(0)
        status_box.pack_start(self.current_tz_label, False, False, 0)

        # Status Label
        self.status_label = Gtk.Label()
        self.status_label.set_markup(f"<i>{escape(_('Status: Ready'))}</i>")
        self.status_label.set_xalign(0)
        self.status_label.set_line_wrap(True)
        status_box.pack_start(self.status_label, False, False, 0)

        main_box.pack_start(status_box, False, False, 0)

    def _create_button_bar(self, main_box):
        """Create and add the button bar to the main box."""
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=UI_MARGIN_STANDARD)
        button_box.set_halign(Gtk.Align.END)
        set_margins(button_box, UI_MARGIN_STANDARD)
        button_box.set_margin_top(UI_MARGIN_SMALL)

        close_button = Gtk.Button(label=_("Close"))
        close_button.connect("clicked", lambda _button: self.destroy())

        self.sync_button = Gtk.Button(label=_("Synchronize"))
        self.sync_button.connect("clicked", self.on_sync_clicked)

        self.apply_button = Gtk.Button(label=_("Apply"))
        self.apply_button.get_style_context().add_class("suggested-action")
        self.apply_button.connect("clicked", self.on_apply_clicked)

        button_box.pack_start(close_button, False, False, 0)
        button_box.pack_start(self.sync_button, False, False, 0)
        button_box.pack_start(self.apply_button, False, False, 0)

        main_box.pack_start(button_box, False, False, 0)

    def create_date_time_tab(self):
        """Create the Date & Time tab"""
        date_time_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_STANDARD)
        set_margins(date_time_box, UI_MARGIN_STANDARD)

        # Date selection
        date_frame = Gtk.Frame(label=_("Date"))
        date_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_SMALL)
        set_margins(date_box, UI_MARGIN_STANDARD)

        self.calendar = Gtk.Calendar()
        self.calendar.connect("day-selected", self.on_time_edited)
        self.calendar.connect("month-changed", self.on_time_edited)
        date_box.pack_start(self.calendar, True, True, 0)
        date_frame.add(date_box)
        date_time_box.pack_start(date_frame, True, True, 0)

        # Time adjustment
        time_frame = Gtk.Frame(label=_("Time"))
        time_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=UI_MARGIN_STANDARD)
        time_box.set_halign(Gtk.Align.CENTER)
        set_margins(time_box, UI_MARGIN_STANDARD)

        self.hour_spinner = Gtk.SpinButton.new_with_range(0, 23, 1)
        self.minute_spinner = Gtk.SpinButton.new_with_range(0, 59, 1)
        self.second_spinner = Gtk.SpinButton.new_with_range(0, 59, 1)

        for label, spinner in (
            (_("Hour:"), self.hour_spinner),
            (_("Minute:"), self.minute_spinner),
            (_("Second:"), self.second_spinner),
        ):
            # "changed" also catches values typed but not yet committed
            spinner.connect("changed", self.on_time_edited)
            time_box.pack_start(Gtk.Label(label=label), False, False, 0)
            time_box.pack_start(spinner, False, False, 0)

        time_frame.add(time_box)
        date_time_box.pack_start(time_frame, False, False, 0)

        self.ntp_hint_label = Gtk.Label()
        self.ntp_hint_label.set_markup(
            f"<i>{escape(_('The date and time are set automatically while network time synchronization is enabled.'))}</i>"
        )
        self.ntp_hint_label.set_line_wrap(True)
        self.ntp_hint_label.set_xalign(0)
        self.ntp_hint_label.set_no_show_all(True)
        date_time_box.pack_start(self.ntp_hint_label, False, False, 0)

        self.notebook.append_page(date_time_box, Gtk.Label(label=_("Date & Time")))

    def create_timezone_tab(self):
        """Create the Timezone tab"""
        tz_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_STANDARD)
        set_margins(tz_box, UI_MARGIN_STANDARD)

        # Search entry
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=UI_MARGIN_SMALL)
        search_label = Gtk.Label(label=_("Search:"))
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.set_hexpand(True)
        search_box.pack_start(search_label, False, False, 0)
        search_box.pack_start(self.search_entry, True, True, 0)
        tz_box.pack_start(search_box, False, False, 0)

        # Create scrollable list for timezones
        self.tz_scrolled_window = Gtk.ScrolledWindow()
        self.tz_scrolled_window.set_min_content_height(200)
        self.tz_scrolled_window.set_vexpand(True)
        self.tz_scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.timezone_list = Gtk.ListBox()
        self.timezone_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.timezone_list.set_filter_func(self.filter_timezone_row)
        self.timezone_list.connect("row-selected", self.on_timezone_selected)
        self.timezone_list.connect("map", self.on_timezone_list_mapped)
        self.tz_scrolled_window.add(self.timezone_list)
        tz_box.pack_start(self.tz_scrolled_window, True, True, 0)

        # Current selection
        self.selection_label = Gtk.Label()
        self.selection_label.set_xalign(0)
        tz_box.pack_start(self.selection_label, False, False, 0)

        self.notebook.append_page(tz_box, Gtk.Label(label=_("Timezone")))

    def create_system_tab(self):
        """Create the System tab"""
        system_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_STANDARD)
        set_margins(system_box, UI_MARGIN_STANDARD)

        # Network time sync frame
        sync_frame = Gtk.Frame(label=_("Time Synchronization"))
        sync_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_STANDARD)
        set_margins(sync_box, UI_MARGIN_STANDARD)

        self.ntp_checkbox = Gtk.CheckButton(
            label=_("Enable Network Time Synchronization")
        )
        self.ntp_checkbox.connect("toggled", self.on_ntp_toggled)
        sync_box.pack_start(self.ntp_checkbox, False, False, 0)

        # NTP servers reported by systemd-timesyncd, only shown when it is running
        self.ntp_servers_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_SMALL)
        self.ntp_servers_box.set_no_show_all(True)

        server_label = Gtk.Label(label=_("NTP Servers:"))
        server_label.set_xalign(0)
        server_label.set_margin_top(UI_MARGIN_SMALL)
        self.ntp_servers_box.pack_start(server_label, False, False, 0)

        self.server_entry = Gtk.Entry()
        self.server_entry.set_editable(False)
        self.ntp_servers_box.pack_start(self.server_entry, False, False, 0)

        note_label = Gtk.Label()
        note_label.set_markup(
            f"<i>{escape(_('Note: NTP servers are configured in {}').format(TIMESYNCD_CONF))}</i>"
        )
        note_label.set_xalign(0)
        note_label.set_line_wrap(True)
        self.ntp_servers_box.pack_start(note_label, False, False, 0)

        sync_box.pack_start(self.ntp_servers_box, False, False, 0)
        sync_frame.add(sync_box)
        system_box.pack_start(sync_frame, False, False, 0)

        # Hardware clock frame
        hw_frame = Gtk.Frame(label=_("Hardware Clock"))
        hw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI_MARGIN_STANDARD)
        set_margins(hw_box, UI_MARGIN_STANDARD)

        self.hw_utc_radio = Gtk.RadioButton(label=_("Hardware clock uses UTC"))
        self.hw_local_radio = Gtk.RadioButton.new_with_label_from_widget(
            self.hw_utc_radio, _("Hardware clock uses local time")
        )
        hw_box.pack_start(self.hw_utc_radio, False, False, 0)
        hw_box.pack_start(self.hw_local_radio, False, False, 0)

        hw_frame.add(hw_box)
        system_box.pack_start(hw_frame, False, False, 0)

        self.notebook.append_page(system_box, Gtk.Label(label=_("System")))

    def populate_timezone_list(self):
        """Populate the timezone list with available timezones."""
        zone_countries = load_zone_countries()
        self.timezone_rows = {}

        for timezone in load_timezones():
            try:
                row = TimezoneRow(timezone, zone_countries.get(timezone, ""))
            except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                continue
            self.timezone_rows[timezone] = row
            self.timezone_list.add(row)

    def _load_state_into_widgets(self):
        """Show the current system configuration in the widgets."""
        self.updating_widgets = True
        self.ntp_checkbox.set_active(self.state["NTP"])
        self.hw_local_radio.set_active(self.state["LocalRTC"])
        self.hw_utc_radio.set_active(not self.state["LocalRTC"])

        self.selected_timezone = self.state["Timezone"]
        row = self.timezone_rows.get(self.selected_timezone)
        if row:
            self.timezone_list.select_row(row)
        else:
            self.timezone_list.unselect_all()
        self.updating_widgets = False

        self.time_dirty = False
        self._update_selection_label()
        self._update_sensitivity()
        self._load_ntp_servers()

    def _reload_state(self):
        """Read the system configuration again and refresh the widgets."""
        try:
            self.state = self.timedate.get_properties()
        except GLib.Error as e:
            self.show_message_dialog(Gtk.MessageType.ERROR, split_dbus_error(e)[1])
            return
        self._load_state_into_widgets()
        self._update_clock()

    def _load_ntp_servers(self):
        """Show the NTP servers in use, as reported by systemd-timesyncd."""
        def on_properties(properties):
            if not properties:
                self.ntp_servers_box.hide()
                return
            servers = (
                properties.get("SystemNTPServers")
                or properties.get("LinkNTPServers")
                or properties.get("FallbackNTPServers")
                or []
            )
            current = properties.get("ServerName")
            if current:
                servers = [current] + [s for s in servers if s != current]
            self.server_entry.set_text(", ".join(servers))
            self.ntp_servers_box.set_no_show_all(False)
            self.ntp_servers_box.show_all()

        self.timedate.get_timesync_properties(on_properties)

    def _update_sensitivity(self):
        """Enable or disable widgets according to NTP and busy state."""
        manual_time = not self.ntp_checkbox.get_active()
        for widget in (self.calendar, self.hour_spinner, self.minute_spinner, self.second_spinner):
            widget.set_sensitive(manual_time and not self.busy)
        self.ntp_hint_label.set_visible(not manual_time)

        self.ntp_checkbox.set_sensitive(self.state["CanNTP"] and not self.busy)
        self.apply_button.set_sensitive(not self.busy)

        self.sync_button.set_sensitive(self.state["NTP"] and not self.busy)
        self.sync_button.set_tooltip_text(
            None if self.state["NTP"] else _("Network time synchronization is disabled.")
        )

    def _set_busy(self, busy):
        self.busy = busy
        self._update_sensitivity()

    def set_status(self, message):
        """Show a message in the status label."""
        self.status_label.set_markup(f"<i>{escape(_('Status:'))}</i> {escape(message)}")

    def _display_zone(self):
        """Timezone used to show the date/time fields: the one about to be applied."""
        try:
            return ZoneInfo(self.selected_timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError):
            return None

    def _on_clock_tick(self):
        self.clock_ticks += 1
        self._update_clock(refresh_rows=self.clock_ticks % ROW_TIME_REFRESH_SECONDS == 0)
        return GLib.SOURCE_CONTINUE

    def _update_clock(self, refresh_rows=False):
        """Refresh every clock shown in the window."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        timezone = self.state["Timezone"]

        try:
            local = now_utc.astimezone(ZoneInfo(timezone))
            self.current_tz_label.set_markup(
                f"<b>{escape(_('Current:'))}</b> {escape(timezone)} {format_utc_offset(local.utcoffset())} "
                f"({escape(_('Local time:'))} {local.strftime('%H:%M:%S')})"
            )
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            self.current_tz_label.set_markup(
                f"<b>{escape(_('Current:'))}</b> {escape(timezone or _('Unknown'))}"
            )

        # Keep the date/time fields ticking until the user edits them
        if not self.time_dirty and not self._time_fields_have_focus():
            zone = self._display_zone()
            self._show_time(now_utc.astimezone(zone) if zone else now_utc.astimezone())

        if refresh_rows:
            for row in self.timezone_rows.values():
                row.refresh(now_utc)

    def _time_fields_have_focus(self):
        return any(
            spinner.has_focus()
            for spinner in (self.hour_spinner, self.minute_spinner, self.second_spinner)
        )

    def _show_time(self, moment):
        """Put a datetime in the calendar and spinners without marking them as edited."""
        self.updating_widgets = True
        year, month, day = self.calendar.get_date()
        if (year, month + 1) != (moment.year, moment.month):
            self.calendar.select_month(moment.month - 1, moment.year)
        if day != moment.day:
            self.calendar.select_day(moment.day)
        self.hour_spinner.set_value(moment.hour)
        self.minute_spinner.set_value(moment.minute)
        self.second_spinner.set_value(moment.second)
        self.updating_widgets = False

    def on_time_edited(self, *_args):
        """Remember that the user changed the date or time."""
        if not self.updating_widgets:
            self.time_dirty = True

    def on_search_changed(self, entry):
        """Filter the timezone list based on search text"""
        self.search_text = fold_text(entry.get_text().strip())
        self.timezone_list.invalidate_filter()

    def filter_timezone_row(self, row):
        return self.search_text in row.search_key

    def on_timezone_list_mapped(self, list_box):
        """Scroll to the selected timezone the first time the list is shown."""
        GLib.idle_add(self._scroll_to_selected_timezone)

    def _scroll_to_selected_timezone(self):
        row = self.timezone_list.get_selected_row()
        if row:
            coordinates = row.translate_coordinates(self.timezone_list, 0, 0)
            if coordinates:
                adjustment = self.tz_scrolled_window.get_vadjustment()
                adjustment.set_value(coordinates[1] - adjustment.get_page_size() / 3)
        return GLib.SOURCE_REMOVE

    def on_timezone_selected(self, list_box, row):
        """Handle timezone selection from the list"""
        if self.updating_widgets:
            return
        self.selected_timezone = row.timezone if row else self.state["Timezone"]
        self._update_selection_label()
        if row:
            place = f"{row.city}, {row.country}" if row.country else row.city
            self.set_status(f"{_('Selected')} {place}")
        if not self.time_dirty:
            self._update_clock()

    def _update_selection_label(self):
        timezone = self.selected_timezone
        self.selection_label.set_markup(
            f"<b>{escape(_('Selected:'))}</b> "
            + (f"{escape(timezone)} ({utc_offset_of(timezone)})" if timezone else escape(_("None")))
        )

    def on_ntp_toggled(self, button):
        """Update the date/time fields; the change itself is made on Apply."""
        if self.updating_widgets:
            return
        if button.get_active():
            self.time_dirty = False
        self._update_sensitivity()

    def _collect_changes(self):
        """Compare the widgets with the system state and return what must change."""
        changes = {}

        ntp = self.ntp_checkbox.get_active()
        if ntp != self.state["NTP"]:
            changes["ntp"] = ntp

        local_rtc = self.hw_local_radio.get_active()
        if local_rtc != self.state["LocalRTC"]:
            changes["local_rtc"] = local_rtc

        timezone = self.selected_timezone
        if timezone and timezone != self.state["Timezone"]:
            changes["timezone"] = timezone

        if self.time_dirty and not ntp:
            # The fields hold the wall time of the timezone being applied
            year, month, day = self.calendar.get_date()
            entered = datetime.datetime(
                year, month + 1, day,
                self.hour_spinner.get_value_as_int(),
                self.minute_spinner.get_value_as_int(),
                self.second_spinner.get_value_as_int(),
                tzinfo=ZoneInfo(timezone)
            )
            changes["datetime"] = entered
            # Keep the clock running while the dialogs are open
            changes["time_offset"] = entered.timestamp() - time.time()

        return changes

    def on_apply_clicked(self, button):
        """Confirmation before applying settings."""
        # Commit values typed in the spinners; typing already marked them as edited
        time_dirty = self.time_dirty
        for spinner in (self.hour_spinner, self.minute_spinner, self.second_spinner):
            spinner.update()
        self.time_dirty = time_dirty

        try:
            changes = self._collect_changes()
        except (ValueError, zoneinfo.ZoneInfoNotFoundError) as e:
            self.show_message_dialog(Gtk.MessageType.ERROR, str(e))
            return

        if not changes:
            self.set_status(_("No changes to apply."))
            return

        lines = []
        if "timezone" in changes:
            timezone = changes["timezone"]
            lines.append(_("Timezone: {} ({})").format(timezone, utc_offset_of(timezone)))
        if "datetime" in changes:
            lines.append(_("Date: {}").format(changes["datetime"].strftime("%x")))
            lines.append(_("Time: {}").format(changes["datetime"].strftime("%H:%M:%S")))
        if "local_rtc" in changes:
            lines.append(_("Hardware clock: {}").format(
                _("Local time") if changes["local_rtc"] else _("UTC")
            ))
        if "ntp" in changes:
            lines.append(
                _("Network time synchronization enabled.") if changes["ntp"]
                else _("Network time synchronization disabled.")
            )

        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("The following settings will be applied:"),
            secondary_text="\n".join(lines) + "\n\n" + _("Do you want to continue?")
        )
        dialog.connect("response", self.on_confirm_response, changes)
        dialog.show_all()

    def on_confirm_response(self, dialog, response, changes):
        """Apply settings if user confirms in the dialog."""
        dialog.destroy()
        if response != Gtk.ResponseType.YES:
            return

        args = []
        if "ntp" in changes:
            args += ["--ntp", "true" if changes["ntp"] else "false"]
        if "local_rtc" in changes:
            args += ["--local-rtc", "true" if changes["local_rtc"] else "false"]
        if "timezone" in changes:
            args += ["--timezone", changes["timezone"]]
        if "time_offset" in changes:
            # The helper adds the offset to its own clock, after the password prompt
            args += ["--time-offset", f"{changes['time_offset']:.6f}"]

        self._set_busy(True)
        self.set_status(_("Applying settings..."))
        self.timedate.apply(args, lambda error: self._on_apply_finished(error, changes))

    def _on_apply_finished(self, error, changes):
        self._set_busy(False)
        self._reload_state()

        if error:
            self.set_status(_("Settings could not be applied."))
            self.show_message_dialog(Gtk.MessageType.ERROR, error)
            return

        self.set_status(_("Settings applied successfully!"))
        if "timezone" in changes:
            self.restart_xfce_panel()
            self.show_message_dialog(
                Gtk.MessageType.INFO,
                _("Settings have been applied successfully!\n\n"
                "The new timezone is now active for system services and new applications. "
                "Some running applications may need to be restarted to use the new timezone settings.")
            )

    def restart_xfce_panel(self):
        """Restart the XFCE panel so its clock picks up the new timezone."""
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        if "XFCE" not in desktop or not shutil.which("xfce4-panel"):
            return
        try:
            GLib.spawn_async(
                ["xfce4-panel", "--restart"],
                flags=GLib.SpawnFlags.SEARCH_PATH
                | GLib.SpawnFlags.STDOUT_TO_DEV_NULL
                | GLib.SpawnFlags.STDERR_TO_DEV_NULL
            )
        except GLib.Error as e:
            self.show_message_dialog(
                Gtk.MessageType.ERROR,
                _("Failed to restart XFCE panel: ") + e.message
            )

    def on_sync_clicked(self, button):
        """Restart the NTP service so it synchronizes the clock right away."""
        self._set_busy(True)
        self.set_status(_("Please wait, synchronizing..."))

        def on_properties(properties):
            last_sync = self._ntp_last_sync(properties)
            self.timedate.apply(
                ["--resync"], lambda error: self._on_ntp_restarted(error, last_sync)
            )

        self.timedate.get_timesync_properties(on_properties)

    @staticmethod
    def _ntp_last_sync(properties):
        """Return when timesyncd last received an NTP reply, or None if unknown."""
        try:
            return properties["NTPMessage"][NTP_MESSAGE_DEST_TIMESTAMP]
        except (TypeError, KeyError, IndexError):
            return None

    def _on_ntp_restarted(self, error, last_sync):
        if error:
            self._finish_sync(_("Synchronization failed."))
            self.show_message_dialog(Gtk.MessageType.ERROR, error)
            return
        self._wait_for_ntp_reply(last_sync or 0, SYNC_POLL_ATTEMPTS)

    def _wait_for_ntp_reply(self, last_sync, attempts):
        """Poll timesyncd until it reports a new NTP reply."""
        def on_properties(properties):
            # properties is None while timesyncd restarts, or when another NTP
            # service (chrony, ntpd) is in use, which cannot be polled
            if (self._ntp_last_sync(properties) or 0) > last_sync:
                self._finish_sync(_("Synchronization completed successfully!"))
            elif attempts <= 0:
                self._finish_sync(_("Synchronization requested. The clock will be adjusted shortly."))
            else:
                GLib.timeout_add_seconds(
                    1, lambda: self._wait_for_ntp_reply(last_sync, attempts - 1) or GLib.SOURCE_REMOVE
                )

        self.timedate.get_timesync_properties(on_properties)

    def _finish_sync(self, message):
        self._set_busy(False)
        self._update_clock()
        self.set_status(message)

    def show_message_dialog(self, message_type, message):
        """Display a dialog with a message."""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=message_type,
            buttons=Gtk.ButtonsType.OK,
            text=message
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.show_all()


def main():
    try:
        timedate = TimedateService()
        win = DateTimeApp(timedate)
    except GLib.Error as e:
        message = f"{_('Error getting timezone')}: {split_dbus_error(e)[1]}"
        print(message, file=sys.stderr)
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text=message
        )
        dialog.run()
        dialog.destroy()
        return 1
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
