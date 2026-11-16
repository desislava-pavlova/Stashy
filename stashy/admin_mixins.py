"""Pieces every admin page of the project shares (plan 03, S112)."""

import logging

from django.contrib.admin.options import IncorrectLookupParameters

logger = logging.getLogger(__name__)


class HugeNumberInAddressMixin:
    """A value in an admin list's address that the database or the calendar can't hold, like
    `?id__in=` followed by thirty digits, `?date__year=9999` or `?date__iso_year=0`, ends in an
    `OverflowError` or a `ValueError` while the list is built. Django's admin only expects
    `IncorrectLookupParameters` from a filter it can't use, so each of them was a 500 page.
    This turns them into that exception, and Django answers its usual way: back to the
    unfiltered list. It says nothing on that page, so the refusal is logged.

    Only the list's address is covered. The Add page's prefilled values and the bulk-action
    form are other ways in (plan 03, S113 and S114).

    Put it before `admin.ModelAdmin` in every admin class's bases.
    """

    def get_changelist_instance(self, request):
        try:
            return super().get_changelist_instance(request)
        except (OverflowError, ValueError) as error:
            # The names only: what was typed is the visitor's, and can be anything.
            logger.warning(
                "Admin list %s refused a value in its address (%s); parameters: %s",
                request.path,
                type(error).__name__,
                ", ".join(sorted(request.GET)),
            )
            raise IncorrectLookupParameters(error) from error
