"""Reading an id from a request: a number that is safe to look up.

Whatever a hand-edited address or a posted form sends goes through
here before it reaches a lookup. It must not touch the database.
"""

from stashy.key_range import fits_the_database, key_range


def as_id(value):
    """`value` as an integer database id if it is plain ASCII digits and
    fits, else None. Whatever a hand-edited URL or form sends must not
    reach a lookup unchecked: a value that isn't a number is a 500 page
    and a traceback in the log. `isdigit()` alone isn't enough, since it
    is also true for characters like "²" that `int()` refuses, and a
    number too big for the database overflows. A lookup by None finds
    nothing, so a bad id answers like an id that doesn't exist."""
    text = value if isinstance(value, str) else ""
    if not (text.isascii() and text.isdigit()):
        return None
    # Too long to be an id at all. Python refuses to turn a string of
    # more than 4,300 digits into an int (a ValueError), so the length
    # is checked first; leading zeros don't count.
    digits = text.lstrip("0") or "0"
    if len(digits) > len(str(key_range()[1])):
        return None
    number = int(digits)
    return number if fits_the_database(number) else None


def as_ids(values):
    """The ids in `values` (see as_id()); anything else is left out."""
    return [found for found in map(as_id, values) if found is not None]
