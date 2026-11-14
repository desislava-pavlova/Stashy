"""Where the stashes are: their folders on disk and their Django connection aliases.

Which stash a request's queries land in is decided per request by
finance.db_router.StashRouter, from the session (finance.middleware.StashSelectionMiddleware).
Every stash is registered as a real Django connection alias, named after the stash itself, by
ensure_registered() below, so the router always has something real to point at.

A stash's existence *is* its folder existing: the picker lists stashes by scanning
directories, since before one is chosen there's no database to query a "list of stashes" from.
"""

import re
import sqlite3
from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections, transaction

from audit.context import audit_suspended, system_action

from .. import backup, db_router, regional
from ..models import Profile, name_key
from . import StashError, security

BACKUPS_DIR_NAME = "backups"

# What a refused restore says (plan 03, S88). Each ends with the reassurance: the refusal comes
# before anything is replaced.
BACKUP_DAMAGED = (
    "That backup file couldn't be read, so it may be damaged. Nothing was restored. Your stash is just as it was."
)
BACKUP_EMPTY = (
    "That backup has no one who can log in to this stash: it is empty, was cut short, or belongs to "
    "another stash. Nothing was restored. Your stash is just as it was."
)
BACKUP_WITHOUT_SETTINGS = (
    "That backup is missing your settings and recovery code, so restoring it would lose them. "
    "Nothing was restored. Your stash is just as it was."
)

# A browser cookie remembering only which stash was last used (never the PIN). It's separate
# from the Django session, which logout() flushes, so Stashy can still land back on the right
# stash's PIN screen right after logging out. ~400 days is the longest modern browsers keep a
# cookie's Max-Age anyway.
LAST_STASH_COOKIE = "stashy_last_stash"
LAST_STASH_COOKIE_AGE = 60 * 60 * 24 * 400


def stashes_dir():
    return Path(settings.STASHES_DIR)


def stash_path(name):
    return stashes_dir() / name


def stash_db_path(name):
    return stash_path(name) / "db.sqlite3"


def stash_backups_dir(name):
    return stash_path(name) / BACKUPS_DIR_NAME


def stash_exists(name):
    return stash_db_path(name).exists()


def list_stashes():
    """Every stash's name, alphabetically - a stash's existence *is* its own folder (holding
    a db.sqlite3) existing, so this is a directory scan, not a database query."""
    directory = stashes_dir()
    if not directory.exists():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_dir() and (p / "db.sqlite3").exists())


def ensure_registered(name):
    """Registers `name`'s db.sqlite3 as a real Django connection alias, named after the stash
    itself, if it isn't already - StashRouter can only point a request at a registered
    connection, not at a file on disk. Safe to call repeatedly (a no-op once registered).
    Called at process start for every stash that already exists (finance/apps.py's ready()),
    again by StashSelectionMiddleware the moment a session picks one (covers a stash created
    after this process started), and directly by create_stash() below for its own new stash."""
    if name in connections.databases:
        return
    template = deepcopy(connections.databases["default"])
    template["NAME"] = str(stash_db_path(name))
    # Django wraps a view in atomic() for *every* alias with ATOMIC_REQUESTS, so leaving it on
    # here would take a write lock on every stash for every request. Only the stash this request
    # actually uses gets a transaction - see finance.middleware.StashAtomicRequestMiddleware.
    template["ATOMIC_REQUESTS"] = False
    connections.databases[name] = template


@contextmanager
def _system_change(name, reason):
    """For changes to stash `name` made outside a request (manage.py, a restore). Outside a
    request nothing is the "current stash", and the audit log writes to the current one, so
    this makes `name` current. The changes are one transaction on that stash, and the audit
    log names them as automatic, with `reason`."""
    with db_router.use_stash(name), transaction.atomic(using=name), system_action(reason):
        yield


def _problem_with_backup(connection, name, needs_a_profile):
    """Why the backup behind `connection` can't be restored, in plain words, or None."""
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        return BACKUP_DAMAGED
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "auth_user" not in tables:
        return BACKUP_EMPTY
    row = connection.execute("SELECT id FROM auth_user WHERE username = ?", (name,)).fetchone()
    if row is None:
        return BACKUP_EMPTY
    if needs_a_profile and not _has_profile(connection, tables, user_id=row[0]):
        return BACKUP_WITHOUT_SETTINGS
    return None


