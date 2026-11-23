# eve-corps.online

An out-of-game directory of EVE Online corporations that are recruiting. Pilots browse, filter and search adverts; a corporation's CEO, directors and Personnel Managers log in with EVE SSO to post and maintain its advert.

Live at <https://eve-corps.online>. Flask, PostgreSQL, SQLAlchemy 2, Alembic.

## Features

**For pilots**
- Filter by timezone, language, activities, space, new-player friendly, SRP, alpha clones welcome, commitment level, alliance, and the skill points you have.
- Filters taken from EVE itself, which recruiters can't fake: can't be war-declared, maximum corp tax, member count, faction warfare side, corporation age. Closed corporations are hidden.
- Full-text search over advert text (Postgres, English stemming), plus corp/alliance names and tickers. Sort by relevance, recently updated, size or newest.
- Corp pages: Markdown advert, "At a glance" facts, contact details (Discord, website, in-game channel, recruiters), and vetting links to zKillboard, EveWho and DOTLAN.
- Link previews (OpenGraph) for sharing adverts on Discord.

**For recruiters**
- EVE SSO login. Plain login asks for no scopes. Managing an advert asks for `esi-characters.read_corporation_roles.v1`, which is used once to check for CEO / `Director` / `Personnel_Manager`; the token is not stored. The check lasts 24 hours.
- Markdown editor with preview. You can start from the corporation's in-game description or a pasted in-game advert (EVE markup is converted).
- "Last updated" moves whenever a recruiter edits the advert or logs in, so pilots can see the corp is active.
- Edit history with diffs, visible to the corp's recruiters and to moderators.

**Moderation**
- Members of the corporations in `MODERATOR_CORP_IDS` (default: Fuzzwork Enterprises) can hide adverts (with a reason the recruiters see), unhide them, and dismiss reports. Every action is logged.
- Logged-in pilots can report adverts. The first report on each advert is posted once to a Discord webhook.

**Security**
- Advert Markdown is rendered with raw HTML off and images disabled, then filtered by `nh3` (tag allowlist, absolute http/https/mailto links only).
- A strict Content-Security-Policy: no inline scripts, and only the exact Bootstrap files from the CDN.
- CSRF tokens on every form, trusted hosts, and a regression suite of XSS payloads in `tests/test_xss.py`.
- Rate limits per character on reports, previews and imports (stored in Postgres, so they hold across workers).

**Recruiter stats**
- Daily page-view counts per advert, shown only to that corp's recruiters and to moderators. Bots, link-preview fetchers and the corp's own recruiters aren't counted, and no IPs or cookies are stored.

## Setup

Requires Python 3.14 and PostgreSQL. Dependencies are managed with [uv](https://docs.astral.sh/uv/).

    uv sync
    cp .env.example .env        # then fill in the values
    FLASK_APP=wsgi.py .venv/bin/flask db upgrade

Register an application at <https://developers.eveonline.com>:
- callback URL `https://<your-host>/sso/callback`
- scope `esi-characters.read_corporation_roles.v1`

Put the client ID and secret in `.env`. Set `ESI_USER_AGENT` to something CCP can contact you through.

### Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `FLASK_SECRET_KEY` | Session signing key |
| `DATABASE_URL` | e.g. `postgresql+psycopg://user:pass@127.0.0.1:5432/evecorps` |
| `EVE_CLIENT_ID`, `EVE_CLIENT_SECRET`, `EVE_CALLBACK_URL` | EVE SSO application |
| `ESI_USER_AGENT` | Sent on every ESI/SSO request; include contact details |
| `MODERATOR_CORP_IDS` | Comma-separated corporation IDs whose members can moderate |
| `DISCORD_WEBHOOK_FILE` | File holding a Discord webhook URL for report alerts; unset = off |
| `TRUSTED_HOSTS` | Comma-separated hostnames the app answers for |

## Development

    FLASK_APP=wsgi.py .venv/bin/flask run --debug
    .venv/bin/pytest

The tests use a separate `evecorps_test` database, the same name as in `DATABASE_URL` with `_test` appended. They can't reach the network: ESI, SSO and Discord are all faked.

Schema changes:

    FLASK_APP=wsgi.py .venv/bin/flask db migrate -m "describe change"
    FLASK_APP=wsgi.py .venv/bin/flask db upgrade

## Deployment

- `deploy/evecorps.service`: systemd unit, gunicorn on `127.0.0.1:8002`, running as `www-data`.
- `deploy/eve-corps.apache.conf`: Apache vhost. It terminates TLS, serves `/static/` directly, serves `/.well-known/` from `htdocs/` for ACME, and proxies everything else.
- `deploy/evecorps-maintenance.{service,timer}`: every 6 hours, `flask refresh-corps` re-fetches ESI facts for corps with adverts (only after ESI's `Expires`; stops on a 420/429), then `flask prune` clears old rate-limit counters and year-old view counts.
- `deploy/evecorps-backup.{service,timer}` and `deploy/backup-db.sh`: a nightly `pg_dump` at 03:30, checked with `pg_restore --list` and kept for 14 days in `/home/web/backups/evecorps-db/`. Restore with `pg_restore --clean --if-exists -d evecorps <file>`. The dumps are on the same disk as the database, so they don't count as an off-site backup.

Both timers alert through `discord-notify@` if a run fails.

## License

MIT. See [LICENSE](LICENSE).

EVE Online and all associated logos and designs are the intellectual property of CCP hf. This project is not affiliated with or endorsed by CCP.
