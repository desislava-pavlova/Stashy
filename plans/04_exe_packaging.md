# Stashy as a Windows .exe

Plan 04, started 02/10/2026. **Status:** idea stage. Nothing here is worked
on until you name it. It starts after plan 02, because Phases 3 and 7 of
plan 02 prepare the ground (see "What plan 02 already does").

## Goal

Stashy runs from a single download on a Windows computer that has no Python
installed. You double-click it and the browser opens. Nothing to install
first, no `.venv`, no `pip`, and no `Stashy.bat`.

Updating Stashy means replacing the program. Your stashes, backups and
settings stay where they are and are upgraded on the first launch, the same
way `Stashy.bat` does it today (back up, then migrate).

## How it would work (first sketch)

- **PyInstaller** (free) packs Python, Django, Waitress, the other
  requirements, Stashy's code, templates, static files and migrations into
  the program. `scripts/bootstrap.py` becomes the program's starting point,
  minus the steps that install things.
- **Static files are collected at build time**, not at each launch. The
  hashed file names from plan 02 item 3.8 are produced once, by the build.
- **Data lives outside the program.** Inside a PyInstaller program the
  code's own folder is temporary, and every update replaces it. So
  `stashes/`, `backups/`, `logs/` and the settings file (today's `.env`) go to
  one stable data folder (see Q1). The program finds that folder in one
  place in the settings.
- **The first launch** creates the data folder and the settings file (a new
  `SECRET_KEY`, `DEBUG=False`), then opens the stash picker.
- **Every launch** backs up and migrates each stash that needs it, takes the
  quarterly backup when due, and serves with Waitress, all as today.
- **Migrations travel inside the program.** Each stash records by name which
  migrations it has run. The program must carry exactly the files that
  produced those records, which is why they're committed to git (plan 02,
  item 3.6).

## What plan 02 already does for this

- **3.5 settings package:** one `base.py`, with the folder locations defined
  in one place. Moving them to a data folder later is a small change.
- **3.7 `DEBUG=False` for new installs, and 3.9 error log and error pages:** a
  shipped program must not show debug pages, and its errors must land in a
  log file you can find.
- **3.8 hashed static files.**
- **Phase 7 removes `settings.TESTING`,** so the code paths the program runs
  are the same ones the tests run.

## Things to watch

- **Antivirus and Windows SmartScreen.** Unsigned PyInstaller programs often
  trigger a "Windows protected your PC" warning, and sometimes an antivirus
  false alarm. Code-signing certificates cost money, so they're out (a hard
  rule). The workaround is "More info → Run anyway", which belongs in the
  README.
- **Size.** Expect roughly 40–80 MB, mostly Python and Django.
- **Moving your current data.** The real stash lives in this project's
  `stashes/` folder today. Moving it into the new data folder needs a
  backup, a copy and a check, done once and by hand, not by the program.
- **Tests.** The suite keeps running from source as today. The program
  itself gets a short smoke test after each build: it starts, the picker
  loads, and a test stash opens.

## Open questions (answer when the plan starts, not before)

- **Q1: Where does the data live?**
  - Option a: next to the program, which is easy to find and copy (portable,
    "carry it on a USB stick").
  - Option b: your Windows user folder (`%LOCALAPPDATA%\Stashy`), which is
    the Windows convention and survives deleting the program's folder.
- **Q2: One file or one folder?**
  - Option a: a single `Stashy.exe`. It unpacks itself on every start, so it
    starts more slowly.
  - Option b: a folder holding `Stashy.exe` and its parts. It starts faster
    but is less tidy.
- **Q3: A plain zip, or an installer** (Inno Setup is free) with a Start
  menu entry and an uninstaller?
- **Q4: A console window or a tray icon?** Today a console window stays open
  while Stashy runs, and closing it stops Stashy. A tray icon is tidier but
  needs a small extra library.
- **Q5: Updates:** check for a new version, or only update by hand?
- **Q6: Migrations, keep the history or start fresh?** Open since
  02/10/2026, and it belongs to plan 02 item 3.6. Starting fresh (one new
  `0001_initial` per app) would leave every older backup with no automatic
  upgrade path.
