"""Reading OFX and QFX files (finance/services/ofx.py): the date and its
time-zone shift, the amount in whatever way a bank writes it, version 1
and version 2 files, encodings, and the files it refuses."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from ..services import ofx
from .base import imported_modules, ofx_file


def xml_file(posted="20260105", amount="-12.50", name="Lidl", memo="weekly shop"):
    """A version 2 (XML) file with one transaction, in UTF-8."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<?OFX OFXHEADER="200" VERSION="211"?>\n'
        "<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST><STMTTRN>"
        f"<TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>{posted}</DTPOSTED><TRNAMT>{amount}</TRNAMT>"
        f"<FITID>1</FITID><NAME>{name}</NAME><MEMO>{memo}</MEMO>"
        "</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    ).encode()


def only(data):
    (transaction,) = ofx.read_transactions(data)
    return transaction


class ReadingAVersionOneFileTests(SimpleTestCase):
    def test_the_date_amount_payee_and_memo_come_out(self):
        self.assertEqual(
            only(ofx_file()),
            ofx.OfxTransaction(date(2026, 1, 5), Decimal("-12.50"), "Lidl", "weekly shop"),
        )

    def test_a_transaction_may_have_no_name_or_memo(self):
        data = ofx_file().replace(b"<NAME>Lidl<MEMO>weekly shop", b"")
        self.assertEqual((only(data).payee, only(data).memo), ("", ""))

    def test_an_empty_name_is_a_blank_payee_not_a_refused_file(self):
        data = ofx_file().replace(b"<NAME>Lidl<MEMO>", b"<NAME></NAME><MEMO>")
        self.assertEqual(only(data).payee, "")

    def test_entities_are_decoded_and_the_space_around_a_value_is_trimmed(self):
        data = ofx_file().replace(
            b"<NAME>Lidl<MEMO>weekly shop", b"<NAME>  AT&amp;T  <MEMO>x &lt; y"
        )
        self.assertEqual((only(data).payee, only(data).memo), ("AT&T", "x < y"))

    def test_every_transaction_of_every_statement_comes_out_in_file_order(self):
        data = ofx_file()
        second = data.replace(b"20260105", b"20260106").replace(b"-12.50", b"-2.00")
        added = re.search(rb"<STMTTRN>.*?</STMTTRN>", second, re.DOTALL).group()
        data = data.replace(b"</BANKTRANLIST>", added + b"</BANKTRANLIST>")
        card = data.replace(b"BANKMSGSRSV1", b"CREDITCARDMSGSRSV1").replace(b"STMTRS", b"CCSTMTRS")

        self.assertEqual(
            [(t.date, t.amount) for t in ofx.read_transactions(data + card)],
            [
                (date(2026, 1, 5), Decimal("-12.50")),
                (date(2026, 1, 6), Decimal("-2.00")),
                (date(2026, 1, 5), Decimal("-12.50")),
                (date(2026, 1, 6), Decimal("-2.00")),
            ],
        )


