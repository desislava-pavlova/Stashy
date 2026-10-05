"""SQLite backup (D8): timestamped copies in backups/, and restoring one.

Pure standard library on purpose - `scripts/bootstrap.py` calls `quarterly_backup_if_due()`
at launch, before the rest of the app is necessarily importable, and the Settings page's
"Back up now" and "Restore" actions call the other functions here the same way.

For the same reason the time stamps come from this computer's own clock (`datetime.now()`),
not the stash's time zone preference: a backup's name says when the file was made, here.

Copies go through SQLite's own backup API (`Connection.backup`), never a plain file copy:
SQLite takes the locks it needs, so a backup is a consistent snapshot even if Stashy is
writing at that moment, and a restore rewrites the live file page by page in a way every
connection the running server still holds notices on its next query.
"""

import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

BACKUP_DIR_NAME = "backups"
QUARTERLY_MIN_GAP_DAYS = 90

# <stash>_backup_<tag>_2026-09-14_11-57-36.sqlite3: which stash, why, and when, readable at a
# glance. The stash name keeps two stashes' backups from having identical names. Older backups
# are never renamed (no reason to touch a file nobody asked to change), so the pattern below
# also recognizes the two earlier schemes, backup_<tag>_<stamp> and db_<tag>_20260914_115736 -
# both for the quarterly-due check and for listing recent backups.
_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
# Stash names never contain "_" (folder names are a-z, 0-9 and "-"), so the optional stash
# prefix is unambiguous. The tag may contain underscores itself (before_restore,
# before_update), so it's matched lazily and the timestamp anchors the end.
_NAME_RE = re.compile(
    r"^(?:[a-z0-9-]+_)?(?:backup|db)_(?P<tag>\w+?)_(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}|\d{8}_\d{6})$"
)
# How long a restore waits for other connections to finish writing before giving up.
_LOCK_TIMEOUT_SECONDS = 20


def _parse_stamp(path):
    """Best-effort timestamp from a backup's filename, across old and new naming schemes -
    falls back to the file's own mtime if the name doesn't match either (never blocks
    startup or the Settings page over an oddly-named file)."""
    match = _NAME_RE.match(path.stem)
    if match:
        stamp = match.group("stamp")
        fmt = _TIMESTAMP_FORMAT if "-" in stamp else "%Y%m%d_%H%M%S"
        try:
            return datetime.strptime(stamp, fmt)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def _tag(path):
    match = _NAME_RE.match(path.stem)
    return match.group("tag") if match else None


def _stash_label(project_root):
    """`project_root` is the stash's own folder, so its name is the stash's name. Made safe
    for the filename pattern above, for a folder that isn't a stash (the tests' temp folders)."""
    return re.sub(r"[^a-z0-9-]+", "-", Path(project_root).resolve().name.lower()).strip("-") or "stash"


def backup_now(db_path, project_root, tag="manual"):
    """Copy the database to backups/<stash>_backup_<tag>_<timestamp>.sqlite3. Returns the new
    Path, or None if there's no database file yet (nothing to back up on a brand new install)."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    backups_dir = Path(project_root) / BACKUP_DIR_NAME
    backups_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
    destination = backups_dir / f"{_stash_label(project_root)}_backup_{tag}_{stamp}.sqlite3"
    _sqlite_copy(db_path, destination)
    return destination


def _sqlite_copy(source, destination):
    """Copy one SQLite database into another with SQLite's backup API."""
    src = sqlite3.connect(str(source), timeout=_LOCK_TIMEOUT_SECONDS)
    try:
        dst = sqlite3.connect(str(destination), timeout=_LOCK_TIMEOUT_SECONDS)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def list_backups(project_root, limit=None):
    """Every backup of any tag, newest first (by the timestamp in its name, not filesystem
    order - the naming schemes don't sort the same way as plain strings)."""
    backups_dir = Path(project_root) / BACKUP_DIR_NAME
    if not backups_dir.exists():
        return []
    found = list(
        {
            *backups_dir.glob("*_backup_*.sqlite3"),
            *backups_dir.glob("backup_*.sqlite3"),
            *backups_dir.glob("db_*.sqlite3"),
        }
    )
    found.sort(key=_parse_stamp, reverse=True)
    return found[:limit] if limit else found


def quarterly_backup_if_due(db_path, project_root):
    """Called once at app launch, never on a background schedule (D8). Backs up only if the
    most recent quarterly backup is missing or more than ~90 days old. Returns the new Path,
    or None if a backup wasn't due (or there's no database yet)."""
    existing = [p for p in list_backups(project_root) if _tag(p) == "quarterly"]
    if existing and date.today() - _parse_stamp(existing[0]).date() < timedelta(days=QUARTERLY_MIN_GAP_DAYS):
        return None
    return backup_now(db_path, project_root, tag="quarterly")


def restore_from(backup_path, db_path, project_root):
    """Restores the database from a backup file, after saving the current database under its
    own safety backup first (tag "before_restore") - so picking the wrong one is itself
    recoverable the same way.

    The caller must not hold an open transaction on `db_path` (the restore needs its write
    lock), and must bring the schema up to date afterwards: a backup from before an update
    comes back with that older schema."""
    backup_now(db_path, project_root, tag="before_restore")
    _sqlite_copy(Path(backup_path), Path(db_path))
