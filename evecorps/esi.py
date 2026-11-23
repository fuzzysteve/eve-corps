"""Minimal ESI client: User-Agent, X-Compatibility-Date and cache expiry on every call."""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from flask import current_app

ESI_BASE = "https://esi.evetech.net"
TIMEOUT = 10


def compatibility_date() -> str:
    # ESI's compatibility day rolls over at 11:00 UTC.
    return (datetime.now(timezone.utc) - timedelta(hours=11)).date().isoformat()


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": current_app.config["ESI_USER_AGENT"],
        "X-Compatibility-Date": compatibility_date(),
        "Accept": "application/json",
    })
    return s


def _expires(resp: requests.Response) -> datetime | None:
    value = resp.headers.get("Expires")
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def get_affiliation(character_id: int) -> dict:
    """Public: current corporation/alliance for a character."""
    resp = _session().post(f"{ESI_BASE}/characters/affiliation", json=[character_id], timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()[0]


def get_corporation(corporation_id: int) -> tuple[dict, datetime | None]:
    """Public corp info plus the time ESI says it may next be refreshed."""
    resp = _session().get(f"{ESI_BASE}/corporations/{corporation_id}", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json(), _expires(resp)


RECRUITER_ROLES = {"Director", "Personnel_Manager"}


def get_roles(character_id: int, access_token: str) -> dict:
    """Authed: the character's corp roles (needs esi-characters.read_corporation_roles.v1)."""
    s = _session()
    s.headers["Authorization"] = f"Bearer {access_token}"
    resp = s.get(f"{ESI_BASE}/characters/{character_id}/roles", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()
