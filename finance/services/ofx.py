"""Reading the transactions out of an OFX or QFX file, the formats banks
let you download.

Only what the importer needs is read: each transaction's date, amount,
payee name and memo. An OFX file is tag soup, not a strict document:
version 1 leaves most tags unclosed (`<NAME>Lidl<MEMO>weekly shop`),
version 2 is XML. A tag's value is the text up to the next `<`, which
reads both the same way.

What this module must not do: touch the database, or guess. A file it
can't read raises OfxError, and the importer turns that into its one
"couldn't read this file" message. A date and an amount are read by
fixed rules (below), and those rules must not change: the importer's
duplicate check compares dates and amounts, so a file imported again
has to give the same ones to be recognised as already imported.
"""

import codecs
import html
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import NamedTuple


class OfxError(ValueError):
    """A file that isn't a readable OFX file (or has a transaction the
    importer can't use)."""


class OfxTransaction(NamedTuple):
    date: date
    amount: Decimal
    payee: str
    memo: str


_OFX_TAG = re.compile(r"<OFX[>\s]", re.IGNORECASE)
_INVESTMENT_STATEMENT = re.compile(r"<INVSTMTRS>", re.IGNORECASE)
_END_OF_TRANSACTION = re.compile(r"</STMTTRN>", re.IGNORECASE)
_START_OF_TRANSACTION = re.compile(r"<STMTTRN>", re.IGNORECASE)
# No real date or amount is longer than this. A longer one is damage or
# an attack, and is refused before any pattern runs over it: the
# patterns below can take a very long time on a very long value.
_LONGEST_VALUE = 100
# 20260105123000[-5:EST]: the part in brackets is the offset from UTC
# in hours (it may have a fraction, as Newfoundland's does) and a name.
_UTC_OFFSET = re.compile(r"\[(?P<hours>[-+]?\d+\.?\d*):\w*\]$")


def read_transactions(data):
    """Every bank or card transaction in the file's bytes, in the order
    they stand in the file. Raises OfxError for a file that isn't OFX,
    for an investment statement (it holds trades, which Stashy has no
    place for) and for a transaction with no usable date or amount."""
    text = _decode(data)
    if not _OFX_TAG.search(text):
        raise OfxError("There is no <OFX> tag in this file.")
    if _INVESTMENT_STATEMENT.search(text):
        raise OfxError("Investment statements can't be imported.")
    return [_transaction(block) for block in _transaction_blocks(text)]


def _transaction_blocks(text):
    """The text of each `<STMTTRN>...</STMTTRN>`, in order. It cuts the
    file at each closing tag and looks back for the opening one, one
    pass over the file however many opening tags have no closing one."""
    for piece in _END_OF_TRANSACTION.split(text)[:-1]:
        parts = _START_OF_TRANSACTION.split(piece)
        if len(parts) > 1:
            yield parts[-1]


def _decode(data):
    """The file's text. A version 1 file says how it is encoded in its
    header lines (before the first tag); with no header it is read as
    UTF-8, else as Windows-1252, the usual encoding of an old bank
    export."""
    first_tag = data.find(b"<")
    codec = _declared_codec(_headers(data[: first_tag if first_tag != -1 else len(data)]))
    if codec:
        try:
            return data.decode(codec)
        except UnicodeDecodeError as error:
            raise OfxError("The file isn't in the encoding its header names.") from error
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _declared_codec(headers):
    """The codec a version 1 header names, or None when it names none
    Python knows (`CHARSET:NONE`, or a number that is no code page): the
    file is then read as if its header had said nothing."""
    declared = headers.get("ENCODING")
    if declared in ("UNICODE", "UTF-8"):
        return "utf-8-sig"
    if declared != "USASCII":
        return None
    charset = headers.get("CHARSET", "1252")
    name = "iso-8859-1" if charset == "8859-1" else f"cp{charset}"
    try:
        return codecs.lookup(name).name
    except (LookupError, ValueError):
        return None


def _headers(head):
    """The `KEY:VALUE` lines of a version 1 header, in upper case."""
    headers = {}
    for line in head.decode("ascii", "replace").splitlines():
        key, colon, value = line.partition(":")
        if colon:
            headers[key.strip().upper()] = value.strip().upper()
    return headers


def _transaction(block):
    posted = _value(block, "DTPOSTED")
    amount = _value(block, "TRNAMT")
    if posted is None or amount is None:
        raise OfxError("A transaction has no date or no amount.")
    if len(posted) > _LONGEST_VALUE or len(amount) > _LONGEST_VALUE:
        raise OfxError("A date or an amount is far too long to be one.")
    return OfxTransaction(
        date=_posted_date(posted),
        amount=_amount(amount),
        payee=_value(block, "NAME") or "",
        memo=_value(block, "MEMO") or "",
    )


def _value(block, tag):
    """The text of the first `<TAG>` in the block, with its entities
    (`&amp;`) decoded and the space around it trimmed; None when the
    block has no such tag. A version 2 file may wrap the text in
    `<![CDATA[...]]>`, which is taken as it stands."""
    match = re.search(
        rf"<{tag}>(?:<!\[CDATA\[(?P<cdata>.*?)\]\]>|(?P<text>[^<]*))",
        block,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    if match["cdata"] is not None:
        return match["cdata"].strip()
    return html.unescape(match["text"]).strip()


def _posted_date(text):
    """The date a transaction was posted. With an offset (`[-5:EST]`)
    the time is moved to UTC first, so 11 pm on 5 January in New York
    is 6 January, and midnight on 5 January in Sofia (`[+2:EET]`) is
    4 January: the day is the one in UTC. A date with no time, or a time
    with no offset, is the date as written."""
    match = _UTC_OFFSET.search(text)
    try:
        offset = timedelta(hours=float(match["hours"])) if match else timedelta()
        try:
            posted = datetime.strptime(text[:14], "%Y%m%d%H%M%S")
        except ValueError:
            posted = datetime.strptime(text[:8], "%Y%m%d")
        return (posted - offset).date()
    except (ValueError, OverflowError) as error:
        raise OfxError(f"The date {text!r} can't be read.") from error


def _amount(text):
    """The amount as a Decimal, whichever way the bank wrote it
    (`10,000.50`, `10.000,50`, `10000,50`, `1 025,53`, `+12.50`). A
    bank's `null` is zero (it marks a rate change on a statement)."""
    number = text
    if re.search(r".*\..*,", number):
        number = number.replace(".", "")  # 10.000,50
    if re.search(r".*,.*\.", number):
        number = number.replace(",", "")  # 10,000.50
    if "." not in number and "," in number:
        number = number.replace(",", ".")  # 10000,50
    number = number.replace(" ", "").replace("+", "")
    try:
        return Decimal(number)
    except InvalidOperation:
        if text in ("null", "-null"):
            return Decimal(0)
        raise OfxError(f"The amount {text!r} can't be read.") from None
