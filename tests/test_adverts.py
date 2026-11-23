from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from evecorps import esi, sso
from evecorps.auth import ROLES_SCOPE
from evecorps.extensions import db
from evecorps.models import Advert, Character, Corporation

CHAR = 90000001
CORP = 98000001
FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)


@pytest.fixture
def eve(monkeypatch):
    """Fake SSO/ESI. Tweak state['roles'], state['scopes'], state['ceo_id'] per test."""
    state = {"roles": ["Personnel_Manager"], "scopes": [ROLES_SCOPE], "ceo_id": 1, "role_calls": 0}
    monkeypatch.setattr(sso, "metadata", lambda: {"authorization_endpoint": "https://sso.test/authorize"})
    monkeypatch.setattr(sso, "exchange_code", lambda code: {"access_token": "tok"})
    monkeypatch.setattr(sso, "validate_token", lambda tok: {
        "sub": f"CHARACTER:EVE:{CHAR}", "name": "Recruiter Pilot", "scp": state["scopes"],
    })
    monkeypatch.setattr(esi, "get_affiliation", lambda cid: {"character_id": cid, "corporation_id": CORP})
    monkeypatch.setattr(esi, "get_corporation", lambda cid: (
        {"name": "Test Corp", "ticker": "TEST", "member_count": 5, "ceo_id": state["ceo_id"]}, FUTURE))

    def get_roles(cid, token):
        state["role_calls"] += 1
        assert token == "tok"
        return {"roles": state["roles"]}

    monkeypatch.setattr(esi, "get_roles", get_roles)
    return state


def login(client, recruiter=True):
    resp = client.get("/sso/login" + ("?purpose=recruiter" if recruiter else ""))
    assert resp.status_code == 302
    with client.session_transaction() as s:
        state = s["sso_state"]
    client.get(f"/sso/callback?code=abc&state={state}")
    client.get("/")  # mint a csrf token
    with client.session_transaction() as s:
        return s.get("csrf")


def form(csrf, **overrides):
    data = {
        "csrf_token": csrf, "title": "Join us", "body": "Line one\nLine two",
        "timezones": ["eu", "us"], "languages": ["en"], "activities": ["pvp", "bogus"],
        "space": ["nullsec"], "srp": "1", "min_sp": "5m", "active": "1",
    }
    data.update(overrides)
    return data


def test_recruiter_login_requests_roles_scope(client, eve):
    resp = client.get("/sso/login?purpose=recruiter")
    assert "esi-characters.read_corporation_roles.v1" in resp.headers["Location"].replace("%2E", ".")
    resp = client.get("/sso/login")
    assert "scope=&" in resp.headers["Location"]


@pytest.mark.parametrize("roles", [["Personnel_Manager"], ["Director"], ["Accountant", "Director"]])
def test_directors_and_recruiters_can_post(client, eve, roles):
    eve["roles"] = roles
    csrf = login(client)
    resp = client.post("/advert", data=form(csrf))
    assert resp.status_code == 302

    advert = db.session.scalar(select(Advert))
    assert advert.corporation_id == CORP
    assert advert.activities == ["pvp"]  # unknown values dropped
    assert advert.min_sp == 5_000_000 and advert.srp and not advert.newbro_friendly
    assert advert.updated_by_id == CHAR


def test_ceo_can_post_without_roles(client, eve):
    eve["roles"], eve["ceo_id"] = [], CHAR
    csrf = login(client)
    assert client.post("/advert", data=form(csrf)).status_code == 302
    assert eve["role_calls"] == 0


@pytest.mark.parametrize("roles", [[], ["Accountant"], ["Station_Manager"]])
def test_other_members_cannot_post(client, eve, roles):
    eve["roles"] = roles
    csrf = login(client)
    assert client.get("/advert").status_code == 403
    assert client.post("/advert", data=form(csrf)).status_code == 403
    assert db.session.scalar(select(Advert)) is None


def test_plain_login_is_not_recruiter(client, eve):
    csrf = login(client, recruiter=False)
    assert eve["role_calls"] == 0
    assert client.post("/advert", data=form(csrf)).status_code == 403


