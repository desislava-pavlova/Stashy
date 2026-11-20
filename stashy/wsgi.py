"""The WSGI entry point: Waitress serves Stashy via `application`."""

import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stashy.settings.base")

application = get_wsgi_application()
