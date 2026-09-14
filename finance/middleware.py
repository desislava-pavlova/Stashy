from . import regional
from .models import Profile


class RegionalPreferencesMiddleware:
    """Show every page in the logged-in person's own date, number and time zone preferences (D10)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)
        with regional.activate(Profile.for_user(user).preferences):
            return self.get_response(request)