def test_declined_scope_is_not_recruiter(client, eve):
    eve["scopes"] = []
    csrf = login(client)
    assert eve["role_calls"] == 0
    assert client.post("/advert", data=form(csrf)).status_code == 403


def test_recruiter_check_expires(client, eve):
    csrf = login(client)
    char = db.session.get(Character, CHAR)
    char.recruiter_verified_at -= timedelta(hours=25)
    db.session.commit()
    assert client.post("/advert", data=form(csrf)).status_code == 403


def test_recruiter_status_lost_on_corp_change(client, eve):
    csrf = login(client)
    db.session.add(Corporation(corporation_id=98000002, name="Other", ticker="OTH"))
    db.session.get(Character, CHAR).corporation_id = 98000002
    db.session.commit()
    assert client.post("/advert", data=form(csrf)).status_code == 403


def test_csrf_required(client, eve):
    login(client)
    assert client.post("/advert", data=form("wrong")).status_code == 400
    assert client.post("/logout").status_code == 400


def test_validation_errors_keep_input(client, eve):
    csrf = login(client)
    resp = client.post("/advert", data=form(csrf, title="", min_sp="lots"))
    assert resp.status_code == 400
    assert b"Title is required" in resp.data and b"Minimum skill points" in resp.data
    assert b"Line one" in resp.data


def test_edit_updates_single_advert(client, eve):
    csrf = login(client)
    client.post("/advert", data=form(csrf))
    client.post("/advert", data=form(csrf, title="New title"))
    adverts = db.session.scalars(select(Advert)).all()
    assert len(adverts) == 1 and adverts[0].title == "New title"


def test_body_is_escaped(client, eve):
    csrf = login(client)
    client.post("/advert", data=form(csrf, body="<script>alert(1)</script>"))
    resp = client.get(f"/corps/{CORP}")
    assert b"<script>alert(1)" not in resp.data and b"&lt;script&gt;" in resp.data


def make_advert(corp_id, name, alliance_id=None, **kw):
    db.session.add(Corporation(corporation_id=corp_id, name=name, ticker=name[:5].upper(), alliance_id=alliance_id))
    db.session.add(Advert(corporation_id=corp_id, title=name, body="x", **kw))


@pytest.fixture
def listings(app):
    make_advert(1, "EuPvp", timezones=["eu"], activities=["pvp"], space=["nullsec"], languages=["en"],
                srp=True, min_sp=10_000_000, alliance_id=99)
    make_advert(2, "UsIndy", timezones=["us"], activities=["industry", "mining"], space=["highsec"],
                languages=["en", "de"], newbro_friendly=True)
    make_advert(3, "Hidden", timezones=["eu"], activities=["pvp"], active=False)
    db.session.commit()


def titles(client, query=""):
    html = client.get("/corps" + query).get_data(as_text=True)
    return {t for t in ("EuPvp", "UsIndy", "Hidden") if f">{t}</a>" in html}


@pytest.mark.parametrize("query,expected", [
    ("", {"EuPvp", "UsIndy"}),
    ("?timezones=eu", {"EuPvp"}),
    ("?timezones=eu&timezones=us", {"EuPvp", "UsIndy"}),     # any within a field
    ("?timezones=us&activities=pvp", set()),                  # all across fields
    ("?languages=de", {"UsIndy"}),
    ("?space=nullsec&space=wormhole", {"EuPvp"}),
    ("?newbro=1", {"UsIndy"}),
    ("?srp=1", {"EuPvp"}),
    ("?alliance=yes", {"EuPvp"}),
    ("?alliance=no", {"UsIndy"}),
    ("?my_sp=5m", {"UsIndy"}),
    ("?my_sp=10m", {"EuPvp", "UsIndy"}),
    ("?timezones=bogus", {"EuPvp", "UsIndy"}),               # unknown values ignored
])
def test_browse_filters(client, listings, query, expected):
    assert titles(client, query) == expected


def test_hidden_advert_404_for_public(client, listings):
    assert client.get("/corps/3").status_code == 404
    assert client.get("/corps/1").status_code == 200
