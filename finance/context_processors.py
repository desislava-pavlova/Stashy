from . import regional


def regional_preferences(request):
    """The active preferences as {{ regional }}, e.g. for the date picker's format in base.html."""
    return {"regional": regional.current()}


def stash_owner(request):
    """The logged-in stash's owner name (L1), for base.html's sidebar footer on every page -
    a context processor rather than each view passing it along by hand. Reuses the Profile
    RegionalPreferencesMiddleware already fetched this request (`request.stashy_profile`)
    instead of querying for it again."""
    profile = getattr(request, "stashy_profile", None)
    if profile is None:
        return {}
    return {"stash_owner_name": profile.owner_name, "stash_owner_possessive": profile.owner_possessive}
