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
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL", (Config.SQLALCHEMY_DATABASE_URI or "").rsplit("/", 1)[0] + "/evecorps_test"
    )


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
