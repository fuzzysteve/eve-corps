import os

import pytest

from evecorps import create_app
from evecorps.config import Config
from evecorps.extensions import db


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    SESSION_COOKIE_SECURE = False
    EVE_CLIENT_ID = "test-client"
    EVE_CALLBACK_URL = "http://localhost/sso/callback"
    TRUSTED_HOSTS = None  # test client uses "localhost"
    # Never use the real admin-channel webhook from .env; tests that need one set their own.
    DISCORD_WEBHOOK_FILE = ""
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL", (Config.SQLALCHEMY_DATABASE_URI or "").rsplit("/", 1)[0] + "/evecorps_test"
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a test tries to reach ESI, SSO or Discord for real."""
    import requests

    def blocked(self, request, **kwargs):
        from urllib.parse import urlsplit
        host = urlsplit(request.url).netloc  # host only: full URLs can carry secrets (webhook tokens)
        raise AssertionError(f"test tried to reach the network: {request.method} {host}")

    monkeypatch.setattr(requests.sessions.Session, "send", blocked)


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        # Close this app's pool: one leaked pool per test exhausted the shared
        # Postgres server's connection slots on a full run.
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()
