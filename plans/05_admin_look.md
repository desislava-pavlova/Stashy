# The Admin's Look

Plan 05, started 23/10/2026. **Status:** planned, nothing built. A phase
starts when you name it. It comes after plan 04 (the fixes and changes you
see) and before plan 06 (the .exe). It came in as plan 04's R10 on
23/10/2026 and moved here the same day (your call): it's a feature of its
own, not a small fix.

## Where it stands today

- The admin (`/admin/`, "Admin & Audit Log" in the sidebar, opens in a new
  tab) still has Django's own look: a blue-grey top bar, a yellow title,
  Django's buttons and fonts.
- Only the wording is Stashy's: `stashy/urls.py` sets "Stashy admin".
- Its own login page is never shown: `/admin/login/` goes to the PIN login.
- **Light and dark mode don't talk to each other.** The admin has its own
  three-way switch (auto, light, dark), saved under its own name (`theme`).
  The website saves its choice as `stashy-theme`.

## Goal

Connected to the website, but recognisably the admin. Simple, but not bare.

- **Shared with the website:**
  - the fonts: Nunito for headings, Manrope for text;
  - the colours: cream background, white panels, acorn-orange buttons and
    links, success and error messages in Stashy's green and red;
  - the squirrel in the top bar.
  - Both read their colours from one place, so a colour changed later
    changes both.
- **Different, so you know where you are:**
  - Django's layout stays: the top bar, the list of tables on the left, the
    tables and forms.
  - The top bar is dark bark-brown with a small gold **"Admin"** tag. The
    website has its green sidebar instead.
  - "View site" becomes "Back to Stashy".
- **Light and dark mode, one switch:**
  - The admin follows the website's choice. Its button works like the
    website's: light ↔ dark, and until you pick one, both follow the
    computer.
  - Switching in either place switches both, even in the other open tab.
  - Django's own three-way switch goes.

## Decisions

- **Its own plan, not a plan 04 row** (your call, 23/10/2026). The .exe
  plan moved from 05 to 06 to make room, as originally intended: packaging
  comes once most things are done.
- **Restyle Django's admin, don't replace it** (agreed 23/10/2026, the
  middle of three options). The admin takes nearly all its colours from
  CSS variables, so the work is pointing them at Stashy's colours, and its
  layout keeps working through Django updates. Rejected: rewriting its
  ~40 templates to look like the website (a lot of work, and it breaks on
  upgrades), or a ready-made theme from PyPI (some need a database table in
  every stash, or a change to every admin class).
