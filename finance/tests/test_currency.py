"""Exchange rates: converting with the latest known rate, and totalling accounts kept in
different currencies."""

from datetime import date
from decimal import Decimal

from ..currency import RateTable, convert_balances
from ..models import Account, ExchangeRate, Transaction
from .base import StashTestCase


class CurrencyConversionTests(StashTestCase):
    def test_convert_uses_the_latest_rate(self):
        ExchangeRate.objects.create(
            base_currency="USD", quote_currency="EUR", rate=Decimal("0.9"), date=date(2026, 1, 1)
        )
        ExchangeRate.objects.create(
            base_currency="USD", quote_currency="EUR", rate=Decimal("0.8"), date=date(2026, 2, 1)
        )
        rates = RateTable("EUR")
        self.assertEqual(rates.convert(Decimal("100.00"), "USD"), Decimal("80.00"))

    def test_a_rate_stored_the_other_way_round_still_works(self):
        ExchangeRate.objects.create(
            base_currency="EUR", quote_currency="USD", rate=Decimal("1.25"), date=date(2026, 1, 1)
        )
        rates = RateTable("EUR")
        self.assertEqual(rates.convert(Decimal("125.00"), "USD"), Decimal("100.00"))

    def test_accounts_missing_a_rate_are_reported_but_others_still_total(self):
        eur_account = Account.objects.create(name="Checking", currency="EUR")
        Transaction.objects.create(account=eur_account, date=date(2026, 1, 1), amount=Decimal("50.00"))
        yen_account = Account.objects.create(name="Japan trip", currency="JPY")
        Transaction.objects.create(account=yen_account, date=date(2026, 1, 1), amount=Decimal("1000.00"))

        total, missing = convert_balances([eur_account, yen_account], "EUR")
        self.assertEqual(total, Decimal("50.00"))
        self.assertEqual(missing, [yen_account])
