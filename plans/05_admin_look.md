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

## Later admin changes

Anything else about the admin can be added here ("add to 05: …"). Claude
files it and doesn't start it, the same as plan 04's rows. The numbers start
with AD, because plan 01 already has A-numbers (A1 is an admin rule there).

| # | Added | What | Status |
|---|-------|------|--------|

<!-- Next number: AD1 -->
