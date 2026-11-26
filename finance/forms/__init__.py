"""Stashy's forms, one module per area like `views/`: `accounts`,
`categories`, `payees`, `transactions`, `reminders`, `imports`,
`filters` (the date-range form Transactions and Reports share),
`settings` and `auth` (the picker and PIN screens), with what they all
share in `_base`.

This file re-exports nothing, so every import names the real module:
`from ..forms.accounts import AccountForm`.

Forms do the validation, so a view only asks `is_valid()`. A rule about
the data itself lives on the model's `clean()`, which a ModelForm runs
for you, so the admin gets it too. Dates follow the stash's own format
through `regional`, never a fixed one.
"""
