"""The one check that a link stays on this site, for the middleware and
the views alike. It imports no model and nothing from `views/`, so any
layer can use it; it must never be loosened to a plain "starts with /"
test."""

from django.utils.http import url_has_allowed_host_and_scheme


def stays_on_this_site(request, url):
    """True if `url` leads to this site and not to another. Django's own
    check also refuses tricks a plain "starts with /" test lets
    through: `//example.com` and `/\\example.com` (browsers read both as
    a link to example.com), or a `javascript:` address. Over https it
    also refuses a plain-http link."""
    return url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
