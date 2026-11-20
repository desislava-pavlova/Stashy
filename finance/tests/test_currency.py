"""Exchange rates: converting with the latest known rate, totalling
accounts kept in different currencies, and fetching the latest rates
from Frankfurter."""

import io
import json
from datetime import date
from decimal import Decimal
from unittest import mock
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from audit.models import AuditLogEntry

from ..currency import RateFetchError, RateTable, convert_balances, fetch_latest_rates
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
        # The sort breaks a tie on the row's id, so the order the rows
        # were saved in must not matter.
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
        self.add_rate(
            ExchangeRate.Source.MANUAL, "1.25", base="EUR", quote="USD"
        )  # 1 EUR = 1.25 USD
        known = RateTable("EUR").known("USD")
        self.assertEqual((known.rate, known.source), (Decimal("0.8"), ExchangeRate.Source.MANUAL))

    def test_accounts_missing_a_rate_are_reported_but_others_still_total(self):
        eur_account = Account.objects.create(name="Checking", currency="EUR")
        Transaction.objects.create(
            account=eur_account, date=date(2026, 1, 1), amount=Decimal("50.00")
        )
        yen_account = Account.objects.create(name="Japan trip", currency="JPY")
        Transaction.objects.create(
            account=yen_account, date=date(2026, 1, 1), amount=Decimal("1000.00")
        )

        total, missing = convert_balances([eur_account, yen_account], "EUR")
        self.assertEqual(total, Decimal("50.00"))
        self.assertEqual(missing, [yen_account])


def frankfurter_replies(payload, raw=None):
    """Stands in for urllib's urlopen(): the reply is `payload` as JSON
    (or the bytes `raw`, for a reply that isn't JSON), in something a
    `with` block opens and json.load() reads. Nothing here touches the
    internet."""
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(
        raw if raw is not None else json.dumps(payload).encode()
    )
    return mock.patch("finance.currency.urllib.request.urlopen", return_value=response)


