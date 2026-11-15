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

    def add_rate(self, source, rate, day=date(2026, 3, 2), base="USD", quote="EUR"):
        return ExchangeRate.objects.create(
            base_currency=base, quote_currency=quote, rate=Decimal(rate), date=day, source=source
        )

    def test_on_the_same_day_a_typed_rate_beats_a_fetched_one_whichever_was_saved_first(self):
        # The sort breaks a tie on the row's id, so the order the rows were saved in must not matter.
        manual, fetched = ExchangeRate.Source.MANUAL, ExchangeRate.Source.FRANKFURTER
        for saved_first in ((manual, fetched), (fetched, manual)):
            with self.subTest(saved_first=saved_first[0]):
                ExchangeRate.objects.all().delete()
                for source in saved_first:
                    self.add_rate(source, "0.80" if source == manual else "0.90")
                known = RateTable("EUR").known("USD")
                self.assertEqual((known.rate, known.source), (Decimal("0.80"), manual))

    def test_a_newer_fetched_rate_beats_an_older_typed_one(self):
        self.add_rate(ExchangeRate.Source.MANUAL, "0.80", day=date(2026, 3, 1))
        self.add_rate(ExchangeRate.Source.FRANKFURTER, "0.90", day=date(2026, 3, 2))
        known = RateTable("EUR").known("USD")
        self.assertEqual(
            (known.rate, known.source, known.date),
            (Decimal("0.90"), ExchangeRate.Source.FRANKFURTER, date(2026, 3, 2)),
        )

    def test_the_same_day_rule_holds_when_the_typed_rate_is_stored_the_other_way_round(self):
        self.add_rate(ExchangeRate.Source.FRANKFURTER, "0.90")  # 1 USD = 0.90 EUR
        self.add_rate(ExchangeRate.Source.MANUAL, "1.25", base="EUR", quote="USD")  # 1 EUR = 1.25 USD
        known = RateTable("EUR").known("USD")
        self.assertEqual((known.rate, known.source), (Decimal("0.8"), ExchangeRate.Source.MANUAL))

    def test_accounts_missing_a_rate_are_reported_but_others_still_total(self):
        eur_account = Account.objects.create(name="Checking", currency="EUR")
        Transaction.objects.create(account=eur_account, date=date(2026, 1, 1), amount=Decimal("50.00"))
        yen_account = Account.objects.create(name="Japan trip", currency="JPY")
        Transaction.objects.create(account=yen_account, date=date(2026, 1, 1), amount=Decimal("1000.00"))

        total, missing = convert_balances([eur_account, yen_account], "EUR")
        self.assertEqual(total, Decimal("50.00"))
        self.assertEqual(missing, [yen_account])