def _has_profile(connection, tables, user_id):
    if "finance_profile" not in tables:  # a backup from before the table existed
        return False
    found = connection.execute("SELECT 1 FROM finance_profile WHERE user_id = ?", (user_id,)).fetchone()
    return found is not None


def _check_backup_can_be_restored(name, backup_path, needs_a_profile):
    """Raises StashError, before anything is replaced, for a backup that would leave the stash
    unusable (plan 03, S88). A backup cut short (a crash while it was written leaves a file
    with no tables, often a 0-byte one) replaces the live database with nothing: no user is
    left to log in, and today's PIN has nowhere to go back to. The only way out would be
    copying the safety backup over it by hand.

    The file is opened read-only, so a damaged one can't be changed by looking at it. A backup
    with no Profile row is refused only if today's stash has one: the recovery code and the
    lockout would have no row to go back into, against what a restore promises.

    This is raw SQL on purpose: the backup isn't a registered connection, and an old backup's
    tables can differ from today's models, so the ORM can't be pointed at it."""
    uri = f"{Path(backup_path).resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            problem = _problem_with_backup(connection, name, needs_a_profile)
    except sqlite3.Error:
        problem = BACKUP_DAMAGED
    if problem:
        raise StashError(problem)


def restore_stash(name, backup_path):
    """Restore stash `name` from one of its backups and bring it onto today's schema.

    A backup taken before an update has fewer migrations applied, and the running server only
    migrates at launch - so the restore migrates straight away instead of leaving pages to
    crash until the next start. The caller must not hold a transaction on this stash
    (backup_restore is non-atomic for that reason).

    A backup that is empty, damaged, from another stash, or without the settings that today's
    stash has is refused with a StashError before the live database is touched.

    A restore brings back data, not old secrets (the user's call, 14/11/2026, plan 03 Q3): the
    PIN, the recovery code and the lockout stay as they are today. Otherwise a recovery code
    replaced because someone saw it would work again, and a lockout or try count saved inside
    the backup would come back too.

    The backup's PIN is the live one from the moment its file replaces the database, so today's
    PIN goes back first, before migrate (which takes a while on an old backup, and can fail).
    The recovery code and lockout go back after it, even if migrate fails; they need the
    current schema, which an old backup doesn't have until migrate has run."""
    ensure_registered(name)
    with db_router.use_stash(name):
        todays_login = security.current_login_state(name)
    needs_a_profile = todays_login is not None and todays_login.has_profile
    _check_backup_can_be_restored(name, backup_path, needs_a_profile)
    backup.restore_from(backup_path, stash_db_path(name), stash_path(name))
    # This thread's connection reopens on its next query; other threads' connections are still
    # valid, because SQLite's backup API rewrites the file in a way they notice.
    connections[name].close()
    with db_router.use_stash(name):
        security.put_back_pin(todays_login)
    try:
        call_command("migrate", database=name, interactive=False, verbosity=0)
    finally:
        with _system_change(name, "Kept today's PIN, recovery code and lockout after a restore"):
            security.put_back_recovery_code_and_lockout(todays_login)


def stash_choices():
    """[(folder_name, owner_name), ...], alphabetized by owner name, for the picker's dropdown -
    reading each stash's own Profile rather than just its folder name, since the folder name
    (generated once from the owner name and never shown) may no longer match if the owner
    name were ever changed by hand."""
    choices = []
    for name in list_stashes():
        ensure_registered(name)
        profile = Profile.objects.using(name).first()
        owner_name = (profile.owner_name if profile else "") or name
        choices.append((name, owner_name))
    return sorted(choices, key=lambda pair: pair[1].lower())


def owner_name_taken(owner_name, exclude_folder=None):
    """True if another stash already has this owner name (case-insensitive) - checked by
    opening each stash's own Profile, since owner names live one-per-database, not in a single
    table a normal unique constraint could cover."""
    target = name_key(owner_name)
    for name in list_stashes():
        if name == exclude_folder:
            continue
        ensure_registered(name)
        profile = Profile.objects.using(name).first()
        if profile and name_key(profile.owner_name) == target:
            return True
    return False


def validate_owner_name(owner_name, exclude_folder=None):
    name = (owner_name or "").strip()
    if not name:
        raise StashError("Enter whose stash this is.")
    if len(name) > 60:
        raise StashError("That's too long - 60 characters max.")
    if owner_name_taken(name, exclude_folder=exclude_folder):
        raise StashError(f'A stash for "{name}" already exists.')
    return name


