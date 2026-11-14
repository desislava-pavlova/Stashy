"""A stash's PIN, the lockout after too many wrong guesses, and its recovery code.

The PIN is the password of the stash's one Django user. The lockout state and the recovery
code's hash live on the stash's one Profile row.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import router, transaction
from django.utils import timezone

from ..models import Profile
from . import StashError

# \Z rather than $, which would also accept a trailing newline; [0-9] rather than \d, which
# would also accept digits from other scripts.
PIN_RE = re.compile(r"^[0-9]{4}\Z")
PIN_MESSAGE = "PIN must be exactly 4 digits."
# The one PIN rule: the forms' PinField and validate_pin() below both use it.
pin_validator = RegexValidator(PIN_RE, PIN_MESSAGE)

LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 5

# No 0/O/1/I - easy to tell apart when handwritten or read off a screen once.
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def validate_pin(pin):
    try:
        pin_validator(pin or "")
    except ValidationError:
        raise StashError(PIN_MESSAGE) from None


def _generate_recovery_code():
    groups = ["".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)


def _hash(secret_value):
    return make_password(secret_value)


def _check(secret_value, hashed):
    return bool(hashed) and check_password(secret_value, hashed)


def new_recovery_code():
    """A fresh recovery code and its hash: (code, hash). Only the hash is ever stored; the code
    itself is shown to the user once."""
    code = _generate_recovery_code()
    return code, _hash(code)


def profile_for_current_stash():
    """The single Profile row in the stash this request's session has selected -
    finance.db_router.StashRouter routes this plain query there. Every stash has exactly one
    user, so there's exactly one Profile, no `request.user` needed to find it."""
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


def clear_failed_attempts(profile):
    """Forgets earlier wrong guesses and any lockout: after a right PIN, or a PIN reset."""
    if profile.failed_pin_attempts or profile.locked_until:
        profile.failed_pin_attempts = 0
        profile.locked_until = None
        profile.save(update_fields=["failed_pin_attempts", "locked_until"])


def _read_again(profile):
    """The caller's Profile may have been read before this request took the stash's write lock
    (request.stashy_profile is read by middleware), and another request may have changed it
    since: counted a try, locked the stash, replaced the recovery code or the PIN. Read again
    inside the lock, the values are current, and no other request can change them until this
    one ends. Without this, requests sent at the same moment all counted from the same old
    number, and most wrong PINs went uncounted (plan 03, S75)."""
    profile.refresh_from_db(fields=["failed_pin_attempts", "locked_until", "recovery_code_hash"])
    profile.user.refresh_from_db(fields=["password"])


def _locked_transaction():
    """The write lock, taken before anything is read (the stash's connection starts every
    transaction with BEGIN IMMEDIATE). Inside a request's own transaction this is a savepoint
    and changes nothing; a view that runs without one (the restore) gets its lock from here, so
    no caller has to remember to open one."""
    return transaction.atomic(using=router.db_for_write(Profile))


def check_pin(profile, pin):
    """True if `pin` is the stash's PIN, for pages that ask for it again while you're logged in
    (Change PIN, a new recovery code, a restore). They count like the PIN screen: a wrong PIN
    is one more try toward the lockout, and a right one clears the earlier tries. While the
    stash is locked out, even the right PIN is refused.

    The PIN is checked with _check(), which never saves. user.check_password() would re-save
    the hash in a newer format after a Django update, and the login is tied to that hash, so
    the next page would log you out (plan 03, S87). Only logging in re-hashes."""
    with _locked_transaction():
        _read_again(profile)
        if is_locked_out(profile):
            return False
        if _check(pin, profile.user.password):
            clear_failed_attempts(profile)
            return True
        register_failed_attempt(profile)
        return False


def reset_pin_with_recovery_code(profile, recovery_code, new_pin):
    """True (and rotates in a fresh recovery code, invalidating the old one) if the recovery
    code matches; False otherwise, counting the same as a wrong PIN toward lockout. Returns
    (ok, new_recovery_code_or_None).

    A right code works even during a lockout, and ends it (the user's call, 14/11/2026, plan 03
    S91): the PIN screen offers this as the way out. It can't be guessed the way a PIN can
    (12 characters of 32), and whoever has it can reset the PIN anyway. A wrong code during a
    lockout is checked and refused but not counted, and doesn't extend the lockout:
    register_failed_attempt() leaves an active lockout alone. (A PIN, by contrast, isn't even
    checked during one.)"""
    with _locked_transaction():
        _read_again(profile)
        if not _check(recovery_code, profile.recovery_code_hash):
            register_failed_attempt(profile)
            return False, None
        new_code, new_hash = new_recovery_code()
        profile.user.set_password(new_pin)
        profile.user.save(update_fields=["password"])
        profile.recovery_code_hash = new_hash
        profile.failed_pin_attempts = 0
        profile.locked_until = None
        profile.save(update_fields=["recovery_code_hash", "failed_pin_attempts", "locked_until"])
        return True, new_code


def regenerate_recovery_code(profile):
    """A fresh recovery code, replacing (invalidating) whatever code existed before - the
    Settings page's self-service version of what a successful "Forgot PIN" reset already does
    to the code, without needing to forget your PIN first. Shown once by the caller; Stashy
    itself never stores or displays it again after this."""
    new_code, profile.recovery_code_hash = new_recovery_code()
    profile.save(update_fields=["recovery_code_hash"])
    return new_code


@dataclass(frozen=True)
class LoginState:
    """What lets someone into a stash: the PIN's hash, the recovery code's hash and the
    lockout. A restore puts today's back over the backup's (registry.restore_stash()).
    `has_profile` is False for a stash with a user and no Profile row yet."""

    username: str
    pin_hash: str
    has_profile: bool = False
    recovery_code_hash: str = ""
    failed_pin_attempts: int = 0
    locked_until: datetime | None = None


def current_login_state(username):
    """The current stash's LoginState, or None if it has no user of that name. A stash's user
    is named after its folder."""
    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        return None
    profile = Profile.objects.filter(user=user).first()
    if profile is None:
        return LoginState(username=username, pin_hash=user.password)
    return LoginState(
        username=username,
        pin_hash=user.password,
        has_profile=True,
        recovery_code_hash=profile.recovery_code_hash,
        failed_pin_attempts=profile.failed_pin_attempts,
        locked_until=profile.locked_until,
    )


def put_back_pin(state):
    """Writes `state`'s PIN hash over the current stash's user. It touches the one column, with
    no read of the others, so it works on a database that still has an older schema: a restore
    does this first, before migrate, because the backup's PIN is the live one until then. The
    user isn't an audited model, so skipping save() hides nothing from the audit log."""
    if state is not None:
        get_user_model().objects.filter(username=state.username).update(password=state.pin_hash)


def put_back_recovery_code_and_lockout(state):
    """Writes `state`'s recovery code and lockout over the current stash's Profile, through
    save(), so the audit log sees what changed (the recovery code's hash shows as "(hidden)").
    Nothing to do for a stash that had no Profile."""
    if state is None or not state.has_profile:
        return
    profile = Profile.objects.filter(user__username=state.username).first()
    if profile is None:
        return
    profile.recovery_code_hash = state.recovery_code_hash
    profile.failed_pin_attempts = state.failed_pin_attempts
    profile.locked_until = state.locked_until
    profile.save(update_fields=["recovery_code_hash", "failed_pin_attempts", "locked_until"])
