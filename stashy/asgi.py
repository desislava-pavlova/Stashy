"""The ASGI entry point, as Django generates it. Nothing uses it:
Waitress serves Stashy through wsgi.py.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stashy.settings.base")

application = get_asgi_application()
