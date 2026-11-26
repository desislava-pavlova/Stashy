"""The one answer to "can the database hold this number as a key?".

Three places ask it: ids read from an address or a form
(finance/ids.py), the multi-choice form field, and the admin's lookups.
It lives in the project package because the app and the audit app can
both reach it and neither may import the other.

What this module must not do: touch a database. It only reads the range
a connection's backend says a column holds.
"""

from django.db import DEFAULT_DB_ALIAS, connections


def key_range(db=DEFAULT_DB_ALIAS, internal_type="BigIntegerField"):
    """(lowest, highest): the numbers a column of `internal_type` can
    hold on the database `db`. Every model here has a BigAutoField key,
    which is a BigIntegerField underneath."""
    return connections[db].ops.integer_field_range(internal_type)


def fits_the_database(number, db=DEFAULT_DB_ALIAS, internal_type="BigIntegerField"):
    """Whether `number` is within that range. A lookup by a number
    outside it is an OverflowError from the database, which no page
    expects from a lookup."""
    lowest, highest = key_range(db, internal_type)
    return lowest <= number <= highest
