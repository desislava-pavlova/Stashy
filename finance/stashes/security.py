"""A stash's PIN, the lockout after too many wrong guesses, and its recovery code.

The PIN is the password of the stash's one Django user. The lockout state and the recovery
code's hash live on the stash's one Profile row.
"""

import re
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
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
