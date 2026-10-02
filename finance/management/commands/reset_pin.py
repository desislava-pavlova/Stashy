"""manage.py reset_pin <name> [--pin 1234]: the last-resort way into a stash whose PIN *and*
recovery code have both been lost (Phase 12). Running this already requires file-level access
to the stash's own folder, so it adds no exposure beyond what that access already grants -
there's deliberately no confirmation prompt or extra check here.
"""

import getpass

from django.core.management.base import BaseCommand, CommandError

from finance import stashes


class Command(BaseCommand):
    help = "Reset a stash's PIN directly - the last resort when both the PIN and its recovery code are lost."

    def add_arguments(self, parser):
        parser.add_argument("name", help="The stash's owner name, or its folder under stashes/ if you know it.")
        parser.add_argument("--pin", help="The new 4-digit PIN. Omit to be prompted (input is hidden).")

    def handle(self, *args, **options):
        name = options["name"]
        pin = options["pin"] or getpass.getpass("New 4-digit PIN: ")
        try:
            stashes.reset_pin_directly(name, pin)
        except stashes.StashError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f'PIN reset for "{name}".'))