class FetchLatestRatesTests(StashTestCase):
    REPLY = {"base": "EUR", "date": "2026-03-02", "rates": {"GBP": 0.85, "USD": 1.16}}

    def test_each_rate_in_the_reply_is_stored_with_the_replys_date(self):
        with frankfurter_replies(self.REPLY):
            stored, unavailable = fetch_latest_rates("EUR", ["USD", "GBP"])

        self.assertEqual(unavailable, [])
        rows = ExchangeRate.objects.order_by("quote_currency")
        self.assertEqual(list(rows), stored)
        self.assertEqual(
            [(r.base_currency, r.quote_currency, r.rate, r.date, r.source) for r in rows],
            [
                ("EUR", "GBP", Decimal("0.85"), date(2026, 3, 2), ExchangeRate.Source.FRANKFURTER),
                ("EUR", "USD", Decimal("1.16"), date(2026, 3, 2), ExchangeRate.Source.FRANKFURTER),
            ],
        )

    def test_it_asks_only_for_the_other_currencies_and_names_them_in_order(self):
        with frankfurter_replies(self.REPLY) as urlopen:
            fetch_latest_rates("EUR", ["USD", "EUR", "GBP", "USD"])

        request = urlopen.call_args.args[0]
        self.assertEqual(
            parse_qs(urlsplit(request.full_url).query), {"base": ["EUR"], "symbols": ["GBP,USD"]}
        )

    def test_the_request_has_a_timeout_and_says_who_is_asking(self):
        with frankfurter_replies(self.REPLY) as urlopen:
            fetch_latest_rates("EUR", ["USD"])

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)  # Settings waits for this
        self.assertEqual(
            urlopen.call_args.args[0].get_header("User-agent"), "Stashy personal finance app"
        )

    def test_fetching_again_the_same_day_updates_the_rate_instead_of_adding_a_second(self):
        with frankfurter_replies(self.REPLY):
            fetch_latest_rates("EUR", ["USD"])
        with frankfurter_replies({**self.REPLY, "rates": {"USD": 1.2}}):
            fetch_latest_rates("EUR", ["USD"])

        self.assertEqual(
            list(ExchangeRate.objects.values_list("rate", flat=True)), [Decimal("1.2")]
        )

    def test_a_reply_dated_outside_the_limits_is_refused_and_nothing_is_stored(self):
        """A rate dated 9999-12-31 would be the latest one for ever."""
        for text in ("9999-12-31", "1899-12-31", "0001-01-01"):
            with (
                self.subTest(date=text),
                frankfurter_replies({**self.REPLY, "date": text}),
                self.assertRaises(RateFetchError),
            ):
                fetch_latest_rates("EUR", ["USD"])
        self.assertFalse(ExchangeRate.objects.exists())

    def test_a_rate_that_cant_be_stored_is_left_out_like_a_currency_with_no_rate(self):
        """1e11 and 1e30 don't fit the column and broke every page that
        converts; NaN was an uncaught error; 1e-30 is stored as zero.
        The good one in the same reply is still stored."""
        rates = {"GBP": "NaN", "USD": "1e11", "CHF": "1e-30", "JPY": 160.5}
        with frankfurter_replies({**self.REPLY, "rates": rates}):
            stored, unavailable = fetch_latest_rates("EUR", ["GBP", "USD", "CHF", "JPY"])
        self.assertEqual(unavailable, ["CHF", "GBP", "USD"])
        self.assertEqual([row.quote_currency for row in stored], ["JPY"])

    def test_a_fetched_rate_is_logged_as_an_automatic_change_with_its_reason(self):
        with frankfurter_replies(self.REPLY):
            fetch_latest_rates("EUR", ["USD"])

        entry = AuditLogEntry.objects.filter(table=ExchangeRate._meta.db_table).first()
        self.assertEqual(
            (entry.source, entry.reason),
            (
                AuditLogEntry.Source.AUTOMATIC,
                "Exchange rates fetched from Frankfurter (European Central Bank)",
            ),
        )

    def test_the_lev_is_never_asked_for_and_is_reported_as_unavailable(self):
        with frankfurter_replies(self.REPLY) as urlopen:
            stored, unavailable = fetch_latest_rates("EUR", ["BGN", "USD"])

        request = urlopen.call_args.args[0]
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)["symbols"], ["USD"])
        self.assertEqual([row.quote_currency for row in stored], ["USD"])
        self.assertEqual(unavailable, ["BGN"])

    def test_the_unavailable_currencies_come_back_in_order(self):
        with frankfurter_replies({**self.REPLY, "rates": {"USD": 1.16}}):
            _, unavailable = fetch_latest_rates("EUR", ["BGN", "AUD", "USD"])

        self.assertEqual(unavailable, ["AUD", "BGN"])

    def test_with_the_lev_as_the_main_currency_nothing_is_fetched(self):
        with frankfurter_replies(self.REPLY) as urlopen:
            stored, unavailable = fetch_latest_rates("BGN", ["USD", "EUR"])

        urlopen.assert_not_called()
        self.assertEqual((stored, unavailable), ([], ["EUR", "USD"]))

    def test_nothing_to_fetch_makes_no_request(self):
        with frankfurter_replies(self.REPLY) as urlopen:
            result = fetch_latest_rates("EUR", ["EUR"])

        urlopen.assert_not_called()
        self.assertEqual(result, ([], []))

    def test_a_currency_missing_from_the_reply_is_unavailable_and_the_rest_are_stored(self):
        with frankfurter_replies({**self.REPLY, "rates": {"USD": 1.16}}):
            stored, unavailable = fetch_latest_rates("EUR", ["USD", "GBP"])

        self.assertEqual([row.quote_currency for row in stored], ["USD"])
        self.assertEqual(unavailable, ["GBP"])

    def test_a_rate_that_is_zero_or_negative_is_never_stored(self):
        with frankfurter_replies({**self.REPLY, "rates": {"GBP": 0, "USD": -1.16}}):
            stored, unavailable = fetch_latest_rates("EUR", ["USD", "GBP"])

        self.assertEqual(stored, [])
        self.assertEqual(unavailable, ["GBP", "USD"])
        self.assertFalse(ExchangeRate.objects.exists())

    def test_offline_raises_and_stores_nothing(self):
        offline = mock.patch(
            "finance.currency.urllib.request.urlopen", side_effect=URLError("no internet")
        )
        with (
            offline,
            self.assertRaisesMessage(RateFetchError, "keeps using the rates it already knows"),
        ):
            fetch_latest_rates("EUR", ["USD"])

        self.assertFalse(ExchangeRate.objects.exists())

    def test_a_reply_that_isnt_what_frankfurter_sends_raises_and_stores_nothing(self):
        replies = {
            "no date": {"base": "EUR", "rates": {"USD": 1.16}},
            "no rates": {"base": "EUR", "date": "2026-03-02"},
            "a date that isn't one": {"date": "yesterday", "rates": {"USD": 1.16}},
            "a rate that isn't a number": {"date": "2026-03-02", "rates": {"USD": "lots"}},
            "rates that aren't a table": {"date": "2026-03-02", "rates": [1.16]},
            "not a table at all": [1, 2, 3],
        }
        for name, payload in replies.items():
            with self.subTest(reply=name), frankfurter_replies(payload):
                with self.assertRaises(RateFetchError):
                    fetch_latest_rates("EUR", ["USD"])
                self.assertFalse(ExchangeRate.objects.exists())

    def test_a_reply_that_isnt_json_at_all_raises_and_stores_nothing(self):
        with (
            frankfurter_replies(None, raw=b"<html>Bad gateway</html>"),
            self.assertRaises(RateFetchError),
        ):
            fetch_latest_rates("EUR", ["USD"])

        self.assertFalse(ExchangeRate.objects.exists())
