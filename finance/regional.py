"""Regional preferences (decision D10): how dates, numbers and times are shown.

They are read from this computer once, when a login's Profile is first created. After that
they belong to the person - changed on the Settings page, never silently re-read - so nothing
changes meaning just because a Windows setting did. RegionalPreferencesMiddleware activates
them for each request; code running outside a request (tests, scripts) gets DEFAULTS, which
match stashy/formats/.
"""

import functools
import re
import zoneinfo
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from importlib import resources
from pathlib import Path

from django.conf import settings
from django.utils import timezone

NBSP = " "

DATE_FORMAT_CHOICES = [("dmy", "DD/MM/YYYY"), ("mdy", "MM/DD/YYYY"), ("ymd", "YYYY-MM-DD")]
NUMBER_FORMAT_CHOICES = [
    ("space_dot", "10 000.50"),
    ("comma_dot", "10,000.50"),
    ("dot_comma", "10.000,50"),
    ("space_comma", "10 000,50"),
]
# The date picker's (JavaScript's) numbering: 0 = Sunday, 1 = Monday, 6 = Saturday.
FIRST_DAY_CHOICES = [(1, "Monday"), (0, "Sunday"), (6, "Saturday")]

# For each date format: how it's shown, which spellings are accepted when typed, and the date picker's notation.
_DATE_STYLES = {
    "dmy": ("%d/%m/%Y", ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"), "d/m/Y"),
    "mdy": ("%m/%d/%Y", ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"), "m/d/Y"),
    "ymd": ("%Y-%m-%d", ("%Y-%m-%d", "%Y/%m/%d"), "Y-m-d"),
}
# (thousands separator, decimal mark). The space is a non-breaking one, so an amount never wraps mid-number.
_NUMBER_STYLES = {
    "space_dot": (NBSP, "."),
    "comma_dot": (",", "."),
    "dot_comma": (".", ","),
    "space_comma": (NBSP, ","),
}


@dataclass(frozen=True)
class Preferences:
    date_format: str = "dmy"
    number_format: str = "space_dot"
    first_day_of_week: int = 1
    time_zone: str = settings.TIME_ZONE
    main_currency: str = "EUR"

    @property
    def date_pattern(self):
        return _DATE_STYLES.get(self.date_format, _DATE_STYLES["dmy"])[0]

    @property
    def date_input_formats(self):
        return list(_DATE_STYLES.get(self.date_format, _DATE_STYLES["dmy"])[1])

    @property
    def picker_date_format(self):
        return _DATE_STYLES.get(self.date_format, _DATE_STYLES["dmy"])[2]

    @property
    def date_placeholder(self):
        return dict(DATE_FORMAT_CHOICES).get(self.date_format, "DD/MM/YYYY")

    @property
    def thousands_separator(self):
        return _NUMBER_STYLES.get(self.number_format, _NUMBER_STYLES["space_dot"])[0]

    @property
    def decimal_separator(self):
        return _NUMBER_STYLES.get(self.number_format, _NUMBER_STYLES["space_dot"])[1]


DEFAULTS = Preferences()
_current = ContextVar("stashy_preferences", default=DEFAULTS)


def current():
    return _current.get()


@contextmanager
def activate(preferences):
    """Use these preferences, time zone included, for everything inside the block."""
    token = _current.set(preferences)
    try:
        with timezone.override(_zone(preferences.time_zone)):  # None (unknown zone) means settings.TIME_ZONE
            yield preferences
    finally:
        _current.reset(token)


def _zone(name):
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError):
        return None


# --- formatting --------------------------------------------------------------


def format_number(value, decimals=2, preferences=None):
    """10000.5 -> '10 000.50' (or 10,000.50 / 10.000,50 / 10 000,50, per the preferences)."""
    thousands, mark = _NUMBER_STYLES.get((preferences or current()).number_format, _NUMBER_STYLES["space_dot"])
    amount = Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    whole, _, fraction = f"{abs(amount):.{decimals}f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    number = ("-" if amount < 0 else "") + thousands.join(groups)
    return f"{number}{mark}{fraction}" if fraction else number


def format_date(value, preferences=None):
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        value = timezone.localtime(value) if timezone.is_aware(value) else value
    return value.strftime((preferences or current()).date_pattern)


def format_datetime(value, seconds=False, preferences=None):
    if value is None or value == "":
        return ""
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    time_pattern = "%H:%M:%S" if seconds else "%H:%M"
    return value.strftime(f"{(preferences or current()).date_pattern} {time_pattern}")


def format_audit_timestamp(value):
    """The audit log's own timestamp column (Phase 14 / A3): always ISO-style, with milliseconds
    and the offset, in the viewer's own time zone - unlike every other date/time in the app,
    it never follows the date_format preference, since it reads like a database log."""
    if value is None or value == "":
        return ""
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    offset = local.utcoffset() if timezone.is_aware(local) else None
    minutes = int((offset or timedelta()).total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, minutes = divmod(abs(minutes), 60)
    return f"{local:%Y-%m-%d %H:%M:%S}.{local.microsecond // 1000:03d} {sign}{hours:02d}:{minutes:02d}"


# --- reading this computer's settings -----------------------------------------


def detect_from_computer():
    """This computer's regional settings as Preferences; anything that can't be read keeps its default."""
    found, country = {}, ""
    try:
        found, country = _read_windows_settings()
    except (ImportError, OSError):
        pass  # not Windows, or the settings aren't in the registry
    zone = _local_time_zone(country)
    if zone:
        found["time_zone"] = zone
    return replace(DEFAULTS, **found)


def _read_windows_settings():
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as key:

        def read(name):
            try:
                return str(winreg.QueryValueEx(key, name)[0])
            except OSError:
                return ""

        detected = {
            "date_format": date_format_from_pattern(read("sShortDate")),
            "number_format": number_format_from_separators(read("sThousand"), read("sDecimal")),
            "first_day_of_week": first_day_from_windows(read("iFirstDayOfWeek")),
        }
        locale_parts = read("LocaleName").split("-")  # e.g. bg-BG, sr-Latn-RS
        country = locale_parts[-1].upper() if len(locale_parts) > 1 else ""
    return {name: value for name, value in detected.items() if value is not None}, country


def date_format_from_pattern(pattern):
    """Windows' short date pattern ('d.M.yyyy', 'M/d/yyyy', 'yyyy-MM-dd') -> 'dmy' / 'mdy' / 'ymd'."""
    letters = re.sub(r"'[^']*'", "", pattern or "").lower()  # quoted text such as 'г.' isn't part of the date
    positions = {part: letters.find(part) for part in "dmy"}
    if min(positions.values()) < 0:
        return None
    order = "".join(sorted(positions, key=positions.get))
    return order if order in _DATE_STYLES else None


def number_format_from_separators(thousands, decimal_mark):
    group = "space" if not thousands or thousands.isspace() else {",": "comma", ".": "dot"}.get(thousands)
    mark = {".": "dot", ",": "comma"}.get(decimal_mark)
    key = f"{group}_{mark}"
    return key if key in _NUMBER_STYLES else None


def first_day_from_windows(value):
    """Windows counts 0 = Monday ... 6 = Sunday; the date picker counts 0 = Sunday, 1 = Monday."""
    try:
        day = (int(value) + 1) % 7
    except ValueError:
        return None
    return day if day in dict(FIRST_DAY_CHOICES) else None


def _local_time_zone(country):
    try:
        import tzlocal

        name = tzlocal.get_localzone_name()
    except Exception:  # tzlocal raises its own error types for unreadable configurations
        return None
    return zone_for_country(name, country) if name else None


def zone_for_country(zone_name, country):
    """Prefer a zone in the person's own country when it keeps the same clock.

    Windows only stores zones like "FLE Standard Time" (Helsinki, Kyiv, Riga, Sofia, Tallinn,
    Vilnius), which tzlocal names after its first city, Europe/Kiev - the right clock but the
    wrong name for someone in Sofia. The computer's region (bg-BG) settles which city is meant.
    """
    reference = _zone(zone_name)
    if reference is None or not country:
        return zone_name
    same_clock = [name for name in zones_in_country(country) if _same_clock(name, reference)]
    if not same_clock or zone_name in same_clock:
        return zone_name
    return same_clock[0]


def _same_clock(name, reference):
    zone = _zone(name)
    year = date.today().year
    return zone is not None and all(
        datetime(year, month, 15, 12, tzinfo=zone).utcoffset()
        == datetime(year, month, 15, 12, tzinfo=reference).utcoffset()
        for month in (1, 7)  # winter and summer, so daylight saving time has to match too
    )


def zones_in_country(country):
    return [name for code, name in _zone_table() if code == country.upper()]


def time_zone_choices():
    names = sorted({name for _, name in _zone_table()} | {"UTC"})
    return names if len(names) > 1 else sorted(zoneinfo.available_timezones())


@functools.cache
def _zone_table():
    """(country code, zone name) pairs from the time zone database's zone.tab."""
    text = ""
    try:
        text = (resources.files("tzdata") / "zoneinfo" / "zone.tab").read_text(encoding="utf-8")
    except (ModuleNotFoundError, OSError):
        for folder in zoneinfo.TZPATH:
            table = Path(folder) / "zone.tab"
            if table.is_file():
                text = table.read_text(encoding="utf-8")
                break
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if not line.startswith("#") and len(parts) >= 3:
            rows.append((parts[0], parts[2]))
    return tuple(rows)
