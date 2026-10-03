"""Stashes: each one is a fully separate SQLite database, stashes/<name>/db.sqlite3, with its own
backups/ folder, its own PIN and its own owner name.

- `registry`: the folders on disk, their Django connection aliases, creating and restoring a
  stash, and the owner names shown in the picker.
- `security`: the PIN, the lockout after wrong guesses, and the recovery code.

`registry` uses `security` (a new stash needs a PIN and a recovery code); `security` never uses
`registry`, so the two can't import each other in a circle.
"""


class StashError(Exception):
    """A stash operation couldn't be completed - the message is safe to show the user directly."""