- **The bark-brown bar and the gold "Admin" tag** (Claude's call: it stands
  apart from the green sidebar while staying in Stashy's colours). You see
  it before it's final and can change it.
- **A two-way switch, not three** (Claude's call: two connected switches
  must work the same way, and the website's has two states).

## Phase 1 — Shared colours and fonts (nothing visible)

- [ ] **1.1** Move Stashy's colour tokens (light and dark) and its
  `@font-face` rules out of `stashy.css` into shared files, used by the
  website and later by the admin. The website must look exactly as before.
- [ ] **1.2** Checks: the full suite, the no-internet test, and the real
  `collectstatic` into a scratch `STATIC_ROOT` (it rewrites the paths inside
  CSS files, so a wrong path fails there first).

## Phase 2 — The admin's look and one theme switch

- [ ] **2.1** Make Stashy's admin templates win. Today
  `django.contrib.admin` comes before `finance` in `INSTALLED_APPS`, so
  Django finds its own `admin/base_site.html` first, and a copy in
  `finance/templates/admin/` would be silently ignored. Either `finance`
  moves above the admin app, or `TEMPLATES["DIRS"]` gets a templates folder
  (decided when building, with a test that Stashy's copy is the one used).
- [ ] **2.2** `admin/base_site.html`: the squirrel, "Stashy" and the gold
  "Admin" tag in the `branding` block, and "Back to Stashy". A new
  `admin.css` points Django's variables (`--primary`, `--secondary`,
  `--header-bg`, `--button-bg`, `--link-fg`, `--message-*`,
  `--font-family-primary`, …) at Stashy's tokens, for light and dark. It
  replaces the `dark-mode-vars` block (Django's `dark_mode.css` and
  `theme.js`).
- [ ] **2.3** One theme switch. The admin sets `data-bs-theme` from
  `stashy-theme` before the page paints, the same way `head_assets.html`
  does. `admin/color_theme_toggle.html` becomes a two-way button. Both
  `stashy.js` and the admin listen for the browser's `storage` event, so a
  switch in one tab changes the other.
- [ ] **2.4** Checks: the admin pages (home, a list, an edit form, a
  delete confirmation, History) render with `admin.css` and no leaked
  template tags, in both modes. The no-internet test and the real
  `collectstatic` pass. Then you look at it in both modes, since Claude
  can't see the screen.
- [ ] **2.5** AD1: dropdowns for the long list filters (see the table
  below). Built after 2.2, which loads its script and styles it, and
  before 2.4's checks, so they cover it too.

## Later admin changes

Anything else about the admin can be added here ("add to 05: …"). Claude
files it and doesn't start it, the same as plan 04's rows. The numbers start
with AD, because plan 01 already has A-numbers (A1 is an admin rule there).

| # | Added | What | Status |
|---|-------|------|--------|
| AD1 | 13/11/2026 | **Dropdowns for the admin's long list filters** (`finance/admin.py`, `audit/admin.py`). You asked for it while reading `finance/admin.py` (13/11/2026). In another project you use the package django-admin-list-filter-dropdown, "but you can use whatever you see fit". **What Claude found in that package** (downloaded and read, not installed): its last release is 1.0.3, from 14/10/2019. It's five one-line classes, each a Django list filter (`SimpleListFilter`, `AllValuesFieldListFilter`, `ChoicesFieldListFilter`, `RelatedFieldListFilter`, `RelatedOnlyFieldListFilter`) that only sets `template`, plus one template: a `<select>` when a filter has more than 4 entries ("All" counts as one), the usual links otherwise. That template is older than Django 5's filter markup. Django 6.1's own `admin/filter.html` puts each filter in `<details data-filter-title=… open>` with a `<summary>By …</summary>`, so you can fold a filter away (and Django's `filters.js` remembers it); the package uses a plain `<h3>`, so its filters would look and behave differently from Django's own next to them. It also writes an inline `<script>` and an `onchange="…"` handler into the page for every filter, which a Content-Security-Policy with `script-src 'self'` blocks (plan 03's S9, question Q2, still open), and an inline `style="width: 95%;margin-left: 2%;"` that `admin.css` would have to fight. **Claude's call: the same idea without the package.** No new dependency, Django 6.1's markup, and it's styled with the rest of the admin in light and dark. (1) One template, `admin/dropdown_filter.html`: Django 6.1's own `filter.html` (the same `<details>`/`<summary>`), with a `<select>` in place of the links when there are more than 4 entries. That's the package's threshold, so short lists stay links. It goes with 2.1's admin templates; its name is new, not one of Django's, so it's found whichever way 2.1 goes. (2) A small `finance/static/finance/js/admin-filters.js` goes to the chosen option when the select changes, so there's no inline script. `admin/base_site.html` (2.2) loads it next to `admin.css`. (3) Three filter classes subclass `RelatedFieldListFilter`, `ChoicesFieldListFilter` and `AllValuesFieldListFilter` and set `template`: the whole package, about 10 lines, in our own code. **Where (your call, 13/11/2026):** one rule for every filter. 4 or more real choices, not counting "All", shows a dropdown, and fewer stay links: that's the threshold above, and the one you asked for ("4-5 choices and above"). You named: Accounts' type (11 types) and currency; Exchange rates' from and to currency (each lists all 31 currencies Stashy knows, used or not); Transactions' category (a new stash that picks the Extended starter list already has 149 categories, 26 main and 123 sub, plus one transfer category per account); and account wherever it's a filter (Transactions, Reconciliations), which becomes a dropdown once a stash has 4 or more accounts. By the same rule, Reminders' frequency (5) and the audit log's table and column become dropdowns too. Categories stays as it is, as you wanted (its type has 3 choices, auto-generated is Yes/No), and so do the other short ones (reconciled, reminder type, source, action) and the audit log's date filter (Django's own "Today", "Past 7 days", …). How (Claude's call): the dropdown classes go on every list filter except the date one, and the template decides by the count, so a list that grows to 4 switches by itself. **Tests** (`test_admin.py`): a filter with more than 4 entries renders a `<select>` whose options carry the filter's query strings; one with 4 or fewer renders links; the template has no `<script>` and no `on…=` handler; the no-internet test also loads an admin list (today it loads only the website's pages, so it wouldn't notice). Proposed phase: 2, item 2.5, after 2.2, so the dropdown is styled with `admin.css` from the start. | open (decided 13/11/2026) |

<!-- Next number: AD2 -->
