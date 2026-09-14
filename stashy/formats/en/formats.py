# European-style date formats for the whole app (templates, forms, admin).
# Django picks this up via FORMAT_MODULE_PATH in settings.py.

DATE_FORMAT = "d/m/Y"
SHORT_DATE_FORMAT = "d/m/Y"
DATETIME_FORMAT = "d/m/Y H:i"
SHORT_DATETIME_FORMAT = "d/m/Y H:i"
TIME_FORMAT = "H:i"
MONTH_DAY_FORMAT = "j F"
YEAR_MONTH_FORMAT = "F Y"

FIRST_DAY_OF_WEEK = 1  # Monday

DATE_INPUT_FORMATS = [
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
]
DATETIME_INPUT_FORMATS = [
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
]

DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = "\u00a0"
NUMBER_GROUPING = 3
