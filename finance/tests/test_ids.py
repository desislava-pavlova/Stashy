"""Reading an id from a request (finance/ids.py): plain ASCII digits
that fit SQLite, else None."""

from django.test import SimpleTestCase

from ..ids import as_id, as_ids


class AsIdTests(SimpleTestCase):
    """S2's helper: an id from a hand-editable place is plain ASCII
    digits that fit SQLite, else None."""

    def test_ids_that_fit(self):
        for text, expected in (
            ("5", 5),
            ("0", 0),
            ("007", 7),
            ("0" * 5000 + "1", 1),
            ("9223372036854775807", 2**63 - 1),
        ):
            with self.subTest(text=text[:30]):
                self.assertEqual(as_id(text), expected)

    def test_everything_else_is_none_and_never_an_error(self):
        for value in (
            "",
            " ",
            "abc",
            "1.5",
            "-1",
            "+1",
            "1 2",
            "²",
            "٣",
            "9223372036854775808",
            "9" * 30,
            "9" * 4301,
            None,
            5,
            ["5"],
        ):
            with self.subTest(value=str(value)[:30]):
                self.assertIsNone(as_id(value))

    def test_a_list_keeps_only_the_ids(self):
        self.assertEqual(as_ids(["1", "x", "²", "3", "9" * 4301, ""]), [1, 3])
