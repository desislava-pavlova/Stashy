# Dependency Upgrade, November 2026

Plan 07. Checked 13/11/2026, done 14/11/2026. **Status:** all six steps
done, 725 tests green after every one. It ran before plan 03, so its number
is out of order: renumbering 03–06 again would muddle the history (see
`plans/README.md`).

Not to be confused with plan 01, also called an upgrade plan: 01 added
Stashy's features, this one brings Python, Django and the libraries up to
date. The upgrader agent (`/upgrade`) ran it: check first, then a plan you
approved, then one step at a time.

## Where it started (13/11/2026)

- Python 3.12.7. 3.12 only gets security fixes now, and those come as
  source code without a Windows installer (the last one was 3.12.10), so
  this computer couldn't get them.
- Django 6.1, already newer than the long-term support version (5.2).
- Every check clean: 725 tests, `manage.py check`, no missing migrations,
  ruff.

## Versions

| Package | Before | After | Step |
|---|---|---|---|
| Python | 3.12.7 | 3.14.10 | 5 |
| Django | 6.1 | 6.1.3 | 2 |
| tzdata | 2026.4 | 2026.6 | 2 |
| tzlocal | 5.4.4 | 5.4.5 | 2 |
| asgiref (for Django) | 3.12.1 | 3.13.0 | 2 |
| sqlparse (for Django) | 0.6.0 | 0.6.1 | 2 |
| python-dateutil | 2.9.0.post0 | 4.0.0 | 3 |
| soupsieve (for beautifulsoup4) | 2.9.2 | 4.1 | 3 |
| ruff | 0.16.10 | 0.17.5 | 4 |
| coverage | 7.16.2 | 7.17.3 | 4 |
| pip | 24.2 | 26.3 | 4 |

Already the latest, so unchanged: python-decouple 3.8, ofxparse 0.21,
waitress 3.0.2, whitenoise 6.12.0, openpyxl 3.1.5, beautifulsoup4 4.15.0,
lxml 6.1.3, six 1.17.0, et_xmlfile 2.0.0, typing_extensions 4.16.0.

The SQLite library that comes with Python went from 3.45.3 to 3.50.4. The
database file format is the same, so no stash needed migrating.

## Decisions

- **Python 3.14, not 3.15** (your choice, 13/11/2026). 3.15 had been out for
  a month. 3.14 has had a year of fixes, Django has supported it since 6.1.0
  (3.15 only since 6.1.3), and the .exe tools of plan 06 usually lag the
  newest Python. 3.14 gets fixes until October 2030.
- **Pin the libraries that other libraries pull in** (your choice,
  13/11/2026). `requirements.txt` pinned only the nine libraries Stashy
  imports. The eight they bring along now sit in a commented group, each
  with the library that needs it, so a fresh install gets the versions the
  tests ran with. ofxparse is why it matters: it calls Beautiful Soup's
  deprecated `findAll`, and a Beautiful Soup without it would break OFX
  imports with no change to Stashy's code.
- **`Stashy.bat` looks for Python 3.15 first.** Its own message tells new
  users to download "the latest Python 3", which is 3.15 today, and without
  `"py -3.15"` in its list it could only find 3.15 through PATH.
- **`pyproject.toml` keeps `target-version = "py312"`** (Claude's call):
  Stashy still runs on 3.12 for a new install, and ruff should only suggest
  syntax that works there.
- **The Python step changes only Python.** The new environment was
  installed with a constraints file frozen from the old one, so it got
  exactly the same library versions.

## Steps

Every step's checks: `manage.py check` and `makemigrations --check
--dry-run` under `stashy.settings.test` (a temporary folder holding only a
throwaway stash, so no real stash is opened), `python -Wa manage.py test`
(every warning shown), `ruff check .` and `ruff format --check .`.

- [x] **Before:** today's work committed; Python 3.14.10 installed by you,
  with the Python install manager; Stashy closed.
- [x] **1. Backups.** Both stashes, with
  `finance.backup.backup_now(..., tag="before_upgrade")`. Each backup
  checked: not empty, and `PRAGMA integrity_check` says ok (opened
  read-only).
- [x] **2. Django 6.1 → 6.1.3**, tzdata, tzlocal, asgiref, sqlparse, and
  the new pinned group. No migrations. 725 tests green.
- [x] **3. python-dateutil 4.0.0 and soupsieve 4.1.** Stashy uses only
  `relativedelta`, whose one change (`yearday=366` in leap years) it never
  calls, and ofxparse uses no CSS selectors. 725 tests green.
- [x] **4. ruff 0.17.5, coverage 7.17.3, pip 26.3.** ruff wanted no
  reformatting; a coverage run works. 725 tests green.
- [x] **5. Python 3.12.7 → 3.14.10.** The old `.venv` renamed to
  `venv.bak` (gitignored). A new one made with
  `"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" -m venv .venv`
  (the exact path, because this computer has two `py` commands) and
  installed with the constraints file. 725 tests green. Two extra checks,
  neither of which opened a database: waitress served Stashy's real app on
  a spare port (the tests use Django's test client, so they never run
  waitress), and `collectstatic` ran into a scratch folder. `Stashy.bat`
  looks for `py -3.15` first.
- [x] **6. Records:** this plan, `plans/README.md`, DEVLOG, `CLAUDE.md`
  (Python 3.14, the pinning rule, `Last upgrade check`).

The only warnings, before and after: ofxparse calling `findAll`, and the
test run's temporary folder being cleaned up implicitly.

## How to undo step 5 (while venv.bak exists)

Close Stashy, delete `.venv`, rename `venv.bak` back to `.venv`, and take
`"py -3.15"` out of `Stashy.bat`'s list. The old environment already has
every library at its new version; only Python goes back.

## Left for later

- [ ] **Around 28/11/2026:** if Stashy has run fine, delete `venv.bak`.
  Python 3.12 can be uninstalled too, unless another project still uses it.
- [ ] **Django 6.1.4**, expected 01/12/2026 (bug fixes).
- [ ] **Django 6.2 LTS**, due April 2027. 6.1 gets security fixes until
  December 2027, so the move belongs between April and December 2027. It's
  a feature release: read its "Backwards incompatible changes" first.
- Found along the way, filed in plan 03 as S71–S74 (14/11/2026), plus the
  `Stashy.bat` line endings (S73):
  - ofxparse 0.21 hasn't had a release since 31/05/2021 and calls Beautiful
    Soup's deprecated `findAll` (plan 03): replace it, or keep a copy inside
    Stashy.
  - `stashy/settings/test.py` never cleans up its temporary folder
    explicitly, so every `-Wa` test run ends with a `ResourceWarning`
    (plan 03). `atexit.register(_test_run_folder.cleanup)` would end it.
  - The README's first-run steps describe the classic Python installer
    ("tick Add python.exe to PATH"). python.org now points Windows users to
    the Python install manager, which has no such box, and stops making the
    classic installer after 3.15 (plan 06 makes Python unnecessary anyway).
