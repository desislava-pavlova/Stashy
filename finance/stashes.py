"""Multi-stash login (Phase 12; rewritten 2026-09-15 to switch live, no restart needed): each
"stash" is a fully separate SQLite database under stashes/<name>/db.sqlite3, with its own
backups/ folder next to it. Which stash a request's queries land in is decided per-request by
finance.db_router.StashRouter off the current session (finance.middleware.
StashSelectionMiddleware) - every stash is registered as a real Django connection alias, named
after the stash itself, via ensure_registered() below, so the router always has something real
to point at.

A stash's existence *is* its folder existing: the picker lists stashes by scanning
directories, since choosing one is what puts a database in play for the session at all -
before that, nothing can query a "list of stashes" the way it could query a normal table.
"""
import re
import secrets
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.models import signals as model_signals
from django.utils import timezone

from . import regional

STASHES_DIR_NAME = "stashes"
BACKUPS_DIR_NAME = "backups"

PIN_RE = re.compile(r"^\d{4}$")

LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 5

# L3: a browser cookie remembering only which stash was last used (never the PIN) - separate
# from the Django session, which logout() flushes, so Stashy can still land back on the right
# stash's PIN screen right after logging out. ~400 days is the longest modern browsers keep a
# cookie's Max-Age anyway.
LAST_STASH_COOKIE = "stashy_last_stash"
LAST_STASH_COOKIE_AGE = 60 * 60 * 24 * 400

# No 0/O/1/I - easy to tell apart when handwritten or read off a screen once.
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class StashError(Exception):
    """A stash operation couldn't be completed - message is safe to show the user directly."""


