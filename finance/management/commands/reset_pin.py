"""manage.py reset_pin <name> [--pin 1234]: the last-resort way into a stash whose PIN *and*
recovery code have both been lost (Phase 12). Running this already requires file-level access
to the stash's own folder, so it adds no exposure beyond what that access already grants -
there's deliberately no confirmation prompt or extra check here.
"""

import getpass
import sys

from django.core.management.base import BaseCommand, CommandError

from finance.stashes import StashError, registry
from finance.text import possessive

# From Python 3.14, getpass can show a * for each key, so you see how many digits you've typed
# without seeing them. Stashy.bat still installs on 3.12 and 3.13, where it shows nothing.
GETPASS_SHOWS_STARS = sys.version_info >= (3, 14)


def ask_for_pin():
    """The new PIN, typed twice like every PIN box on the website: the stars show how many
    digits you typed, not which ones, and a typo here would lock you out again."""
    star = {"echo_char": "*"} if GETPASS_SHOWS_STARS else {}
    pin = getpass.getpass("New 4-digit PIN: ", **star)
    if getpass.getpass("Type it again: ", **star) != pin:
        raise CommandError("PINs don't match. Nothing was changed.")
    return pin


class Command(BaseCommand):
    help = "Reset a stash's PIN directly - the last resort when both the PIN and its recovery code are lost."

    def add_arguments(self, parser):
        parser.add_argument("name", help="The stash's owner name, or its folder under stashes/ if you know it.")
        parser.add_argument(
            "--pin",
            help=(
                "The new 4-digit PIN. Leave it out to be asked for it twice (each digit shows as *, from "
                "Python 3.14). A PIN typed here is saved in your terminal's history."
            ),
        )

    def handle(self, *args, **options):
        name = options["name"]
        try:
            # Before asking for a PIN, so a mistyped name doesn't cost two prompts first.
            registry.require_stash_name(name)
            pin = options["pin"] or ask_for_pin()
            folder, owner_name = registry.reset_pin_directly(name, pin)
        except StashError as error:
            raise CommandError(str(error)) from error
        # Which stash changed, not just the name that was typed: a name could match one
        # stash's folder and another stash's owner.
        self.stdout.write(self.style.SUCCESS(f'PIN reset for {possessive(owner_name)} stash (folder "{folder}").'))
