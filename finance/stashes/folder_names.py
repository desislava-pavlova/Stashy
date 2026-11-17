"""Which folders under stashes/ are stashes, and which names no stash
may take.

Standard library only, on purpose: `scripts/bootstrap.py` reads this
while it is still setting things up, before Django is configured, so
that it and the app agree on what a stash is. There is one definition,
`stash_folders()`; a test checks that this file and the packages above
it import nothing else.

A stash's folder name is also its connection alias, and "default" is
already the picker's empty placeholder database: ensure_registered()
would do nothing for it, and the stash would be built inside the
placeholder. The others are Windows device names: a folder with one can
be made from a script, but Explorer and the command line treat it as the
device and can't open, copy or delete it.
"""

from pathlib import Path

# "default" is Django's DEFAULT_DB_ALIAS, written out because this
# module can't import Django; a test checks that the two match.
RESERVED_FOLDER_NAMES = frozenset(
    {
        "default",
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)


def is_reserved_folder_name(name):
    """Whether `name` is reserved, in any upper/lower case: Windows
    treats `CON` and `con` as the same device, and `Default` as the same
    folder as `default`."""
    return name.lower() in RESERVED_FOLDER_NAMES


def stash_folders(directory):
    """The stashes' folders inside `directory`, sorted by name: a real
    folder that holds a db.sqlite3 and doesn't have a reserved name. A
    stash's existence *is* its folder existing, so this is a directory
    scan, not a database query."""
    directory = Path(directory)
    if not directory.exists():
        return []
    return sorted(
        (
            folder
            for folder in directory.iterdir()
            if folder.is_dir() and not is_reserved_folder_name(folder.name) and (folder / "db.sqlite3").exists()
        ),
        key=lambda folder: folder.name,
    )