def stashes_dir():
    return Path(settings.BASE_DIR) / STASHES_DIR_NAME


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
    itself, if it isn't already - live stash switching needs every stash a session might pick
    to be a real registered connection for finance.db_router.StashRouter to point requests at,
    not just a file on disk. Safe to call repeatedly (a no-op once registered). Called at
    process start for every stash that already exists (finance/apps.py's ready()), again by
    StashSelectionMiddleware the moment a session picks one (covers a stash created after this
    process started), and directly by create_stash() below for its own brand new stash."""
    if name in connections.databases:
        return
    template = deepcopy(connections.databases["default"])
    template["NAME"] = str(stash_db_path(name))
    connections.databases[name] = template


def possessive(name):
    """"Alex" -> "Alex's"; "The Smiths" -> "The Smiths'" (L1: a name already ending in s
    gets just the apostrophe). Shared with Profile.owner_possessive, for the views here that
    only have the plain owner name string (stash creation, before there's a Profile row)."""
    suffix = "'" if name.endswith(("s", "S")) else "'s"
    return f"{name}{suffix}"


def _generate_recovery_code():
    groups = ["".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)


@contextmanager
def _audit_signals_suspended():
    """The audit log's signal handlers always write to whatever finance.db_router.StashRouter
    currently resolves for this thread, which would be wrong here - that's this request's own
    session's stash (or `default`, the picker's placeholder, if none is selected yet), not the
    brand new stash being built. There's nothing to audit yet in a database that was empty a
    moment ago anyway, so this just turns the signals off for the moment."""
    from audit import signals as audit_signals

    uids = ("audit.pre_save", "audit.post_save", "audit.pre_delete", "audit.post_delete", "audit.m2m_changed")
    for uid in uids:
        model_signals.pre_save.disconnect(dispatch_uid=uid)
        model_signals.post_save.disconnect(dispatch_uid=uid)
        model_signals.pre_delete.disconnect(dispatch_uid=uid)
        model_signals.post_delete.disconnect(dispatch_uid=uid)
        model_signals.m2m_changed.disconnect(dispatch_uid=uid)
    try:
        yield
    finally:
        model_signals.pre_save.connect(audit_signals.remember_old_values, dispatch_uid="audit.pre_save")
        model_signals.post_save.connect(audit_signals.log_save, dispatch_uid="audit.post_save")
        model_signals.pre_delete.connect(audit_signals.remember_deleted, dispatch_uid="audit.pre_delete")
        model_signals.post_delete.connect(audit_signals.log_delete, dispatch_uid="audit.post_delete")
        model_signals.m2m_changed.connect(audit_signals.log_m2m, dispatch_uid="audit.m2m_changed")


def validate_pin(pin):
    if not PIN_RE.match(pin or ""):
        raise StashError("PIN must be exactly 4 digits.")


def stash_choices():
    """[(folder_name, owner_name), ...], alphabetized by owner name, for the picker's dropdown
    (L3) - reading each stash's own Profile rather than just its folder name, since the folder
    name (generated once from the owner name and never shown, L1) may no longer match if the
    owner name were ever regenerated by hand."""
    from .models import Profile

    choices = []
    for name in list_stashes():
        ensure_registered(name)
        profile = Profile.objects.using(name).first()
        owner_name = (profile.owner_name if profile else "") or name
        choices.append((name, owner_name))
    return sorted(choices, key=lambda pair: pair[1].lower())


def owner_name_taken(owner_name, exclude_folder=None):
    """True if another stash already has this owner name (case-insensitive, L1) - checked by
    opening each stash's own Profile, since owner names live one-per-database, not in a single
    table a normal unique constraint could cover."""
    from .models import Profile

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
    """"Alex" -> "alex"; "The Smiths" -> "the-smiths" (L1: generated once, never shown, and
    made unique below if another stash's owner name happens to slugify to the same thing)."""
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
    (hashed) fingerprint is kept.

    Registers the new stash as a permanent connection alias (ensure_registered()) rather than
    a throwaway one - the whole point of the live-switching rewrite is that the caller's own
    session can select this stash and use it immediately, with nothing to restart."""
    owner_name = validate_owner_name(owner_name)
    validate_pin(pin)
    folder_name = _folder_name_from_owner(owner_name)

    stash_path(folder_name).mkdir(parents=True, exist_ok=True)
    stash_backups_dir(folder_name).mkdir(exist_ok=True)
    ensure_registered(folder_name)
    call_command("migrate", database=folder_name, verbosity=0, interactive=False, run_syncdb=True)

    from django.contrib.auth import get_user_model

    from .models import Profile

    recovery_code = _generate_recovery_code()
    with _audit_signals_suspended():
        user = get_user_model().objects.db_manager(folder_name).create_superuser(
            username=folder_name, email="", password=pin
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
            recovery_code_hash=_hash(recovery_code),
        )

    return folder_name, recovery_code


def _hash(secret_value):
    from django.contrib.auth.hashers import make_password

    return make_password(secret_value)


def _check(secret_value, hashed):
    from django.contrib.auth.hashers import check_password

    return bool(hashed) and check_password(secret_value, hashed)


def profile_for_current_stash():
    """The single Profile row in whatever stash this request's session has selected -
    finance.db_router.StashRouter routes this plain, unqualified query there automatically.
    Every stash has exactly one user, so there's exactly one Profile, no `request.user`
    needed to find it."""
    from .models import Profile

    return Profile.objects.select_related("user").first()


def is_locked_out(profile):
    return bool(profile.locked_until) and profile.locked_until > timezone.now()


def lockout_seconds_remaining(profile):
    if not is_locked_out(profile):
        return 0
    return max(0, int((profile.locked_until - timezone.now()).total_seconds()))


def register_failed_attempt(profile):
    """Counts a wrong PIN/recovery code. Locks out at the threshold rather than extending an
    already-active lockout indefinitely if someone keeps guessing while locked."""
    if is_locked_out(profile):
        return
    profile.failed_pin_attempts += 1
    if profile.failed_pin_attempts >= LOCKOUT_THRESHOLD:
        profile.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_MINUTES)
        profile.failed_pin_attempts = 0
    profile.save(update_fields=["failed_pin_attempts", "locked_until"])


def register_successful_login(profile):
    if profile.failed_pin_attempts or profile.locked_until:
        profile.failed_pin_attempts = 0
        profile.locked_until = None
        profile.save(update_fields=["failed_pin_attempts", "locked_until"])


def reset_pin_with_recovery_code(profile, recovery_code, new_pin):
    """True (and rotates in a fresh recovery code, invalidating the old one) if the recovery
    code matches; False otherwise, counting the same as a wrong PIN toward lockout. Returns
    (ok, new_recovery_code_or_None)."""
    if is_locked_out(profile):
        return False, None
    if not _check(recovery_code, profile.recovery_code_hash):
        register_failed_attempt(profile)
        return False, None
    new_recovery_code = _generate_recovery_code()
    profile.user.set_password(new_pin)
    profile.user.save(update_fields=["password"])
    profile.recovery_code_hash = _hash(new_recovery_code)
    profile.failed_pin_attempts = 0
    profile.locked_until = None
    profile.save(update_fields=["recovery_code_hash", "failed_pin_attempts", "locked_until"])
    return True, new_recovery_code


def regenerate_recovery_code(profile):
    """A fresh recovery code, replacing (invalidating) whatever code existed before - the
    Settings page's self-service version of what a successful "Forgot PIN" reset already does
    to the code, without needing to forget your PIN first. Shown once by the caller; Stashy
    itself never stores or displays it again after this."""
    new_recovery_code = _generate_recovery_code()
    profile.recovery_code_hash = _hash(new_recovery_code)
    profile.save(update_fields=["recovery_code_hash"])
    return new_recovery_code


def resolve_stash_name(name_or_owner):
    """The manage.py reset_pin CLI accepts either the internal folder name or the owner name
    (L1's checklist item - the folder name is never shown on screen, so someone using this
    would only know the owner name). Tries the folder name first since it's a plain existence
    check with no need to open every stash; falls back to a case-insensitive owner-name match."""
    if stash_exists(name_or_owner):
        return name_or_owner
    target = (name_or_owner or "").strip().lower()
    for folder_name, owner_name in stash_choices():
        if owner_name.strip().lower() == target:
            return folder_name
    return None


def reset_pin_directly(name_or_owner, new_pin):
    """The manage.py reset_pin CLI last resort, for when the recovery code is lost too -
    operates on the named stash directly rather than whatever a session might currently have
    selected, since the locked-out stash isn't necessarily that one. Running this already
    requires file-level access to the stash, so it needs no authentication of its own."""
    validate_pin(new_pin)
    name = resolve_stash_name(name_or_owner)
    if name is None:
        raise StashError(f'No stash named "{name_or_owner}".')

    ensure_registered(name)
    from django.contrib.auth import get_user_model

    from .models import Profile

    # User isn't a finance-app model (not audit-tracked) and .update() sends no signals at
    # all, so neither of these needs the audit signals suspended the way create_stash() does -
    # this command only ever runs standalone (manage.py), never alongside the live server, so
    # there's no risk of it racing another request's own audit entries either.
    user = get_user_model().objects.db_manager(name).get(username=name)
    user.set_password(new_pin)
    user.save(using=name, update_fields=["password"])
    Profile.objects.using(name).filter(user=user).update(failed_pin_attempts=0, locked_until=None)
