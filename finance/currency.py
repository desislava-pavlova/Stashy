"""The currency list and how an amount is written in a currency
(decision D2): plain data and formatting, nothing else.

It must not read the database or the network, and it imports nothing
from `models`: `models` imports this module for its currency choices.
Exchange rates, which do read and write the database, are in
`services/rates.py`.
"""

from decimal import Decimal

from . import regional


DEFAULT_CURRENCY = "EUR"

CURRENCIES = {
    "EUR": "Euro",
    "BGN": "Bulgarian lev",
    "USD": "US dollar",
    "GBP": "British pound",
    "AUD": "Australian dollar",
    "BRL": "Brazilian real",
    "CAD": "Canadian dollar",
    "CHF": "Swiss franc",
    "CNY": "Chinese yuan",
    "CZK": "Czech koruna",
    "DKK": "Danish krone",
    "HKD": "Hong Kong dollar",
    "HUF": "Hungarian forint",
    "IDR": "Indonesian rupiah",
    "ILS": "Israeli shekel",
    "INR": "Indian rupee",
    "ISK": "Icelandic króna",
    "JPY": "Japanese yen",
    "KRW": "South Korean won",
    "MXN": "Mexican peso",
    "MYR": "Malaysian ringgit",
    "NOK": "Norwegian krone",
    "NZD": "New Zealand dollar",
    "PHP": "Philippine peso",
    "PLN": "Polish złoty",
    "RON": "Romanian leu",
    "SEK": "Swedish krona",
    "SGD": "Singapore dollar",
    "THB": "Thai baht",
    "TRY": "Turkish lira",
    "ZAR": "South African rand",
}
CURRENCY_CHOICES = [(code, f"{code} – {name}") for code, name in CURRENCIES.items()]

# Symbol shown instead of the bare ISO code in an amount (the `moneyc`
# template filter). A currency missing here just shows its code, e.g.
# "9.66 XYZ" - safe, if less pretty.
CURRENCY_SYMBOLS = {
    "USD": "$",
    "CAD": "$",
    "AUD": "$",
    "NZD": "$",
    "HKD": "$",
    "SGD": "$",
    "MXN": "$",
    "GBP": "£",
    "EUR": "€",
    "JPY": "¥",
    "CNY": "¥",
    "INR": "₹",
    "ILS": "₪",
    "TRY": "₺",
    "KRW": "₩",
    "PHP": "₱",
    "THB": "฿",
    "BRL": "R$",
    "ZAR": "R",
    "CHF": "CHF",
    "BGN": "лв.",
    "PLN": "zł",
    "SEK": "kr",
    "NOK": "kr",
    "DKK": "kr",
    "ISK": "kr",
    "CZK": "Kč",
    "HUF": "Ft",
    "RON": "lei",
    "MYR": "RM",
    "IDR": "Rp",
}
# Most local European currencies conventionally trail the number with a
# space ("9.66 €"); everything else (mostly Latin-American/Anglo
# currencies) is prefixed directly ("$9.66"). A simplification, not a
# full locale database - flag if a particular currency reads oddly.
SUFFIX_CURRENCIES = {"EUR", "BGN", "PLN", "SEK", "NOK", "DKK", "ISK", "CZK", "HUF", "RON"}


def format_money(amount, currency_code, decimals=2, preferences=None):
    """Format a signed amount with its currency's symbol: "$9.66",
    "-$9.66", "9.66 €"."""
    amount = Decimal(str(amount))
    negative = amount < 0
    number = regional.format_number(abs(amount), decimals=decimals, preferences=preferences)
    symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code)
    body = f"{number} {symbol}" if currency_code in SUFFIX_CURRENCIES else f"{symbol}{number}"
    return f"-{body}" if negative else body
