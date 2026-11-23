from datetime import datetime, timedelta, timezone

import pytest

from evecorps import auth, esi, sso
from evecorps.extensions import db
from evecorps.models import Character, Corporation

CLAIMS = {"sub": "CHARACTER:EVE:90000001", "name": "Test Pilot", "owner": "hash1"}
FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)


@pytest.fixture
def fake_eve(monkeypatch):
    calls = {"corp": 0}
    monkeypatch.setattr(sso, "metadata", lambda: {"authorization_endpoint": "https://sso.test/authorize"})
    monkeypatch.setattr(sso, "exchange_code", lambda code: {"access_token": "tok"})
    monkeypatch.setattr(sso, "validate_token", lambda tok: CLAIMS)
    monkeypatch.setattr(esi, "get_affiliation", lambda cid: {"character_id": cid, "corporation_id": 98000001})

    def get_corp(corp_id):
        calls["corp"] += 1
        return {"name": "Test Corp", "ticker": "TEST", "member_count": 5, "ceo_id": 1}, FUTURE

    monkeypatch.setattr(esi, "get_corporation", get_corp)
    return calls


def start_login(client, next_path=None):
    url = "/sso/login" + (f"?next={next_path}" if next_path else "")
    resp = client.get(url)
    assert resp.status_code == 302
    with client.session_transaction() as s:
        return s["sso_state"]


def test_login_creates_character_and_corp(client, fake_eve):
    state = start_login(client)
    resp = client.get(f"/sso/callback?code=abc&state={state}")
    assert resp.status_code == 302

    char = db.session.get(Character, 90000001)
    assert char.name == "Test Pilot" and char.owner_hash == "hash1"
    assert char.corporation.ticker == "TEST"
    with client.session_transaction() as s:
        assert s["character_id"] == 90000001
        assert "sso_state" not in s
    assert b"Test Pilot [TEST]" in client.get("/").data


def test_bad_state_rejected(client, fake_eve):
    start_login(client)
    resp = client.get("/sso/callback?code=abc&state=wrong")
    assert resp.status_code == 302
    assert db.session.get(Character, 90000001) is None
    with client.session_transaction() as s:
        assert "character_id" not in s
    assert b"expired" in client.get("/").data


def test_callback_without_login_started(client, fake_eve):
    resp = client.get("/sso/callback?code=abc&state=anything")
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    assert db.session.get(Character, 90000001) is None


@pytest.mark.parametrize("query", [
    "error=access_denied&state={state}",
    "state={state}",
])
def test_cancel_at_eve_returns_to_page(client, fake_eve, query):
    state = start_login(client, "/corps")
    resp = client.get("/sso/callback?" + query.format(state=state))
    assert resp.status_code == 302 and resp.headers["Location"] == "/corps"
    assert db.session.get(Character, 90000001) is None
    with client.session_transaction() as s:
        assert "character_id" not in s and "sso_state" not in s
    assert b"Login cancelled" in client.get("/corps").data


def test_state_is_single_use(client, fake_eve):
    state = start_login(client)
    client.get(f"/sso/callback?code=abc&state={state}")
    with client.session_transaction() as s:
        csrf = s.get("csrf") or "x"
        s["csrf"] = csrf
    client.post("/logout", data={"csrf_token": csrf})
    client.get(f"/sso/callback?code=abc&state={state}")
    with client.session_transaction() as s:
        assert "character_id" not in s


@pytest.mark.parametrize("nxt,expected", [
    ("/foo", "/foo"), ("//evil.example", "/"), ("https://evil.example", "/"), ("/\\evil.example", "/"),
])
def test_next_redirect_is_local_only(client, fake_eve, nxt, expected):
    state = start_login(client, nxt)
    resp = client.get(f"/sso/callback?code=abc&state={state}")
    assert resp.headers["Location"] == expected


def test_corp_not_refetched_before_expiry(app, fake_eve):
    auth.refresh_corporation(98000001)
    db.session.commit()
    auth.refresh_corporation(98000001)
    assert fake_eve["corp"] == 1

    db.session.get(Corporation, 98000001).esi_expires = datetime.now(timezone.utc) - timedelta(seconds=1)
    auth.refresh_corporation(98000001)
    assert fake_eve["corp"] == 2


def test_rejects_non_character_sub():
    with pytest.raises(ValueError):
        sso.character_id_from_claims({"sub": "CORPORATION:EVE:1"})
    assert sso.character_id_from_claims(CLAIMS) == 90000001