class PostedDateTests(SimpleTestCase):
    """A date with an offset is moved to UTC first, and the day is the
    one it lands on. The rule must not change: the importer's duplicate
    check compares dates."""

    def posted(self, text):
        return only(ofx_file(posted=text)).date

    def test_a_date_without_a_time_or_an_offset_is_as_written(self):
        for text in ("20260105", "20260105120000", "20260105235959", "20260105120000.123"):
            with self.subTest(text=text):
                self.assertEqual(self.posted(text), date(2026, 1, 5))

    def test_a_late_evening_west_of_utc_is_the_next_day(self):
        self.assertEqual(self.posted("20260105230000[-5:EST]"), date(2026, 1, 6))

    def test_a_midnight_east_of_utc_is_the_day_before(self):
        self.assertEqual(self.posted("20260105000000[+2:EET]"), date(2026, 1, 4))
        self.assertEqual(self.posted("20260105000000[2:EET]"), date(2026, 1, 4))

    def test_noon_is_the_same_day_for_almost_every_zone(self):
        for offset in ("0:GMT", "-8:PST", "-11:SST", "+2:EET", "+11:AEDT"):
            with self.subTest(offset=offset):
                self.assertEqual(self.posted(f"20260105120000[{offset}]"), date(2026, 1, 5))

    def test_noon_in_the_far_east_is_still_the_day_before_in_utc(self):
        # New Zealand in summer is UTC+13: noon there is 11 pm UTC the
        # day before.
        self.assertEqual(self.posted("20260105120000[+13:NZDT]"), date(2026, 1, 4))

    def test_a_fractional_offset_is_honoured(self):
        # Newfoundland: UTC-3:30, so 8 pm is 11:30 pm UTC, the same day,
        # and 9 pm is 0:30 am the next day.
        self.assertEqual(self.posted("20260105200000[-3.5:NST]"), date(2026, 1, 5))
        self.assertEqual(self.posted("20260105210000[-3.5:NST]"), date(2026, 1, 6))

    def test_a_date_that_isnt_one_is_refused(self):
        for text in ("00000000", "20261305", "20260230", "abc", "", "2026-01-05"):
            with self.subTest(text=text), self.assertRaises(ofx.OfxError):
                self.posted(text)


