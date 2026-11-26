"""The one "can the database hold this number as a key?" answer
(stashy/key_range.py), which ids, the multi-choice field and the admin's
lookups all use."""

from django.test import SimpleTestCase

from stashy.key_range import fits_the_database, key_range


class KeyRangeTests(SimpleTestCase):
    def test_the_range_is_what_a_sqlite_integer_holds(self):
        self.assertEqual(key_range(), (-(2**63), 2**63 - 1))

    def test_the_edges_fit_and_one_past_them_does_not(self):
        for number, fits in [
            (0, True),
            (2**63 - 1, True),
            (2**63, False),
            (-(2**63), True),
            (-(2**63) - 1, False),
        ]:
            with self.subTest(number=number):
                self.assertEqual(fits_the_database(number), fits)

    def test_a_column_type_can_be_named(self):
        self.assertTrue(fits_the_database(2**63 - 1, internal_type="IntegerField"))