def _folder_name_from_owner(owner_name):
    """ "Alex" -> "alex"; "The Smiths" -> "the-smiths" (generated once, never shown, and made
    unique below if another stash's owner name happens to slugify to the same thing)."""
    slug = re.sub(r"[^a-z0-9]+", "-", owner_name.strip().lower()).strip("-")
    base = slug or "stash"
    candidate = base
    suffix = 2
    while stash_exists(candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def create_stash(owner_name, pin):
    """Builds stashes/<generated-folder-name>/ (a fresh, fully migrated db.sqlite3 plus its
    own backups/ folder), a single superuser with `pin` as its password, and a Profile row
    holding `owner_name` and a freshly generated recovery code's hash. Returns (folder_name,
    recovery_code) - the code in the clear, the only time it's ever available, since only its
    hash is kept.

    The new stash is registered as a permanent connection alias, so the caller's own session
    can select it and use it straight away, with nothing to restart."""
    owner_name = validate_owner_name(owner_name)
    security.validate_pin(pin)
    folder_name = _folder_name_from_owner(owner_name)

    stash_path(folder_name).mkdir(parents=True, exist_ok=True)
    stash_backups_dir(folder_name).mkdir(exist_ok=True)
    ensure_registered(folder_name)
    call_command("migrate", database=folder_name, verbosity=0, interactive=False, run_syncdb=True)

    recovery_code, recovery_code_hash = security.new_recovery_code()
    # The audit log writes to the stash this request's session has selected (or the picker's
    # empty placeholder), not to the new one being built here - and a database that was empty
    # a moment ago has nothing worth auditing yet.
    with audit_suspended():
        user = (
            get_user_model()
            .objects.db_manager(folder_name)
            .create_superuser(username=folder_name, email="", password=pin)
        )
        detected = regional.detect_from_computer()
        Profile.objects.using(folder_name).create(
            user=user,
            owner_name=owner_name,
            welcome_step=0,
            time_zone=detected.time_zone,
            date_format=detected.date_format,
            number_format=detected.number_format,
            first_day_of_week=detected.first_day_of_week,
            recovery_code_hash=recovery_code_hash,
        )

    return folder_name, recovery_code


def resolve_stash_name(name_or_owner):
    """The manage.py reset_pin command accepts either the internal folder name or the owner
    name (the folder name is never shown on screen, so someone using it may only know the owner
    name), either one in any upper/lower case. The folder names come from list_stashes(), not
    from asking Windows whether a folder exists: Windows would find "Alex" for the folder
    "alex", and the stash's user is named after the folder's exact spelling."""
    target = name_key(name_or_owner)
    for folder_name in list_stashes():
        if name_key(folder_name) == target:
            return folder_name
    for folder_name, owner_name in stash_choices():
        if name_key(owner_name) == target:
            return folder_name
    return None


def require_stash_name(name_or_owner):
    """The folder name of the stash `name_or_owner` means, or a StashError saying there's none."""
    name = resolve_stash_name(name_or_owner)
    if name is None:
        raise StashError(f'No stash named "{name_or_owner}".')
    return name


def reset_pin_directly(name_or_owner, new_pin):
    """The manage.py reset_pin last resort, for when the recovery code is lost too - works on
    the named stash directly rather than whatever a session might have selected, since the
    locked-out stash isn't necessarily that one. Running this already requires file-level
    access to the stash, so it needs no authentication of its own.

    Returns (folder name, owner name), so the command can say which stash it changed."""
    security.validate_pin(new_pin)
    name = require_stash_name(name_or_owner)

    ensure_registered(name)
    # The PIN is the user's password, which isn't audited. The lockout lives on the audited
    # Profile, so it's cleared through save(), as an automatic change the log can name.
    with _system_change(name, "PIN reset with manage.py reset_pin"):
        user = get_user_model().objects.filter(username=name).first()
        if user is None:
            raise StashError(f'The stash in the folder "{name}" has no user of that name, so its PIN cannot be reset.')
        user.set_password(new_pin)
        user.save(update_fields=["password"])
        # Every stash create_stash() makes has a Profile, but one copied in by hand may not.
        # The PIN is what this command is for, so a missing Profile only skips the lockout.
        profile = Profile.objects.filter(user=user).first()
        if profile is not None:
            security.clear_failed_attempts(profile)
    return name, (profile.owner_name if profile else "") or name
