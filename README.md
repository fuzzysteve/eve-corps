# eve-corps.online

Flask + PostgreSQL app for finding and advertising EVE Online corporations.

## Setup

    uv sync
    cp .env.example .env        # fill in the values
    FLASK_APP=wsgi.py .venv/bin/flask db upgrade

Register an application at https://developers.eveonline.com with callback URL
`https://eve-corps.online/sso/callback` (no scopes needed for login) and put
the client ID/secret in `.env`.

## Development

    FLASK_APP=wsgi.py .venv/bin/flask run --debug
    .venv/bin/pytest            # uses the evecorps_test database

## Schema changes

    FLASK_APP=wsgi.py .venv/bin/flask db migrate -m "describe change"
    FLASK_APP=wsgi.py .venv/bin/flask db upgrade

## Deploy

`deploy/evecorps.service` (systemd, gunicorn on 127.0.0.1:8002) and
`deploy/apache-proxy.conf` (vhost proxy lines).
