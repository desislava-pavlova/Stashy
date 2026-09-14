from . import regional


def regional_preferences(request):
    """The active preferences as {{ regional }}, e.g. for the date picker's format in base.html."""
    return {"regional": regional.current()}
