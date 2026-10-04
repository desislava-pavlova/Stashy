# Stashy Dev Log

Running log of notable changes and the reasoning behind them, kept for
later walkthroughs/teaching purposes. Smaller mechanical edits (typo fixes,
formatting) won't all get an entry — this tracks decisions worth explaining.

Dates are DD/MM/YYYY. This file is committed to git, so it never names real
balances, payees, banks, places or stash owners - "the real stash" stands in
for the stash that holds real data.

---

## 12/09/2026 — Branding cleanup: squirrel mascot + quickfin → stashy

**What changed:** Added a 🐿️ squirrel next to the "Stashy" wordmark in the
sidebar, mobile navbar, and login card, and set it as the browser favicon
(inline SVG/emoji data URI — no image assets needed). Renamed leftover
`qf-` prefixed CSS (`--qf-navy`, `--qf-accent`, `--qf-accent-soft`,
`.qf-sidebar`) to `stashy-` — residue from the project's old "QuickFin"
name that had survived even though all visible text already said "Stashy".

**Why:** Explicitly requested as the only two allowed changes in that
step, done before any larger work started.

**Files:** `finance/templates/finance/base.html`,
`finance/templates/registration/login.html`.

---

## 12/09/2026 — Config hardening: real SECRET_KEY, branded ALLOWED_HOSTS, drop Postgres

**What changed:**
- Generated a real random `SECRET_KEY` via Django's own
  `get_random_secret_key()` and put it in `.env` (gitignored); kept a
  placeholder in `.env.example`.
- `ALLOWED_HOSTS` now includes `stashy.app` alongside `localhost`/
  `127.0.0.1` — a friendlier name to browse to, but only resolves once you
  map `127.0.0.1 stashy.app` in your OS hosts file (documented in
  `.env.example`); the IP/localhost fallbacks stay so nothing breaks if
  you skip that step.
- Removed Postgres support entirely: `psycopg2-binary` and
  `dj-database-url` dropped from `requirements.txt`, `settings.py` now
  points straight at SQLite with no `DATABASE_URL` branching, README's
  Postgres section removed.

**Why:** Stashy is explicitly a local, single-user app — Postgres was
unnecessary setup friction for that use case, and the placeholder secret
key/generic hosts were never actually configured for this project.

**Files:** `.env` (new, gitignored), `.env.example`, `stashy/settings.py`,
`requirements.txt`, `README.md`.

---

## 12/09/2026 — Full analysis + upgrade plan written (no code changes)

**What changed:** Read through the whole codebase (models, views, forms,
urls, templates, admin) and wrote `UPGRADE_PLAN.md` — an 11-phase plan
covering everything requested: one-file setup, UI/mascot redesign,
account taxonomy + multi-currency + opening-balance-as-transaction,
category rules, transaction numbering + register UX, payee memorization
redesign, reminders overhaul, a full reconciliation workflow, import,
backup/export, and small report additions. Also found and logged 5 real
bugs in the current code (float-vs-Decimal money parsing, silent category-
cascade delete, silent custom-date-range fallback, unpaginated large
lists, payee-defaults over-refreshing on unrelated edits) for the plan to
fix. Went through two rounds of clarifying questions with the user to
pin down ambiguous requirements (opening balance modeled as a real
transaction, real currency conversion via Frankfurter, Payment/Deposit
columns, no leaf-only category enforcement, "end of period" reminder due
dates, Skip instead of is_active for reminders, paired-transaction
transfers replacing the broken `transfer_account` field, launch-time-only
quarterly backups) — all captured in `UPGRADE_PLAN.md`'s decisions log.

**Why:** The requested scope is effectively a v2 rewrite touching nearly
every model and page. Planning it all out first, in writing, before
touching code means we can implement phase by phase without repeatedly
reworking the same page, and there's a durable record of *why* each
design choice was made for later reference.

**Files:** `UPGRADE_PLAN.md` (new).

---

## 12/09/2026 — Plan refined: mascot assets, audit log, transaction numbering, import rules

**What changed (still planning only, no code):** User supplied real
mascot artwork (`images/` folder — a squirrel-with-backpack-and-acorn-coin
character in three crops) to replace the emoji placeholder once Phase 2
lands. Added a new early phase for an audit log (tracks every create/
update/delete, including automated ones like payee-default refreshes or
reminder auto-advances, surfaced read-only in Django admin) — placed right
after Phase 1 since it's infrastructure everything else benefits from.
Renamed the account liquidity groups to the user's preferred wording
(Physical Liquid / Digital Liquid / Illiquid) with short explainer text.
Moved main-currency from an app-wide setting to a per-user `Profile`
model. Worked out the transaction-numbering design properly (see D9 in
the plan): the Quicken "sorts weirdly" bug the user remembered is a
classic symptom of sorting a *text* column as if it were numeric (string
sort puts "10" before "2") — storing it as a real integer and always
sorting numerically avoids that outright, which also means the "start at
10001" trick isn't needed anymore either (that was only a workaround for
Excel stripping leading zeros off a zero-padded *text* value; a plain,
unpadded integer never has a leading zero to strip). Settled the CSV
import category question (fail vs. auto-create): recommended auto-create
with an `is_auto_generated` flag + UI badge rather than baking
"(AutoGenerated)" into the category name forever, since a big one-time
historical migration is the main use case and requiring every category
to exist upfront would make that painful — while accounts still hard-fail
on import if missing, since a wrong account placement is a worse mistake
to auto-correct than a slightly-off category.

**Why it matters for later:** these are the kind of decisions where the
"obvious" choice (fail-fast everywhere, keep 10001, bake AutoGenerated
into names) looks safer at first glance but is actually worse ergonomics
or solves a problem that doesn't exist once the underlying data type is
right — worth remembering the reasoning, not just the conclusion.

**Files:** `UPGRADE_PLAN.md` (updated).

---

## 12/09/2026 — Phase 1: one-click launcher, confirmed bug fixes, European formatting

A database backup was taken first: `backups/db_before_phase1_*.sqlite3`.
No database schema changes were needed in this phase (`makemigrations
--check` reports nothing).

### One-click setup: `Stashy.bat` + `scripts/bootstrap.py`

**What:** Double-clicking `Stashy.bat` finds Python 3.12+, creates the
private `.venv` if missing (or rebuilds it if it was copied from another
computer and is broken), then hands over to `scripts/bootstrap.py`, which:
installs requirements (skipped when a hash of `requirements.txt` matches a
marker file in `.venv`, so it only reinstalls after the file changes);
creates `.env` with a random secret key if missing; backs up `db.sqlite3`
into `backups/` *before* applying any pending migrations; asks for a
username/password only if no login exists yet; then starts the server and
opens the browser once the port actually answers. If Stashy is already
running, it just opens the browser.

**Why split into two files:** batch files are fragile — special characters
like `%`, `&`, `^`, `)` break them in surprising ways (a random secret key
contains exactly those). So the `.bat` does only what *must* happen before
Python exists (find Python, make the venv), and everything else is Python,
which is readable and testable. Every step checks "is this already done?"
first — that's called being *idempotent* — which is what lets the same
double-click serve as both installer and everyday launcher.

**Lesson worth remembering — test the real install path:** running the
launcher against a clean environment exposed two bugs that had been in
`requirements.txt` all along, invisible because the existing `.venv` was
already populated: (1) the file was saved as UTF-16 (Windows PowerShell's
`>` does that by default) and pip only reads UTF-8, and (2) it pinned
`python-dateutil==2.9.0.post1`, a version that has never existed
(installed one is `2.9.0.post0`). A brand-new computer could never have
installed Stashy. Verified afterwards by building a throwaway venv from
scratch and installing cleanly.

### Bug 1 — money is Decimal everywhere

**What:** New `finance/money.py` with `parse_amount()`, used by CSV and
OFX import. Reports now compute with `Decimal` and only convert to float
at the very last moment when handing numbers to the JavaScript charts.

**Why float is wrong for money:** floats are binary fractions; most
decimal amounts (like 0.10 or 19.99) can't be represented exactly, so
sums drift by tiny amounts that eventually show up as a cent. `Decimal`
stores base-10 digits exactly.

**Also found:** the old importer did `.replace(",", "")`, so a European
`12,50` became `1250` — a 100x error. `parse_amount` decides which
character is the decimal separator (the last one present, or a single
comma followed by 1-2 digits) and handles `1 234.56`, `1.234,56`,
`1,234.56`, `(12.00)` for negatives, and currency symbols.

**Teaching note — SQLite has no real decimal type:** it stores these
columns as floating point internally. Django rounds values back to 2
decimal places when it reads them, which keeps personal-finance-sized sums
exact in practice. Worth knowing if we ever do heavy math inside SQL.

### European dates and `10 000.50` numbers everywhere

**Dates:** `stashy/formats/en/formats.py`, activated via
`FORMAT_MODULE_PATH` in settings. That's Django's official hook for
overriding locale formats — one place that makes every `{{ some_date }}`
in every template, every form, and the admin use `DD/MM/YYYY`, with
Monday as the first day of the week.

**Date inputs:** the browser's built-in `<input type="date">` displays in
the *operating system's* locale, which we can't control — on an en-US
Windows it shows MM/DD/YYYY no matter what the server sends. So date
fields became text inputs formatted `DD/MM/YYYY` with the **flatpickr**
date picker attached (downloaded into `finance/static/finance/vendor/`,
not loaded from the internet, so it works offline — MIT licensed, license
file included).

**CSV import dates:** the importer used to try American `MM/DD/YYYY`
*before* `DD/MM/YYYY`, so `03/04/2026` became 4 March. American formats
were removed entirely: if both orders are allowed, any date with a day and
month both ≤ 12 is silently ambiguous.

**Numbers:** a `money` template filter (`finance/templatetags/
stashy_format.py`) outputs `10 000.50` using a *non-breaking* space so an
amount can never wrap onto two lines in a table cell. I deliberately did
**not** turn on Django's global `USE_THOUSAND_SEPARATOR` — that would also
insert spaces into things like database IDs inside URLs and JavaScript
(`/accounts/1 234/`), silently breaking links. Chart.js axes and tooltips
were set to the same style via global chart defaults in `base.html`. The
hardcoded `$` signs were removed since per-account currency arrives in
Phase 3.

### Bug 2 — category delete warns about subcategories

`Category.descendants()` walks down the tree level by level (so
sub-subcategories are included too), and a new
`category_confirm_delete.html` *extends* the generic delete page, filling
an empty `{% block delete_warning %}` with the list and a count of
transactions that will become uncategorized. Template inheritance means
the other delete pages didn't need to change.

### Bug 3 — invalid custom report range is an error, not a silent fallback

`DateRangeFilterForm.clean()` rejects "Custom range" without both dates
(or with start after end); the reports page shows the error in red and
states plainly that the default range is being shown instead.
`clean()` is Django's hook for validation that involves several fields
together.

### Bug 5 — payee defaults only refresh when a memorized field changes

