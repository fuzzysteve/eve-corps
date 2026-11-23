import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    EVE_CLIENT_ID = os.environ.get("EVE_CLIENT_ID", "")
    EVE_CLIENT_SECRET = os.environ.get("EVE_CLIENT_SECRET", "")
    EVE_CALLBACK_URL = os.environ.get("EVE_CALLBACK_URL", "")
    ESI_USER_AGENT = os.environ.get("ESI_USER_AGENT", "EveCorpsOnline/0.1")

    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