class AmountTests(SimpleTestCase):
    def amount(self, text):
        return only(ofx_file(amount=text)).amount

    def test_every_way_a_bank_writes_a_number(self):
        for text, expected in [
            ("-12.50", "-12.50"),
            ("+12.50", "12.50"),
            ("12.5", "12.5"),
            ("1,234.56", "1234.56"),
            ("1.234,56", "1234.56"),
            ("12,50", "12.50"),
            ("1 025,53", "1025.53"),
            ("0", "0"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.amount(text), Decimal(expected))

    def test_a_bank_null_is_zero(self):
        for text in ("null", "-null"):
            with self.subTest(text=text):
                self.assertEqual(self.amount(text), 0)

    def test_text_that_is_no_number_is_refused(self):
        for text in ("abc", "", "12.50.50", "--5"):
            with self.subTest(text=text), self.assertRaises(ofx.OfxError):
                self.amount(text)


class VersionTwoAndEncodingTests(SimpleTestCase):
    def test_an_xml_file_reads_like_a_version_one_file(self):
        self.assertEqual(only(xml_file()), only(ofx_file()))

    def test_a_utf_8_file_with_cyrillic_names_is_read(self):
        self.assertEqual(only(xml_file(name="Кауфланд")).payee, "Кауфланд")

    def test_a_file_that_declares_its_charset_is_read_in_it(self):
        header = "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nENCODING:USASCII\nCHARSET:1251\n\n"
        body = (
            "<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST><STMTTRN>"
            "<DTPOSTED>20260105<TRNAMT>-1.00<FITID>1\n<NAME>Лидл</STMTTRN>"
            "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
        )
        self.assertEqual(only((header + body).encode("cp1251")).payee, "Лидл")

    def test_a_latin_1_charset_is_read_as_latin_1(self):
        data = (
            ofx_file()
            .replace(b"CHARSET:1252", b"CHARSET:8859-1")
            .replace(b"Lidl", "Café".encode("latin-1"))
        )
        self.assertEqual(only(data).payee, "Café")

    def test_a_file_with_no_declared_encoding_falls_back_to_windows_1252(self):
        data = xml_file().replace(b"Lidl", "Café".encode("cp1252"))
        self.assertEqual(only(data).payee, "Café")

    def test_a_charset_that_is_no_code_page_reads_as_if_none_was_named(self):
        for charset in (b"9999", b"NONE", b"UTF-8", b"\x00", b"1252.x"):
            data = ofx_file().replace(b"CHARSET:1252", b"CHARSET:" + charset)
            with self.subTest(charset=charset):
                self.assertEqual(only(data).payee, "Lidl")

    def test_bytes_that_the_declared_encoding_cannot_read_are_refused(self):
        data = (
            ofx_file()
            .replace(b"Lidl", b"\xff\xfe")
            .replace(b"ENCODING:USASCII", b"ENCODING:UTF-8")
        )
        with self.assertRaises(ofx.OfxError):
            ofx.read_transactions(data)

    def test_a_dotted_capital_i_in_the_header_part_does_not_move_the_cut(self):
        """`lower()` makes "İ" two characters, which once moved the
        cut of a transaction one character right for each."""
        data = xml_file().replace(b"<TRNTYPE>DEBIT</TRNTYPE>", b"")
        data = data.replace(
            b"<BANKMSGSRSV1>", ("<FI><ORG>" + "İ" * 40 + "</ORG></FI><BANKMSGSRSV1>").encode()
        )
        self.assertEqual(only(data).amount, Decimal("-12.50"))

    def test_a_cdata_wrapped_payee_is_read_as_it_stands(self):
        data = xml_file(name="<![CDATA[AT&amp;T <Shop>]]>")
        self.assertEqual(only(data).payee, "AT&amp;T <Shop>")

    def test_a_huge_offset_is_a_refused_date_not_a_crash(self):
        with self.assertRaises(ofx.OfxError):
            only(ofx_file(posted="20260105[99999999999999:X]"))


class RefusedFileTests(SimpleTestCase):
    def test_a_file_that_is_not_ofx(self):
        for data in (b"", b"not really that kind of file", b"<html><body>hello</body></html>"):
            with self.subTest(data=data), self.assertRaises(ofx.OfxError):
                ofx.read_transactions(data)

    def test_a_transaction_with_no_date_or_no_amount(self):
        for data in (
            ofx_file().replace(b"<DTPOSTED>20260105", b""),
            ofx_file().replace(b"<TRNAMT>-12.50", b""),
        ):
            with self.subTest(data=data), self.assertRaises(ofx.OfxError):
                ofx.read_transactions(data)

    def test_a_date_or_amount_far_too_long_to_be_one_is_refused(self):
        """A pattern run over a value of megabytes could take hours; no
        real date or amount has a hundred characters."""
        for tag, text in (("DTPOSTED", "1" * 5000), ("TRNAMT", "." * 5000 + ",")):
            data = ofx_file().replace(f"<{tag}>".encode(), f"<{tag}>{text}".encode())
            with self.subTest(tag=tag), self.assertRaises(ofx.OfxError):
                ofx.read_transactions(data)

    def test_opening_tags_that_never_close_make_no_transactions_and_take_one_pass(self):
        data = b"<OFX>" + b"<STMTTRN><TRNAMT>1<DTPOSTED>20260105" * 20000
        self.assertEqual(ofx.read_transactions(data), [])

    def test_an_unclosed_transaction_before_a_closed_one_is_not_part_of_it(self):
        data = ofx_file().replace(b"<STMTTRN>", b"<STMTTRN><TRNAMT>999<STMTTRN>")
        self.assertEqual(only(data).amount, Decimal("-12.50"))

    def test_tags_in_lower_case_are_read(self):
        data = (
            ofx_file()
            .replace(b"<STMTTRN>", b"<stmttrn>")
            .replace(b"</STMTTRN>", b"</stmttrn>")
            .replace(b"<TRNAMT>", b"<trnamt>")
            .replace(b"<DTPOSTED>", b"<dtposted>")
        )
        self.assertEqual(only(data).amount, Decimal("-12.50"))

    def test_an_investment_statement(self):
        data = ofx_file().replace(b"STMTRS", b"INVSTMTRS")
        with self.assertRaises(ofx.OfxError):
            ofx.read_transactions(data)

    def test_a_file_with_no_transactions_is_read_as_none(self):
        data = ofx_file().replace(rb"<STMTTRN>", b"<X>").replace(b"</STMTTRN>", b"</X>")
        self.assertEqual(ofx.read_transactions(data), [])


class OfxModuleStandsAloneTests(SimpleTestCase):
    def test_it_imports_only_the_standard_library(self):
        """It must not touch the database or the app: it is plain text
        in, plain values out."""
        path = Path(settings.BASE_DIR) / "finance" / "services" / "ofx.py"
        names = imported_modules(path.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(set(names)), ["codecs", "datetime", "decimal", "html", "re", "typing"]
        )
