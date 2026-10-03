"""Where the stashes are: their folders on disk and their Django connection aliases.

Which stash a request's queries land in is decided per request by
finance.db_router.StashRouter, from the session (finance.middleware.StashSelectionMiddleware).
Every stash is registered as a real Django connection alias, named after the stash itself, by
ensure_registered() below, so the router always has something real to point at.

A stash's existence *is* its folder existing: the picker lists stashes by scanning
directories, since before one is chosen there's no database to query a "list of stashes" from.
"""

import re
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections

from audit.context import audit_suspended

from .. import backup, regional
from ..models import Profile
from . import StashError, security

BACKUPS_DIR_NAME = "backups"

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


def restore_stash(name, backup_path):
    """Restore stash `name` from one of its backups and bring it onto today's schema.

    A backup taken before an update has fewer migrations applied, and the running server only
    migrates at launch - so the restore migrates straight away instead of leaving pages to
    crash until the next start. The caller must not hold a transaction on this stash
    (backup_restore is non-atomic for that reason)."""
    ensure_registered(name)
    backup.restore_from(backup_path, stash_db_path(name), stash_path(name))
    # This thread's connection reopens on its next query; other threads' connections are still
    # valid, because SQLite's backup API rewrites the file in a way they notice.
    connections[name].close()
    call_command("migrate", database=name, interactive=False, verbosity=0)


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
    target = owner_name.strip().lower()
    for name in list_stashes():
        if name == exclude_folder:
            continue
        ensure_registered(name)
        profile = Profile.objects.using(name).first()
        if profile and profile.owner_name.strip().lower() == target:
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
    name). Tries the folder name first, since that's a plain existence check with no need to
    open every stash; falls back to a case-insensitive owner-name match."""
    if stash_exists(name_or_owner):
        return name_or_owner
    target = (name_or_owner or "").strip().lower()
    for folder_name, owner_name in stash_choices():
        if owner_name.strip().lower() == target:
            return folder_name
    return None


def reset_pin_directly(name_or_owner, new_pin):
    """The manage.py reset_pin last resort, for when the recovery code is lost too - works on
    the named stash directly rather than whatever a session might have selected, since the
    locked-out stash isn't necessarily that one. Running this already requires file-level
    access to the stash, so it needs no authentication of its own."""
    security.validate_pin(new_pin)
    name = resolve_stash_name(name_or_owner)
    if name is None:
        raise StashError(f'No stash named "{name_or_owner}".')

    ensure_registered(name)
    # User isn't a finance model (not audited) and .update() sends no signals at all, so
    # neither of these needs the audit log suspended the way create_stash() does. This command
    # only runs standalone (manage.py), so it can't race a request's own audit entries either.
    user = get_user_model().objects.db_manager(name).get(username=name)
    user.set_password(new_pin)
    user.save(using=name, update_fields=["password"])
    Profile.objects.using(name).filter(user=user).update(failed_pin_attempts=0, locked_until=None)
