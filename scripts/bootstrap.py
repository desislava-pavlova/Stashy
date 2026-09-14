"""Set up (first run) and launch Stashy. Started by Stashy.bat using Stashy's own Python environment.

Every step is safe to repeat: anything already done is skipped, so the same
double-click works for first-time setup and for everyday launching.
"""
import hashlib
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _configured_port():
    """Read PORT=... from .env directly (no dependency needed yet - this runs before
    install_requirements(), so python-decouple may not be installed on a brand new machine)."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("PORT="):
                try:
                    return int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
    return 8000


HOST, PORT = "127.0.0.1", _configured_port()
URL = f"http://{HOST}:{PORT}/"


class SetupError(Exception):
    pass


def step(message):
    print(f"\n>> {message}", flush=True)


def run_python(*args, **kwargs):
    return subprocess.run([sys.executable, *args], cwd=ROOT, **kwargs)


def port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((HOST, PORT)) == 0


def stashy_already_running():
    if not port_in_use():
        return False
    try:
        with urllib.request.urlopen(URL, timeout=3) as response:
            if b"Stashy" in response.read():
                return True
    except OSError:
        pass
    raise SetupError(
        f"Another program is already using port {PORT} on this computer, so Stashy can't start.\n"
        "   Close that program (or restart the computer) and try again."
    )


def install_requirements():
    requirements = ROOT / "requirements.txt"
    marker = Path(sys.prefix) / ".stashy-requirements.sha256"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if marker.exists() and marker.read_text().strip() == digest:
        return
    step("Installing Stashy's components (first run or after an update - this can take a minute)...")
    result = run_python("-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements))
    if result.returncode != 0:
        raise SetupError(
            "Installing components failed - see the messages above.\n"
            "   The first run needs an internet connection; if you're offline, connect and try again."
        )
    marker.write_text(digest)


def ensure_env_file():
    env_file = ROOT / ".env"
    if env_file.exists():
        return
    step("Creating your private settings file (.env)...")
    env_file.write_text(
        "# Created automatically by Stashy.bat. Keep this file private.\n\n"
        f"SECRET_KEY={secrets.token_urlsafe(50)}\n"
        "DEBUG=True\n"
        "ALLOWED_HOSTS=localhost,127.0.0.1,stashy.app\n"
        f"PORT={PORT}\n"
        "# Change PORT above (then run Stashy.bat again) if you want Stashy to run\n"
        "# alongside something else already using port 8000 on this computer.\n",
        encoding="utf-8",
    )


def migrate_database():
    pending = run_python("manage.py", "migrate", "--check", capture_output=True)
    if pending.returncode == 0:
        return
    database = ROOT / "db.sqlite3"
    if database.exists():
        backups = ROOT / "backups"
        backups.mkdir(exist_ok=True)
        backup = backups / f"db_before_update_{datetime.now():%Y%m%d_%H%M%S}.sqlite3"
        shutil.copy2(database, backup)
        print(f"   Your data was backed up first to {backup.relative_to(ROOT)}")
    step("Preparing the database...")
    if run_python("manage.py", "migrate", "--noinput").returncode != 0:
        raise SetupError("Preparing the database failed - see the messages above.")


def collect_static_files():
    result = run_python("manage.py", "collectstatic", "--noinput", "--verbosity", "0")
    if result.returncode != 0:
        raise SetupError("Preparing Stashy's style and script files failed - see the messages above.")


def ensure_login_exists():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stashy.settings")
    import django

    django.setup()
    from django.contrib.auth import get_user_model

    if get_user_model().objects.exists():
        return
    step("Create your Stashy login")
    print("   Pick a username and password - you'll use them to log in to Stashy.")
    print("   Email is optional: just press Enter to skip it.")
    print("   (While typing the password nothing will appear on screen - that's normal.)\n")
    if run_python("manage.py", "createsuperuser").returncode != 0:
        raise SetupError("The login wasn't created. Run Stashy.bat again to retry.")


def open_browser_when_ready():
    for _ in range(60):
        if port_in_use():
            webbrowser.open(URL)
            return
        time.sleep(0.5)


def main():
    try:
        if stashy_already_running():
            print(f"Stashy is already running - opening {URL}")
            webbrowser.open(URL)
            return 0
        install_requirements()
        ensure_env_file()
        migrate_database()
        collect_static_files()
        ensure_login_exists()
    except SetupError as error:
        print(f"\n!! {error}")
        return 1

    from waitress import serve

    from stashy.wsgi import application

    step(f"Starting Stashy at {URL}")
    print("   Your browser will open automatically.")
    print("   Keep this window open while you use Stashy - close it to stop Stashy.\n")
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    try:
        serve(application, host=HOST, port=PORT, threads=8, ident="Stashy")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
