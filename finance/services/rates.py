"""Exchange rates (decision D2): the table the reports convert through,
and fetching fresh rates.

Rates live in ExchangeRate rows: typed in by hand, or fetched when
"Check rates" is pressed from Frankfurter - a free service, no API key,
that republishes the European Central Bank's daily reference rates.
Nothing is fetched in the background, so without internet Stashy simply
keeps using the last rates it knows. The currency list and the way an
amount is written are in `currency.py`.
"""

import json
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlencode

from django.db import router, transaction
from django.db.models import F, Q

from audit.context import system_action

from ..limits import is_plausible_date, is_plausible_rate
from ..models import ExchangeRate
from ..money import CENT, ZERO


FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
# Frankfurter no longer has the lev (Bulgaria uses the euro since 2026);
# its rate can still be typed in by hand.
NOT_ON_FRANKFURTER = {"BGN"}


@dataclass(frozen=True)
class KnownRate:
    rate: Decimal  # 1 unit of the currency is worth this much of the target currency
    date: date
    source: str


class RateTable:
    """The latest known rate from every currency into one target
    currency, loaded with one query.

    A rate stored either way round counts (EUR->USD 1.16 also gives
    USD->EUR 1/1.16). The most recent date wins; on the same date, a
    rate typed in by hand beats a fetched one.
    """

    def __init__(self, target):
        self.target = target
        self._known = None

    def _load(self):
        known = {}
        rows = ExchangeRate.objects.filter(
            Q(base_currency=self.target) | Q(quote_currency=self.target)
        ).exclude(base_currency=F("quote_currency"))
        manual = ExchangeRate.Source.MANUAL
        # Later rows override earlier ones; a manual rate wins its own
        # day.
        for row in sorted(rows, key=lambda r: (r.date, r.source == manual, r.pk)):
            if row.quote_currency == self.target:
                known[row.base_currency] = KnownRate(row.rate, row.date, row.source)
            else:
                known[row.quote_currency] = KnownRate(1 / row.rate, row.date, row.source)
        return known

    def known(self, currency):
        if currency == self.target:
            return None
        if self._known is None:
            self._known = self._load()
        return self._known.get(currency)

    def convert(self, amount, currency):
        """`amount` of `currency` in the target currency, rounded to
        cents; None when no rate is known."""
        if currency == self.target:
            return amount
        known = self.known(currency)
        return (
            None if known is None else (amount * known.rate).quantize(CENT, rounding=ROUND_HALF_UP)
        )


def convert_balances(accounts, main_currency, rates=None):
    """Set .current_balance and .main_balance on each account.

    Returns (their total in the main currency, the accounts left out
    because no rate is known).
    """
    if rates is None:
        rates = RateTable(main_currency)
    total, missing = ZERO, []
    for account in accounts:
        account.current_balance = account.balance
        account.main_balance = rates.convert(account.current_balance, account.currency)
        if account.main_balance is None:
            missing.append(account)
        else:
            total += account.main_balance
    return total, missing


class RateFetchError(Exception):
    """Frankfurter couldn't be reached, or answered with something
    unusable."""


def fetch_latest_rates(main_currency, currencies):
    """Fetch the latest rates from `main_currency` into each of
    `currencies` and store them.

    Returns (stored ExchangeRate rows, currencies Frankfurter has no
    rate for). Raises RateFetchError when offline - nothing is stored
    then, so the previous rates stay in use. The internet request
    finishes before anything is written, so the database is never locked
    while waiting on the network.
    """
    wanted = sorted(set(currencies) - {main_currency})
    unavailable = [code for code in wanted if code in NOT_ON_FRANKFURTER]
    wanted = [code for code in wanted if code not in NOT_ON_FRANKFURTER]
    if main_currency in NOT_ON_FRANKFURTER:
        unavailable, wanted = sorted(unavailable + wanted), []
    if not wanted:
        return [], unavailable

    url = f"{FRANKFURTER_URL}?{urlencode({'base': main_currency, 'symbols': ','.join(wanted)})}"
    request = urllib.request.Request(url, headers={"User-Agent": "Stashy personal finance app"})

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
        rates_date = date.fromisoformat(payload["date"])
        if not is_plausible_date(rates_date):
            # A rate dated 9999-12-31 would be the latest one for ever.
            raise ValueError("date outside the limits")
        rates = {code: Decimal(str(value)) for code, value in payload["rates"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, InvalidOperation) as error:
        raise RateFetchError(
            "Couldn't get exchange rates from Frankfurter. Check your internet connection and "
            "try again — until then Stashy keeps using the rates it already knows."
        ) from error

    stored = []
    # Short and only for the writes, on the stash's own database
    # (`atomic()` alone would open it on `default`, the picker's empty
    # placeholder).
    with (
        transaction.atomic(using=router.db_for_write(ExchangeRate)),
        system_action("Exchange rates fetched from Frankfurter (European Central Bank)"),
    ):
        for code in wanted:
            rate = rates.get(code)
            if rate is None or not is_plausible_rate(rate):
                unavailable.append(code)
                continue

            row, _ = ExchangeRate.objects.update_or_create(
                base_currency=main_currency,
                quote_currency=code,
                date=rates_date,
                source=ExchangeRate.Source.FRANKFURTER,
                defaults={"rate": rate},
            )
            stored.append(row)

    return stored, sorted(unavailable)
