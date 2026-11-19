"""Pieces every admin page of the project shares.

What this module must not do: change what the admin shows or allows to a
person using its pages. It only answers a request no browser would send
(a hand-typed address, a hand-made form post) politely instead of with
a 500 page.
"""

import logging

from django.contrib import messages
from django.contrib.admin import helpers
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import ValidationError
from django.db import connections, models
from django.db.models.constants import LOOKUP_SEP

logger = logging.getLogger(__name__)

# Lookups no admin page ever sends. Typed into an address, a bad
# pattern is an error from inside SQLite, and a good one can run for
# minutes on a long table; `range` needs exactly two values, and an
# address gives it one string, which Django splits into its letters.
REFUSED_LOOKUPS = frozenset({"regex", "iregex", "range"})

# What no page sends and SQLite refuses: a filter value of thousands of
# characters (an `id__in` of 30,000 ids is "too many SQL variables"),
# and a search of 50,000 characters or thousands of words ("LIKE or GLOB
# pattern too complex", "expression tree is too large").
MAX_LOOKUP_VALUE_LENGTH = 1000
MAX_SEARCH_WORD_LENGTH = 200
MAX_SEARCH_WORDS = 50


def names_for_log(names, limit=200):
    """The names of a request's parameters as one line for the log. Each
    is written with `ascii()`, so a name holding a line break or a
    terminal control character can't forge a log line, and the whole is
    cut short: what was typed is the visitor's, and can be anything."""
    text = ", ".join(ascii(name) for name in sorted(names))
    return text if len(text) <= limit else f"{text[:limit]}..."


def key_fits(key_field, value, db):
    """Whether `value` can be looked up as a value of `key_field` (a
    model's primary key, or the field a foreign key points to) on the
    database `db`: it is a number of the right kind, and the database
    can hold it. A lookup by anything else is a ValueError or an
    OverflowError, which Django's admin doesn't expect from a lookup.

    A key that is itself a relation (a primary key that is a one-to-one
    field) is not followed to the number at its end: no model here has
    one, and it would need that step first."""
    try:
        number = key_field.to_python(value)
    except ValidationError:
        return False
    if not isinstance(key_field, models.IntegerField):
        return True
    lowest, highest = connections[db].ops.integer_field_range(key_field.get_internal_type())
    return number is not None and lowest <= number <= highest


class SafeAutocompleteSelect(AutocompleteSelect):
    """Django's autocomplete dropdown, minus the 500 page for a selected
    value that can't be a key. The widget looks its selected value up
    with `pk__in=[...]` while the form is drawn, which is after the view
    has returned, so no `try` in the view can catch it. It is reached by
    the Add page's prefilled address (`?account=zz`) and by a form that
    is posted with such a value and drawn again with its errors. A value
    left out is just not selected, as if it had been an id that doesn't
    exist."""

    def optgroups(self, name, value, attr=None):
        key_field = self.field.target_field
        db = self.choices.queryset.using(self.db).db
        usable = [candidate for candidate in value if key_fits(key_field, candidate, db)]
        return super().optgroups(name, usable, attr)


class HandMadeRequestMixin:
    """Requests no page of the admin sends, which Django's admin turns
    into a 500 page, and what answers them here:

    - A value in a list's address that the database or the calendar
      can't hold, like `?id__in=` followed by thirty digits,
      `?date__year=9999` or `?date__iso_year=0`, ends in an
      `OverflowError` or a `ValueError` while the list is built.
      Django's admin only expects `IncorrectLookupParameters` from a
      filter it can't use, so this turns them into that exception, and
      Django answers its usual way: back to the unfiltered list. It
      says nothing on that page, so the refusal is logged.
    - A `regex`, `iregex` or `range` lookup in a list's address is
      refused like any lookup the admin doesn't allow (a 400 page): a
      pattern that isn't valid is an error from inside SQLite, and only
      once the table has a row, and a range is never given its two
      values.
    - A filter value of over a thousand characters is refused the same
      way, and so is a search (a list's `q`, an autocomplete's `term`)
      with a word of over two hundred characters or more than fifty
      words: it finds nothing. SQLite can't run them.
    - An empty `_to_field` in the address of a change or delete page is
      read as not given. Django's own `get_object()` asks the model for
      a field called "" instead.
    - An autocomplete dropdown given a value that can't be a key (see
      `SafeAutocompleteSelect`). Only foreign keys are covered, the one
      kind of autocomplete field the admins use.
    - A bulk action posted with a selected id that can't be a key (the
      boxes on the list only send real ones). It is answered with a
      warning and no change, the way Django answers an action with
      nothing selected, and the list is shown again.

    Put it before `admin.ModelAdmin` in every admin class's bases. It
    is written for `ModelAdmin`: an inline admin would need its own
    copy of the dropdown and lookup guards.
    """

    def get_changelist_instance(self, request):
        try:
            return super().get_changelist_instance(request)
        except (OverflowError, ValueError) as error:
            # The names only, never the values.
            logger.warning(
                "Admin list %s refused a value in its address (%s); parameters: %s",
                request.path,
                type(error).__name__,
                names_for_log(request.GET),
            )
            raise IncorrectLookupParameters(error) from error

    def lookup_allowed(self, lookup, value, request):
        refused = lookup.split(LOOKUP_SEP)[-1] in REFUSED_LOOKUPS
        too_long = len(str(value)) > MAX_LOOKUP_VALUE_LENGTH
        if refused or too_long:
            return False
        return super().lookup_allowed(lookup, value, request)

    def get_search_results(self, request, queryset, search_term):
        words = search_term.split()
        if len(words) > MAX_SEARCH_WORDS or any(len(word) > MAX_SEARCH_WORD_LENGTH for word in words):
            return queryset.none(), False
        return super().get_search_results(request, queryset, search_term)

    def get_object(self, request, object_id, from_field=None):
        # `from_field or None`: Django's own callers hand over "" for an
        # empty `_to_field`, which this method reads as a field's name.
        return super().get_object(request, object_id, from_field or None)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if "widget" not in kwargs and db_field.name in self.get_autocomplete_fields(request):
            kwargs["widget"] = SafeAutocompleteSelect(db_field, self.admin_site, using=kwargs.get("using"))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def response_action(self, request, queryset, **kwargs):
        # **kwargs: Django 6.1 passes `action_location`, older versions
        # didn't, so whatever comes is handed on untouched.
        selected = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
        if not all(key_fits(self.opts.pk, selected_id, queryset.db) for selected_id in selected):
            # The count only: what was sent is the visitor's.
            logger.warning(
                "Admin list %s refused a bulk action with ids that can't be keys (%d selected)",
                request.path,
                len(selected),
            )
            self.message_user(
                request,
                "Some of the selected items could not be found. No items have been changed.",
                messages.WARNING,
            )
            return None
        return super().response_action(request, queryset, **kwargs)