`Transaction.save()` now reads the row's previous values from the
database and only refreshes the payee's memory if `payee`, `category`,
`memo` or `amount` actually changed (or it's a new transaction). Toggling
cleared/reconciled no longer touches it. Note `Decimal("-30") ==
Decimal("-30.00")` compares by value, so re-saving the same amount with
different formatting doesn't count as a change.

### Bug 4 — no row cap, compact rows, and a hidden N+1 query

Removed the 500/200 row caps, made register rows compact with a sticky
header, and added a row count. The important fix was invisible: each row
displays its category as "Parent: Child", and fetching each parent was a
separate database query — 4,468 rows would have meant ~4,468 extra
queries. That pattern is called an **N+1 query**. Fixed with
`select_related("category__parent")`, which fetches everything in one
joined query. Measured with 4,468 generated transactions: ~450 ms
server-side, a fixed 5 queries, ~2.4 MB of HTML — so no "load more" was
needed.

### Tests

Added `finance/tests.py` (the project had none): 19 tests covering every
fix above, including a query-count test that fails if the N+1 problem ever
comes back. Django runs them against a throwaway in-memory database, never
`db.sqlite3`. Run: `.venv\Scripts\python manage.py test finance`.
Visual checks were done by rendering real pages and screenshotting them
with headless Edge.

**Files:** `Stashy.bat`, `scripts/bootstrap.py`, `requirements.txt`,
`finance/money.py`, `finance/templatetags/stashy_format.py`,
`stashy/formats/en/formats.py`, `stashy/settings.py`, `finance/models.py`,
`finance/forms.py`, `finance/views.py`, `finance/tests.py`,
`finance/static/finance/vendor/flatpickr/*`, templates (`base`,
`dashboard`, `account_list`, `account_detail`, `transaction_list`,
`transaction_form`, `payee_list`, `reminder_list`, `reports`,
`generic_confirm_delete`, new `category_confirm_delete`), `README.md`.

---

## 12/09/2026 — Phase 1B: audit log, real web server, Sofia time, account delete warning

Backup first: `backups/db_before_phase1b_*.sqlite3`. One new migration,
`audit.0001_initial`, which only adds a table. You confirmed the current
data is test-only, so future phases may reset data rather than convert it.

### The audit log (new `audit` app)

**What you get:** every create, change and delete of any Stashy record is
written to one timeline, which you can see in the admin under *Audit log*.
Each entry has: when (Sofia time, to the second), what happened, **Source**
("You" or "Automatic"), which record, field-by-field *before → after*, a
**reason** for automatic changes ("Imported from bank.csv", "Memorized
defaults updated from transaction #12"...), the request that caused it
(`POST /categories/5/delete/`), and a **"same action"** link that shows
everything a single click changed — e.g. one account delete and the 123
transactions that went with it. The log is read-only: nobody, including
you, can add, edit or delete entries through the admin. Every other record
in the admin has a **History** button that now opens that record's trail.

**How it works (the concepts worth learning):**
- **Signals.** Django announces "about to save / just saved / about to
  delete / just deleted" for every model. The audit app listens: before a
  save it loads the row's old values, after the save it compares and logs
  only fields that really changed — saving something unchanged logs
  nothing. Values are stored as readable text *at the time* (category
  names, `DD/MM/YYYY`, two decimals), so an entry still makes sense after
  the category it mentions has been deleted.
- **ContextVar — "who and why" without passing it around.** A middleware
  marks everything during a web request as done by you. Automatic code is
  wrapped in `with system_action("reason"):`. The signal handler reads this
  from a context variable — like a global that's private to the current
  request/thread — so no function had to grow extra parameters. Wrapped
  so far: payee memory refresh, reminder due date advancing, entering a
  reminder, imports, payees auto-created from a new name.
- **Changes Django makes silently.** When a category is deleted, Django
  blanks the category on its transactions with one bulk `UPDATE` that
  sends *no* signals. The audit app finds those rows before the delete and
  logs them *after* the delete commits — only for rows that still exist,
  so a transaction deleted in the same action doesn't also get a
  misleading "category cleared" entry.

**Why custom instead of `django-simple-history`:** that popular library
keeps a separate history table *per model* — excellent for "history of this
one transaction", but "what happened to my data yesterday?" would mean
looking in seven places. You asked for one place to see all changes,
including automatic ones and why, so a single timeline fit better — and
it's about 250 readable lines with no extra dependency.

**Honest limits:** changes made *outside Django* (e.g. feeding payees
straight into `db.sqlite3` with a SQLite tool, as you planned) never pass
through the signals, so the log can't see them. Same for bulk operations
(`queryset.update()`, `bulk_create()`); code that changes audited data
must save row by row or log explicitly — noted in the plan for Phase 4's
mass re-categorize.

### Side fix: payees were created even when a transaction failed to save

`TransactionForm.clean()` used to create the payee while validating — so a
form that then failed (say, a mistyped date) still left a new payee behind.
Payee creation moved into `save()`, which only runs for valid forms.

### Every request is all-or-nothing (`ATOMIC_REQUESTS`)

Each request now runs inside one database transaction: if anything fails
part-way, *nothing* it changed is kept. That closes the backlog item about
half-finished imports — there's a test that uploads a garbage OFX file and
checks that no import record and no log entries survive. SQLite also got
`transaction_mode=IMMEDIATE` with a 20-second timeout: with a multi-threaded
server, two requests that both read and then try to write can deadlock and
fail with "database is locked"; taking the write lock at the start makes
the second request simply wait its turn. The cost — requests run one at a
time — doesn't matter for a single person.

### A real web server: Waitress + WhiteNoise

**Why you saw the warning:** `manage.py runserver` is Django's
*development* server — convenient (it reloads on code changes) but not
built or security-tested for real use, so Django prints that warning on
purpose. The message you saw came from your own `python manage.py
runserver` (it's still running on port 8000 as I write this); `Stashy.bat`
no longer uses it.

**What replaced it:** **Waitress**, a production-grade web server written
in pure Python that works on Windows (the usual Linux choices, gunicorn and
uWSGI, don't). Django itself doesn't serve CSS/JS files outside the
development server, so **WhiteNoise** does that — compressed and with
proper caching, no separate nginx needed. On every launch `Stashy.bat` runs
`collectstatic`, which gathers all static files into `staticfiles/` (fast;
only changed files are copied). `DEBUG` stays `True` as you asked, so
detailed error pages still show; later, flip it to `False` in `.env`.

**Trade-off:** no automatic reload on code changes — after I change the
code, close the Stashy window and start `Stashy.bat` again.

### Time zone: Europe/Sofia

The database keeps timestamps in UTC (standard practice: unambiguous even
across daylight-saving changes) and Django converts them to Sofia time for
display. Times only appear where they matter — import history and the
audit log; transactions and reminders are dates only.

Two record labels still broke those rules and were fixed after the audit
log made them visible: transactions read `2026-09-02 | Lidl | -42.30` (now
`02/09/2026 | ...`), and import records printed their time in UTC — `18:57`
when it was 21:57 in Sofia. The import label now converts with
`timezone.localtime()` first, which is the rule to remember: a timezone-aware
datetime from the database is in UTC until you convert it for display.

### Account delete warning

Like categories: the confirmation page now says exactly how many
transactions, reminders and import records will be deleted, mentions
transactions in *other* accounts that will lose their transfer link, and
suggests unticking *Is active* instead if you only stopped using it.

### Verification

32 tests (13 new for the audit log, 2 for the account warning). The
launcher was run end to end with Waitress on a spare port: pages, static
files and admin styles served, `Server: Stashy` header, no
development-server text. Screenshots of the account warning and the audit
log admin were taken from a throwaway database filled by clicking through
realistic actions.

**Files:** new `audit/` app (`models.py`, `context.py`, `signals.py`,
`middleware.py`, `admin.py`, `apps.py`, `tests.py`,
`migrations/0001_initial.py`), `stashy/settings.py`, `scripts/bootstrap.py`,
`requirements.txt` (+`waitress`, `whitenoise`), `finance/admin.py`,
`finance/models.py`, `finance/forms.py`, `finance/views.py`,
`finance/tests.py`, new `finance/templates/finance/account_confirm_delete.html`,
`README.md`.

---

## 12/09/2026 — Decision D10: regional settings detected once, then editable

You asked whether Stashy could use the computer's date/time settings so a
New York user wouldn't need to change anything, and left the decision to
me. Decision (recorded as D10 in the plan, built in Phase 3): when the
profile is created, detect time zone, date format, number format and first
day of week from the computer, save them as editable preferences, and don't
re-read them on every launch. Why not follow the OS live: Windows' display
language often differs from the date format people actually want (lots of
Europeans run English Windows), and data such as CSV imports must never
silently change meaning because a Windows setting changed. Two knock-on
effects: CSV import gets a per-file date format (a bank file's format has
nothing to do with your computer), and the audit log will store raw values
and format them when displayed.

---

## 12/09/2026 — Phase 2: Stashy look and feel, dark mode, one-screen dashboard

### Everything works offline

Bootstrap, the icon font and Chart.js used to load from an internet CDN —
without internet, Stashy came up unstyled with broken charts. They now live
in `finance/static/finance/vendor/` together with their licenses (MIT, and
SIL OFL for fonts), and a test fails if any page ever references an
`http(s)://` asset again.

### A design system instead of scattered styles

`finance/static/finance/css/stashy.css` defines the palette as **tokens**
(CSS variables) taken from the mascot: acorn orange, forest green, sage,
coin gold, cream. Every token has a light and a dark value, and Bootstrap
5.3 is themed through its *own* variables rather than by overriding
hundreds of rules — so a Bootstrap component picks up Stashy's colors
automatically. Text colors were checked against the WCAG accessibility
contrast rule (at least 4.5:1): e.g. acorn `#b54e17` on cream is 4.9:1 and
white on an acorn button 5.2:1; a brighter orange looked nicer but failed.

**Fonts:** Nunito for headings (rounded, friendly — fits a younger
audience) and Manrope for text and numbers. Both are *variable* fonts (one
file covers every weight) and both include **Cyrillic**, since your payees
will often be Bulgarian. `unicode-range` makes the browser download only
the script subsets a page actually uses. I checked the font files
themselves (with the `fontTools` library) for **tabular digits** — digits
that are all the same width, so amounts line up in columns: Manrope has
them, which is why it carries the numbers.

### The mascot art

`images/` stays your gitignored source art. From it, web-sized copies were
generated into `finance/static/finance/img/`: a 3 KB favicon, the face at
15/50 KB and the full body as WebP at 11/32 KB (the originals were 1–1.6 MB
each). One surprise worth knowing about AI-generated PNGs: nearly every
pixel was 98% opaque instead of 100%, plus a faint orange glow around the
edges — invisible on white, muddy on a dark background. The alpha channel
was cleaned (faint pixels fully transparent, near-opaque fully opaque). The
full-body render has a solid cream background, so it sits in a matching
cream frame that looks intentional in both themes.

### Light and dark mode

Stashy follows the operating system's setting until you press the toggle;
after that your choice is remembered in the browser (`localStorage`). A
tiny script in the page `<head>` applies the theme *before* anything is
drawn, which prevents a white flash when you're in dark mode. (This
computer is set to dark mode — which is exactly how I noticed my first
"light" screenshots were dark: Stashy was following Windows, as designed.)
Charts listen for a `stashy:themechange` event and recolor themselves.

### The dashboard

It fits one screen — verified by measuring page height against window
height at 1280×720, 1366×768 and 1920×1080. The trick is a CSS grid whose
last row is `minmax(0, 1fr)`: the panels share whatever height is left and
long lists scroll *inside* their own panel instead of stretching the page.

- Tiles: net worth, income this month, spent this month, and **saved last
  month** = (income − spending) ÷ income. It's blank when there was no
  income (dividing by zero means "no answer", not 0%), and negative when
  you spent more than you earned.
- "Recent transactions" is gone.
- **Drill-down donut:** slices are top-level categories; clicking one with
  two or more subcategories shows its breakdown, and clicking again opens
  the transaction list filtered to that category and this month.
- **Due soon:** the next 7 days, including overdue items, with badges.
- A brand-new user gets a welcome card instead of empty widgets.

Two techniques worth remembering from the donut: the data travels from
Django to JavaScript with `json_script`, which escapes it so a category
named `</script>` can't break the page; and the legend is built with
`textContent`, never `innerHTML`, so category names are always shown as
text and can't inject HTML.

### Transaction list filters (groundwork for Phase 5)

The drill-down needed new URL filters: `start`/`end` dates, `category=none`
for uncategorized, and `with_subcategories=1`. Malformed values are now
ignored — before, `?category=abc` crashed the page with a server error.

### Forms and mobile

Forms sit in the left column with a "Stashy tips" card beside them, instead
of a lone narrow form next to empty space. On phones there used to be **no
navigation at all** (the sidebar hid below 768 px and nothing replaced it);
there's now a top bar with a slide-out menu and the theme toggle.

A testing lesson: headless Edge refuses to render narrower than 492 px, so
my first "phone" screenshots were silently cropped and looked broken. The
fix was to load the page inside a 390 px iframe, which gives a true phone
width — and that also revealed a real bug on the login page, now fixed.

### Bugs fixed along the way

- **Category loop hang:** nothing stopped a category from being its own
  parent (or sitting under one of its own subcategories). The subcategory
  lookup Phase 1 added for the delete warning would then loop forever,
  freezing the page. The lookup now guards against loops, and the form
  rejects such a parent.
- Error messages showed grey instead of red: Django calls the level
  `error`, Bootstrap calls the style `danger` — mapped in `MESSAGE_TAGS`.
- Report chart axes showed ISO dates; they now read `DD/MM/YYYY`.
- The admin is titled "Stashy admin".

### Verification

50 tests (18 new: savings rate, drill-down grouping, loop protection,
dashboard, transaction filters, the no-internet check, form tips).
Screenshots of every redesigned page in light, dark and phone width, the
one-screen measurement, and the real launcher serving the new fonts and
images through Waitress and WhiteNoise.

**Files:** new `finance/static/finance/css/stashy.css`,
`finance/static/finance/js/{stashy,charts,dashboard}.js`,
`finance/static/finance/img/*`, `finance/static/finance/vendor/{bootstrap,
bootstrap-icons,chartjs,fonts}/*`, `finance/templates/finance/includes/*`,
`finance/test_dashboard.py`; changed `finance/views.py`,
`finance/reporting.py`, `finance/models.py`, `finance/forms.py`,
`stashy/settings.py`, `stashy/urls.py`, templates (`base`, `dashboard`,
`generic_form`, `transaction_form`, `transaction_list`, `reports`, list
pages, `registration/login`), `README.md`.

---

## 12/09/2026 — Loaded test_data.TXT into the real database

You dropped `test_data.TXT` (a Quicken "Register Report" export, tab-
delimited with a header banner and summary rows mixed in) into the project
root and asked for it to be loaded so there's something real to look at.
Backed up first: `backups/db_before_test_data_*.sqlite3`.

**What happened:** `scripts/load_test_data.py` parses it (a different
format from the CSV/OFX importer, so it got its own small parser rather
than reusing that code) and loads it straight into the real `db.sqlite3` —
not a throwaway test database, since the point was to actually see it.
It first clears the handful of placeholder rows from early manual testing
(`test_expense`, `bank`, `debit`...) since you'd said none of that needed
keeping. Result: 779 transactions, 6 accounts, 103 categories, 156 payees.
The sum of every imported amount matched the file's own "NET TOTAL" line
exactly — good confirmation nothing was parsed wrong.

**Two rough edges, both expected and left alone rather than "fixed" behind
your back:**
- The source file itself has inconsistent payee capitalization
  (`Lidl` / `LIdl` and a couple of other pairs)
  which became separate Payee rows rather than one, since matching is
  exact today. This is a live example of exactly the case-insensitive-
  matching problem **Phase 9**'s import design already accounts for.
- Transfers (`Transfer Money` rows) don't have a category in the source
  data — Stashy's current model has no transfer-type category to put them
  in yet, so they show up lumped into "Uncategorized" on the dashboard's
  spending breakdown, inflating it. This is precisely the "false high
  numbers" problem **D7**'s per-account transfer category is designed to
  fix in Phase 3 — this dataset will look noticeably better once that
  lands.

Also noticed in passing (not fixed, cosmetic only): `Account.balance`
sometimes returns a `Decimal` with several trailing zeros (e.g.
`9.66000000000000`) rather than exactly `9.66`, because SQLite's `SUM()`
has no real decimal type and does the addition as floating point
internally before Django converts the result back. Every place the app
actually *displays* a balance goes through the `money` template filter,
which re-quantizes to 2 decimal places, so this never reaches the screen
— confirmed by checking `money(balance)` for every account. Worth fixing
properly if a raw `.balance` value is ever computed with further down the
line (e.g. before it hits a filter), but not urgent.

**Files:** new `scripts/load_test_data.py`; `db.sqlite3` (data only, real
database — not a template/fixture, so this doesn't touch git either way).

---

## 13/09/2026 — Phase 3: accounts, transfer categories, transaction numbers, currency, regional preferences

**What changed:** The full Phase 3 checklist from `UPGRADE_PLAN.md` — new
account taxonomy (Physical Liquid / Digital Liquid / Illiquid / Inactive),
opening balance as a real transaction instead of a `starting_balance`
field, per-account transfer categories + a **Transfer** button, per-account
transaction numbers, real multi-currency with Frankfurter rate fetching,
and regional preferences (date/number format, first day of week, time
zone) detected from this computer once and editable on a new **Settings**
page.

**Why this shape, not something simpler:**

- **Opening balance as a transaction, not a number (D1).** A plain
  `starting_balance` field is a special case every report and balance
  calculation has to remember to add in. Making it a real `Transaction`
  (payee "Opening Balance", using the account's own transfer category)
  means `Account.balance` is *just* `sum(transactions)` — one code path,
  no special-casing — and the opening balance shows up, and can be
  corrected, exactly like anything else in the register.
- **Transfer categories are per-account, not a link between accounts.**
  The old `transfer_account` field on `Transaction` tried to remember
  "this transaction's other half lives over there" — but that's a
  relationship between two *rows*, not two *accounts*, and it had already
  drifted out of sync (flagged as a confirmed bug). Giving every account
  its own transfer-type `Category` sidesteps the whole problem: a transfer
  is just two ordinary transactions, each categorized as "this account's
  money moved," with nothing to keep in sync and nothing that breaks when
  one side is deleted. The `Category.CategoryType.TRANSFER` type is what
  lets reports exclude these rows from income/spending without deleting or
  hiding the transactions themselves.
- **Transaction numbers as a real integer column (D9).** The bug you
  remembered from Quicken — order going 1, 10, 100, 2, 20 instead of
  1, 2, 3 — happens specifically when a number is sorted as *text*: string
  comparison looks at characters left to right, so `"10"` sorts before
  `"2"` the same way `"apple"` sorts before `"banana"`. A real integer
  column has no such ambiguity — `2 < 10` is just true — so the bug can't
  come back here regardless of how large the numbers get. That also means
  the old "start at 10001" trick (which only existed to stop Excel from
  stripping leading zeros off a zero-padded *text* value) isn't needed:
  a plain integer has no leading zeros to lose.
- **Currency conversion reads the latest known rate, not a historical
  one, for now (D2).** Storing an actual historical rate per transaction
  would make old balances immune to today's exchange rate — more correct,
  but a real feature (backdated rate lookups, handling a transaction on a
  day with no published rate, etc.). Converting at the *current* rate for
  every calculation is one honest simplification instead of a half-built
  historical system; it's flagged in the plan as a place to revisit.
- **Regional settings are detected once, not read live (D10).** Windows
  keeps this computer's date/number formatting and the signed-in Windows
  account's *display language* as separate settings — someone can have
  Windows in Bulgarian but still want dot-decimal numbers (exactly this
  computer's setup, it turns out). Re-reading the OS setting on every
  launch would silently change how *already-imported* numbers and dates
  are displayed if that OS setting ever changed, which is exactly the kind
  of surprise a finance app should never produce. Detecting once and then
  treating it as a normal, user-editable preference avoids that.

**A design decision worth flagging on its own:** Windows only stores a
time zone as a name like `"FLE Standard Time"` — one clock shared by
Helsinki, Kyiv, Riga, Sofia, Tallinn and Vilnius — and the `tzlocal`
library that turns that into an IANA name (`Europe/Sofia`, `Europe/Kyiv`,
...) just picks the *first* city alphabetically, which happened to be
`Europe/Kiev` on this machine even though the clock is genuinely Sofia's.
Since the actual UTC offset is identical for every city sharing that
Windows zone, there's no way to tell them apart from the clock alone — so
`regional.zone_for_country()` also reads the computer's *region* (`bg-BG`
→ country `BG`) from the registry and, when a same-offset zone exists in
that country, prefers it. This is a small example of a general lesson:
when translating between two naming systems that don't map 1-to-1 (Windows
zone keys ↔ IANA zone names), the "obvious" library answer can be
technically correct (same clock) while still being the wrong *name* for
what the person would actually recognize.

**Scope cut, disclosed rather than silently skipped:** D10 says the audit
log should store raw values and format them per-viewer at display time.
Doing that properly means every entry in the `changes` JSON would need to
remember not just "before/after" but *what kind* of field it was (a date?
a decimal? a choice list?) so it can be reformatted later — a real
refactor of `audit/signals.py`, not a quick tweak, and one that would
touch every exact-text assertion in `audit/tests.py`. Given this is a
single-user app where the viewer *is* the person who set the preference,
the payoff today is small (it only matters if you change your date/number
format after data already exists) — so only the admin's timestamp column
(a genuinely raw field) was switched over, and the rest is logged as a
backlog item rather than rushed through under time pressure.

**Bug fixed while at it:** `Account.balance` returned Decimals with
junk trailing digits like `9.66000000000000` — SQLite's `SUM()` does the
addition as floating point internally, so the result needs rounding back
to cents before Django hands it over. Harmless on screen (the `money`
filter already re-quantized), but now fixed at the source too, since
Phase 3 started actually adding these balances together (currency
conversion, net worth) where that raw value matters.

**Verification:** 75 tests (25 new), plus a manual pass against the real,
freshly-reloaded 779-transaction dataset — every page and the two new
admin screens (Profile, Exchange rates) load without error.

**Files:** new `finance/regional.py`, `finance/currency.py`,
`finance/middleware.py`, `finance/context_processors.py`,
`finance/templates/finance/{transfer_form,settings}.html`,
`finance/test_phase3.py`, `finance/migrations/0002_phase3_accounts.py`;
changed `finance/models.py`, `finance/forms.py`, `finance/views.py`,
`finance/reporting.py`, `finance/admin.py`, `finance/urls.py`,
`finance/templatetags/stashy_format.py`, `finance/static/finance/js/
{stashy,charts}.js`, `finance/static/finance/css/stashy.css`,
`finance/templates/finance/{base,account_list,account_detail,
account_confirm_delete,transaction_form,transaction_list,reminder_list,
import_form,includes/sidebar_contents}.html`, `audit/admin.py`,
`scripts/load_test_data.py`, `requirements.txt`, `README.md`,
`finance/tests.py`, `finance/test_dashboard.py`, `audit/tests.py`.

---

## 13/09/2026 — Three small post-Phase-3 adjustments

Three follow-ups after Phase 3 landed, kept together in one entry since
each was small on its own.

### 1. Configurable port, so Stashy and `manage.py runserver` can both run

**What:** You hit "port 8000 already in use" because `manage.py runserver`
(which you sometimes run for development) also defaults to 8000, the same
port `Stashy.bat` was hardcoded to. `scripts/bootstrap.py` now reads a
`PORT=` line from `.env` (still defaulting to 8000 for a fresh install);
yours is now set to `8001`, so both can run at once.

**Why read `.env` by hand instead of using `python-decouple`** (the
library already used everywhere else for this): the port has to be known
*before* `install_requirements()` runs, so on a genuinely first-ever run
`python-decouple` isn't necessarily installed yet to read it. A small
manual parse of `PORT=` has no such dependency.

**Files:** `scripts/bootstrap.py`, `.env`, `.env.example`, `README.md`.

### 2. Fixed two leftover references after your `AccountType` rename

**What happened:** You renamed `AccountType.CHECKING` → `DEBIT_CARD` and
`PROPERTY` → `OTHER` directly in `finance/models.py`. Two spots still used
the old names and would have raised `AttributeError` the moment they ran:
`scripts/load_test_data.py` (mapping "Debit Card" and a bank account to a type
that no longer existed) and one test in `finance/test_phase3.py`. Fixed
both to use `DEBIT_CARD`.

**Also needed, not a "you forgot this" but a consequence of the rename:**
the *database* still had the old string values baked in (your 6 real
accounts were stored with `account_type="checking"`, which no longer
matches any choice). A new migration
(`finance/migrations/0003_account_type_rename.py`) picked up the changed
`choices`/`default`, and `scripts/load_test_data.py` was re-run so your
real accounts got the new values too. Verified with the full test suite
(75 tests) plus a check against the reloaded real data.

**Files:** `finance/migrations/0003_account_type_rename.py`;
changed `scripts/load_test_data.py`, `finance/test_phase3.py`.

### 3. Currency symbols instead of a bare ISO code (`$9.66` not `9.66 USD`)

**What:** A `CURRENCY_SYMBOLS` table and `format_money()` helper in
`finance/currency.py`, and a new `moneyc` template filter (alongside the
existing plain `money` one) that wraps an amount with its currency's
symbol. Applied wherever an amount clearly belongs to one currency:
account balances, transaction amounts (each row uses *its own* account's
currency, since the all-accounts transaction list can mix currencies),
reminder amounts, and the dashboard/reports figures (your main currency).

**Placement is a judgment call, not a spec, so flagging it:** most local
European currencies (€, лв., zł, kr, Kč, Ft, lei) print *after* the
number with a space (`9.66 €`); everything else (`$`, `£`, `¥`, ₹, ...)
prints *before*, no space (`$9.66`). Say if you'd rather have `€9.66`.

**Left as a bare number, deliberately:** a memorized payee's default
amount (`payee_list.html`) — a payee can be used from accounts in
different currencies, so there's no one currency to attach a symbol to
without guessing.

**A real bug surfaced and fixed while doing this:** the Transfer form let
you pick a destination account in a *different* currency than the source,
then used the exact same number for both legs — so transferring "100"
would leave as 100 EUR and arrive as 100 USD, quietly inventing or
destroying money. It now only offers same-currency accounts as a transfer
destination. Harmless today (everything is EUR), but would have been a
real, silent correctness bug the first time a foreign-currency account
existed — a good example of why touching code "just for display" is worth
re-reading the logic around it, not only the template.

**Files:** changed `finance/currency.py`, `finance/forms.py` (`TransferForm`),
`finance/views.py` (`account_transfer`), `finance/templatetags/stashy_format.py`,
`finance/templates/finance/{account_list,account_detail,transaction_list,
reminder_list,dashboard,reports}.html`.

### Where things stand for next time

- **Tests:** still 75, all passing (no new ones added for these three
  fixes — they were verified manually against the real data instead,
  since none of them changed a decision worth locking in with a test).
- **`UPGRADE_PLAN.md`** wasn't updated for any of these three — they're
  small fixes/tweaks to already-shipped Phase 3 work, not new plan items.
  Phase 4 (Categories) is next whenever you're ready; nothing here changes
  its scope, just be aware `Account.AccountType` now has `DEBIT_CARD`/
  `OTHER` (not `CHECKING`/`PROPERTY`) and that `|moneyc:currency` is the
  filter to reach for on any new amount tied to one account/currency.

---

## 13/09/2026 — Phase 4: category search, usage, grouping, mass re-categorize

**What changed:** The full Phase 4 checklist — a Parent dropdown that
narrows to matching-type categories as you pick Income/Expense, `is_active`
removed from categories entirely, a redesigned category list (search,
usage counts, Income → Expense → Transfer grouping), and a mass
re-categorize tool.

**Why a client-side JS filter for Parent, not a server round-trip or a
database rule:** the two obvious alternatives both cost more than the
problem is worth. A database constraint (subcategory must share its
parent's type) would need enforcing in the model *and* the form, for a
rule that's genuinely just a helpful nudge — nothing in the plan asked
for it to be unbreakable, and Quicken itself doesn't enforce it either.
A server round-trip (reload the page or fetch new options whenever the
type changes) is the kind of thing that feels laggy for something this
small. Tagging each `<option>` with its own category's type
(`data-category-type`) and hiding mismatches client-side is instant, and
degrades safely if JS is somehow off: every option just stays visible,
matching the old (untyped) behavior instead of breaking.

**Why "usage" only shows a category's *own* transactions, not its
subcategories' too:** the delete flow already has a reason to roll up the
whole subtree (deleting a parent genuinely does cascade-delete every
subcategory beneath it, so the warning has to be honest about everything
that's about to disappear). Re-categorizing is different — you're looking
at *one* category and deciding where its transactions belong; folding in
every subcategory's transactions too would make it unclear which category
a listed transaction is actually sitting in right now, right when that's
the one thing you need to know before moving it somewhere else.

**Why moves are a loop of individual `.save()` calls instead of one
`queryset.update()`:** this project's own rule for audited data (every
automatic or bulk change must go through a model's normal `save()`/
`delete()`, never a bulk queryset method that skips signals) exists
precisely for a moment like this — a mass re-categorize is exactly the
kind of change you'd want to be able to look back at in the audit log
("why did all these move?") if a balance or report ever looks off later.
`queryset.update()` would be faster, but it writes silently: no audit
entry, and it would also skip the payee-memory-refresh logic that a
normal transaction save runs. All the moves from one submit still share a
single audit *operation id*, so "what happened in this one click" is
still answerable — Django's per-request context (already built for
Phase 1B) takes care of that without any extra bookkeeping here.

**A small design choice worth naming:** a transfer category is never
offered as a re-categorize *destination* (Transfer is excluded from the
"move to" list, same exclusion `CategoryForm` already used for its Parent
list). Moving a transaction into one by hand would leave it looking like
a transfer without an actual second leg anywhere — the Transfer button is
the only path that creates both sides correctly, so re-categorize
deliberately doesn't offer a shortcut that would produce a fake half of
one.

**Verification:** 89 tests (14 new), plus a manual check against the real
779-transaction dataset. One of those manual checks briefly moved a real
transaction's category by accident (a POST in a throwaway smoke-test
script) — caught immediately since I was verifying the pages by hand
right after, and fixed by re-running `scripts/load_test_data.py`, which
is exactly the safety net that script exists for. No data was actually
at risk (the reload is idempotent and matches the source file exactly),
but flagging it rather than quietly moving on, since "I touched your real
data, here's what happened" is worth saying even when it self-corrected.

**Files:** new `finance/test_phase4.py`,
`finance/templates/finance/category_usage.html`,
`finance/migrations/0004_remove_category_is_active.py`; changed
`finance/models.py` (`grouped_categories`), `finance/forms.py`
(`CategoryParentSelect`, `CategoryForm`), `finance/views.py`
(`CategoryListView`, new `category_usage`), `finance/urls.py`,
`finance/admin.py`, `finance/templates/finance/{category_list,
generic_form}.html`, `finance/test_dashboard.py`, `finance/test_phase3.py`.

---

## 13/09/2026 — Four Phase 4 refinements after seeing it in action

You asked for four small changes after trying Phase 4: `[Cash]` instead
of "Cash transfers" for transfer categories, a clearer main-vs-subcategory
look, a hard one-level limit on subcategories, and "Parent > Child"
wherever a full category name is shown.

**Why the one-level limit lives in `CategoryForm`, not the database:**
every other category business rule already lives there (transfer
categories can't be edited by hand, a category can't be its own parent),
so this keeps the pattern in one place instead of introducing a second
one. The trade-off, worth naming plainly: the Django admin bypasses all
of these rules today, for every model, not just categories — `AccountAdmin`,
`PayeeAdmin` and friends all use Django's default admin form too. That's
a deliberate existing shape (the admin is documented as a power-user
escape hatch for "anything the UI doesn't cover"), not something this
change tightened or loosened. Two guards were needed, not one: stopping a
subcategory from being *offered* as a parent (the dropdown's queryset)
handles the common case, but editing an *existing* category that already
has children needed its own check too — otherwise you could demote a
category with subcategories into someone else's subcategory, quietly
turning its children into second-level "grandchildren" the UI would then
have nowhere clean to show.

**Why "Parent > Child" took one line, not a find-and-replace:** this
codebase already had exactly one place that builds a category's full
display name (`Category.full_name`), and everything else — the category
list's subcategory rows, the transaction filter dropdown, the delete
confirmation's subcategory list, the audit log's category diffs, the
mass-recategorize "move to" list — calls it (directly or through
`Category.__str__`) rather than rebuilding the string itself. That's the
payoff of putting a formatting decision in exactly one place the first
time: changing it later is a one-line diff instead of a search-and-check
across a dozen templates.

**A real bug this surfaced, not just a style question:** while adding
"a category with children can't become a subcategory," it became clear
the *old* validation (`clean_parent`'s loop-of-ancestors check) was
solving a more general problem than the app needs now. With parent
choices restricted to top-level categories only, a genuine multi-level
loop is already structurally impossible — so the check simplified down to
the one case that can still happen: a top-level category selecting
*itself*. Removing the now-redundant generality made room to actually
add the new, real check (children-blocks-demotion) cleanly instead of
bolting it onto logic already doing more than it needed to.

**Verification:** 95 tests (6 more), plus re-checking the real dataset —
confirmed the Parent dropdown on Add/Edit Category now lists exactly the
21 real top-level categories, transfer categories render as `[Cash]` etc.,
and subcategory rows read "Parent > Child" throughout.

**Files:** changed `finance/models.py` (`ensure_transfer_category`,
`full_name`, `parent` field docstring), `finance/forms.py`
(`CategoryForm.__init__`/`clean_parent`), `finance/templates/finance/
category_list.html`, `finance/static/finance/css/stashy.css`,
`finance/test_phase4.py`, `finance/tests.py` (one stale format assertion).

---

## 13/09/2026 — Phase 5: transaction filters, sorting, register upgrades

**What changed:** The full Phase 5 checklist — date-range presets and a
reconciled/unreconciled filter on the transaction list, an editable
transaction number shown as a column, Payment/Deposit split instead of
one signed Amount column, an inline "add a transaction" row at the
bottom of an account's register, sortable column headers, and a "this
year" default view.

**Why "this year" only applies when nothing at all was asked for, not
whenever the filters happen to be empty:** the naive version of "default
to this year" would keep re-imposing itself every time you clicked "All
time" and it came back empty-looking on the next click of an unrelated
filter. The fix was realizing HTTP query strings can represent "you asked
for nothing" and "you asked for `range=` (empty, meaning all time)" as
two different things — Django's `QueryDict` lets you check whether a key
was submitted at all, not just what its value was. That distinction is
what makes "All time" sticky instead of fighting the default.

**Why Payment/Deposit strips the sign:** D3 asked for two columns instead
of one, but the point of splitting a column by direction is that the
column *itself* now carries that meaning — showing "-20.00" under a
column literally labeled "Payment" says the same thing twice, the same
way you wouldn't write "expenses: -50" on a receipt that already has an
"Expenses" heading. Small thing, but it's the difference between the
split actually reading as cleaner and it just being the old column
copy-pasted twice.

**Why the inline add row went on the account page, not the all-accounts
transaction list:** it needs one definite account to create the
transaction against and to autofill sensible defaults for — the all-
accounts list exists specifically to *not* assume one account. This also
matches what "the register" has always meant in Quicken: one account's
own transaction history, not a cross-account report. If a cross-account
quick-add ever becomes worth it, it would need its own account picker
in the row, which felt like a different, bigger feature than "add a row."

**A bug the editable Number field surfaced, not just a feature:**
`Transaction.save()` only ever auto-assigned a number when a transaction
was first created — reasonable when the field wasn't editable, since
there was no other way to end up with a blank one. The moment editing was
allowed, that same "only on create" rule meant clearing the field on an
existing transaction would silently save it as blank instead of picking
a new number, because nothing was watching for that case on an update.
Fixed in `TransactionForm.save()` rather than the model, since it's
specifically a "the person just told me to renumber this" moment — the
model's own auto-numbering stays about first creation.

**Verification:** 115 tests (20 new). Checked every new filter/sort
combination against the real 779-transaction dataset read-only, and
tested the inline add row's actual database write against a **scratch
copy** of `db.sqlite3` — a direct lesson from the Phase 4 session, where
a smoke-test POST briefly moved a real transaction's category. This time
the write path was verified without ever pointing at the real file.

**Files:** new `finance/test_phase5.py`,
`finance/templates/finance/includes/sort_arrow.html`; changed
`finance/forms.py` (`TransactionForm` gains `transaction_number`, new
`InlineTransactionForm`), `finance/views.py` (`_sorted`, rewritten
`transaction_list`/`account_detail`), `finance/templatetags/
stashy_format.py` (`abs_value`), `finance/templates/finance/
{transaction_list,account_detail,transaction_form}.html`,
`finance/static/finance/css/stashy.css`, `finance/test_dashboard.py`.

---

## 13/09/2026 — Phase 6: payee memory computed from history, not cached

**What changed:** Removed `Payee.default_category`/`default_memo`/
`default_amount` entirely, replacing them with `category_history()` — one
entry per category a payee has ever been used with, always computed
fresh from the transactions themselves. A payee detail page shows the
full breakdown; the transaction form and the register's inline add row
now offer a pick list when a payee has more than one; the payee list
gained search/filter/sort; the two system payees stay out of the typing
suggestions.

**Why this is a deletion, not just an addition:** the old design had a
real, structural problem the plan named directly — `default_category`
etc. were a *cache* of "whatever the last transaction happened to be,"
kept in sync by a signal-like check in `Transaction.save()`
(`_memorized_fields_changed()`) that had to run on every save and get
the "did a memorized field actually change" logic exactly right. That's
the shape of bug that's easy to get subtly wrong (Phase 1 already found
one: it used to refresh on *every* save, even toggling `reconciled`).
Computing the same information live from the transactions that already
exist doesn't just avoid a *class* of future bugs — it deletes the code
that could contain them. `Transaction.save()` is shorter now, not longer,
despite the feature getting richer (one memory per category instead of
one, ever).

**Why the payee list needed to stop being a plain `ListView`:** sorting
by "the category of a payee's most recent transaction" isn't a column
that exists on `Payee` — it has to be computed. The tempting shortcut
would be calling `category_history()` once per payee while building the
page, but that's a query per row on a page that can show over a hundred
payees at once (this dataset already has 156). A single correlated
subquery (each payee's own most-recent transaction id, fetched in one
query) plus one bulk fetch of those specific transactions keeps the page
at a small, fixed number of queries regardless of how many payees exist
— the same query-count discipline Phase 1 first put in place for the
transaction register, applied here to a page that hadn't needed it yet
because it used to just read three flat fields off `Payee` directly.

**Why the "multiple categories" case gets a pick list instead of picking
the most recent one automatically:** silently guessing wrong is worse
than asking. The whole point of "per-category memory" is a payee like a
pharmacy that's sometimes a health expense and sometimes a gift — if
Stashy silently applied whichever was most recent, it would get it wrong
exactly as often as the categories alternate, defeating the feature. A
single-category payee still autofills instantly (nothing to choose
between), so this only asks when there's an actual decision to make.

**A nice side effect of writing the JS as a shared module:** the
transaction form and the account register's inline row had near-
identical autofill `<script>` blocks already (copy-pasted when Phase 5
added the inline row). Giving the pick-list behavior to both would have
meant pasting it a second time; writing it once in
`payee-autofill.js` instead means both surfaces get the new behavior
from the same code, and any future fix only has one place to happen.

**Verification:** 134 tests (18 new), plus checking every new page/filter
against the real 779-transaction dataset read-only — including finding
an actual multi-category payee in it (a bookshop: stationery and
home decor) and confirming its pick list shows both.

**Files:** new `finance/test_phase6.py`, `finance/templates/finance/
payee_detail.html`, `finance/static/finance/js/payee-autofill.js`,
`finance/migrations/0005_payee_history_not_cached.py`; changed
`finance/models.py` (`Payee.category_history`, simplified
`Transaction.save`), `finance/forms.py` (`PayeeForm`), `finance/views.py`
(`payee_list`, `payee_detail`, `api_payee_autofill`, `api_payee_list`,
`_guessed_category`), `finance/urls.py`, `finance/admin.py`,
`finance/templates/finance/{payee_list,transaction_form,
account_detail}.html`, `finance/static/finance/css/stashy.css`,
`finance/tests.py`, `audit/tests.py`, `finance/test_dashboard.py`.

---

## 13/09/2026 — Phase 6.1: fixes from actually using Phases 3–6

**What changed:** A batch of corrections and small requests after you'd
clicked through what the last several phases shipped — the payee list's
columns, case-insensitive payee matching, a styled payee dropdown, the
edit-transaction redirect, reverting the inline add row, two more
sortable columns, a couple of UI polish items, and two investigations
("where did my reminder go" and "why do new accounts start at 1").

**The reminder investigation is the one worth explaining properly.**
Since Phase 3, whenever a schema change needed the test dataset reloaded,
I re-ran `scripts/load_test_data.py` — a script whose whole job is to
wipe the finance tables and rebuild them from `test_data.TXT`. That was
exactly right the day it was written, when the database held nothing but
a handful of throwaway rows from early manual testing. It quietly stopped
being right the moment you started actually using Stashy *between* my
sessions — every rerun since then wiped anything you'd typed into the
real app, reminders included, with no warning that it was about to
happen. Checking the database backups taken right before each of those
reruns confirmed it: a reminder due 14/09/2026 (the "tomorrow" one) is
sitting right there in the backup from just before Phase 3's migration,
and gone in the very next one. I hadn't registered that "reload the test
data" and "the user might have entered real data since last time" were
now in tension — the script's own docstring still described a database
that no longer existed by the time I was running it. Fixed by making that
docstring loudly say so, and by changing my own practice: no more
reflexive reruns without checking whether there's real data at stake
first. I can't bring back what was already deleted, but I'd rather you
have the precise explanation than a vague "not sure."

**The transaction-numbering question turned out not to be a bug.** Two
brand-new accounts each showing "1" as their opening balance's number is
`Transaction.next_number()` working exactly as D9 specified: numbering is
scoped per account, per year, on purpose — "matches how a physical
checkbook works" was the reasoning at the time, and two different
checkbooks both starting at check #1 isn't a collision, it's just two
separate books. I checked the actual code path and the actual data
(two real test accounts, each with exactly one transaction, each
correctly numbered 1) rather than assuming either way, and it's behaving
as designed. Worth naming clearly since it means there's a real design
question underneath the bug report, if a shared global sequence is
actually what's wanted instead — that would be revisiting D9, not
patching a bug.

**Why the inline add row got reverted rather than patched:** you tried
it and decided the dedicated New Transaction page was already good
enough on its own — simpler is better here, and the honest move is to
delete `InlineTransactionForm` and its view/template wiring rather than
leave a second, now-unused way to create a transaction sitting in the
codebase "just in case."

**Why the payee-name dropdown needed real JS, not just CSS:** the
browser's native `<datalist>` popup can't be restyled at all in most
browsers — no border-radius, no theme colors, no dark mode. Matching "part
of the UI" meant building the dropdown ourselves (reusing the same
pick-list pattern Phase 6 already introduced for category combos) rather
than fighting a browser control that was never going to look right.

**Verification:** 144 tests (28 new), plus a read-only pass over the real
dataset for every changed page — no repeat of the Phase 4 mishap where a
smoke test briefly mutated real data.

**Files:** new `finance/test_phase6_1.py`; changed `finance/models.py`
(nothing structural - see below), `finance/forms.py` (`_resolve_payee`
case-insensitive, `AccountForm` notes widget, `InlineTransactionForm`
removed), `finance/views.py` (`payee_list` rewritten, `_get_or_create_payee`,
`transaction_form_view`'s `next` handling, `account_detail` simplified,
sort field dicts), `finance/static/finance/js/payee-autofill.js`
(name-typeahead), `finance/static/finance/css/stashy.css` (`.payee-field`,
`.usage-empty`), `finance/templates/finance/{payee_list,account_detail,
transaction_list,transaction_form,category_list,includes/
sidebar_contents}.html`, `scripts/load_test_data.py` (docstring),
`finance/test_phase5.py`, `finance/test_phase6.py`. No migration — every
change here is behavior/display, not schema.

---

## 13/09/2026 — D9 revised: transaction numbers are per-year only, not per-account

**What changed:** after the last entry explained *why* two new accounts
both showing "1" wasn't a bug (D9 originally scoped numbering to
(account, year), on purpose, "like two different checkbooks"), you asked
for the design itself to change: one shared sequence per year, across
every account. `Transaction.next_number()` dropped its account filter —
the highest number for the year, full stop, plus one.

**Why this was a one-line change with no data migration:** the numbering
rule was never a database constraint, just Python logic that runs when a
transaction is first saved (`Transaction.save()`) or when its number is
cleared on an edit. The `transaction_number` column itself doesn't care
*how* the number was chosen. The only migration this needed
(`0006_transaction_number_help_text.py`) is a metadata-only one Django
wanted for the field's help text, which I updated to stop saying "per
account." Historical imported data (`test_data.TXT`'s own `Num` column)
is untouched either way, since those numbers were carried over as-is,
never computed by this rule.

**Why this counts as revising D9, not patching a bug:** the previous
entry already established the old behavior was intentional, not broken —
so this isn't "found and fixed a mistake," it's "the person who owns the
decision changed their mind after seeing it in practice," which is a
completely normal and expected outcome of trying a design out. Recorded
directly in D9's own text (with the date) rather than quietly editing it,
so the reasoning trail stays intact for anyone reading the decisions log
later and wondering why an account's very first transaction doesn't
start at 1.

**Verification:** full test suite (144, with `TransactionNumberingTests`
updated to assert the new shared-sequence behavior), plus a scratch copy
of the real database confirming two new accounts get sequential numbers
(10779, then 10780) instead of both landing on 1.

**Files:** changed `finance/models.py` (`Transaction.next_number`),
`finance/forms.py` (help text), `finance/test_phase3.py`; new
`finance/migrations/0006_transaction_number_help_text.py`.

---

## 13/09/2026 — Reminder's Name removed; new-transaction number previewed

**What changed:** two more quick ones. `Reminder.name` is gone entirely
(model, form, admin) — a reminder is identified the way a pending
transaction would be, by payee first and category as a fallback, matching
`Transaction.__str__`'s own approach. And the New Transaction page now
shows the number it's *about* to assign, instead of leaving the field
blank until you save and find out.

**Why `Reminder.__str__` mirrors `Transaction.__str__` instead of getting
its own new idea:** the user's own framing ("I want to see them kinda
like pending transactions") is doing real design work here, not just
describing a feeling — a reminder genuinely *is* an unrealized
transaction, so reusing the same "what identifies this row" logic that
already exists for a real transaction (payee, then category, then a
generic fallback) means one mental model covers both instead of two
subtly different ones a reader has to keep straight.

**Why the number preview reads it fresh rather than caching anything:**
`Transaction.next_number()` was already a pure function of "today's
transactions so far this year" — calling it once more, at page-load
time for the initial value, costs nothing architecturally new. The
preview can go stale if you then change the date to a different year
before saving (it won't silently recompute), which is an acceptable,
disclosed limitation rather than building a live-recalculating field for
a small convenience — clearing the number by hand still gets you a
correct fresh one for whatever date ends up on the form, same as before.

**Verification:** full suite (151 tests, 7 new), plus a read-only check
against the real database — an existing reminder now reads
"16/09/2026 | Landlord | 10.00", and a fresh Add Transaction page prefilled
10779, matching what `Transaction.next_number()` computes directly.

**Files:** new `finance/migrations/0007_remove_reminder_name.py`,
`finance/test_phase6_2.py`; changed `finance/models.py` (`Reminder`),
`finance/forms.py` (`ReminderForm`, transaction number help text),
`finance/admin.py`, `finance/views.py` (`reminder_enter`,
`transaction_form_view`), `finance/templates/finance/{reminder_list,
dashboard}.html`, `finance/tests.py`, `finance/test_dashboard.py`.

---

## 13/09/2026 — A real bug: payee categories weren't actually distinct

**What happened:** you asked for the payee list to show only distinct
categories, one per row. Building the "one per row" part first (easy),
I went to check the "distinct" part expecting it to already be true —
and found it wasn't. The water company's payee was listing
"Bills & Utilities > Water" **eight times**, once for every transaction,
not once for the category.

**Why `.distinct()` didn't distinct:** this is a genuine Django gotcha,
not a logic mistake in the grouping code itself. `Transaction` has a
default ordering (`Meta.ordering`, by date) so that plain `Transaction.
objects.all()` always comes back newest-first without every caller
asking for it explicitly. That's normally invisible and convenient — but
when you combine a default ordering with `.values(...).distinct()`,
Django has to keep the SQL valid: `ORDER BY` columns generally have to
appear in `SELECT DISTINCT`'s column list, so Django quietly adds them.
The result: `SELECT DISTINCT payee_id, category_id, ...` silently became
`SELECT DISTINCT payee_id, category_id, ..., date, transaction_number,
created_at` under the hood — and once date is in there, *every*
transaction is "distinct" again, because no two transactions share the
exact same date and creation timestamp. The fix is one call:
`.order_by()` right before `.distinct()`, which clears that inherited
ordering for this one query and lets DISTINCT mean what it says.

**Why this was worth catching now rather than shipping "looks right in
my small test":** my own Phase 6.1 unit test for this exact code
(`test_each_row_shows_all_categories_total_amount_and_usage`) used a
payee with only *one* transaction per category — which never triggers
this bug, since one row can't collide with itself. It passed cleanly and
told me nothing was wrong. The bug only shows up with real, messier data
(a payee used many times in the same category), which is exactly why
"looks right on real data too" checks keep earning their place in this
project's process even after the automated tests are green — added a
second test now that specifically re-creates the many-transactions-one-
category shape, so this can't quietly regress.

**The other two changes were straightforward by comparison:** the
dashboard's Accounts panel now reuses `grouped_accounts()` (the same
function the Accounts page already uses) instead of a separate plain
alphabetical query, so the two pages agree on ordering without a second
implementation to keep in sync; and the spending donut's center number
gets the main currency appended in a smaller sub-label, matching how
every other dashboard figure already discloses its currency.

**Verification:** full suite (156 tests, 5 new), plus re-checking the
exact real payee that exposed the bug — it now shows "Water"
once, with the correct 8-transaction count and summed total.

**Files:** changed `finance/views.py` (`payee_list`'s category query,
`dashboard`'s account grouping), `finance/templates/finance/{payee_list,
dashboard}.html`, `finance/static/finance/js/dashboard.js`,
`finance/static/finance/css/stashy.css`; new `finance/test_phase6_3.py`.

---

## 13/09/2026 — Phase 7: Reminders redesigned (optional amount, Skip, real "Paid" status, Enter opens a form)

**What changed:** `Reminder.amount` is now optional (variable bills);
`is_active` is gone in favor of a **Skip** action that just advances the
due date; `auto_enter_days_before` (confirmed unused anywhere but its own
field/form entry) is deleted; **Enter** no longer silently creates a
transaction — it opens the normal transaction form pre-filled from the
reminder, so you can review or fill in a variable amount before saving;
the list is redesigned to Status/Due/Type/Account/Payee/Amount with
Enter/Skip/Edit/Delete, plus filter buttons (15/30/90/365 days) and a
"Show paid" toggle; reminders can auto-resolve to the last day of their
period (D5) instead of a fixed date; and the Add/Edit form's Category
dropdown now narrows to match the picked Income/Expense type, the same
JS pattern Categories already used.

**The interesting design problem: what does "Paid" even mean here?**
Stashy's Reminder model deliberately stores no link to the transactions
it generates (D6) — deleting a reminder was never supposed to touch your
transaction history, and that's still true. But a *computed* "Paid"
status needs to know whether the current occurrence has actually been
entered, and there was nothing to compute that from. I asked directly
rather than guess at something that would have been awkward to walk back
later; the answer was to add exactly one field for it:
`Reminder.last_entered_transaction`, a one-way, nullable
`on_delete=SET_NULL` foreign key set only when you press **Enter** (never
Skip). Because it points *from* the reminder *to* the transaction, the
non-cascading guarantee from D6 falls out for free — deleting a reminder
just deletes that row, the transaction it once pointed at doesn't know or
care. Deleting the transaction, on the other hand, nulls the FK
automatically (SQLite/Django's `SET_NULL` behavior), which is exactly the
"if the transaction gets deleted, change the status" the user asked for,
with no extra code needed.

**Status is computed with a priority order**, not three independent
checks: overdue beats due-soon beats paid beats pending. That ordering is
what makes "Paid" a temporary state instead of a permanent label — once
the reminder's *next* real due date actually gets close again, "due soon"
naturally overrides the leftover paid link from last cycle without either
status needing to know about the other. I added "Overdue" as its own
visible label (red) rather than folding it into "Due soon" (amber), since
the plan only named three states but the underlying data already tells
these two apart and a bill three weeks late shouldn't look the same as
one due in five days.

**Skip clears the paid link on purpose.** If it didn't, skipping a
reminder that still had an old `last_entered_transaction` from last month
could show "Paid" again the moment its date drifted back out past the
due-soon window — technically true from stale data, but misleading, since
you explicitly said "not this one" by skipping.

**A real bug turned up while rewriting `advance_due_date()`:** its audit
log message still built a string from `self.name` — a field removed in
Phase 6.2, earlier the same day. Since `advance_due_date()` runs on every
Enter (and, before today, was the *only* way to advance a reminder), this
meant **every reminder entered since the Name removal would have raised
`AttributeError` and failed**, before this session ever got back to
Reminders. Caught by inspection while restructuring the method for the
end-of-period logic, not by a failing test — Phase 6.2's own tests never
exercised the advance step through `reminder_enter`, only `__str__`
directly. Fixed by rebuilding the message from payee/category the same
way `__str__` already does, and added test coverage that actually POSTs
through Enter/Skip rather than calling the model method in isolation, so
this class of gap doesn't recur.

**Smaller judgment calls, all documented in `UPGRADE_PLAN.md`:** "yearly"
in the filter buttons reads as a 365-day window, following the pattern of
the other three; end-of-period biweekly anchors to the Monday of the due
date's own week (matching the weekly convention); the Category dropdown
filter reuses Phase 4's existing `CategoryParentSelect` widget as-is,
since Reminder's category field already excludes transfer categories.

**Verification:** full suite (188 tests, 32 new in
`finance/test_phase7.py`). Migration `0008_phase7_reminders.py` applied
to the real database after checking its one real reminder first (active,
default settings — nothing meaningful lost); re-checked read-only
afterward that the dashboard, reminder list, reminder edit form, and the
Enter flow (`?reminder=<id>`) all render correctly against it.

**Files:** `finance/models.py` (`Reminder`), `finance/forms.py`
(`ReminderForm`), `finance/views.py` (`reminder_list`, new
`reminder_skip`, `transaction_form_view`, dashboard's due-soon query,
removed `reminder_enter`), `finance/urls.py`, `finance/admin.py`,
`finance/templates/finance/{reminder_list,generic_form,
transaction_form}.html`, `finance/test_dashboard.py`; new migration
`0008_phase7_reminders.py` and `finance/test_phase7.py`.

---

## 14/09/2026 — Phase 7.1: auto-generated future reminder occurrences, and a real "one-time" frequency

**What changed:** entering or editing a recurring reminder now
auto-generates its future occurrences as ordinary, independently
actionable `Reminder` rows instead of only ever holding a single due
date; a new `Frequency.ONE_TIME` (now the default) covers reminders that
were never meant to recur; and Enter/Skip on a one-time reminder (a real
one-off, or one of these new generated occurrences) marks it Paid/Skipped
and keeps the row instead of deleting it.

**Why this needed a real conversation, not just code:** Phase 7 (built
the day before) gave every reminder a single self-advancing due date -
correct for "what's the next thing due," but the user's actual mental
model, based on Quicken, was different: one recurring definition that
*materializes* several real, independently-actionable future rows ahead
of time. "I created a monthly reminder for the next few months, when I
entered it, there is no new reminder created" was the tell - they
expected paying September to still leave October and November sitting
there, not collapse back down to a single line. Two design points were
genuinely ambiguous enough to ask about rather than guess:
1. **How far ahead to generate when there's no end date.** The user's
   own phrasing - "90 days or yearly, whichever is bigger" - looks like
   it trivially reduces to "always yearly" (365 > 90 always), so I asked
   to make sure I wasn't missing something. Their answer reframed
   "yearly" as **31 December of the current year**, not a rolling
   365-day window - which makes "whichever is bigger" a real, changing
   comparison: in March, year-end is ~9 months out and wins easily; by
   mid-November, 90 days spills past New Year's and wins instead. This
   also meant going back and fixing the "Yearly" filter button Phase 7
   had already shipped the day before, which used a rolling 365 days -
   it now means the same thing everywhere.
2. **What happens to a one-time reminder once it's dealt with.** Enter or
   Skip leaves nothing to advance to, so the row could either vanish or
   stick around as a record. The user chose to keep it, visibly marked
   Paid/Skipped, which is *not* what the equivalent Phase 7 "Paid" status
   does for a recurring reminder (there, it deliberately fades back to
   "due soon" once the next real occurrence approaches) - for something
   with no next occurrence, there's nothing for it to fade back into, so
   it just stays resolved.

**The data model question underneath: materialize rows, or compute them
on the fly?** A recurring reminder could show its future occurrences as
virtual, uncommitted projections (nothing in the database beyond the one
row) instead of real ones. That would need zero regeneration bookkeeping
- always trivially correct, since nothing is ever stale. I went with real
rows anyway, because the user's own language ("auto-create reminders...
if the end date changes, the records change") and the ask to enter one
future month without disturbing the others both point at independently
editable, independently actionable rows - which a virtual projection
can't be (you can't hand-edit or individually Skip a row that doesn't
exist yet). The trade-off is the regeneration logic this now requires.

**Regeneration lives on `Reminder.save()`, following existing precedent.**
`Account.save()` already calls `ensure_transfer_category()` after saving
(Phase 3) so a transfer category always exists without every call site
having to remember to create one. I used the same shape here:
`Reminder.save()` calls `self.regenerate_occurrences()` for any
non-one-time reminder, so direct ORM use (the admin, a script, the
Django shell) keeps a series in sync exactly like the form does, rather
than the auto-generation only working when a request passes through
`ReminderForm`. `advance_due_date()`'s own save (used by Enter/Skip on
the current occurrence) triggers the same path, so the generated set
re-tops-up automatically after every Enter/Skip on the series head
without a separate call.

**Two rules keep regeneration from ever clobbering something you did on
purpose:** it never deletes an occurrence that's already been paid or
skipped, even if a shortened end date would otherwise drop it, and it
never rewrites the fields of an occurrence that already exists for a
wanted date - so if you open one specific month's occurrence and bump
its amount by hand, the next regeneration (triggered by editing the
series' notes, say) leaves that edit alone. Only genuinely missing dates
get created and genuinely no-longer-wanted, unresolved dates get removed.

**A real reminder existed before this feature did.** The database's one
real recurring reminder (monthly, due 16/10/2026, ending 24/12/2026)
predates the `series`/`skipped` fields, and Django migrations don't run
model signals or call `.save()` - so after migrating, it still had zero
generated occurrences until *something* saved it again. Rather than wait
for the user to next edit or enter it, called
`reminder.regenerate_occurrences()` once by hand against the real
database, which produced exactly the November and December occurrences
the series should already have.

**Verification:** full suite (203 tests, 15 new in
`finance/test_phase7_1.py`; a handful of Phase 7's own tests updated
where they depended on the old MONTHLY default or the renamed
`show_paid` parameter). Migration applied to a fresh backup of the real
database; confirmed read-only afterward that the real reminder's two
generated occurrences appear in the reminder list with the new
auto-generated marker, and that the dashboard still renders.

**Files:** `finance/models.py` (`Reminder`: `Frequency.ONE_TIME`,
`series`, `skipped`, `status()`, `regenerate_occurrences()`, `save()`),
`finance/forms.py` (`ReminderForm`), `finance/views.py`
(`reminder_list`, `reminder_skip`, `transaction_form_view`'s reminder
branch, dashboard's due-soon query), `finance/templates/finance/
{reminder_list,generic_form}.html`; new migration
`0009_reminder_auto_generated_occurrences.py` and
`finance/test_phase7_1.py`.

---

## 14/09/2026 — Phase 7.2: polish batch, and a course-correction on asking first

**What changed:** a mixed batch of 12 requests after using Phase 7.1 for
real - default 15-day reminder window, reordered/renamed "Last day of
period" field, alphabetical category dropdowns (subcategories next to
their parent instead of two separate blocks), a free `interval` field
replacing the fixed `BIWEEKLY` choice, Enter's Memo preferring
payee+category history over the reminder's generic notes, signed
reminder amounts everywhere, a removed marker icon, an explanatory
delete-confirmation for reminders, currency on every dashboard donut
amount, a Delete button on the all-accounts transaction list, and
renamed status-filter labels.

**The course-correction:** partway through triaging this batch, the user
said directly - "i feel like you forgot i ordered you to ask me about
vague things before deciding for yourself." They were right to flag it.
Most of the dozen items in the message were concrete enough to just
build (default window, field reorder, label renames, signed amounts),
but three were not, and the right move was to separate them out and ask
rather than let "most of this batch is clear" excuse guessing on the
rest:
1. **Reminder Edit, "this instance or all"** - the user wants editing a
   reminder that's part of a series to ask which scope to edit at. But
   *what* "all" actually changes was never specified, and every option
   has real consequences: does it update just the root's definition (so
   only *future* auto-generated occurrences pick it up), or every
   still-open occurrence too? Does an already-paid/skipped occurrence
   ever get touched? Given `regenerate_occurrences()` was deliberately
   built in Phase 7.1 to *never* rewrite an existing occurrence's fields
   (so a hand-edited one survives regeneration), silently picking an
   interpretation here risked contradicting a decision already made
   carefully the day before.
2. **"Make the delete template a little bit more explanatory, I don't
   like the way it shows currently"** - no specifics attached. Guessing
   at *what* was disliked (tone? missing information? layout?) and
   shipping a redesign risked missing the actual complaint entirely and
   needing a second round anyway.
3. **"Make reconciled and not transactions a little bit different to
   look"** - open on *how* (icon, muted row, background tint, badge).

**Why this is worth writing down, not just fixing:** the previous
session's own workflow rule (asking numbered questions before building)
was written for *whole requests* being big or unclear. This batch was
mostly small, clear, incremental asks - exactly the shape where it's
easy to read "the request overall is manageable" as permission to guess
on the few parts that aren't. Updated the standing memory on this
(`feedback-plan-first-for-big-requests`) to say explicitly: triage
per-item inside a batch, not just at the batch level.

**A real design fix found while implementing the delete-warning
requirement, not asked for:** building "warn how many occurrences a
series-head delete would also remove" surfaced that `Reminder.delete()`
didn't distinguish resolved occurrences from open previews - `series` is
`on_delete=CASCADE`, so deleting a recurring reminder silently deleted
*every* occurrence it had ever generated, including ones already paid or
skipped, which are real records at that point, not previews. Fixed:
`Reminder.delete()` now detaches (`series` -> null) any already-resolved
occurrence before the cascade runs, so only still-open ones go with the
series; the confirmation's occurrence count reflects the same rule.

**Also noted for future sessions:** the user confirmed the database
still holds only disposable test data and gave standing permission to
add, change or delete it freely for visualization, until the real data
replaces it after the upgrade finishes - recorded in the
`project-stashy-upgrade-plan` memory so a future session doesn't default
back to scratch-copy caution unnecessarily.

**Verification:** full suite (222 tests, 19 new in
`finance/test_phase7_2.py`). Migration applied to a fresh backup of the
real database; confirmed read-only afterward that the dashboard,
reminder list, reminder edit, and transaction list all render, and the
real recurring reminder picked up `interval=1`/`end_of_period=False`
correctly.

**Files:** `finance/models.py` (`Reminder`: interval, `_advance`/
`_period_end` generalized, `delete()`, `categories_alphabetical()`),
`finance/forms.py` (`ReminderForm`, `TransactionForm`,
`DateRangeFilterForm`), `finance/views.py` (`reminder_list`,
`ReminderDeleteView`, `TransactionDeleteView`, `transaction_form_view`'s
memo prefill), `finance/admin.py`, `finance/templates/finance/
{reminder_list,reminder_confirm_delete,generic_confirm_delete,
generic_form,transaction_list,dashboard}.html`,
`finance/static/finance/js/dashboard.js`; new migration
`0010_reminder_interval_and_field_order.py`, new
`finance/templates/finance/reminder_confirm_delete.html` and
`finance/test_phase7_2.py`.

---

## 14/09/2026 — Phase 7.2 continued: the three questions, answered

**What changed, once the three questions above were answered:**

1. **Reminder Edit's series scope.** Answer: "the whole series" updates
   the root's own definition *and* every still-open, already-generated
   occurrence - not just future ones. Implemented as a small set of
   shared fields (`account, payee, category, amount, reminder_type,
   notes`) that get copied from whichever row you actually edited onto
   the root and onto every other open occurrence; schedule fields
   (frequency, interval, the dates) stay per-row on purpose, since they
   describe *when* a specific row is due, not what it's for - syncing
   those across rows with different dates wouldn't mean anything.
   Editing that root is what routes through `ReminderUpdateView` before
   (now `reminder_edit`, a function view, since a class-based `UpdateView`
   had nowhere to hang the extra "which scope?" step). A reminder with no
   series connection at all skips the prompt and behaves exactly as
   before - no new friction for the common case.
2. **Delete page.** Answer: "too plain/abrupt." Since
   `generic_confirm_delete.html` is the one template every delete flow in
   the app extends, fixing it once fixed accounts, categories, payees,
   reminders and transactions together - a `.stat-icon-danger` badge
   (matching the dashboard's own icon-tile language), the warning
   promoted from muted gray text to a proper red alert line with an icon,
   and the button relabeled "Yes, delete it" for less ambiguity about
   which side is which.
3. **Reconciled rows.** Answer: "muted/faded." Used CSS `opacity` on the
   row rather than a flat muted text color on its cells, specifically so
   it doesn't fight the red/green payment-deposit coloring for
   specificity - opacity dims the whole row uniformly, so a faded red is
   still readably reddish rather than turning gray.

**Why asking first paid off here specifically:** all three had more than
one reasonable-sounding answer, and at least one guess would likely have
been wrong. For the edit scope in particular, guessing "root only, never
touch existing occurrences" (matching how `regenerate_occurrences()`
already behaves) would have been a defensible-sounding default - but it's
exactly the option the user didn't pick, and shipping it would have meant
rebuilding this same feature a second time.

**Verification:** full suite (231 tests, 9 more in
`finance/test_phase7_3.py`). Re-checked against the real database: the
one real recurring reminder's edit page now shows the scope prompt, and
the transaction list's reconciled rows (726 of ~779) render faded.

**Files:** `finance/views.py` (new `reminder_edit`,
`_propagate_series_edit`, removed `ReminderUpdateView`),
`finance/urls.py`, `finance/templates/finance/
{reminder_edit_scope,generic_form,generic_confirm_delete,
transaction_list}.html`, `finance/static/finance/css/stashy.css`
(`.stat-icon-danger`, `.row-reconciled`); new
`finance/templates/finance/reminder_edit_scope.html` and
`finance/test_phase7_3.py`.

---

## 14/09/2026 — Quick polish pass, and the other half of the balance

**What changed:** an amountless reminder now shows blank instead of
"Varies"; the reminder delete page reads "Delete Income reminder for
Landlord?" instead of stitching the reminder's dry `date | payee |
amount` string into "Delete X?"; the delete page is centered instead of
sitting in the top-left corner of a wide column; and the dashboard
donut's currency is a real symbol (€, $) everywhere it appears, not the
3-letter code, matching how every other amount on the page already
looks via `|moneyc`.

**The other half of "ask before deciding":** earlier today the user
corrected a case of guessing on genuinely open design forks. This round
was the opposite correction - four straightforward polish calls (an
empty field showing a placeholder word instead of nothing, dry object
`__str__` leaking into a confirmation sentence, a page not centered,
a chart using a currency code instead of a symbol) that a senior
developer should just fix with good taste, not multiple-choice back to
the user. Both corrections point at the same underlying judgment: reserve
questions for choices with more than one defensible answer and real
consequences either way (the reminder edit-scope semantics, what "more
explanatory" should mean) - not for quality/polish calls with an
obviously-right answer once you look at them like a user would.

**The `|moneyc` inconsistency was the interesting bit.** The donut
legend/tooltip/center were built in an earlier session as pure
client-side JS, using the raw currency *code* from a `data-main-currency`
attribute - reasonable at the time, but it never actually matched how
`|moneyc` (the template filter every other amount on the page goes
through) renders the same currency: a symbol, with a prefix/suffix rule
that varies by currency ("$9.66" vs "9.66 €"). Fixed by exposing the
same `CURRENCY_SYMBOLS` table to templates as a new `|currency_symbol`
filter, and passing a `currency_is_suffix` flag alongside the symbol so
the JS can place it the same way `|moneyc` would, instead of always
suffixing (which happened to look right only because this user's main
currency, EUR, is itself a suffix currency).

**Verification:** full suite (235 tests, 4 more in
`finance/test_phase7_4.py`; one Phase 6.2 test updated for the
code -> symbol change). Re-checked against the real database: the
dashboard shows "€", the real recurring reminder's delete page reads
"Delete Income reminder for Landlord?", and an amountless reminder's amount
column is blank.

**Files:** `finance/templates/finance/{reminder_list,
reminder_confirm_delete,generic_confirm_delete,dashboard}.html`,
`finance/templatetags/stashy_format.py` (`currency_symbol`),
`finance/views.py` (`dashboard`'s `currency_is_suffix`),
`finance/static/finance/js/dashboard.js` (`formatAmount`).

---

## 14/09/2026 — Phase 8: Reconciliation

**What changed:** a **Reconcile** button on the account page opens a
Quicken-style two-step flow - state your statement's ending date and
balance, then check off which unreconciled transactions it covers, with
a running total that updates live as you check boxes. Finishing marks
everything checked `reconciled` (and `cleared`); if there's a leftover
difference you can accept it, which creates a single adjustment
transaction so the account balances exactly. A new `Reconciliation`
model records each session (account, statement date/balance, opening
balance, the adjustment transaction if any) as an audit trail.

**Two implementation choices worth explaining, neither asked about since
both are single-clearly-right-answer calls once you look at what the
alternatives would actually do:**

- **Opening balance = sum of everything already reconciled**, not a
  literal "balance up to the date of the first unreconciled transaction"
  as the plan's own wording suggested. Both give the same number in the
  ordinary case (you reconcile in chronological order), but the
  sum-of-reconciled version is a plain aggregate query and stays correct
  even if something were reconciled out of order for any reason - the
  "find the earliest unreconciled row, then sum everything strictly
  before its date" version would need to special-case an account with
  nothing reconciled yet (no "first unreconciled row" boundary to anchor
  on) for no real benefit.
- **Nothing is written to the database until you click Finish.** The
  checklist page is a single form; checking and unchecking boxes is
  pure client-side JS, and the whole session's state (statement date,
  balance, which boxes are checked) travels in that one form's fields.
  Navigating away mid-session just discards it - there's no draft
  `Reconciliation` row or half-applied state to clean up, matching how
  the rest of the app already treats in-progress actions (e.g. the
  Transfer form has the same shape).

**A small existing gap got a real use:** the transaction register has
always shown a Reconciled/Cleared/neither (R/C/—) status column, but
nothing in the app actually *did* anything with "cleared" specifically -
it was just a flag you could tick by hand with no workflow attached.
Reconciliation now pre-checks anything already marked cleared when you
open the checklist, which is the Quicken convention this was presumably
modeled on in the first place (cleared = "I've seen this on a statement
before," reconciled = "and now it's been through a full reconciliation
pass").

**The adjustment transaction is deliberately excluded from reports**,
the same way an opening balance or a transfer is (D1/D7) - it uses a new
third system payee (`Payee.RECONCILIATION_ADJUSTMENT`, same
`Payee.system()` get-or-create helper the other two use) and the
account's own transfer category, since a reconciliation adjustment is a
bookkeeping correction, not real income or spending, and shouldn't
quietly inflate either.

**Verification:** full suite (246 tests, 11 new in
`finance/test_phase8.py`). Ran the actual flow end-to-end against the
real database after a fresh backup - picked a real test account with one
genuine unreconciled transaction, walked it through start -> checklist ->
finish, and confirmed the transaction came out `reconciled`/`cleared`
and a `Reconciliation` row was created.

**Files:** `finance/models.py` (`Reconciliation`,
`Account.reconciled_balance()`, third system payee), `finance/forms.py`
(`ReconcileStartForm`), `finance/views.py` (`account_reconcile`),
`finance/urls.py`, `finance/admin.py`, `finance/templates/finance/
{account_detail,account_reconcile_start,account_reconcile_review}.html`;
new migration `0011_phase8_reconciliation.py`, new
`finance/static/finance/js/account_reconcile.js` and
`finance/test_phase8.py`.

---

## 14/09/2026 — Phase 9: Import (categories, accounts, dates all resolved by name)

**What changed:** the CSV importer now reads two more optional columns -
**Category** (auto-creating unknown ones instead of requiring every
category to exist beforehand, matching this app's own `Parent:Child` /
`Parent > Child` convention) and **Account** (so one combined,
multi-account export can route each row to its own existing account
instead of everything landing in whatever account you picked in the
dropdown) - and detects the file's own date format instead of always
assuming day-first, with a form field to override it if the guess is
wrong.

**The plan's "Accounts must already exist" bullet looked already-done
until I checked the actual target file.** The importer already required
picking one existing account before uploading, which trivially prevents
importing into a missing account - I almost filed this bullet as "no
change needed, already true by construction." But the plan's own
justification for this rule was about a *specific* historical migration
(several years of your transactions), and this project's `test_data.TXT` - a
Quicken register report, the exact shape that migration will probably
come in - has its own **Account column on every row**, for one combined
export instead of one file per account. Confirming "already done"
against the actual target file rather than just the current form's
behavior was the difference between shipping something that looks
finished and something that will actually work when you eventually run
the real migration.

**Two follow-on decisions the plan explicitly left open, made and
documented rather than guessed silently:**
- **Row vs. whole-import failure for an unmatched account name:** row.
  Skip just that row and keep going, report the count. A multi-thousand-
  row historical file with one typo'd account name is exactly the kind
  of import where "the whole thing failed, start over" is the worst
  possible failure mode - matches how duplicate rows already work.
- **A new subcategory's type when its parent already exists:** inherit
  the parent's type, never infer from the row's own amount sign in that
  case. Only a brand-new *top-level* category gets its type inferred
  from the sign (positive -> income, negative -> expense) - an existing
  parent already has a real answer, and guessing from one transaction's
  sign could create an income/expense-mismatched pair for no reason.

**Date detection reuses `regional.py` rather than adding a second
format table.** `Preferences.date_input_formats` already existed (built
for D10's date-input parsing) and covers exactly the three format
families this needed (day-first/month-first/ISO) - detection just scans
the file's raw date strings for the first value that's unambiguous (a
day over 12 in either slot, or a 4-digit first component) and returns
which family it belongs to, falling back to the viewer's own profile
setting if the whole file happens to be ambiguous (e.g. every date is
the 1st through 12th of its month). The old `CSV_DATE_FORMATS` constant
only ever tried day-first patterns - a bank export using month-first
dates was silently parsed wrong before this (exactly the D10 risk
flagged when regional preferences were first designed), not just
unsupported.

**Verification:** full suite (268 tests, 19 new in
`finance/test_phase9.py`). Verified against the real database: imported
a small CSV naming both a real existing account and a two-level
`Parent:Child` category, confirmed the row landed in the right account
with a correctly-nested auto-generated category, then deleted that
smoke-test row and category afterward since it was only there to prove
the feature works.

**Files:** `finance/models.py` (`Category.is_auto_generated`),
`finance/forms.py` (`ImportUploadForm`), `finance/views.py`
(`import_transactions`, `_import_csv`, `_resolve_category`,
`_detect_date_format`, `_import_ofx`'s return shape), `finance/admin.py`,
`finance/templates/finance/{import_form,category_list}.html`; new
migration `0012_category_is_auto_generated.py` and
`finance/test_phase9.py`.

---

## 14/09/2026 — Phase 10: Backup & Export

**What changed:** a "Back up now" button on the Settings page (plus the
5 most recent backups listed, with size, so backups are visible without
opening the folder), an automatic quarterly backup checked once at every
app launch (D8 - no background scheduler), and CSV/XLSX export buttons
on the Transactions, Accounts, Categories and Payees list pages.

**The backup helper is deliberately pure standard library**, in its own
`finance/backup.py` with no Django import at all. `scripts/bootstrap.py`
needs to call it *before* the rest of the app is reliably importable
(it's mid-setup at that point - installing requirements, creating the
`.env` file, running migrations), and it already had its own inline
`shutil.copy2` backup-before-migrating step from Phase 1. Rather than
leave that logic duplicated in two places (the launcher's pre-migration
backup and a new Django-view backup button), both now call the same two
functions: `backup_now()` for an explicit copy, `quarterly_backup_if_due()`
for the D8 launch-time check.

**The quarterly check trusts filenames, not file timestamps.** Deciding
whether a new quarterly backup is due by reading the most recent
`db_quarterly_*.sqlite3`'s *filesystem* mtime would break if someone ever
copies the backups folder somewhere else and back (a common enough thing
to do with a folder called "backups") - the mtime would reset to the
copy time, not the actual backup time. Parsing the timestamp already
embedded in the filename itself sidesteps that entirely.

**Export reuses the transaction list's own filters rather than building
a separate export form.** Pulling `transaction_list`'s filter-building
logic out into `_filtered_transactions()` (used by both the page and the
new export view) means exporting is just "whatever's on screen right
now, as a file" - change the date range or account filter, the export
buttons export that, with no separate "configure your export" step to
keep in sync with the list's own filtering as it evolves. Accounts,
Categories and Payees are simpler (small, unfiltered lists) so they just
export everything, minus the system-managed rows (transfer categories,
system payees) that wouldn't mean anything outside the app.

**Verification:** full suite (281 tests, 13 new in
`finance/test_phase10.py`). The backup helper's actual file-copying is
tested against isolated temp directories, not the real project, for a
fully deterministic result; the Settings button is tested by patching
`backup_now` itself, since Django's test database is SQLite's `:memory:`
and has no real file for a backup to copy. Ran the quarterly check for
real against the actual project afterward (nothing existed yet, so it
created the project's first real `db_quarterly_*.sqlite3`), and
exercised all 8 export endpoints (4 data types × CSV/XLSX) against the
real data.

**Files:** new `finance/backup.py`; `scripts/bootstrap.py`
(`run_quarterly_backup`), `finance/views.py` (`settings_view`'s backup
handling, `_filtered_transactions`, `export_transactions`/
`export_accounts`/`export_categories`/`export_payees`,
`_csv_response`/`_xlsx_response`), `finance/urls.py`,
`finance/templates/finance/{settings,transaction_list,account_list,
category_list,payee_list}.html`, `requirements.txt` (`openpyxl`); new
`finance/test_phase10.py`.

---

## 14/09/2026 — Phase 10 follow-up: backup polish, full export, import tips

Same-day polish batch after actually using Phase 10's Settings page and
Import page for the first time.

**Backup filenames got readable, but old ones stay as they are.** Went
from `db_manual_20260914_120447.sqlite3` to
`backup_manual_2026-09-14_12-04-47.sqlite3`. Renaming the backups
already sitting in `backups/` wasn't worth the risk for a cosmetic
change, so `backup.py`'s `_parse_stamp()` recognizes both the old and
new filename shapes (and falls back to the file's own mtime for the
handful of even older ad-hoc `db_before_phaseN_*` copies made by hand
with a plain `cp`, before this module existed) - both the quarterly-due
check and the Settings list need to keep seeing every backup regardless
of which era it's from.

**Restore is a confirm-then-act page, not a button.** A backup can now
be restored straight from the Settings list, but overwriting the live
database is the single most destructive thing this app can do to
itself, so it got the same treatment as deleting something: a dedicated
confirmation page, not a one-click POST. It also takes its own safety
backup (tag `before_restore`) of whatever's currently in the database
before overwriting it, so restoring the wrong file is itself
recoverable the same way, rather than a dead end.

**Export collapsed from two buttons to one with a dropdown** ("Export" →
CSV / Excel) on all four list pages, via a new shared
`includes/export_dropdown.html` - same functionality, less button noise
next to "Add account" / "New transaction" etc.

**Categories and Payees export is now genuinely "everything".**
Previously these silently excluded system-managed rows (transfer
categories, the 3 system payees) on the reasoning that they're plumbing
rather than something you'd manage - but that's a judgment call the
export shouldn't be making quietly on your behalf when you asked for a
full export. Both are included now, each with a new "System-managed"
column so a spreadsheet can still filter them back out if wanted.

**Payee list Usage column is sortable.** Ascending on first click, so
the least-used payees surface first - useful for spotting payees worth
deleting, which is exactly what prompted the change.

**Import page's File field help text was doing too much in one line** -
recognized columns, auto-created categories, the Parent:Child syntax,
the Account-column override, all mashed together under the file picker.
Split it: a one-line description stays under File, and the fuller
explanation moved into a "Stashy tips" panel on the right, the same
two-column layout `generic_form.html` already uses for every other form
in the app (account, category, payee, transaction, reminder) - Import
was the one holdout still cramming everything into inline `form-text`.

**Verification:** full suite, 289 tests (8 new: backup helper's
dual-naming-scheme parsing and `list_backups`/`restore_from`, the
restore confirm/POST flow including a path-traversal-in-filename
rejection test, full-export assertions for categories/payees, and payee
usage-sort). Smoke-tested against the real project via `manage.py
shell` + `Client`: Settings page renders with the Restore button and no
more "Good to know" card, the restore confirm page shows the right
filename and 404s on an unknown one, Import page's tips render, payee
usage-sort and both full exports return real data - all without
actually performing a restore (which would overwrite the real
database).

**Files:** `finance/backup.py` (filename format, `_parse_stamp`,
`list_backups`, `restore_from`), `finance/views.py`
(`_backup_path_or_404`, `backup_restore`, `export_categories`/
`export_payees` full-export change, `PAYEE_SORT_KEYS["usage"]`,
`IMPORT_TIPS`), `finance/urls.py` (`backup_restore`),
`finance/templates/finance/{settings,import_form,payee_list,
transaction_list,account_list,category_list}.html`; new
`finance/templates/finance/backup_restore_confirm.html`,
`finance/templates/finance/includes/export_dropdown.html`;
`finance/test_phase10.py`, `finance/test_phase6.py` (payee usage-sort).

---

## 14/09/2026 — Phase 11: Reports redesign

**What changed:** the Reports page went from 4 fairly generic charts to
exactly the 6 the user actually wanted - Spending by category, Current
spending by category vs. average, Income & expense by category (with a
savings-rate stat), Income & expense by category vs. average, Net
worth, and Bills & Utilities - two per row, each with an "Expand"
button that hides the other five and shows a data table under that one
chart, and a single "Back to all reports" button to return.

**Reports had zero test coverage before this.** Every other app in this
project got a test file as it was built; Reports was the one exception,
apparently never circled back to. Phase 11 added `finance/test_phase11.py`
(17 tests) covering the new `reporting.py` functions directly, not just
the view - trailing-average math especially benefits from being tested
in isolation from a full page render.

**"Bills & Utilities" needs that exact category to exist - Stashy
doesn't infer it.** There's no "this is a bill" flag anywhere in the
data model, and guessing from a keyword match felt like exactly the
kind of quiet, hard-to-predict behavior this app has been trying to
avoid elsewhere (see the import category-matching design). The user
confirmed: use a top-level category named exactly "Bills & Utilities"
if one exists, and show a plain "missing - here's how to add it"
message if it doesn't, rather than inventing one on the spot. Their
longer-term idea - Stashy offering a small set of starter categories so
this and similar reports work without manual setup - became Phase 12,
deliberately left unscoped since they want to add more to it first.

**The "vs. average" reports compare against trailing history, not the
selected date range - inherited from how the single current-vs-average
number already worked pre-redesign**, not a new decision: `category_
totals_vs_average(interval)` always looks at "this interval-bucket so
far" against the average of the 12 same-length buckets before it,
independent of whatever Start/End is chosen in the filter form. Made
that explicit in a short line of help text under Interval and Date
range, since it's the kind of thing that's invisible until you notice
the numbers don't move when you change the date range.

**Expand/Back needed no new request to the server.** The chart data was
already being sent to the browser either way (for Chart.js), and the
per-report tables are rendered server-side with real `Decimal` money
formatting (`|moneyc`, same as every other table in the app) and simply
sit `hidden` in the page until their Expand button is clicked - so
switching between "all reports" and "one report + table" is instant,
client-side, in the new `finance/js/reports.js`. Also switched the
chart data from the old `{{ value|safe }}` inline-script pattern to
Django's `|json_script` filter (already used on the dashboard) while
touching this file anyway - category names are free text a user can
type themselves, and json_script escapes correctly for embedding inside
a `<script>` tag rather than trusting `|safe`.

**Account filtering only applies where "totals for the selected range"
already made it apply** - Spending by category and Income & expense by
category. The two vs.-average reports and Net worth look at trailing
history / all accounts' running balance respectively, the same as
before this redesign; an account filter doesn't have an unambiguous
meaning there without also filtering the historical side, which nothing
asked for.

**Verification:** full suite, 306 tests (17 new). Verified against the
real project via `manage.py shell` + `Client`: all six charts and their
Expand buttons render, the summary line now reads "Showing 01/10/2025
to 14/09/2026, bucketed month." in its new spot under the filter form
with correctly localized dates (it was previously showing the raw,
unformatted date - a small pre-existing bug fixed along the way), and
the real database's actual "Bills & Utilities" category (from the
original `test_data.TXT` import) renders its chart rather than the
missing-category message.

**Files:** `finance/reporting.py` (`category_totals`,
`category_totals_vs_average`, `current_interval_start`,
`bills_and_utilities_totals`, `income_and_spending` gained an `account`
param), `finance/views.py` (`reports_view` rewritten, `_float_rows`,
`_net_worth_series` now keeps `Decimal`), new
`finance/static/finance/js/reports.js`; rewrote
`finance/templates/finance/reports.html`; new `finance/test_phase11.py`.

---

## 14/09/2026 — Phase 11 follow-up: pivot tables, exports, a real precision bug

Same-day expansion after using the redesigned Reports page for the
first time: Expand collapsed into a single toggle button ("Expand" ⇄
"Contract" - no more separate "Back" button), every report table is now
exportable, both "vs. average" tables gained a Difference column, and a
new "Subtotal tables by" filter turns the three range-total tables
(Spending by category, Income & expense by category, Bills &
Utilities) into a pivot: one column per period plus a row-level Total.

**Category rows now follow the category list page's own logic,
everywhere.** Every report table (not just the pivoted ones) groups
Income before Expense and lists a subcategory right under its own
parent, exactly like `grouped_categories()` already orders the category
list - and a parent row is rolled up to include its subcategories' own
amounts, so "Groceries" in a table means "Groceries and everything under
it," matching what the category list page already implies. Charts
deliberately kept the old flat, amount-sorted behavior instead - ranked
by size reads better in a chart than grouped alphabetically, and
nothing asked for the chart's own ordering to change.

**Net worth's columns are the Subtotal filter, not "Average interval."**
Renamed Interval to "Average interval" since it only ever sized the
trailing-average window for the two vs.-average reports, never the
report's own date range - a separate "Subtotal tables by" filter (week/
month/quarter/half-year/year) does the range-slicing job instead, and
Net worth's new per-account table reuses that same filter for its
columns (defaulting to month when it's off), since "bucket the selected
range into periods" is exactly what both features need. Net worth gets
a Total *row* (every account's balance, summed per period - the single
combined line the old chart already drew) but no Total *column*:
summing a balance across time isn't a meaningful number the way summing
a flow (spending, income) across time is.

**A real bug, caught by writing tests for the new logic rather than
just eyeballing the page.** `Sum()` aggregates on SQLite can come back
with far more decimal places than the underlying amounts actually have
- `Decimal("-132.370000000000")` for what's really -132.37. This was
invisible everywhere the app already displays money (`|moneyc` rounds
for display regardless), so it had presumably been lurking since
earlier phases, and only became visible now that a report's raw
`Sum()` result gets written straight into a CSV export as text with no
formatting step to hide it. Fixed at the source - a new
`money.to_cent()` helper, applied to every aggregate result in
`reporting.py` - rather than patched only at the export boundary, since
the same imprecise Decimal would otherwise also corrupt the vs.-average
Difference column's arithmetic. A second real bug surfaced the same
way: the new pivot table function wasn't taking `abs()` of expense
amounts, so "Spending by category" would have shown negative numbers
styled red (redundant double-negative) the first time subtotal columns
were used for real - caught by
`test_parent_rolls_up_its_subcategory_total` before it ever reached the
page.

**Verification:** full suite, 331 tests (25 new in
`finance/test_phase11_follow_up.py`, covering the pivot/hierarchy
builder, the vs.-average Difference column, per-account net worth, the
six new export endpoints, and the form's renamed/new fields directly -
not just the page rendering without errors). Verified against the real
project: all six report exports (CSV + XLSX) return real data with
clean 2-decimal amounts, Expand/Contract toggles correctly with no
leftover "Back to all reports" button, and the Income & expense card
shows Income → Expense → Total → Savings rate (with its trailing
average) in that order.

**Also recorded, not built:** the user asked whether a 4-digit PIN is
reasonable for a planned multi-"stash" (multi-database) login feature,
given the app is `127.0.0.1`-only with no inbound network exposure.
Answered in the plan doc rather than the code: reasonable for this
app's actual threat model, provided a login lockout/backoff gets added
alongside it (10,000 combinations is brute-forceable in seconds without
one, even locally). Filed as part of Phase 12, unscoped, since the user
said they'd add more to that phase before it's picked up.

**Files:** `finance/reporting.py` (`period_bucket`, `_period_columns`,
`category_report_rows`, `category_vs_average_report_rows`,
`net_worth_by_account`, `income_and_spending_vs_average`),
`finance/money.py` (`to_cent`), `finance/views.py` (`reports_view`
extended, six new `export_report_*` views, `_report_filters`,
`_export_category_table`, `_export_vs_average_table`,
`transaction_list`'s `total_amount`), `finance/forms.py`
(`DateRangeFilterForm`: `interval` relabeled, new `subtotal` field),
`finance/urls.py` (6 export routes), `finance/templatetags/
stashy_format.py` (`amount_class`), rewrote
`finance/templates/finance/reports.html`, new `finance/templates/
finance/includes/{report_category_table,report_vs_average_table}.html`,
updated `export_dropdown.html` usage across the report cards,
`finance/templates/finance/transaction_list.html`;
`finance/static/finance/js/reports.js` (expand/contract toggle);
new `finance/test_phase11_follow_up.py`.

---

## 14/09/2026 — Phase 11.1: chart/table polish after using it more

A third same-day round on Reports, after actually working with the
pivoted tables from the last round: main categories only in the charts
(subcategories were crowding them out), collapsible subcategories in
every table with the parent/child styling the category list page
already uses, income & expense as columns instead of bars with three
extra summary columns, Net worth's table grouped like the accounts
page, and Bills & Utilities switched from a bar to a monthly trend line.

**Charts and tables now deliberately disagree on categories, and
that's the point.** The chart shows one bar per *main* category
(`category_totals_top_level()` rolls every subcategory into its
parent) because a dozen subcategories fighting for space next to a
handful of main ones made the chart hard to read at a glance; the table
right below it still shows the full parent → subcategory breakdown,
now collapsible so you can hide the detail you don't need without
losing it. Chart for scanning, table for digging in - two different
jobs, two different shapes of the same data.

**Net worth's table borrows the accounts page's own grouping, not a
category-style hierarchy that doesn't actually exist for accounts.**
Accounts have no parent/child relationship the way categories do - what
they do have is `grouped_accounts()`'s existing Physical Liquid /
Digital Liquid / Illiquid / Inactive split, already used on the
accounts page. Making each group's own rollup row play the "parent"
role (bold, collapsible, summing its accounts) let the exact same
collapse-toggle JavaScript work unmodified across category tables *and*
the net worth table - one mechanism, two different kinds of grouping
underneath it.

**Bills & Utilities became a line because "spending over time" is what
a line chart is for.** Net worth's chart earned its line style by
literally being a value over time; Bills & Utilities' old bar chart
compared subcategories against each other for one period, which is a
different question. Re-reading it as "how has Bills & Utilities
spending trended, month by month" - the same shape as Net worth, but a
flow instead of a running balance - made switching to a line chart make
sense on its own terms, not just because "make it look like the other
one." `bills_and_utilities_series()` samples monthly regardless of the
Subtotal filter, matching how Net worth's own chart already ignores it.

**Verification:** full suite, 340 tests (8 new in
`finance/test_phase11_1.py`, plus fixes to the net-worth tests from the
previous round for its new grouped shape). Verified against the real
project: the collapsible toggle markup and parent/child CSS classes
render on the page, the income & expense chart's embedded JSON
includes "Total income"/"Total expense"/"Total" alongside the real
categories, and the net worth export shows each account group's own
rollup row directly above its indented accounts.

**Files:** `finance/reporting.py` (`category_totals_top_level`,
`bills_and_utilities_series`, `net_worth_by_account` restructured
around `grouped_accounts()`, `id`/`parent_id`/`has_children` added to
`category_report_rows`/`category_vs_average_report_rows` rows),
`finance/views.py` (`reports_view`'s chart data sources, three extra
Total entries appended to the income & expense chart,
`export_report_net_worth` updated for the grouped shape),
`finance/static/finance/css/stashy.css` (`.report-toggle-icon`,
`.report-value-sub`), `finance/static/finance/js/reports.js` (columns
instead of bars for income & expense, Bills & Utilities as a line, the
collapse-toggle handler), `finance/templates/finance/reports.html`
(filter form's two-row layout, Net worth's grouped/collapsible
markup), `finance/templates/finance/includes/{report_category_table,
report_vs_average_table}.html` (parent/child styling + toggle,
per-table `prefix` to keep toggle ids unique across the page),
`finance/templates/finance/transaction_list.html` (Total moved to the
row's right edge); new `finance/test_phase11_1.py`, updates to
`finance/test_phase11_follow_up.py`.

---

## 14/09/2026 — Phase 12: Starter categories from a file, not code

**What changed:** an "Add starter categories" button on the Category
list page that creates whatever's missing from `basic_categories.txt`
(a plain text file at the project root, one category name per line).

**The list lives in a file on purpose, not in Python.** The user
created `basic_categories.txt` themselves and was explicit: keep it as
a file, don't hardcode the names in code, so they can change it without
touching anything. `finance/starter_categories.py` reads it fresh every
time the button is clicked - there's no cached copy, no migration, no
Python list to keep in sync. The only piece of actual logic is
inferring a category's type from its name (anything containing
"income", case-insensitively, becomes Income; everything else,
Expense) - simple enough that the file doesn't need a second column for
type, and general enough that it isn't really "hardcoding the
categories," just one small naming convention.

**A button, not an automatic first-run seed.** Phase 11's plan entry
had left the mechanism undecided. An automatic seed only make sense for
a genuinely empty database - this project's database usually has real
(or real-shaped test) data in it already, and silently creating 26
categories the moment the app starts would be a surprising thing to
happen without being asked. A button on the Category list page mirrors
"Back up now" and "Check rates now" elsewhere in the app: explicit,
discoverable, and safe to click more than once - matching by name
(case-insensitive) against what already exists means a second click
after adding a line to the file only creates that one new line, not a
pile of duplicates.

**Verification:** full suite, 350 tests (10 new in
`finance/test_phase12.py`). Verified against the real project: 24 of
the file's 26 names already existed from the original `test_data.TXT`
import; clicking the button created exactly the 5 that didn't
(Education, Fees & Taxes, Kids, Pets, Smoking), all correctly typed as
Expense, and a second click correctly reported nothing left to add.

**Files:** new `finance/starter_categories.py`, `finance/views.py`
(`add_starter_categories_view`), `finance/urls.py`
(`add_starter_categories`),
`finance/templates/finance/category_list.html` (the button); new
`finance/test_phase12.py`.

---

## 15/09/2026 — Phase 12: multi-stash login

**What changed:** Stashy now supports more than one completely
separate set of data ("stashes"), each its own SQLite database with
its own single login. A picker screen (before any stash is chosen)
lists existing stashes and lets you create a new one; picking one and
restarting the app logs you into that stash with a 4-digit PIN instead
of a username/password.

**A stash's existence is its folder existing - not a database row,**
because at the point the picker needs to list stashes, no database has
been chosen yet to query one from. `stashes/<name>/db.sqlite3` present
means the stash exists; `current_stash.txt` at the project root names
which one is active, read once by `stashy/settings.py` at process
start to decide `DATABASES['default']['NAME']`. Switching stashes is
restart-based on purpose - picking a different one writes the new name
to that file and asks you to close and reopen Stashy.bat, rather than
trying to make a running Django process swap its own database
mid-flight.

**Creating a stash builds a real schema in a database this process
isn't normally connected to.** That needs Django's supported (if
uncommon) pattern for a one-off extra database: register a temporary
alias pointing at the new file, run `migrate` against just that alias,
create the superuser there, then drop the alias again. The first
version hand-wrote that alias's settings dict and hit `KeyError:
'TIME_ZONE'` deep inside the migration executor - Django normally fills
in several required keys itself when a database is loaded the usual
way, and skipping them fails somewhere unhelpful instead of at the
actual mistake. Copying `default`'s own already-normalized dict and
just swapping `NAME` fixed it.

**A forgotten PIN gets a recovery code, not a dead end.** Decided
earlier this week: a one-time code shown at stash creation (write it
down, it's never shown again), hashed the same way a password is, that
resets the PIN from the login screen without needing file-level access
to the machine. Using it issues a **new** code and invalidates the old
one - single-use, not a permanent backdoor. `manage.py reset_pin
<name>` is the documented last resort if that's lost too, since running
it already requires the kind of access that makes its own confirmation
prompt pointless.

**PINs needed a lockout Django's stock login doesn't have.** 4 digits
is only 10,000 combinations, and `AuthenticationForm`/`LoginView` don't
rate-limit on their own. `StashLoginView` (Django's own `LoginView`,
subclassed) counts wrong PINs and wrong recovery codes toward the same
lockout on `Profile` (new `failed_pin_attempts`/`locked_until` fields) -
5 wrong attempts, a 5-minute cooldown, and a failed attempt while
already locked doesn't extend it further.

**The existing test suite doesn't know stashes exist, and that's
fine.** Every test logs in via `Client.force_login()` and never touches
the picker - retrofitting 350+ existing tests to fake choosing a stash
first wasn't reasonable. A new `settings.TESTING` flag (`'test' in
sys.argv`) lets `RequireStashMiddleware` skip its gate entirely under
`manage.py test`. That flag needed to be checked in a couple of other
places too, not just once at the top: `_stash_backup_root()` and
`StashLoginView` both read real state (`current_stash.txt`, `settings.
CURRENT_STASH`) that depends on what's actually on the machine running
the suite - this only became visible once this phase's own "adopt the
real database as a stash" step (below) made that file real for the
first time, and a few previously-green tests broke because backups
suddenly looked in the wrong folder. Both now treat `TESTING` as the
final word regardless of what's really on disk.

**`create_stash()` and `reset_pin_directly()` aren't covered by
TestCase at all** - Django's test framework blocks any database alias
that isn't declared in `settings.DATABASES` up front, and `databases =
"__all__"` on a TestCase only recognizes aliases already known there,
not ones registered dynamically at runtime mid-test. There's no
supported way through that from inside the test runner, so these two
were verified directly instead: real creation against an isolated temp
directory (folder/db/backups layout, the superuser can authenticate,
the recovery code's hash matches, duplicate/unknown-name rejection),
and the full HTTP flow in fresh processes mirroring real restarts
(create → restart → login → 5 wrong PINs really locks you out, even
with the right PIN, until the cooldown → reset with the real recovery
code → old code stops working → change PIN from Settings → reset again
via the CLI).

**The project's real database became the "test" stash**, as agreed
before starting: copied (not moved - the original stays in place as a
safety net) into `stashes/test/db.sqlite3`, migrated, its existing
superuser renamed to `test` to match the convention the login screen
relies on (username = stash name), given a PIN, and issued a recovery
code - both handed to the user directly rather than written into any
file. A Stashy instance was already running on port 8001 (started
before this session touched any code) while this happened; its
already-loaded process kept using the original top-level `db.sqlite3`
the whole time regardless - it needs restarting to pick up today's
changes at all, same as after any other code change, not something
this step made newly true.

**Verification:** full suite, 364 tests (14 new in
`finance/test_phase12_stash_login.py`, covering lockout math, the
recovery-code flow, and the middleware directly). Real end-to-end
verification covered everything TestCase couldn't reach, listed above.
All 783 of the real project's transactions confirmed intact after the
adoption-as-a-stash migration.

**Files:** new `finance/stashes.py`, `finance/auth_forms.py`,
`finance/auth_views.py`; `finance/models.py` (`Profile`:
`recovery_code_hash`, `failed_pin_attempts`, `locked_until`, migration
`0013_profile_stash_fields`); `finance/middleware.py`
(`RequireStashMiddleware`); `finance/views.py` (`_stash_backup_root`,
Settings' Change PIN handling); `stashy/settings.py` (`CURRENT_STASH`,
`STASHES_DIR`, `TESTING`, stash-aware `DATABASES`); `stashy/urls.py`
(picker/login/forgot-pin routes); `scripts/bootstrap.py` (stash-aware
backup/migrate paths, dropped the old `ensure_login_exists`); new
`finance/management/commands/reset_pin.py`; new templates
`finance/templates/registration/{stash_picker,stash_restart,
stash_recovery_code,forgot_pin}.html`, rewrote `registration/
login.html`; `finance/templates/finance/settings.html` (Login card);
`finance/static/finance/css/stashy.css` (`.pin-input`); `.gitignore`
(`stashes/`, `current_stash.txt`, `_picker.sqlite3`, `backups/`); new
`finance/test_phase12_stash_login.py`.

---

## 15/09/2026 — Reports follow-up, dashboard/account-register cleanup, opening balance always created, category dropdown ordering

**What changed:**

- **Reports export button actually hides now.** Its wrapper `<div>` carried
  Bootstrap's `d-inline-block` utility class alongside the plain `hidden`
  attribute. Bootstrap utility classes carry `!important` on the property
  they set — `.d-inline-block { display: inline-block !important; }` — and
  `!important` always wins the cascade regardless of selector specificity,
  so it silently beat the browser's own unstyled `[hidden] { display: none; }`
  rule. The export button was visible on every collapsed report card the
  whole time, not just the expanded one, even though the JS toggling it was
  already correct. Fix was to drop the redundant class — inside a flex
  container a plain `<div>` lays out identically to an `inline-block` one
  anyway, so nothing else needed to change. **Teaching point:** a `hidden`
  attribute (or any low-specificity default style) can be silently defeated
  by an unrelated `!important` utility class elsewhere on the same element —
  always suspect this when a hide/show toggle "does nothing" but the JS
  logic reads correctly.
- **Net worth and Bills & Utilities report tables now start fully expanded**
  (every account/subcategory row visible); the other four reports keep
  their existing collapsed-by-default subcategory rows. Implemented via a
  new `expanded` flag on the shared `report_category_table.html` include
  (Bills & Utilities passes it; Net worth's own inline table in
  `reports.html` got the same two-line change directly). The collapse
  arrows still work either way, so this only changes the *default* view.
- **Average interval gained a "Last 9 periods" option** (3/6/9/12). The
  average was already built from periods strictly *before* the selected
  range with no overlap (`reporting.vs_average_periods()`), so "this month
  vs. last 6 months" already never counted the current month as one of
  those six — that part needed no code change, just confirming it already
  worked as wanted.
- **Removed the "New transaction" button from the dashboard** — Transactions
  and each account's own page still have theirs.
- **Transactions viewed through an account's own register (`account_detail.
  html`) now have a Delete button next to Edit**, matching the main
  Transactions list, reusing the same `transaction_delete` view/URL with a
  `?next=` back-link.
- **New accounts always get an opening-balance transaction now**, even a
  €0.00 one dated today if both fields are left blank — previously a blank
  opening balance silently skipped creating any transaction at all, which
  meant a fresh account's register started truly empty and had nothing for
  Reconcile to start from. `AccountCreateForm.clean()` now defaults a blank
  amount to `ZERO` and a blank date to today instead of leaving them `None`;
  `save()` no longer conditions the transaction's creation on the amount
  being truthy.
- **Category dropdowns everywhere are now ordered Income → Expense →
  Transfer**, alphabetical within each type (was: alphabetical only,
  types interleaved). One function change covers every dropdown in the
  app — `models.categories_alphabetical()` is the single place every
  category `<select>` (transactions, reminders, the payee list's category
  filter, etc.) gets its queryset ordering from, so this didn't need
  touching each form/view individually. A type with nothing in a given
  queryset just contributes no rows — there's no empty "Transfer" header
  to skip since these are plain `<option>` lists, not `<optgroup>`s.

**Why:** A batch of concrete fixes and small feature requests; the Reports
export-button issue in particular was reported as "doesn't work" after an
earlier attempt that looked correct on inspection — the actual bug was one
CSS class away from the visible symptom, which is why it's worth the fuller
explanation above.

**Files:** `finance/templates/finance/reports.html`,
`finance/templates/finance/includes/report_category_table.html`,
`finance/forms.py` (`DateRangeFilterForm.INTERVAL_CHOICES`,
`AccountCreateForm.clean`/`save`), `finance/templates/finance/dashboard.
html`, `finance/templates/finance/account_detail.html`, `finance/models.py`
(`categories_alphabetical`). Updated three existing tests whose
expectations matched the old, since-changed behavior: `finance/
test_phase3.py`, `finance/test_phase11_follow_up.py`, `finance/
test_phase11_1.py`. Full suite: 375 tests, all passing.

---

## 15/09/2026 — Transactions list: category filter's real bug, plus multi-select

**What changed:** The Transactions page's own category *filter* dropdown (not the
add/edit form's category field, which was already correct) turned out to have never
been touched by the previous session's category-ordering fix — `views.
transaction_list()` built it from a bare `Category.objects.select_related("parent")`,
which was never routed through `models.categories_alphabetical()` at all. That queryset
falls back to `Category.Meta.ordering = ["parent__name", "name"]`, which sorts every
top-level category first (its `parent__name` is `NULL`) and every subcategory after
(grouped by *its* parent's name) — exactly the "all mains, then all subs" symptom
reported, plus no Income/Expense/Transfer grouping at all since that ordering doesn't
know about `category_type`. Fixed by routing it through `categories_alphabetical()`
like every other category dropdown already does.

Also converted this same filter to a multi-select (`<select multiple>`), since
Transfer categories are deliberately still included here (unlike the add/edit form,
which hides them by design) and the user wants to filter by several categories,
including transfer ones, at once. `_filtered_transactions()` (shared with CSV/XLSX
export) now reads `request.GET.getlist("category")` instead of a single value, and
ORs together "any of the selected category ids" with "uncategorized" when both are
picked. The existing `with_subcategories` flag (still only ever set by the
dashboard's own drill-down link, one category at a time) now expands *every* selected
category's descendants rather than just one, a natural extension of the same idea.
Dropped the redundant `onchange="this.form.submit()"` on this one field — instant
submit-per-click doesn't suit holding Ctrl/Cmd to build a multi-selection — so it
now waits for the row's existing Filter button, same as the search box already did.

**Why:** Reported as "still wrong" after the previous session's category-ordering
fix, which had in fact fixed every *other* category dropdown in the app — this was
the one place that fix never reached, because this view built its own queryset
instead of going through the shared helper.

**Files:** `finance/views.py` (`_filtered_transactions`, `transaction_list`),
`finance/templates/finance/transaction_list.html`. Full suite: 375 tests, all passing
(none exercised this dropdown's ordering or multi-select directly, so none needed
updating).

---

## 15/09/2026 — Category filter: back to a closed dropdown + checkboxes, parent auto-checks its subs

**What changed:** The multi-select from earlier today looked like a permanently-open
list box, which the user didn't like the look of. Replaced it with a `<button>`
styled with the `form-select` class (same border/height/chevron as a real closed
`<select>`) that opens a Bootstrap dropdown menu of checkboxes on click
(`data-bs-auto-close="outside"` so checking several boxes doesn't close the menu
after the first one) - closed, it looks exactly like the old single-select did, and
its label switches between "All categories" / one category's name / "N categories
selected" depending what's checked. Checking a main category now also checks all of
its own subcategories (`finance/static/finance/js/category-filter.js`, matched up via
`data-category-id`/`data-parent-id` attributes) - one-directional, unchecking a
subcategory by hand doesn't uncheck its parent, since that's the direction that's
actually useful (browsing by a whole main category doesn't mean hand-picking every
child underneath it).

**Files:** `finance/templates/finance/transaction_list.html`, new
`finance/static/finance/js/category-filter.js`, `finance/static/finance/css/
stashy.css` (`.category-filter*`), `finance/views.py` (`transaction_list` now builds
`category_groups`/`category_filter_label` via `grouped_categories()` instead of the
flat `categories_alphabetical()` list the plain `<select>` used). Full suite: 375
tests, all passing.

---

## 15/09/2026 — Phase 12 revised: stashes switch live, no restart (logged after the fact)

**What changed:** Multi-stash login shipped earlier this day as restart-based
(pick a stash, then "close this window and run Stashy.bat again"). It was
rewritten the same day so stashes switch live — but that rewrite was never
logged here or in UPGRADE_PLAN.md. Caught up now, from the code itself:
- Nothing reads `current_stash.txt` any more. Django's `default` database is
  a small placeholder (`_picker.sqlite3`) that never holds app data.
- Every stash is registered as its own connection alias, named after the
  stash (`finance/stashes.py::ensure_registered()`).
- `StashSelectionMiddleware` reads which stash this session picked and keeps
  it in a thread-local for the request; `finance/db_router.py::StashRouter`
  sends every query there; it's cleared again when the request ends.
- Sessions became signed cookies.
- **Switch stash** logs out and returns to the picker - no restart.
- `scripts/bootstrap.py` migrates and backs up **every** stash on launch,
  since there's no single "current" one any more.

**Why:** Closing and reopening Stashy just to open a different stash was
clunky; choosing the database per request removes the need entirely.

**Teaching note — database routers and thread-locals:** Django can hold
several databases at once, plus a *router* that answers "which database
should this query use?". Usually that answer is fixed (say, reads go to a
replica). Here it depends on who's asking, so the middleware stores the
answer in a *thread-local* — a variable every server thread sees its own
copy of — for the length of one request. Waitress serves requests on 8
threads, so the value is cleared in a `finally:` block; otherwise a thread
could carry the previous visitor's stash into its next request. Sessions
moved to signed cookies for a chicken-and-egg reason: Django's normal
sessions are stored in a database, but the session is exactly what tells us
which database to use.

**Files:** `finance/stashes.py`, `finance/db_router.py`,
`finance/middleware.py`, `finance/auth_views.py`, `stashy/settings.py`,
`stashy/urls.py`, `scripts/bootstrap.py`,
`finance/test_phase12_stash_login.py`.

---

## 15/09/2026 — Follow-up batch: inactive accounts and small polish (logged after the fact)

**What changed** (built and tested earlier the same day, never logged):
- An account can only be made inactive once its balance is zero, and a new
  account can't start inactive with a non-zero opening balance.
- Saving a transaction on an inactive account now asks "save anyway and make
  it active again?" instead of silently blocking the save or silently
  reactivating the account.
- The payee list's category filter uses the same order as every other
  category dropdown.
- The Transactions list Total shows a currency symbol.
- Removed the helper text under Opening balance (new account) and Currency
  (edit account).
- Reports: the Expand button relabels to "Collapse" (was "Contract").

**Teaching note — ask before changing data on someone's behalf:**
reactivating an account is a real change to your data, and refusing the save
throws away what you just typed. Showing the question and resubmitting the
same form with a confirmation flag (`confirm_inactive_account`) keeps both
safe: nothing is saved until you say yes, and nothing you typed is lost.

**Tests:** `finance/test_phase13.py` (named before the Phase 12.1 / 13
numbering existed).

---

## 15/09/2026 — Phase 13 (Reports) and Phase 14 (Audit log) planned; docs caught up

**What changed** (no app code, apart from removing one dead setting):
- `UPGRADE_PLAN.md`: new **Phase 12.1** (the day's two fix batches, ticked),
  **Phase 13 — Reports redesign** (decisions R1–R8 plus a checklist) and
  **Phase 14 — Audit log rewrite**. Phase 12 gained a revision note for live
  stash switching. Backlog tidied: payee matching ticked (case-insensitive
  already; no merge tool wanted), the invalid-filter warning and Net worth
  currency conversion moved into Phase 13, audit-log raw values moved into
  Phase 14, plus two new backlog items: deleting Phase 12's leftovers
  (the old top-level `db.sqlite3` and `current_stash.txt`), and
  `scripts/load_test_data.py` still loading into the empty placeholder
  database instead of a stash. The parked
  "PIN or password-only login" idea was removed, since Phase 12 built it.
- `README.md` caught up where it had fallen behind: stashes and PINs instead
  of username/password, per-stash backups and migrations, reconciliation,
  export, starter categories, and the reports as they are today.
- `stashy/settings.py`: removed `MAILERS`. Django 6.1 has no such setting
  (checked against `django.conf.global_settings`) and Stashy sends no email,
  so it did nothing. `manage.py check` passes.

**Teaching notes — why the report decisions came out this way:**
- *Charts vs. tables:* a chart answers "where is my money going?"; a table
  answers "exactly how much, in which month?". So a subtotal doesn't cram
  26 categories × 12 periods into one chart - the chart switches to one
  column per period and the table carries the detail.
- *An average that can predict:* "this month vs. an average month" only
  helps predict spending if the average months are whole calendar months,
  and if a month where nothing happened counts as 0 - otherwise a one-off
  1 800.00 purchase makes every month "expect" 1 800.00. Months from before
  your records begin are left out instead, because "no data yet" isn't the
  same as "spent nothing".
- *Bars vs. columns:* there are 26 categories with long names, so
  categories get horizontal bars (names stay readable and the chart grows
  downward); time reads left to right, so periods get vertical columns.
- *Color:* past about 7 colors, people stop telling them apart. Only the 5
  biggest categories get their own color; the rest share one muted shade.
- *Refunds as income:* agreed with the user - money coming back is shown as
  income rather than quietly shrinking what a category cost.

---

## 15/09/2026 — Phase 13 decisions settled; Phase 12 leftovers deleted; test-data loader fixed

**What changed:**
- **Phase 13 plan (`UPGRADE_PLAN.md`)**, agreed with the user; Phase 14 is on
  hold until Phase 13 is agreed and built:
  - R1: **the category alone decides** income vs. spending, never the sign
    of the amount. An earlier draft counted a positive amount in an expense
    category as income; the user corrected that. Expenses are expected to
    be negative, and one that isn't simply nets against its own category.
  - R4: with fewer earlier periods than Average interval asks for (say 2
    months of data, Last 3), the average just uses what exists, and the
    report's heading names the real count instead of adding a warning.
  - R5: 36 periods at most per subtotal. Past it, a notice in the app's
    usual tone points at the next size up: "Too many weeks to show here.
    Try subtotaling by month instead."
  - Currency conversion applies to every report, not only Net worth.
- **Deleted** the old top-level `db.sqlite3` (Phase 12's safety copy - the
  data lives in `stashes/test/`, confirmed intact with 784 transactions
  first) and `current_stash.txt` (nothing has read it since stashes started
  switching live).
- **`scripts/load_test_data.py`** now requires `--stash <name>`, lists what
  it's about to delete, and asks for the stash's name typed back before
  wiping anything (`--yes` skips the question). Its load runs inside
  `transaction.atomic(using=<stash>)`.

**Why:** Since stashes switch live, Django's `default` database is only the
picker's empty placeholder, so the old loader would have quietly loaded into
that instead of any stash.

**Teaching note — a router isn't a transaction:** a database router decides
where each *query* goes, but `transaction.atomic()` wraps one *connection*,
and without `using=` that's `default`. Just fixing the router would have
sent every insert to the stash while the "all or nothing" safety net guarded
the empty placeholder - if the load crashed half-way, the stash would keep
a half-wiped, half-loaded mess instead of rolling back.

**Teaching note — typed confirmation:** "Are you sure? (y/n)" gets answered
on autopilot. Typing the stash's own name back forces you to read which
stash you're about to wipe - the same idea as GitHub asking for a
repository's name before deleting it.

**Verified:** an unknown stash name gives a clear error and changes
nothing. A throwaway stash `loadcheck` loaded all 779 rows, and its audit
entries landed in that stash too. A second run answered with the wrong name
stopped with nothing changed. The placeholder database stayed empty, the
real `test` stash was untouched (784 transactions before and after), and
`loadcheck` was deleted afterwards.

---

## 15/09/2026 — Phases 14 and 15 planned; starter categories built from the Quicken list

**What changed** (no app code):
- `UPGRADE_PLAN.md`: the earlier "Phase 14 — Audit log rewrite" draft is
  replaced by **Phase 14 — Admin, tags, audit log** (decisions A1–A6) and
  **Phase 15 — Login and new stash setup** (L1–L6), split at the user's
  request. D10's "audit log formats values per viewer" point is marked as
  superseded.
- New **`starter_categories.xlsx`** (project root), built from the user's
  Quicken category list, `categories.txt`:
  - Sheet **Basic**: the 26 main categories. Checked identical to
    `basic_categories.txt`.
  - Sheet **Extended**: every one of their 123 subcategories, plus Smoking,
    the only main category without subcategories.
  - Left out: the per-account `[Transfer]` categories (Stashy creates those
    with each account) and the personal "Not Sure" / "Not Sure, Biz"
    placeholders. Quicken's truncated "Other Clothing & Accesso..." was
    restored to its full name.
  - Nothing reads the file yet; Phase 15's welcome page will.

**Teaching note — what an audit log is for:** a log that only says "deleted
#781" can't answer the one question you'll ask later: *what was it?* So
Phase 14 keeps a record's full values inside its INSERT/DELETE entry, and
writes a name next to every id - ids outlive names, and a bare `44` means
nothing once "Groceries" is renamed or gone.

**Teaching note — names for people vs. names for computers:** the name you
see ("The Smiths") and the name the computer needs (a folder name with no
spaces or punctuation, guaranteed unique) have different jobs. Phase 15
generates the second from the first, so nobody ever has to invent, or
look at, a technical name.

**Teaching note — reading a printed report as data:** Quicken's category
list is meant for printing - page-break blank lines, a usage column, and
names cut off at the column width. The parser splits on runs of spaces,
skips what isn't a category, and the result was checked against the
hand-made Basic list, to prove nothing was lost or misread.

---

## 15/09/2026 — Housekeeping before implementing Phases 13–15

**What changed** (no app code):
- **Deleted `categories.TXT`** - its contents now live in
  `starter_categories.xlsx`. The two "Not Sure" categories it listed are old
  and unwanted; they were already left out of the Excel file, and neither
  exists in the test stash, so there was nothing to delete there.
- **Deleted the old top-level `backups/` folder**: 17 database snapshots
  from 12–15/09/2026, about 85 MB. Nothing in the app reads that folder any
  more - since Phase 12, every stash keeps its own `stashes/<name>/backups/`.
  They held older copies of the same test data that now lives, newer, in
  `stashes/test/` (which has its own quarterly backup).
- **`.gitignore`**: dropped `/current_stash.txt`, a file that no longer
  exists and that nothing creates.
- **Kept on purpose:**
  - `images/`: the original mascot artwork. Nothing reads it, but it's
    source material, not leftovers.
  - `test_data.TXT`: `scripts/load_test_data.py` needs it.
  - `basic_categories.txt`: today's "Add starter categories" button reads it
    until Phase 15 removes both.

**Teaching note — when is something safe to delete?** "No code reads it"
isn't enough on its own. It also has to hold nothing you can't get back
from somewhere else. The old backups passed both tests: unused, and every
bit of their data exists, newer, in the stash. The mascot images pass only
the first - nothing reads them, but they're the originals the app's own
images were made from - so they stay.

---

## 15/09/2026 — Phase 13 (Reports redesign)

**What changed:** every part of R1-R8, agreed over several planning
rounds earlier the same day - see `UPGRADE_PLAN.md`'s Phase 13 for the
full decisions log and implementation notes. In short:
- `reporting.py` rewritten so a category's own **type** decides income vs.
  spending everywhere (dashboard included), never the amount's sign; every
  total now converts through the main currency's latest known rate.
- Reports gained two new reports (Income by category, Income vs. average,
  mirroring Spending's own two), a "vertical columns split by category"
  chart for Income & expense and Income & expense vs. average, colored per
  R6 (the range's 5 biggest categories get distinct bright colors, chosen
  from a palette actually checked for colorblind safety - the old one
  wasn't and failed outright).
- "Bills & Utilities" became **Watched expenses**: a title + any set of
  expense categories, chosen in Settings, migrated from whatever "Bills &
  Utilities" category already existed so nothing looks different on day
  one.
- New "Earliest to date" range, a 36-column subtotal limit with a plain-
  English notice past it, an uncategorized-transactions notice, Show
  all/Hide all per report, and averages that now zero-fill empty periods
  and stop counting periods from before the data begins.

**Caught late, before calling it done:** the actual Reports *defaults*
(This month, Last 3 periods) had never been wired in - `resolve_range()`'s
fallback and the filter form's `initial=` were both still the old
YTD/12-period values, left over from before this phase was even planned.
Easy to miss because the page still worked fine either way; only the
*default* view was wrong. Fixed before moving on, not left as a follow-up
- a page's agreed defaults are exactly the kind of thing this project
doesn't get to redo later almost by accident.

**Also caught late:** the report cards were paired wrong (Spending by
category next to Income by category, instead of next to its own vs.-
average) - the plan text spelled the row order out explicitly and it
still got transposed while building the template. And report 6's chart
sat in a fixed-height div with no `maintainAspectRatio: false` set, which
Chart.js needs whenever a chart lives in a sized wrapper instead of a bare
`<canvas height="...">` - without it every report chart would have
rendered at the wrong size. Both are the kind of thing a browser click-
through catches instantly and a careful re-read of the plan text catches
on paper; this session had no browser to check in, so the plan text was
re-read line by line against the finished template specifically to catch
this class of mistake.

**Teaching note — no live browser this session.** Every number in this
phase is proven by tests (408 passing, `finance` + `audit`) and
`manage.py check`; nothing about how the page actually *looks* has been
seen rendered. That's a real gap, not a formality - the two mistakes
above are exactly the kind a screenshot would have caught in five seconds
and took a deliberate line-by-line re-check to find instead. Restart
`Stashy.bat` (migrates the "test" stash and runs `collectstatic`, so the
new CSS/JS actually ship) and look the page over before trusting it fully.

**Known simplifications, on purpose:** the report card include is only
partly shared (`includes/report_card_header.html` - just the header; the
chart/table body differs too much per report to unify further without
adding indirection for little real gain). Report 6's CSV/XLSX export
skips the two synthetic "Income total"/"Expense total" rows the on-screen
table shows (already visible in the stat row above it, not worth
duplicating the injection logic into the export path for two rows).

---

## 15/09/2026 — Phase 13 bug: `{# #}` doesn't span lines

**What happened:** the user opened the real page after `Stashy.bat` and
reported Reports "messed up to a point it's better to scrap it and start
over" and the new Watched expenses picker "horrible" - a multi-line
paragraph of comment text sitting where the widget should be, on every
single report card and in Settings.

**Cause:** two of Phase 13's new includes
(`includes/report_card_header.html`, `includes/category_checkbox_filter.
html`) opened with a `{# ... #}` comment spanning several lines. Django's
`{# #}` tag is matched without `re.DOTALL` - it only closes on the *same
line* it opens on. A multi-line one never matches as a comment at all, so
the whole thing - `{#`, the text, `#}` - falls through as literal page
text instead of being stripped. `{% comment %}...{% endcomment %}` is the
multi-line-safe form; `{# #}` is single-line only. Every previous
template in this project happened to keep its comments to one line, so
this never came up before Phase 13.

**Fix:** shortened both comments to one line each. Added
`NoLeakedTemplateSyntaxTests` (`test_phase13_reports.py`) asserting `{#`
and `#}` never appear in the rendered Reports, Settings or Transactions
pages, so this exact mistake fails a test next time instead of shipping
silently. Also widened the Watched expenses picker's own row into
narrower columns (it sat in Settings' wide right-hand column, stretching
edge to edge - nowhere near as bad as the leaked text, but still not what
"one multiselect dropdown like the Transactions one" should look like).

**Teaching note - what "tests pass" didn't catch here.** All 408 tests
were green through every step of Phase 13, and still would have been with
this bug in place - not one of them asserted "the page doesn't contain
literal `{#`". `assertContains(response, "some real text")` proves that
text *is* there; it says nothing about *other* text also being there that
shouldn't be. This session had no browser to click through, so the plan
text was re-read against the finished template as a substitute check -
that caught two real layout mistakes (see the "Phase 13" entry above) but
missed this one, because re-reading plan text isn't the same as looking
at rendered output. The fix going forward isn't "write more of the same
kind of test" - it's this one specific new test (leaked template syntax
is a distinctive, checkable failure mode) plus treating "I re-read the
code carefully" as real but incomplete verification whenever a browser
genuinely isn't available, not as equivalent to having looked at the page.

---

## 15/09/2026 — Phase 13 fixes, second round (from using the real page)

**What changed:** the user went through every report on the real page and
listed what was wrong. Full list in `UPGRADE_PLAN.md` (Phase 13, "Second
round of fixes"). The ones worth understanding:

- **Watched expenses "does nothing".** Settings saved correctly all along.
  The Reports card decided "no categories chosen" by checking
  `{% if not watched_totals %}` - and `watched_totals` is an *empty list*
  when categories are chosen but nothing was spent in them in the selected
  range (This month, early in the month). In a template, an empty list and
  "nothing at all" both count as false. That's why it "worked after some
  time": it started working once there was spending in the range.
  **Teaching note:** "is there anything?" and "was anything chosen?" are
  different questions - ask the one you mean. The card now checks the
  chosen category ids.
- **Subtotal never changed the charts.** The chart reads
  `data-subtotal="{{ subtotal }}"` off the page, but the view never put
  `subtotal` into the template context - Django renders a missing variable
  as an empty string instead of raising an error, so every chart quietly
  saw "no subtotal". **Teaching note:** Django's silence about missing
  template variables is convenient until it hides exactly this. The new
  test checks the rendered attribute itself, not just the calculation.
- **Colors, revised with the user.** The top-5 scheme exists to tell
  *pieces of one stacked column* apart. On separate bars it just paints
  the same measure in different colors for no reason, and on separate
  lines each line needs its own identity rather than a "rank". Now: stacks
  get top-5 colors plus stripes for the rest; separate bars get one color;
  separate lines get one color each.
- **Net worth / Watched expenses charts follow Subtotal.** Net worth's chart
  used to recompute its own always-monthly series; it now plots its
  table's Total row, so the chart and table can't disagree.

---

## 15/09/2026 — Phase 13 fixes, third round

**What changed:** colors simplified to green (income), red (expense) and
blue (net) with dark lines between stacked pieces; averages now use whole
earlier months/quarters/years as R4 always specified; a missing earlier
period counts as 0 instead of showing "No earlier data to compare with.";
average dots at 0 no longer get cut off; the two vs-average titles are
"Current spending/income by category vs. average". Details in
`UPGRADE_PLAN.md` (Phase 13, "Third round").

**Found while fixing something else - R4 was never really built.** The
agreed rule was "This month is compared with the previous full months".
The code still compared it with earlier windows of the same *length*: on
15 September, "This month" is 15 days long, so it was averaged against
three earlier 15-day blocks. The numbers looked plausible, which is
exactly why nobody noticed. The plan's R4 box had been ticked anyway.
**Teaching note:** a ticked box is a claim, not a proof. The new tests
spell out the exact periods for each range type (This month on 15/09 →
August, July, June), so a regression shows up as the wrong dates, not a
vaguely-off average.

**Why the dots at 0 disappeared:** Chart.js clips anything drawn outside
the chart area by default, and a dot at exactly 0 sits on the edge of a
horizontal bar chart - so half of it (or all of it, depending on size)
was clipped. `clip: false` on that dataset lets it draw over the edge.

---

## 16/09/2026 — Phase 13 fixes, fourth round

**What changed:** averages tell "no earlier activity" apart from "earlier
activity worth 0"; the vs-average reports step aside entirely when there
are fewer than two earlier periods; help popovers open on hover, one at a
time; stacked-piece dividers and average dots are dark brown in light
mode. Details in `UPGRADE_PLAN.md` (Phase 13, "Fourth round").

**Teaching note — 0 and "nothing" are not the same number.** Yesterday's
round replaced a missing average with 0, because a missing dot looked
broken. On Year to date that produced a chart of dots sitting at 0, which
reads as "you used to spend nothing here" when the truth is "there's
nothing from before to compare this with". Both an absent dot and a dot
at 0 are *statements*, and they're different statements: the fix is to
say the true one per category (no earlier activity → no average, no dot;
earlier activity → average, counting empty periods as real zeros), and to
stop drawing the report at all when there isn't enough history to average
in the first place. A blank is only confusing when the page doesn't say
why - hence the message in place of the report.

**The titles are the user's now.** They edited the report names directly
in `reports.html` between rounds. Anything touching that file from here on
works around those strings instead of regenerating them.

**Show all/Hide all, same day:** it was showing on every collapsed card,
and read "Show all" even on Net worth and Watched expenses, whose rows are
already open. Now it's hidden until the card is expanded, like Export, and
its label is derived from the table (`anyRowHidden`) rather than from its
own current wording - so it also stays correct after individual chevrons
are clicked. **Teaching note:** the old code decided what to do by reading
its own label ("if it says Show all, then show"). That works only while
the label can't drift from reality; the moment a table starts out expanded,
or a single row is toggled by hand, the button is lying about what it will
do. Deriving the label from the rows means there's one source of truth and
nothing to keep in sync. The button also had to be wrapped in a plain
`<div hidden>` rather than carrying `hidden` itself: Bootstrap's `.btn`
sets `display: inline-block`, and an author rule beats the browser's own
`[hidden]` styling - the same trap as the `d-inline-block` export bug.

---

## 16/09/2026 — Phase 14: admin, tags, audit log

**What changed:** the Django admin now hides Users and Groups and makes
Profile view-only (regular people use Settings, not the admin, to change
their preferences); the unused `Tag` model and `Transaction.tags` are
gone entirely; and the audit log was rewritten from a friendly "4 fields
changed" summary into a real database-style log - one row naming a new or
deleted record (with its full column values kept, A4), one row **per
changed column** on an update, everything in raw stored form (`category_id`
not "Category", `debit_card` not "Debit Card", `2026-09-15` not
`15/09/2026`), with the related record's name next to every foreign key so
a raw id is still readable. Existing entries were in the old format and
got cleared (A5) - nothing reads them. Details and decisions (A1-A6) in
`UPGRADE_PLAN.md`, Phase 14.

**Teaching note - one row per change, not one row per save().** The old
log wrote one `AuditLogEntry` per `save()`, with every changed field packed
into a `changes` JSON dict. That's fine for a human summary but wrong for
a *database* audit log: you can't filter "show me every time `category_id`
changed on a transaction" or "every time anyone touched `amount`" without
unpacking JSON client-side. Splitting to one row per column makes those
ordinary admin filters (`column`, `table`) instead of custom code. The
trade-off is that grouping - "what else changed in this same click" -
had to move to a different axis: the `operation` id, shared by every row
from the same request or automatic action, already existed for exactly
this in the old code (`same_action`/now `same_operation`) and needed no
new plumbing.

**The `operation` id needed a fix once rows could multiply.** A single
`save()` with three changed columns now writes three rows, and they need
to *share* one operation id and one timestamp so "show all rows from this
action" actually shows all three. Inside a real request that's automatic -
`request_context()` mints the id once for the whole request - but a raw
model call outside any request (a script, a test, `manage.py shell`) has
no ambient id, and the old per-call logic (`context.operation or
new_operation_id()`) would mint a *fresh* one for every row in the loop.
Fixed by resolving the id once per event (`_resolved_context()`) and
reusing that same context object for every row the event produces -
one instance of the exact bug class Phase 13 kept hitting: something
that happens to work when there's only one of a thing breaks silently
the moment there are several.

**A6, enforced once, not per call site.** "Never log the recovery-code
hash" could have been special-cased in three places (create, update,
delete) - instead it's one `HIDDEN_FIELDS` check inside the single `_raw()`
value-formatting function that every code path already runs through, so
adding a future secret field to hide is a one-line change, not a hunt
through every write path.

**Real-stash follow-up, same day: the migration failed on the real 'test'
stash** with `sqlite3.OperationalError: no such column: audit_
auditlogentry.model`, even though the table plainly had that column and
the full test suite (in-memory `default` database) was green. Root cause:
Stashy routes every ORM query through `finance.db_router.StashRouter`,
which reads which stash to use from a thread-local - set by the web
middleware per request, but never set by a bare `manage.py migrate
--database <stash>` process. A migration's `RunPython` step queries
through the normal ORM, so `AuditLogEntry.objects.all().delete()` (no
`.using(...)`) asked the router "which database?", got "no opinion"
(nothing set that thread-local), and Django's fallback is `default` - the
picker's always-empty placeholder database, which `migrate_database()` in
`scripts/bootstrap.py` migrates *first*, before any real stash. So the
delete silently ran against the picker (already on the new schema by
then), not the stash actually being migrated - "no such column" is what
querying an old-schema queryset against an already-new-schema table looks
like. Fixed by binding the query to `schema_editor.connection.alias`, the
one thing in a migration that reliably names the actual target database.
**Teaching note - the in-memory test suite couldn't have caught this.**
`manage.py test` never uses named stashes or the picker fallback; it just
has one `default` alias, so `current_stash_alias()` being unset never
matters there - a query with no opinion and a database with no other
candidate land in the same place by coincidence. The bug only exists where
more than one database is registered, which is exactly what only shows up
against a real, multi-stash install. **Found the same latent bug in Phase
13's own `0014_phase13_watched_expenses.py`** (the "prefill Watched
expenses from Bills & Utilities" migration) while looking for others of
its kind - fixed there too, though it had already run against the real
stash by luck of the picker being empty either way (a fresh stash's
prefill is *always* a no-op, since there's no "Bills & Utilities" category
yet regardless of which database answered). Any future migration with a
`RunPython` step that touches real data needs the same `.using(schema_
editor.connection.alias)` treatment - it's now called out in both files.

---

## 16/09/2026 — Settings: recovery code regeneration; Cleared removed

**What changed, both requested in the same follow-up conversation:**
Settings now has "Get a new recovery code" (confirm, then a fresh code
shown once, replacing the old one - `finance.stashes.
regenerate_recovery_code()`); and `Transaction.cleared` is gone entirely -
the register's `C` badge, the transaction form checkbox, the CSV/XLSX
export column, the admin filter, and the reconcile checklist's "pre-check
anything already Cleared" convenience. See `UPGRADE_PLAN.md`'s Backlog for
the full list of touched files.

**Why Cleared, specifically.** The user's own words: "I don't think I'm
going to use it. I want the app to be as simple as possible so people
would use it day-to-day, not to be some fancy elaborated accounting app
that most people would dismiss because of too much work." Cleared was a
real Quicken-style convenience (pre-checking the reconcile checklist with
whatever you'd already ticked off day-to-day) but it's a second habit to
maintain for a benefit ("Mark all" already covers the common case, a
statement that matches everything) most people won't miss. Matches the
standing rule for delegated calls - this
was a clear, unambiguous ask, not a design fork, so it was implemented
directly rather than turned into a question.

**Confirm dialog, not a confirm page.** Every destructive action elsewhere
in the app (delete account/category/reminder) uses a dedicated confirm
page - `*_confirm_delete.html`. Recovery-code regeneration uses a plain
`onsubmit="return confirm(...)"` instead, matching the one other place in
the codebase that already does this (the auth flow's own recovery-code
page, "I've saved it"). The difference: a delete confirm page needs to
*show what would be lost* (a category's subcategories, an account's
transaction count) before you can decide - there's nothing to look up
here, just a yes/no on losing the old code, so a native confirm() carries
the same weight without a page load.

**Found and fixed a self-inflicted bug while adding the recovery-code
tests**, worth a note since it's a mistake to watch for: appending a new
test class with `Edit`'s exact-string replacement matched only through
the *middle* of an existing test method (the `old_string` ended at
`self.assertEqual(...transactions"]), 600)`, one line short of that
method's actual last line, `self.assertLess(len(queries.captured_
queries), 15)`). The edit inserted the new class in between, silently
relocating that trailing assertion to the end of the *new* class's last
method, where it referenced an undefined `queries` name from a scope that
never had it. It ran without a syntax error (still valid Python) but broke
in exactly one test, immediately caught by the real test run. **The
lesson:** when appending after an existing method via find-and-replace,
match through to a line that's unambiguously the method's real end (a
blank line, a dedent, the next `class`/`def`) - matching a plausible-looking
line in the middle is how another line ends up silently relocated.

---

## 16/09/2026 — Phase 15: owner names, login redesign, welcome wizard

**What changed:** a stash now belongs to someone by name ("Alex", "The
Smiths"), set once at creation and shown everywhere instead of an internal
folder name nobody ever typed on purpose. Creating one is its own page
(whose it is + PIN); the picker is just a dropdown of those names. Stashy
opens on the last-used stash's PIN screen (a separate cookie from the
session, so logging out still lands there). Settings' Login card is gone -
Change PIN and Get a new recovery code are their own pages now, the latter
finally showing the new code on the page itself instead of a browser
`confirm()` and a message banner the user called "so ugly" and "very easy
to be missed." A new stash's first login walks through a four-step wizard
(money & region, starter categories from `starter_categories.xlsx`,
watched expenses, first account) before reaching the dashboard - closing
Stashy partway through resumes at the right step. Full breakdown, every
decision (L1-L8) and every implementation note in `UPGRADE_PLAN.md`'s
Phase 15 section.

**Picture it before you build it.** Every screen in this phase - Settings'
new layout, Change PIN, the recovery-code page, the welcome wizard - was
sketched as ASCII mockups in chat and confirmed before a single template
was written, at the user's explicit request after front-end mistakes in
recent phases. It caught real preferences a text description alone
wouldn't have: the two Settings buttons stacked in their own card rather
than inline forms, Back appearing on Change PIN once the user saw it
missing from the mockup, and the welcome wizard's two-big-buttons
Categories step surviving unchanged because the sketch was already right.
Confirming a picture is cheap; rebuilding a wrong template is not.

**Yesterday's migration bug, guarded against by construction this time.**
Phase 14's audit-migration failure came from routing through `StashRouter`
with no explicit `.using(...)`, silently hitting the wrong database. Every
new `stashes.py` function here that opens *another* stash's database
(`stash_choices()`, `owner_name_taken()`) was verified against a real,
fully isolated temp directory from the start - `finance.stashes.
stashes_dir` monkeypatched away, never the project's own `stashes/`, per
test_phase12_stash_login.py's already-documented reason `create_stash()`
and `reset_pin_directly()` can't run under Django's TestCase at all
(dynamically-registered database aliases aren't in `settings.DATABASES`,
which the test runner requires up front).

**A bug that only showed up in the real click-through, not the unit
tests.** The "remember last stash" cookie fallback first set `request.
session['stash']` from the cookie and carried on with the same request -
looked right, and every unit test of `RequireStashMiddleware` in
isolation passed. But `StashSelectionMiddleware` (which sets the actual
DB-routing thread-local) runs *before* it in the chain, so it had already
read the stale, empty session by the time the fallback filled it in - the
first page after "remembered" would still query the wrong database for
that one request. Fixed by redirecting back to the same URL instead of
continuing in-request, so the *next* request is the one that sees the
session already populated. Caught only by the full login → logout →
cookie-remembered-PIN-screen round trip through the real Django test
client, run with `settings.TESTING` deliberately left `False` so the
app's own TESTING-mode shortcuts didn't mask it - a reminder that a
middleware ordering bug like this is invisible to a test that only checks
what one middleware returns, since the damage is in what an *earlier*
middleware already did with the request before this one saw it.

**Required, except when it can't be.** The Categories step is "required:
Basic or Extended" per the plan - no Skip, by design, since a stash with
zero categories is a worse starting point than picking one of two
reasonable lists. But if `starter_categories.xlsx` itself is missing, both
buttons are disabled and "required" would mean a genuine dead end with no
way to reach step 3. Added an explicit Skip for exactly that one case
(neither sheet available) rather than leaving a wizard the user can't get
past - the kind of edge case worth just fixing rather than re-asking about,
since forcing a dead end when a project file happens to be missing has
only one reasonable answer.

---

## 16/09/2026 — Import: accept XLSX, and Quicken's full column set

**What changed:** The import page now accepts `.xlsx` alongside CSV, OFX
and QFX. Both CSV and XLSX now read the same eight columns a Quicken/Money
"transaction report" export uses: Date, Num, Account, Payee, Category,
Memo, Amount, Reconciled - only Date needs a value in a row (Amount still
has to parse into a number, since a transaction can't be saved without
one, but Num/Payee/Category/Memo/Reconciled can all be blank). `Num`
fills the transaction number shown in the register instead of always
auto-assigning one; `Reconciled` sets the transaction's reconciled flag
(anything other than blank/"N"/"no"/"false"/"0"/"unreconciled"/"uncleared"
counts as reconciled).

**Why one shared function instead of two parallel importers:** CSV and
XLSX differ only in *how the file gets turned into rows of text* - a CSV
row is already strings, an XLSX row comes back from `openpyxl` as native
Python values (a cell holding a date is a real `datetime.date`, not
text). `_import_csv` and `_import_xlsx` now each do only that conversion
(XLSX additionally turns date cells into `YYYY-MM-DD` strings so the
existing date-format detection/parsing works unchanged) and hand a plain
list of `{column: text}` dicts to one shared `_import_rows()`, which is
the exact same matching/creation logic the CSV path already had. Two
independent copies of that logic would have been the more obvious first
draft, but every future column or matching rule would then need updating
twice and could quietly drift apart.

**Left unchanged on purpose - category auto-create:** a category named in
the file that doesn't exist yet is still auto-created (flagged "auto" in
the category list), same as Phase 9 already does; a blank Category column
falls back to the payee's own history same as before, landing on
Uncategorized only when there's no history either. Asked directly whether
to change that to "unmatched name → Uncategorized instead of creating it"
and the answer was to leave it as-is.

**No library to add:** `openpyxl` was already a dependency (used for
XLSX export and the starter-categories wizard step), so this needed no
new install.

**Verification:** existing `finance/test_phase9.py` (19 tests covering
CSV account/category/date-format handling) still passes unchanged, plus a
throwaway test written to exercise an XLSX file with Num/Reconciled/blank
cells through the real upload view before being deleted once confirmed
working.

**Files:** changed `finance/views.py` (`import_transactions`,
`_import_csv`, new `_import_xlsx`/`_import_rows`), `finance/forms.py`
(`ImportUploadForm.file` help text), `finance/templates/finance/
import_form.html`.

---

## 16/09/2026 — Welcome wizard: Watched expenses dropdown and empty Title

**What changed:** On a new stash's Watched expenses step, the Categories
dropdown didn't open and Title came pre-filled with "Bills & Utilities".
Both fixed: the dropdown now works exactly like the Transactions one, and
Title starts empty (coming Back to the step after saving keeps your title).

**Why the dropdown was dead:** it's the very same widget as Transactions
(`includes/category_checkbox_filter.html` + `category-filter.js`), but
opening a Bootstrap dropdown needs Bootstrap's own JavaScript
(`bootstrap.bundle.min.js`). The normal app layout (`base.html`) loads it;
the wizard has its own layout (`welcome_base.html`) that only loaded
flatpickr and `stashy.js`, so the button rendered but clicking it did
nothing. The markup was fine, which is why the Phase 15 tests (which read
HTML, not click it) never caught it - worth remembering: a test that
checks a page *contains* a dropdown doesn't prove it *opens*.

**Why Title was pre-filled:** the model field defaults to "Bills &
Utilities" (kept from Phase 13, where that report used to be hardcoded),
and a form built from the profile shows the stored value. The wizard now
passes an empty `initial` for the title until the step is completed.
Title is still required when you press Next (the report needs a heading);
Skip doesn't need it.

**Files:** `finance/templates/finance/welcome_base.html`,
`finance/welcome_views.py`, `finance/test_phase15.py` (2 new tests).

---

## 16/09/2026 — Loaded the full transaction history into the real stash

You dropped `all_data.xlsx` (several years of history across every
account) in the project root and asked for it to go into the real stash -
not a test database. Backed up first, into that stash's own `backups/`.

**What happened:** Ran it through the exact same `_import_xlsx()` path the
Import page uses (called directly against the real stash's database,
via `finance.db_router.set_current_stash_alias`, rather than through a
browser session) - so this is a real exercise of the feature just built,
not a separate one-off parser. The file's columns (Date, Num, Account,
Payee, Memo, Category, Reconciled, Amount) matched the importer's expected
layout exactly, `Num` turned out to be genuinely unique per calendar year
across every account, matching Stashy's own numbering scheme precisely -
confirms this file was prepared specifically with the new importer's
column set in mind. Every account already existed with a matching name
(created during the welcome wizard/Settings), and every category already
existed from the starter-categories step - nothing had to be auto-created.
Verified against the file itself afterward: every row landed, and the
accounts' balances added up to exactly the file's own Amount column
total.

**One real catch - the "[Account]" transfer categories:** the file uses
`[Cash]`, `[Debit Card]` etc. as the Category on opening-balance/transfer
rows, which is *exactly* the name Stashy already gives each account's own
transfer category (`Account.ensure_transfer_category()`) - but the
importer's `_resolve_category()` deliberately excludes account-scoped
categories from its matching (that exclusion is correct for its normal
job: matching *user-named* categories, not Stashy's own internal ones).
Left alone, it would have created 5 duplicate top-level categories
literally named `[Cash]`, `[Savings]` etc., disconnected from the real
transfer bookkeeping. Patched `_resolve_category` for the duration of this
one script only (not a change to the importer itself) so a bracketed name
resolves to that account's real transfer category when one exists.

**A second catch - 14 real transactions the duplicate check couldn't tell
apart:** the importer's dedup fingerprint is account + date + amount +
payee only (memo and category aren't part of it, by design - see
`_fingerprint()`). 14 rows happened to coincidentally share all four with
another row already imported (e.g. two separate purchases in the same
shop on the same day for the same amount, or two identical bus fares) and got
silently skipped as duplicates. Checked each one by hand against its memo/
category to confirm it was a genuine separate transaction, not a real
repeat, then inserted the missing 14 directly (no `import_fingerprint`
set, same as a manually-entered transaction). This is a real, standing
limitation of the fingerprint's design worth remembering for any *future*
large historical import: it's built to stop the same file being imported
twice, not to distinguish two honestly-identical-looking purchases within
one large file.

**Files touched:** the real stash's `db.sqlite3` only (real data,
not tracked in git either way); no application code changed.

---

## 18–19/09/2026 — Save and add another, reminder "This month", indented dropdowns, report tints (logged after the fact)

**What changed** (built and tested at the time, logged on 27/09/2026 from
the code and tests):
- **Transaction form:** Save on a *new* transaction returns to the
  Transactions list. A second button, **Save and add another**, reopens a
  blank form with the same account and date already filled in
  (`?account=&date=`), for typing in a pile of receipts. Editing and
  entering a reminder work as before. Tests: `finance/test_save_and_add.py`.
- **Reminders:** a recurring reminder is never "Paid" itself. Entering or
  skipping one leaves a Paid/Skipped one-time row for that period
  (`Reminder.record_period`), and the list opens on **This month**, showing
  paid and skipped rows too. Tests: `finance/test_reminder_month.py`.
  Five Paid rows that should already have existed were added straight into
  the stash databases with SQL (after a backup), so they aren't in the audit
  log.
- **Single-choice category dropdowns** (transaction, reminder, the payee
  filter, "Move to") list each main category followed by its subcategories,
  indented (`Category.indented_name`). The multi-choice filters stay as
  they were. Tests: `finance/test_category_dropdown_indent.py`.
- **vs.-average reports:** the Difference cells are tinted (`diff_tint`
  filter): spending going up is pink, income or net going up is green.
  Tests: `finance/test_vs_average_tint.py`.

**Teaching note — why the tint needed `.table td.diff-bad`:** Bootstrap
paints table cells through its own `.table > :not(caption) > * > *` rule,
which is more specific than a lone `.diff-bad` class, so the first version
changed nothing on screen. A rule only wins if it is at least as specific
as the one it fights, which is why the selector names the table and the
cell.

---

## 22/09/2026 — Transactions: Edit mode (logged after the fact)

**What changed:** the Transactions list got an **Edit mode** button. In
Edit mode every row's cells turn into fields, and each field saves itself as
soon as you leave it (`transaction_quick_edit`), without leaving the page.
Tests: `finance/test_transaction_quick_edit.py`.

**Why it reuses `TransactionForm`:** the quick-edit endpoint runs the very
same form as the full edit page, so validation, payee matching and number
handling can't drift apart between the two. The one difference is on
purpose: saving to an *inactive* account is refused with a pointer to the
full edit page, since a per-field autosave has no room for the "make it
active again?" question and shouldn't reactivate an account silently.

**Known cost:** every row carries its hidden edit fields, including a full
category dropdown, so a long date range is a heavy page. Plan 02 (8.10,
8.11) makes it lighter and pages ranges longer than a year.

---

## 25/09/2026 — Architecture review; plans moved into plans/

**What changed:** No application code. A full architecture review of the
project (Django conventions, SOLID, PEP 8) produced a second plan,
`plans/02_architecture_cleanup.md`: 40 findings (F1–F40) grouped into 8
phases, each with checkboxes, like the first plan. The original
`UPGRADE_PLAN.md` moved to `plans/01_upgrade_plan.md` with its content
unchanged, and `plans/README.md` indexes both. `.gitignore` now ignores
`/plans/` instead of `UPGRADE_PLAN.md`, so the plans stay local like before.
Two test docstrings that pointed at `UPGRADE_PLAN.md` now point at the new
path; older entries in this log still use the old name.

**Why a folder:** one plan per file, numbered, never deleted once finished.
The history of *what we planned* sits next to this log, which records *why*.

**The review's most important findings** (details and fixes in the plan):
- Every request opened a `BEGIN IMMEDIATE` transaction on *every* stash's
  database, not just the active one. `ensure_registered()` copies
  `ATOMIC_REQUESTS=True` into each stash alias, and Django wraps each view
  in `atomic()` for every alias that has it. Checked directly:
  `{'default': True, '<real stash>': True, 'test': True}`.
- Restore copies a backup over the live SQLite file while that same request
  holds a transaction on it, which risks corrupting the database. SQLite's
  own backup API is the safe way.
- `UniqueConstraint(name, parent)` doesn't stop two top-level categories
  with the same name, because NULL parents never count as equal in SQL.
- The dashboard adds up amounts from accounts in different currencies
  without converting them. Reports does convert.
- `views.py` is 2,011 lines, business logic (import, reconcile, reminders)
  lives in views, and production code branches on `settings.TESTING`, so the
  tests never run the real stash/login middleware.

**Files touched:** `plans/` (new), `.gitignore`,
`finance/test_phase12_stash_login.py`, `finance/test_phase15.py` (docstring
path only), this log.

---

## 27/09/2026 — Source mascot art renamed

**What changed:** The files in `images/` got names that say what they are,
matching the web copies generated from them in `finance/static/finance/img/`:

| Old name | New name | Web copies made from it |
|---|---|---|
| `f542a5ff-….png` | `mascot-full.png` | `mascot-full-360/720.webp` (login, welcome, PIN screens) |
| `ChatGPT Image … 07_44_13 PM.png` | `stashy-icon.png` | `stashy-icon-32/180/192.png` (favicon, home-screen icon) |
| `ChatGPT Image … 07_46_44 PM.png` | `mascot-face.png` | `mascot-face-96/192.png` (sidebar, dashboard, empty states, help cards) |
| `icon.ico` | unchanged | — |

**Why:** No code reads `images/` — templates only point at the small web
copies — so the rename touched no code. The old names (a UUID and
ChatGPT timestamps) made it impossible to tell which original produced
which web image when regenerating them.

---

## 27/09/2026 — Second review of the architecture cleanup plan

**What changed:** No application code. `plans/02_architecture_cleanup.md`
was checked line by line against the code. 18 findings were added (F41–F58),
and five items were dropped, moved or changed. The full list and the reasons
are in the plan's "Plan review, 27/09/2026" section. Test baseline that day:
510 tests, all green, in 243 s.

**Why these additions matter:**
- The Django admin has its own login page, and it doesn't use Stashy's PIN
  lockout. A 4-digit PIN could be guessed there without limit (F41).
- Django's `transaction.atomic()` and `on_commit()` use the `default`
  database unless you pass `using=`. In Stashy, `default` is the empty
  picker database, not the stash. Today it doesn't matter, because every
  request wraps every database. Phase 1's fix removes that wrapping, which
  would expose the problem, so both get fixed in the same step (F42).
- Restoring a backup taken before an update brings back the older table
  layout. The server only migrates at launch, so the restore has to run
  `migrate` itself (F44).
- Django's cookie sessions are *signed* (tamper-proof) but not *encrypted*
  (anyone with the cookie can read them). A new recovery code was briefly
  stored there (F45).
- A second sweep the same day added F55–F58: the login's `next` parameter
  accepted `/\evil.com`, which browsers treat as another site (an *open
  redirect*); a few small repeated queries; static files with no
  cache-busting; and no error log or error pages once `DEBUG` is off.

**Why some items were dropped:** a plan should fix real problems, not
theoretical ones. The transaction-number constraint would have rejected
legitimate data, and the race it guarded against can't happen under
`BEGIN IMMEDIATE`. Moving report sums into SQLite would swap exact
`Decimal` sums for floating-point ones, with no measured slowness to justify
it. The `%b` locale fix guarded against something nothing in the code does.

**Files touched:** `plans/02_architecture_cleanup.md`, this log.

---

## 27/09/2026 — Answers to the plan's questions; plan 03; CLAUDE.md; docs ready for git

**What changed** (no application code):
- **Plan 02's open questions answered.** Names that differ only in case are
  one category or payee, and the spelling already saved wins (Q1). The
  Transactions page gets lighter and pages ranges longer than a year at
  1,000 rows, with the total still covering every page (Q3). Edit mode
  will lock the filters (8.10b). On Transactions, "All time" stays and
  "Earliest to date" goes (8.13). Typing a custom date switches the range
  to Custom, but nothing reloads until you press Filter (8.14).
- **New `plans/03_fixes_and_changes_plan.md`.** Bugs and change requests
  you notice while plan 02 runs are collected there as R1, R2, ... and
  planned into phases once 02 is done, so they don't creep into 02's scope.
- **New `CLAUDE.md`** at the project root: the rules, map and pitfalls
  Claude needs at the start of every session. It doesn't repeat this log
  (the *why*, for you) or the plans (what's next). Facts that are only true
  on this computer, such as which stash holds real data and which port your
  Stashy uses, live in `CLAUDE.local.md`, which git ignores.
- **Docs made ready to commit.** `plans/` and this log are now committed,
  so real balances and totals, payee, bank and place names, and the real
  stash's name were replaced with neutral wording or made-up examples
  ("Alex" as the owner name in examples). `.gitignore` no longer ignores
  `DEVLOG.md`, and ignores `CLAUDE.local.md`. The same clean-up reached the
  code: examples, tests and the "Whose stash?" placeholder say "Alex"
  instead of a real name, a test's payee is "City Water Co", and
  `load_test_data.py` no longer names a real bank. All 510 tests pass.
- **Migrations weren't in git.** `.gitignore` had `**/migrations/*.py`, so
  git only ever held `finance/migrations/0001_initial.py`. A fresh clone
  would have had models that no migration creates. Those ignore lines are
  gone.

**Teaching note — migrations are code, not generated junk:** it's tempting
to ignore them because `makemigrations` writes them. But each file records
one step in the database's history, and every copy of the app (a fresh
clone, or a backup being restored) replays them in order to reach the
current tables. Generate them once, review them, and commit them together
with the model change.

**Teaching note — once it's pushed, it's public history:** deleting a line
in a later commit doesn't remove it from git's history, so personal data
has to come out *before* the first commit that includes the file.

**Files touched:** `DEVLOG.md`, `README.md`, `CLAUDE.md`, new
`CLAUDE.local.md`, `.gitignore`, `plans/01_upgrade_plan.md`,
`plans/02_architecture_cleanup.md`; `finance/auth_forms.py`,
`finance/models.py`, `finance/stashes.py` (docstrings),
`finance/test_phase15.py`, `finance/test_phase6_3.py`,
`scripts/load_test_data.py`.

---

## 01/10/2026 — Plan 02, Phase 1: data safety and wrong numbers

**What changed** (details and ticks in `plans/02_architecture_cleanup.md`):
- **One stash, one transaction (1.1, 1.2).** Every request used to open a
  `BEGIN IMMEDIATE` write lock on *every* stash's file. Now only the stash
  you're using gets a transaction (`StashAtomicRequestMiddleware`), and the
  two places that opened a transaction or an "after commit" step without
  naming a database now name the stash.
- **Backups and restore (1.3, 1.4).** Both go through SQLite's own backup
  API instead of copying the file. Restoring migrates the stash straight
  away, so a backup from before an update comes back on today's tables.
  Backups whose tag has an underscore (`before_restore`, `before_update`)
  now sort by the time in their name.
- **Reconcile (1.6):** a garbled "Finish" step returns to step 1 with a
  message instead of a server error.
- **"Choose a different stash" (1.7)** is a button that sends a form, not a
  link, so another website can't log you out.
- **Dashboard (1.8, 1.9):** the tiles and the donut convert other currencies
  into your main one, like Reports. Each donut category is its own net
  total: spending 40 and getting 100 back shows as −60. It gets no wedge
  (a donut can't draw a negative slice) but stays in the list, and the
  centre total nets it, so it matches the Spending tile.
- **Import (1.10):** the whole file is read before anything is saved, so a
  broken CSV, Excel or OFX file gets a message and leaves nothing behind.
  CSV can be UTF-8 or Windows-1251. Rows with an unreadable date or amount
  are counted in the summary, and a bad `Num` (negative, huge, `inf`) means
  "no number" instead of a crash.
- **Export (1.11):** Excel files hold real numbers and dates, CSV starts with
  a BOM so Excel shows Cyrillic, and text that looks like a formula can't
  run as one.
- **Admin login (1.12):** `/admin/login/` sends you to the PIN screen, so the
  lockout covers it too.
- **"Check rates" (1.13)** no longer locks the stash while it waits on the
  internet.
- **Recovery code (1.14):** a new code waits in the server's memory, not in
  the session cookie.
- **Open redirect (1.15):** `next=/\evil.com` is refused now.

**Teaching note — why the transaction wraps the view, not the middleware
chain:** the obvious version is a middleware that does
`with atomic(): return get_response(request)`. But by the time an error
comes back up through the middleware chain, Django has already turned it
into a 500 *response*, so the `with` block sees a normal return and commits
whatever the view did halfway. Django's own `ATOMIC_REQUESTS` wraps just the
view, where the exception is still an exception. `process_view` is the hook
that runs right before the view, so the new middleware calls the view there,
inside the transaction.

**Teaching note — copying a live database:** a SQLite file is pages plus a
counter that tells every open connection "something changed, re-read".
Copying the file over itself bypasses that: other connections keep stale
pages in memory, and a copy taken mid-write can be half old, half new.
SQLite's backup API copies page by page under SQLite's own locks and bumps
that counter, so a backup is always consistent and a restore is seen by
every connection the server still holds. A new test proves the second part.

**Teaching note — signed is not secret:** Stashy's session lives in a cookie
that's *signed* (it can't be changed without the server noticing) but not
*encrypted* (anyone holding it can read it). Fine for "which stash", wrong
for a recovery code. The code now waits in the server's memory under a
random one-time key, and only that key travels in the cookie.

**Verification:** full suite green: 540 tests in 335 s (510 in 243 s
before; the new restore test really migrates a stash, twice), 30 new tests (`finance/test_cleanup_phase1.py`, plus two in
`test_phase10.py`), and an end-to-end run through the real login and
middleware chain on two throwaway stashes in a temp folder: back up, change,
restore, confirm the change is gone, and confirm the other stash was never
locked. Both real stashes were backed up first
(`backup_before_plan02_phase1_*`). Not checked in a browser: the
"Choose a different stash" button (styled as a link) and the donut with a
negative category.

**Files:** `finance/middleware.py`, `finance/stashes.py`, `finance/backup.py`,
`finance/views.py`, `finance/forms.py`, `finance/reporting.py`,
`finance/currency.py`, `finance/auth_views.py`,
`finance/static/finance/js/dashboard.js`,
`finance/templates/registration/login.html`, `audit/signals.py`,
`stashy/settings.py`, `stashy/urls.py`; tests `finance/test_cleanup_phase1.py`
(new), `finance/test_phase10.py`, `finance/test_phase11_follow_up.py`,
`audit/tests.py`; `README.md`, `CLAUDE.md`, the plan.

**Afterwards, same day:** you checked the new "Choose a different stash"
button (fine; the donut had no refund-heavy category to show). You also put
`**/migrations/*.py` back in `.gitignore`: migrations stay out of git for
now, and the pros and cons are parked in the plan (3.6) to discuss later.
The Transactions range "All time" will be called "All dates" when 8.13 is
built.

---

## 01/10/2026 — Plan 02, Phase 2: database constraints

**What changed** (details and ticks in `plans/02_architecture_cleanup.md`):
- **One name, one payee, in any alphabet (2.3, 2.4).** Payees and
  categories now keep a hidden `name_key` column: the name in "case-folded"
  form. The database refuses a second row with the same key, so "Lidl" and
  "LIDL" can't both exist, and neither can "ЛИДЛ" and "лидл". Two main
  categories can't share a name, and neither can two subcategories of the
  same main category (the same subcategory name under two different parents
  is fine). The Add/Edit forms say "A payee with this name already exists"
  (or the category version) instead of failing.
- **One way to find a payee.** `Payee.objects.resolve(name)` finds the payee
  in any case (the spelling already saved wins) or creates it. The
  transaction form, the import, autofill and Stashy's own payees ("Opening
  Balance") all go through it, replacing three hand-written lookups.
- **Rules on the model, not only the form (2.6, 2.8).** The "one level of
  subcategories" rule moved into `Category.clean()`, so the admin keeps it
  too. The database also refuses a negative reminder amount, an interval
  below 1, and a Transfer category that doesn't belong to an account (or an
  account's category that isn't Transfer). An account's currency can't
  change once it has transactions, and the admin shows it as plain text.
- **Dead column dropped (2.9):** `Transaction.payee_name` was always blank.
- Before the migration, both stashes were scanned (nothing to fix) and
  backed up (`backup_before_plan02_phase2_*`). Stashy.bat migrates them on
  the next launch.

**Teaching note — SQLite's `lower()` only knows A to Z.** The plan said
`UniqueConstraint(Lower("name"))`. A quick check showed SQLite's built-in
`lower('ЛИДЛ')` returns `ЛИДЛ` unchanged, and `LIKE` (what Django's
`iexact` becomes on SQLite) is case-blind for English letters only. So the
old "case-insensitive" payee matching never worked for Cyrillic names, and
a `Lower()` constraint wouldn't have either. Python's `str.casefold()` knows
every alphabet. Computing the key in Python, storing it, and putting the
unique rule on that column gives the database a rule it can check
correctly. The cost: the key must stay in step with the name. `save()`
recomputes it every time, and Stashy never changes these rows with
`queryset.update()`, which would skip `save()`.

**Teaching note — why the messages come from `clean()`.** Django's forms
check database constraints before saving, but only those whose columns are
on the form. `name_key` is hidden, so the form would skip the unique rule
and the save would crash instead. `Payee.clean()` and `Category.clean()`
check the same thing in Python first and give the friendly message. The
database rule stays as the safety net for anything that doesn't go through
a form.

**Verification:** full suite green: 572 tests in 500 s (540 in 335 s
before; this run shared the computer with a second test run). After a last
message tweak, the 163 tests that touch reminders or this phase passed
again. 32 new tests in
`finance/test_cleanup_phase2.py` (Cyrillic case-duplicates refused by the
database, "lidl" saved as "Lidl" from the transaction form and the import,
the one-level rule's messages, the admin rules, the dropped column, and the
audit log leaving `name_key` out). The migration was tried on throwaway
copies of both stashes first: integrity check ok, every name got its key.
The Add category, Add payee and Add reminder pages were rendered with a
duplicate or a negative amount, and the admin pages were rendered too: the
messages show, and no template code leaks. Not checked in a browser.

**Files:** `finance/models.py`, `finance/forms.py`, `finance/views.py`,
`finance/admin.py`, `finance/starter_categories.py`, `audit/signals.py`,
`finance/migrations/0018_phase2_constraints.py` (local only, not in git);
tests `finance/test_cleanup_phase2.py` (new); `README.md`, `CLAUDE.md`, the
plan.

---

## 02/10/2026 — Plan 02, Phase 3: tooling and settings

**What changed** (details and ticks in `plans/02_architecture_cleanup.md`):
- **A linter and formatter (3.1–3.3).** `pyproject.toml` configures ruff,
  and `requirements-dev.txt` lists it with `coverage` (tools for working on
  Stashy; Stashy.bat never installs them). The reformat went in as its own
  commit, so it couldn't hide any real change. Afterwards the ~100 findings
  ruff can't fix itself were fixed by hand: two lambdas became small
  functions, an if/else whose branches were identical became one line,
  `f`/`t` became `filters`/`txn` where they lived across long functions,
  `save()` moved next to `__str__()` in five models (Django's style
  guide), and `raise ... from error` now keeps the original error attached.
  `ruff check` is clean.
- **Requirements (3.4).** The two leftover Postgres packages were removed
  from `.venv`. `beautifulsoup4` and `lxml` looked unused but turned out to
  be the OFX importer's own dependencies, so they stay.
- **Settings package (3.5).** `stashy/settings.py` became
  `stashy/settings/base.py` (the app) and `test.py` (the test suite, picked
  by `manage.py test` by itself). There's no built-in fallback `SECRET_KEY`
  any more, `DEBUG` is off unless `.env` turns it on, and the unused password
  rules are gone (a stash's login is its PIN). Every folder Stashy uses is
  named once, at the top of `base.py`.
- **DEBUG off, with somewhere for errors to go (3.7, 3.9).** New `.env` files
  get `DEBUG=False`. A crash now shows a friendly "Something went wrong"
  page, and its full traceback goes to `logs\stashy.log` (and the console
  window). The log rotates at 2 MB and keeps 3 older files. A missing page
  shows "Nothing stashed here". Both pages use the login page's layout.
- **Fingerprinted static files (3.8).** `collectstatic` now also saves each
  CSS/JS/image file under a name containing a fingerprint of its content
  (`stashy.d4f3e63f4df4.css`), and pages link to that name.
- `.gitignore` now covers `staticfiles/` and `logs/`.

**Teaching note — why the test suite went from 5 minutes to 15 seconds.**
Django stores passwords (here, PINs) with a hash that is slow *on purpose*:
it repeats its work over a million times, so someone who steals the database
can only try a few guesses per second. Every test that creates a user paid
that cost, hundreds of times per run. `test.py` swaps in MD5, which is
instant and would be unsafe for real logins, but test users live only in a
throwaway in-memory database. One line of test-only settings cut the suite
from 298 s to 15 s.

**Teaching note — what the fingerprint in a file name buys.** A browser
keeps a copy of each CSS and JS file so it doesn't download it on every
page. The trouble comes after an update: it can keep running the old
`stashy.js` against new pages. With the content's fingerprint in the name,
a changed file gets a new name. The browser has never seen that name, so it
fetches it, and an unchanged file keeps its name and stays cached. That's
why WhiteNoise can now tell the browser to keep these files "for good"
(`Cache-Control: immutable`).

**Teaching note — the check that stopped a broken launch.** While making
those names, Django also rewrites every file one CSS file points to, and
stops if one is missing. Bootstrap and Chart.js end with a comment naming a
`.map` file (a debugging aid for the library's own developers) that Stashy
never shipped. With the plain fingerprinting storage, `collectstatic`
failed, and Stashy.bat runs it on every launch, so Stashy wouldn't have
started. Running the real command before calling the work done caught it.
`stashy/storage.py` now leaves those comments alone, and a test runs the
real `collectstatic`, so this can't come back unnoticed.

**Coverage baseline (for Phase 4):** 89% overall: 3,696 statements, 425
never run by a test. The lowest are `stashes.py` (52%), `auth_views.py`
(40%) and `auth_forms.py` (62%). The tests skip the stash middleware for now;
plan 02 Phase 7 changes that. Measure again with
`.venv\Scripts\coverage.exe run manage.py test` and
`.venv\Scripts\coverage.exe report`.

**Verification:** full suite green: 580 tests in 15.4 s. That's 8 new tests
in `finance/test_cleanup_phase3.py`: the 404 and 500 pages with `DEBUG` off
and no template code leaking, the traceback reaching the log file, the real
`collectstatic`, and every `{% static %}` a template names existing. The
tests never write to the real `logs\stashy.log`.

Separately, a throwaway Waitress server on port 8765 ran the app's real
settings with `DEBUG` off. The stash picker rendered, and the fingerprinted
stylesheet was served with the long-lived cache header. All main pages were
rendered against a fingerprinted `collectstatic` copy: every CSS/JS/image
link carried its fingerprint, and the icon and text fonts inside the CSS
were rewritten too. Not checked in a browser.

**Files:** `pyproject.toml`, `requirements-dev.txt`,
`stashy/settings/__init__.py`, `stashy/settings/base.py` (was
`stashy/settings.py`), `stashy/settings/test.py`, `stashy/storage.py`,
`finance/templates/404.html`, `finance/templates/500.html`,
`finance/templates/finance/error_page.html`,
`finance/test_cleanup_phase3.py` (all new), `manage.py`, `stashy/wsgi.py`,
`stashy/asgi.py`, `scripts/bootstrap.py`, `.env.example`, `.gitignore`,
`finance/apps.py`, `finance/stashes.py`, `finance/views.py`,
`finance/forms.py`, `finance/models.py` and a few small ruff fixes
elsewhere; `README.md`, `CLAUDE.md`, the plans.

---

## 02/10/2026 — Plan 04 (Stashy as an .exe) started; why the migration history stays

**What changed:** `plans/04_exe_packaging.md` holds the idea of shipping
Stashy as a Windows program that needs no Python. It's in the idea stage,
with its questions open, and starts after plan 02.

**Teaching note — why not start the migrations over with one clean file
each?** Twenty migration files look like clutter, and a fresh
`0001_initial` per app would describe the same tables. But each stash
remembers, by name, which migrations it has run, and restoring a backup runs
`migrate` to bring an older backup up to date. A fresh file would carry the
*same name* as the old first migration. Django would read an older backup's
records as "0001_initial: done, nothing else to do" and never add the
columns that came later, so pages would crash after a restore. Keeping the
history costs about a second when a brand-new stash is created, and it
keeps every backup restorable, including from inside a future `.exe`.

---

## 02/10/2026 — Stashy's address: stashy.localhost

**What changed:** Stashy.bat now opens `http://stashy.localhost:<port>/`
instead of `http://127.0.0.1:<port>/`. `stashy.localhost` is always allowed
in `base.py`, whatever `.env` lists, and the old address still works. The
`stashy.app` name and its hosts-file instructions are gone from
`.env.example` and from the `.env` that Stashy.bat creates.

**Why:** a name instead of numbers, with no setup on the computer.

**Teaching note — why `stashy.app` couldn't work.** Browsers ship with a
list of names that may only be opened over HTTPS (the encrypted kind of
connection), and the *whole* `.app` ending is on it. So the browser turns
`http://stashy.app` into `https://stashy.app` before connecting. Stashy
speaks plain HTTP and has no certificate, so the page fails, and for these
names there's no "continue anyway" button. A hosts-file entry only answers
"which computer?", not "is it HTTPS?".

**Teaching note — why `.localhost` needs no setup.** Names ending in
`.localhost` are reserved for "this computer" (RFC 6761), so Chrome, Edge
and Firefox send them to 127.0.0.1 by themselves, without asking Windows.
Windows itself doesn't know these names, which is why Stashy.bat's own
"is Stashy already running?" check still uses `127.0.0.1`. One side effect:
browsers keep the login cookie and the light/dark choice per address, so the
first visit at the new address asks for the PIN again.

**Verification:** full suite green, 582 tests. Two new tests: a request
for `stashy.localhost:8001` is accepted, and one for `stashy.app` is still
refused. Not checked in a browser.

**Files:** `stashy/settings/base.py`, `scripts/bootstrap.py`,
`.env.example`, `README.md`, `finance/test_cleanup_phase3.py`.

---

## 02/10/2026 — Plan 02, Phase 4: service layer

**What changed** (details and ticks in `plans/02_architecture_cleanup.md`):
- **The importer moved out of the views (4.1, 4.2).** Everything that
  reads a CSV, Excel or OFX file and turns its rows into transactions now
  lives in `finance/services/importing.py`, behind one function:
  `import_file(upload, account, date_format)`. It returns an
  `ImportResult` (how many rows were imported, skipped as duplicates, or
  unreadable). The Import page's view shrank to "check the form, call
  `import_file()`, show the summary".
- **Reconciling moved too (4.3).** `finance/services/reconciliation.py` has
  `reconcile()`. If the ticked transactions don't match the statement and
  you haven't accepted the difference, it raises `UnacceptedDifferenceError`
  and saves nothing. The view catches that and shows the checklist again.
- **Reminder rules live on the reminder (4.4).** `reminder.mark_entered(txn)`,
  `reminder.skip()` and `reminder.apply_to_series()` replace code that was
  copied between two views. The "moved it to …" messages now use your
  stash's date format instead of always DD/MM/YYYY.
- **One Watched-expenses picker (4.5).** Settings and the welcome wizard
  each built the category picker and its button label. Both now call
  `watched_expenses_picker_context()`.
- **The Import page checks the file type in the form (4.6).** A `.pdf` now
  gets its message right under the File field, and the page shows any
  field's error, which it never did.
- **Reports are built from named pieces (4.8).** "Has a category, isn't a
  transfer, in this date range, for this account" was written out by hand
  in nine report functions. It's now `Transaction.objects.reportable()
  .in_range(start, end).for_account(account)`. One `_rollup_to_parents()`
  replaces the two copies of "add each subcategory into its main category".
- New tests (4.7): `finance/test_cleanup_phase4.py`, 28 of them, calling the
  services and model methods directly, with no web request. Two of them are
  the first tests of a successful Excel and OFX import.

**Teaching note — what a "service layer" is for.** A view's job is the
web part: read the request, pick a template, redirect. When the rules for
importing a file live inside the view, the only way to use or test them is
to fake a whole web request, with a logged-in user, a form and an upload.
Moved into a plain function, the same rules can be called from a test, a
management command or a future `.exe` (plan 04) the same way. The view gets
short enough to read in one glance. C4 in the plan's decisions log says
these are plain functions in modules, not classes: the smallest thing that
does the job.

**Teaching note — how to prove a refactor changed no numbers.** "The tests
still pass" only proves what the tests check. Before touching
`reporting.py`, a throwaway test filled a database with 600 random
transactions. They covered two currencies, refunds, transfers, uncategorized
rows and subcategories. The test called every report function in every
combination (expense/income/both, with and without subtotals, one account
or all) and wrote all 140 results to a file. After the refactor it ran
again, and the two files were byte-for-byte identical. This is called a
*characterization test*: it records what the code does today, right or
wrong, so you can tell whether a change kept it.

**Found along the way (filed, not fixed):** that probe showed a quirk in the
vs-average table. A main category whose subcategory is used for the first
time this period shows an average of 0.00, while the subcategory itself
correctly shows none. This phase promised identical numbers, so it stays as
it is. It's filed as plan 03, R2, for you to decide.

**Coverage:** 90% (3,711 statements, 384 not run by any test), up from 89%
and 425 before Phase 4. `services/importing.py` is at 95% and
`services/reconciliation.py` at 100%.

**Verification:** full suite green: 610 tests in 17.2 s (582 before).
`ruff check .` and `ruff format --check .` are clean, and `makemigrations
--check` finds no model change (a new manager needs no migration). The
Import page was rendered through the test client with a wrong file type:
the message shows under the field, and no template code leaks. Not checked
in a browser.

**Files:** `finance/services/__init__.py`, `finance/services/importing.py`,
`finance/services/reconciliation.py`, `finance/test_cleanup_phase4.py` (all
new); `finance/views.py`, `finance/models.py`, `finance/forms.py`,
`finance/reporting.py`, `finance/welcome_views.py`,
`finance/templates/finance/import_form.html`,
`finance/test_cleanup_phase1.py`; `README.md`, `CLAUDE.md`, the plans.

---

## 03/10/2026 — Plan 02, Phase 5: the views split into a package

**What changed** (details and ticks in `plans/02_architecture_cleanup.md`):
- **`finance/views.py` (2,035 lines) is now a `finance/views/` folder (5.1)**
  with one file per area of the app: `dashboard.py`, `accounts.py`,
  `categories.py`, `payees.py`, `transactions.py`, `reminders.py`,
  `imports.py`, `exports.py`, `reports.py`, `settings.py` and `api.py`.
  `auth_views.py` and `welcome_views.py` moved in as `auth.py` and
  `welcome.py`. Small pieces several files use (column sorting, the safe
  "next" link, the CSV/Excel writers) are in `_helpers.py`. The largest
  file is `reports.py` at 370 lines. `finance/urls.py` names each view by
  its file (`accounts.account_detail`), so the URL list doubles as a map.
- **One view for every report export (5.2).** Eight nearly identical
  `export_report_*` views became one, `export_report`, plus a table
  (`REPORT_EXPORTS`) that says, per report, how to build it, how to lay out
  its columns and what to call the file. The addresses
  (`/reports/export/spending/` and so on) are exactly as before.
- **Each form on Settings posts to its own address (5.3).** Before, one
  view received all four forms and guessed which one was sent from the
  button's name. Now "Check rates now" posts to `/settings/rates/check/`,
  "Create a backup" to `/settings/backups/create/`, Watched expenses to
  `/settings/watched-expenses/`, and the preferences to `/settings/` itself.
- **The help-card tips moved to `finance/content.py` (5.4).** They're
  wording, not logic.
- **Every page needs a login unless it says otherwise (5.6).** Each view
  used to carry its own `@login_required`, and a new view that forgot it
  would have been open to anyone. Django's `LoginRequiredMiddleware` now
  protects everything. The few pages that must work before you log in (the
  stash picker, creating a stash, the PIN screen, forgot PIN, logout) are
  marked `@login_not_required`. The two Stashy middlewares that let some
  addresses through (the picker, the welcome wizard) now work those
  addresses out from the URL names instead of having them typed in.
- Comments in the moved code lost their history notes ("Phase 6",
  "2026-09-14 follow-up"); that history lives here. New tests:
  `finance/test_cleanup_phase5.py`, 23 of them.

**Teaching note — why split a big file at all?** Python doesn't care
whether a view is in a 2,000-line file or a 100-line one. People do. To
change the Reminders page you had to find it among nine other features, and
the imports at the top listed everything any of them used, so you couldn't
tell what Reminders actually depends on. Now `reminders.py` imports only
what reminders need, and a file name tells you where to look. A folder with
an `__init__.py` is a *package*: `from .views import reminders` loads the
file `views/reminders.py`, the same way `from . import models` loads
`models.py`.

**Teaching note — "secure by default".** When protection is something each
view has to ask for, safety depends on nobody ever forgetting. Turning it
around (everything is protected, and opening a page up takes a visible
decorator) makes the safe choice the one that happens when you do nothing.
One trap showed up right away: Django's `LoginView` is already marked
public, but the mark sits on its `dispatch()` method. Stashy's
`StashLoginView` writes its own `dispatch()`, which replaced the marked one,
so the PIN screen itself suddenly needed a login and redirected to itself.
Two existing tests failed on it before it could reach you. The fix gives
the mark back with `method_decorator`.

**Teaching note — a registry instead of copies.** Eight export views
differed only in three things: which report function they called, which
column layout they wrote, and the file name. When the differences are that
small, put them in a table (a dict) and write the shared code once. Adding a
ninth report export is now one line in `REPORT_EXPORTS`, not a new view, a
new URL and a template change.

**Coverage:** 91% (3,771 statements, 335 not run by any test), up from
90%. Every new view module is at 91–100%, except `auth.py` at 45% (the PIN
login and lockout run only with real stashes, which Phase 7 makes testable).

**Verification:** full suite green: 633 tests in 22 s (610 before).
`ruff check .` and `ruff format --check .` are clean. Every main page was
rendered through the test client and checked for leaked template code. A
test follows each Export link on the Reports page and gets a file back.
The real middleware chain (which tests skip) was tried on a throwaway server
on port 8765 against the `test` stash, with GET requests only. With no stash
chosen, every page goes to the picker. Once one is chosen, the PIN screen
opens and every app page goes to it with `?next=`. Not checked in a
browser.

**Files:** `finance/views/` (all new: `__init__.py`, `_helpers.py`,
`accounts.py`, `api.py`, `auth.py`, `categories.py`, `dashboard.py`,
`exports.py`, `imports.py`, `payees.py`, `reminders.py`, `reports.py`,
`settings.py`, `transactions.py`, `welcome.py`), `finance/content.py`,
`finance/test_cleanup_phase5.py` (new); `finance/views.py`,
`finance/auth_views.py`, `finance/welcome_views.py` (removed);
`finance/urls.py`, `finance/middleware.py`, `stashy/urls.py`,
`stashy/settings/base.py`, `finance/templates/finance/reports.html`,
`finance/templates/finance/settings.html`,
`finance/templates/finance/includes/report_card_header.html`,
`finance/templates/finance/includes/export_dropdown.html`, five test files;
`README.md`, `CLAUDE.md`, the plans.

---

## 03/10/2026 — Plan 03, R4, R5 and R6: file names, and averages of 0

You left the timing of R4–R6 to Claude. R4 and R5 were small, so they're
done now, ahead of plan 03's own phases; R6 followed once its questions were
answered, at your request in the same commit.

**What changed:**
- **Exports carry the time (R4).** A file used to be called
  `stashy-net-worth-20261003.csv`, so a second export the same day got the
  same name and the browser had to rename it ("(1)"). Now it's
  `stashy-net-worth-2026-10-03_14-32-05.csv`, in the stash's own time zone.
- **Backups carry the stash's name (R5).** The backups taken before each
  plan phase had identical names in every stash's folder. New backups are
  named `<stash>_backup_<tag>_<time>.sqlite3`, for example
  `alex_backup_manual_2026-10-03_14-32-05.sqlite3`. Old backups keep their
  names: the list on Settings, Restore and the quarterly check still read
  all three naming schemes.
- Along the way, `Stashy.bat`'s "back up before an update" step now uses
  the same `backup_now()` as everything else. Before, it was the only
  backup made with a plain file copy instead of SQLite's backup API.
- **A category with no earlier activity averages 0 (R6, also settles R2).**
  Compare this month with the last 3: if those months have data but no
  Accommodation, that means no vacation, not missing data. So Accommodation
  now averages 0. The table still shows "—" in its Average cell (you prefer
  that), but its Difference shows the full amount with its colour, and the
  chart draws its average dot at 0. The export writes 0.00 and the
  difference. What stays: with fewer than two earlier periods of data the
  reports still say "No earlier data to compare with.", and a category with
  some earlier activity still averages over every period (600 in one of 3
  months → 200). This replaces the 16/09/2026 rule "no earlier activity, no
  average, no dot". A main category whose subcategory is brand new (R2) now
  averages 0 too, instead of 0.00 beside a subcategory with no average.
- New tests: `finance/test_fixes_plan03.py` (15). Three older tests that
  encoded the 16/09 rule were updated or renamed.

**Teaching note — why a regex can tell the names apart.** A backup's name
now has up to four parts: stash, the word `backup`, a tag (`manual`,
`before_update`) and the time. Tags can contain underscores, so "split on
`_`" doesn't work. Stash folder names, though, never contain one (they're
made of a-z, 0-9 and `-`), so the pattern
`(stash_)?backup_(tag)_(time)` has only one way to match each name. That's
how `quarterly_backup_if_due()` knows a stash called "quarterly" with a
manual backup isn't a quarterly backup. A test checks exactly that.

**Teaching note — "nothing" vs. "zero", revisited.** On 16/09 the worry
was that an average of 0 reads as "you used to spend nothing here" when the
truth might be "Stashy doesn't know". The answer then was to show no average
at all. The better split turns out to be at the level of the whole stash:
if the earlier months have data at all, an empty month for one category is
a real fact (you didn't spend there), so 0 is the honest average. Only when
there's no earlier data anywhere is "unknown" the right answer. The flag
`no_earlier_activity` keeps the one thing the old rule got right: the table
can still say "—" so the eye isn't drawn to a misleading 0.00.

**Verification:** full suite green, 648 tests. `ruff check .` and
`ruff format --check .` are clean. `scripts/bootstrap.py` compiles; its
before-update backup runs only when a migration is pending, so it will first
run for real at the next update.

The Spending vs average row was read from the real Reports page HTML in a
test: current and difference filled in, "—" for the average. Not checked in
a browser.

**Files:** `finance/backup.py`, `finance/views/exports.py`,
`finance/reporting.py`,
`finance/templates/finance/includes/report_vs_average_table.html`,
`scripts/bootstrap.py`, `finance/test_fixes_plan03.py` (new),
`finance/test_phase10.py`, `finance/test_phase11.py`,
`finance/test_phase11_follow_up.py`, `finance/test_phase13_reports.py`;
`README.md`, `CLAUDE.md`, `plans/02_architecture_cleanup.md`,
`plans/03_fixes_and_changes_plan.md`.

---

## 03/10/2026 — Plan 02, Phase 6: side effects and boundaries

**What changed** (details and ticks in `plans/02_architecture_cleanup.md`):
- **Saves do their extra work only when it matters (6.1, 6.2).** Saving an
  account used to look up (and maybe rename) its `[Account]` transfer
  category every time, even when you only ticked "inactive". Now that runs
  only for a new account or a new name. A recurring reminder used to rebuild
  its future occurrences on every save, including "mark this period paid".
  Now a save of chosen fields rebuilds them only if one of those fields can
  move a date (frequency, interval, due date, end date, last day of period).
  Saving the whole reminder from the form still rebuilds them, because
  that's also what moves the "90 days or year-end" horizon forward.
- **The audit log has its own off switch (6.3).** Creating a stash needs the
  audit log off for a moment (the new database has nothing to log yet). The
  stash code used to reach into the audit app and unplug its five signal
  handlers by name. That had a hidden flaw: unplugging is for the whole
  program, so while one person created a stash, changes made by any other
  request at that moment weren't logged either. Now the audit app offers
  `audit_suspended()`, which marks only the current request as "don't log".
- **The router remembers the current stash in a ContextVar (6.4),** the same
  tool the audit log already used, instead of `threading.local`.
- **`stashes.py` became a `stashes/` package (6.5):** `registry.py` (where
  the stashes are: folders, connections, creating, restoring, owner names)
  and `security.py` (the PIN, the lockout, recovery codes). Creating a stash
  and the `reset_pin` command now have tests on a real temporary stash; they
  had none before.
- **One PIN rule (6.7).** The "exactly 4 digits" check was written out five
  times and "PINs don't match" three times. Now there's one `PinField` and
  one `PinConfirmMixin`. Every wrong PIN gets the same message, "PIN must
  be exactly 4 digits." (a too-short one used to get Django's "Ensure this
  value has at least 4 characters").
- **The audit app stands on its own (6.8).** It formats its timestamps
  itself instead of borrowing `finance.regional`, so it no longer imports
  anything from the finance app.
- **One rule for a new subcategory's type (6.9):** it always takes its main
  category's type, whether the importer or the starter-categories sheet
  creates it (`Category.create_subcategory()`). The starter sheet also can't
  put a subcategory under an account's `[Transfer]` category any more. The
  sheet that ships with Stashy never disagreed with this rule, so new stashes
  get the same categories as before.
- **No hidden import cycles (6.10).** `possessive()` ("Alex's") moved to a
  tiny `finance/text.py`. Imports went to the top of their modules. The four
  that must stay inside a function each say why in a `# Imported here: ...`
  comment, and a test checks that.
- New tests: `finance/test_cleanup_phase6.py` (27).
- Found along the way and filed as plan 03 **R9**: `manage.py reset_pin
  Alex` crashes when the stash's folder is `alex`, because Windows ignores
  upper/lower case in folder names. It was already like that; this phase
  only moved the code.

**Teaching note — side effects in `save()`.** Putting "also do X" inside
`save()` is handy: the admin, the shell and scripts all get X for free. The
price is that *every* save pays for X, including saves that can't possibly
need it. Django's `save(update_fields=[...])` says exactly which columns are
being written, so `save()` can ask "could this change matter to X?" and skip
the work when the answer is no. To spot a renamed account, Django's
`from_db()` hook (called whenever a row is loaded) remembers the name as it
was in the database, so `save()` can compare.

**Teaching note — global switch vs. a per-request flag.** Django's signals
belong to the whole running program, and Waitress serves several requests at
once on different threads. Disconnecting a signal handler to silence it
"here" silences it *everywhere* until it's reconnected. A ContextVar is a
value private to the current request, so setting a "suspended" flag in it
silences only the code running inside the `with audit_suspended():` block.
A test runs a second thread during the block and checks it still logs.

**Teaching note — import cycles.** `models.py` used to import `stashes`
inside a function, and `stashes` imported `models` inside functions. Each
hid the other's import to dodge an "import cycle" (two modules that each
need the other while loading). The fix is to move the shared piece somewhere
both can use: `possessive()` needs nothing from either, so it lives alone in
`text.py`. Imports at the top of a file are a table of contents: they tell
you what the module depends on before you read a line of it.

**Coverage:** 94% (3,776 statements, 233 not run by any test), up from 91%
at Phase 5. `stashes/registry.py` is at 88%, `stashes/security.py` at 93%.

**Verification:** full suite green: 675 tests in 23 s (648 before).
`ruff check .` and `ruff format --check .` are clean. `makemigrations
--check` finds nothing: this phase has no database changes. The real
middleware chain was tried on a throwaway server on port 8765 against the
`test` stash, GET requests and the picker form only: with no stash chosen
every page goes to the picker; once `test` is chosen, the PIN screen and
Forgot PIN open and show their PIN boxes. Not checked in a browser.

**Files:** `finance/stashes/` (new: `__init__.py`, `registry.py`,
`security.py`), `finance/text.py`, `finance/test_cleanup_phase6.py` (new);
`finance/stashes.py` (removed); `finance/models.py`, `finance/auth_forms.py`,
`finance/db_router.py`, `finance/middleware.py`, `finance/apps.py`,
`finance/regional.py`, `finance/currency.py`, `finance/forms.py`,
`finance/starter_categories.py`, `finance/services/importing.py`,
`finance/views/auth.py`, `finance/views/settings.py`,
`finance/views/_helpers.py`, `finance/management/commands/reset_pin.py`,
`audit/admin.py`, `audit/apps.py`, `audit/context.py`, `audit/signals.py`,
`scripts/load_test_data.py` (import only), `finance/test_cleanup_phase1.py`,
`finance/test_phase12_stash_login.py`, `finance/test_phase14.py`,
`finance/test_phase15.py`; `README.md`, `CLAUDE.md`,
`plans/02_architecture_cleanup.md`, `plans/03_fixes_and_changes_plan.md`.

## 04/10/2026 — Plan 02, Phase 7 (part 1): tests by feature, through the real middleware

**What changed** (details and ticks in `plans/02_architecture_cleanup.md`):
- **The tests moved into `finance/tests/`, one file per area (7.3).** The
  37 files named after the phase that wrote them (`test_phase7_2.py`,
  `test_cleanup_phase4.py`, `tests.py`, …) became 23 files named after the
  code they test: `test_accounts.py` goes with `views/accounts.py`,
  `test_reporting.py` with `reporting.py` (the Reports page itself is
  `test_reports.py`), `test_project.py` holds Django's settings, logging and
  the rules the code follows, `test_pages.py` the checks every page must
  pass. Four audit-log tests that lived in finance moved to
  `audit/tests.py`. The 25 copies of `LoggedInTestCase` became three shared
  base classes in `finance/tests/base.py`.
- **Moved, not rewritten.** A script moved each class as text, and a second
  script compared every test method's syntax tree with the original. The
  only differences are planned ones: imports made inside a test now sit at
  the top of its file (one, `from . import models`, would have broken one
  folder down); two classes got new names because their names clashed in
  one file (`ReportExportRegistryTests`, `ReportsCustomRangeErrorTests`);
  the clashing pretend-today dates are `TODAY_13_SEPT`, `TODAY_14_SEPT` and
  `TODAY_19_SEPT` (and `TODAY_12_SEPT` on the dashboard); a few docstrings
  pointed at deleted files; and one test used a real person's name as a
  stash name, now "alex". All 675 tests ran, name for name, before anything
  else changed.
- **Tests now run through the real middleware (7.1).** Until now the stash
  picker, the stash choice and the welcome-wizard gate all stepped aside
  whenever `settings.TESTING` was on, so the suite never ran the code that
  every real request runs. Now each test run makes its own throwaway stash,
  "tester", in a temporary folder that is deleted afterwards. Every database
  test runs inside it: its own queries go to that stash, and its test client
  starts with the stash already chosen, as if picked on the picker. The
  `TESTING` switch is gone from the app code (a test checks it stays gone),
  and so is `apps.py`'s check of the command line.
- **The placeholder database is off limits in tests.** `default` (the
  picker's empty database) can't be touched by a test unless the test asks
  for it, so code that forgets which stash it's in fails loudly instead of
  quietly writing to the placeholder. The app code passed. Eight tests
  failed at first: their own `transaction.atomic()` didn't say which
  database, so it opened one on `default`. They now say
  `using=settings.TEST_STASH`.
- **The middleware chain has tests of its own (7.2):** with no stash chosen,
  every page goes to the picker; the "last stash used" cookie opens that
  stash's PIN screen; picking a stash and typing its PIN reaches the
  dashboard; an unfinished welcome wizard comes before every page; a page in
  a chosen stash runs no query on the placeholder. Each test fails when its
  piece of middleware is switched off (checked once by hand).
- **Shared test data is built once per class (7.4, part 1).** The shared
  base classes create their user and account in `setUpTestData` instead of
  before every test. The classes with a `setUp` of their own come next.
- **Found along the way:** two old backup tests wrote into a `backups/`
  folder in the project itself, which is why an empty one kept appearing
  there. They now use the test stash's own folder, and the empty folder is
  gone. A test run started with the app's own settings (an editor set up
  that way, say) now stops at once with "Run the tests with
  stashy.settings.test".

**Teaching note — testing the real path.** A switch like `if TESTING:` makes
tests easier to write, but every line behind it is code the tests never run,
and those lines are exactly the ones that decide which database a request
uses. The fix is to make the test world look like the real one (a real stash
chosen in a real session) instead of making the code behave differently
under test. One small change made it possible: the middleware used to
*clear* the current stash after each request. Now `db_router.use_stash()`
*puts back* whatever was there before (a ContextVar "token" remembers it).
In the server that is always "nothing", so Stashy behaves exactly as before.
In a test, the test's own stash stays current after each request it makes.

**Teaching note — setUp vs. setUpTestData.** `setUp` runs before every
test, so 50 tests in a class build the same rows 50 times. `setUpTestData`
builds them once per class. That is safe because Django wraps each test in a
transaction it rolls back, and hands every test its own copy of the objects
made there, so no test sees another test's changes. Anything that isn't
database data (logging in a test client, mocks, temporary files) stays in
`setUp`.

**Old → new test files** (number of classes that went to each):

| Old file | New file(s) in `finance/tests/` |
|---|---|
| `test_category_dropdown_indent.py` | test_categories.py |
| `test_cleanup_phase1.py` | test_auth.py (2), test_dashboard.py (2), audit/tests.py (1), test_backup.py (1), test_exports.py (1), test_imports.py (1), test_middleware.py (1), test_reconciliation.py (1), test_settings.py (1), test_transactions.py (1) |
| `test_cleanup_phase2.py` | test_payees.py (3), test_categories.py (2), audit/tests.py (1), test_admin.py (1), test_transactions.py (1) |
| `test_cleanup_phase3.py` | test_project.py (4), test_pages.py (1) |
| `test_cleanup_phase4.py` | test_imports.py (3), test_reporting.py (2), test_reconciliation.py (1), test_reminders.py (1), test_reports.py (1) |
| `test_cleanup_phase5.py` | test_pages.py (2), test_auth.py (1), test_exports.py (1), test_middleware.py (1), test_settings.py (1) |
| `test_cleanup_phase6.py` | test_project.py (2), audit/tests.py (1), test_accounts.py (1), test_auth.py (1), test_categories.py (1), test_middleware.py (1), test_reminders.py (1), test_stashes.py (1) |
| `test_dashboard.py` | test_dashboard.py (3), test_categories.py (1), test_pages.py (1), test_transactions.py (1) |
| `test_fixes_plan03.py` | test_backup.py (1), test_exports.py (1), test_reports.py (1) |
| `test_phase3.py` | test_accounts.py (4), test_formatting.py (2), test_currency.py (1), test_reporting.py (1), test_transactions.py (1) |
| `test_phase4.py` | test_categories.py |
| `test_phase5.py` | test_transactions.py (4), test_accounts.py (1) |
| `test_phase6.py` | test_payees.py (3), test_api.py (2), test_imports.py (1) |
| `test_phase6_1.py` | test_accounts.py (1), test_categories.py (1), test_pages.py (1), test_payees.py (1), test_transactions.py (1) |
| `test_phase6_2.py` | test_reminders.py (2), test_transactions.py (1) |
| `test_phase6_3.py` | test_dashboard.py (2), test_payees.py (1) |
| `test_phase7.py` | test_reminders.py |
| `test_phase7_1.py` | test_reminders.py (3), test_dashboard.py (1) |
| `test_phase7_2.py` | test_reminders.py (6), test_categories.py (1), test_transactions.py (1) |
| `test_phase7_3.py` | test_categories.py (1), test_reminders.py (1), test_transactions.py (1) |
| `test_phase7_4.py` | test_reminders.py |
| `test_phase8.py` | test_reconciliation.py |
| `test_phase9.py` | test_imports.py |
| `test_phase10.py` | test_backup.py (3), test_exports.py (2) |
| `test_phase11.py` | test_reporting.py (2), test_reports.py (1) |
| `test_phase11_1.py` | test_reporting.py (1), test_reports.py (1), test_transactions.py (1) |
| `test_phase11_follow_up.py` | test_reporting.py (4), test_reports.py (2), test_exports.py (1), test_transactions.py (1) |
| `test_phase12_stash_login.py` | test_auth.py (3), test_middleware.py (2) |
| `test_phase13.py` | test_accounts.py (2), test_transactions.py (2), test_payees.py (1), test_reports.py (1) |
| `test_phase13_reports.py` | test_reports.py (6), test_reporting.py (5), test_pages.py (1) |
| `test_phase14.py` | audit/tests.py (1), test_transactions.py (1) |
| `test_phase15.py` | test_welcome.py (7), test_settings.py (3), test_categories.py (2), test_middleware.py (2), test_stashes.py (1) |
| `test_reminder_month.py` | test_reminders.py |
| `test_save_and_add.py` | test_transactions.py |
| `test_transaction_quick_edit.py` | test_transactions.py |
| `test_vs_average_tint.py` | test_reports.py |
| `tests.py` | test_formatting.py (3), test_reports.py (2), test_accounts.py (1), test_categories.py (1), test_imports.py (1), test_payees.py (1), test_transactions.py (1) |

**Test changes beyond the move** (all for 7.1; no assertion changed except
where said): 53 classes changed base from `TestCase` to `StashTestCase`;
15 `@override_settings(TESTING=False)` lines went; eight `atomic()` calls
got `using=`; two backup tests use the test stash's own backups folder; one
test chooses the stash again after logging out; one counts queries on the
stash's connection; the audit "after-commit" test now expects the test stash
where it expected `default`; the settings test checks the settings module
where it checked `TESTING`; two new settings tests.

**Suite time:** 675 tests in 23.2 s before; 683 tests in 23.0 s after 7.1
and 7.2; 22.4 s once the shared bases used `setUpTestData`.

**Coverage:** 94% (3,764 statements, 208 not run by any test, down from
233). `middleware.py` and `db_router.py` are now fully covered.

**Verification:** full suite green, 683 tests. `ruff check .` and `ruff
format --check .` are clean. Single modules run on their own too
(`manage.py test finance.tests.test_reminders`, `manage.py test audit`). The
real middleware was tried on a throwaway server on port 8765 with the `test`
stash, GET requests and the picker form only: with no stash chosen, every
page goes to the picker; choosing `test` opens its PIN screen; forgot PIN
and the admin login behave as before. Not checked in a browser. No stash
file was written.

**Still to do (7.4, part 2):** the 67 test classes with a `setUp` of their
own.

**Files:** `finance/tests/` (new: `__init__.py`, `base.py` and 23
`test_*.py`); `finance/tests.py` and the 36 old `finance/test_*.py` files
(removed); `audit/tests.py`, `finance/apps.py`, `finance/db_router.py`,
`finance/middleware.py`, `finance/views/auth.py`, `finance/views/settings.py`,
`stashy/settings/base.py`, `stashy/settings/test.py`, `pyproject.toml`
(coverage leaves out `finance/tests/`); `README.md`, `CLAUDE.md`,
`DEVLOG.md`, `plans/02_architecture_cleanup.md`, `plans/README.md`.
