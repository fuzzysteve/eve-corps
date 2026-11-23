# CLAUDE.md

eve-corps.online: Flask + PostgreSQL directory of recruiting EVE Online corporations. Live on this server; see README.md for features and setup.

## Commands

    .venv/bin/pytest                                   # full suite (~450 tests, ~25s)
    FLASK_APP=wsgi.py .venv/bin/flask db migrate -m "..."
    FLASK_APP=wsgi.py .venv/bin/flask db upgrade       # applies to the LIVE database in .env
    systemctl restart evecorps                         # deploy code/template changes
    journalctl -u evecorps                             # app logs

uv manages dependencies. Python 3.14 is installed in `/opt/uv-python` (pinned in `pyproject.toml`), because www-data can't read uv's default location under `/root`. Don't recreate `.venv` with another interpreter.

## Layout

- `evecorps/__init__.py`: app factory, Jinja globals and filters, CSP and security headers.
- `config.py`: everything from `.env` (gitignored; `.env.example` has placeholders only).
- `models.py`: Corporation, Alliance, Character, Advert, AdvertRevision, Report, ModerationLog. Advert has a generated `search_vector` (tsvector) column.
- `auth.py`: SSO login and callback, recruiter and moderator checks, ESI corp/alliance refresh, CSRF, NUL-byte rejection.
- `sso.py`: EVE SSO (metadata, code exchange, JWT validation). `esi.py`: ESI client.
- `views.py`: browse, search and filters; corp page; editor; history; reports; preview/import/paste endpoints.
- `moderation.py`: moderation blueprint. `notify.py`: Discord report alert.
- `formatting.py`: Markdown rendering and sanitizing, EVE-markup to Markdown, plain-text excerpts.
- `choices.py`: fixed option lists (tags, FW factions, size/tax/age buckets, commitment).
- `deploy/`: systemd unit and a copy of the live Apache vhost. Keep the copy in sync with `/etc/apache2/sites-available/eve-corps.conf`.

## Rules

**Security (don't regress these)**
- Never use `|safe` or `Markup()` on user or ESI text. Advert Markdown goes only through `render_markdown()`, which is markdown-it with HTML off and images disabled, followed by nh3 with `url_relative="deny"`.
- The CSP forbids inline scripts and styles: JavaScript goes in `evecorps/static/js/`. A new external script, style or image host needs a CSP change, and script/style sources are pinned to exact URLs.
- Every POST form needs `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- Add new user-facing output paths to `tests/test_xss.py`.

**ESI / EVE data**
- Every request goes through `esi._session()` (User-Agent from `.env`, `X-Compatibility-Date` that rolls at 11:00 UTC). Corp and alliance data are only re-fetched after ESI's `Expires`; never poll faster.
- Check route shapes against the live spec (`https://esi.evetech.net/meta/openapi.json`, needs `X-Compatibility-Date`). For example, corp tax is `tax_rates.isk` as a 0-100 percentage.
- Verify EVE IDs and names (factions etc.) against the SDE (`mcp__eve-sde__*`) and/or ESI before hardcoding them.
- Login tokens are never stored. Recruiter status means a roles check within the last 24h; moderator status means an ESI-confirmed corp membership within the last 24h.

**Database**
- SQLAlchemy 2.0 style (`select()`, `db.session.get()`, explicit commits).
- Postgres is shared with other sites on this server (max 100 connections). The test fixture disposes each app's engine; keep it that way.
- Migrations run against the live DB immediately. Prefer additive, nullable or server-defaulted columns.

**Tests**
- `TestConfig` (`tests/conftest.py`) uses `evecorps_test`, disables trusted hosts and the Discord webhook, and an autouse fixture blocks all real network access. Fake ESI/SSO/Discord with `monkeypatch`.
- Assertions on rendered HTML must allow for escaping (`'` becomes `&#39;`).

**Git**
- Before committing, scan staged files for the DB password, Flask key, EVE client secret and the webhook token. Commit messages end with the Co-Authored-By line.
- `TODO.md` is a personal list: gitignored, never committed.

## Operating notes for this server

- Apache serves `/static/` directly from `evecorps/static/`. `htdocs/` only holds `.well-known/` for acme.sh (its webroot); don't remove it.
- The old PHP site is archived in `/home/web/archive/corps-php/`.
- Snap Chromium (`/snap/bin/chromium`) can't read or write `/tmp`. For screenshots or browser checks, drive it over the DevTools protocol (`--remote-debugging-port`) and use `Page.captureScreenshot`.
- Stop temporary servers by port (`ss -ltnp`), not `pkill -f <name>`, which can match and kill the calling shell.
- Report alerts use the shared "Error Reports" webhook (`/opt/pricegetter-redis/discord-webhook.txt`). Never post to it from tests.
