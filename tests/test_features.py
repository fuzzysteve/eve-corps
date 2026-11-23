"""Copyright notice, last-updated, moderation, reports, contacts, link previews, vetting links."""
from datetime import datetime, timedelta, timezone

import pytest
import requests
from sqlalchemy import select

from evecorps import esi
from evecorps.extensions import db
from evecorps.models import Advert, Character, Corporation, ModerationLog, Report
from test_adverts import CHAR, CORP, eve, form, login  # noqa: F401  (eve is a fixture)

FZZW = 98072480
MOD = 90926985


def as_character(client, char_id, corp_id, corp_name="Corp", verified=True):
    """Log in directly as a character (bypassing SSO) and return a CSRF token."""
    if db.session.get(Corporation, corp_id) is None:
        db.session.add(Corporation(corporation_id=corp_id, name=corp_name, ticker=corp_name[:4].upper()))
    char = db.session.get(Character, char_id) or Character(character_id=char_id, name=f"Pilot {char_id}")
    char.corporation_id = corp_id
    char.corp_verified_at = datetime.now(timezone.utc) if verified else None
    db.session.add(char)
    db.session.commit()
    with client.session_transaction() as s:
        s["character_id"] = char_id
        s["csrf"] = "tok"
    return "tok"


@pytest.fixture
def advert(app):
    db.session.add(Corporation(corporation_id=CORP, name="Test Corp", ticker="TEST", alliance_id=99000001))
    ad = Advert(corporation_id=CORP, title="Join us", body="**We fly** stuff & things\n\nMore text",
                timezones=["eu"], activities=["pvp"], srp=True,
                last_confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    db.session.add(ad)
    db.session.commit()
    return ad


# --- CCP notice ----------------------------------------------------------

def test_ccp_notice_in_footer(client):
    html = client.get("/").get_data(as_text=True)
    assert "registered trademarks of CCP hf." in html
    assert "has granted permission to eve-corps.online" in html
    assert "CCP Copyright Note" not in html


# --- Last updated ---------------------------------------------------------

def test_recruiter_login_bumps_last_updated(client, eve, advert):
    login(client)
    assert db.session.get(Advert, advert.id).last_confirmed_at.year == datetime.now(timezone.utc).year
    assert db.session.get(Advert, advert.id).last_confirmed_at > datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_plain_login_does_not_bump(client, eve, advert):
    login(client, recruiter=False)
    assert db.session.get(Advert, advert.id).last_confirmed_at == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_non_recruiter_login_does_not_bump(client, eve, advert):
    eve["roles"] = []
    login(client)
    assert db.session.get(Advert, advert.id).last_confirmed_at == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_save_bumps_and_page_shows_last_updated(client, eve, advert):
    csrf = login(client)
    db.session.get(Advert, advert.id).last_confirmed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.session.commit()
    client.post("/advert", data=form(csrf))
    stamp = db.session.get(Advert, advert.id).last_confirmed_at
    assert stamp > datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert f"Last updated {stamp:%Y-%m-%d}" in client.get(f"/corps/{CORP}").get_data(as_text=True)


def test_browse_orders_by_last_updated(client, advert):
    db.session.add(Corporation(corporation_id=2, name="Newer", ticker="NEW"))
    db.session.add(Advert(corporation_id=2, title="Fresh", body="x",
                          last_confirmed_at=datetime(2026, 6, 1, tzinfo=timezone.utc)))
    db.session.commit()
    html = client.get("/corps").get_data(as_text=True)
    assert html.index(">Fresh</a>") < html.index(">Join us</a>")
    assert "updated 2026-01-01" in html


# --- Moderation -----------------------------------------------------------

def test_fuzzwork_member_is_moderator(client, advert):
    as_character(client, MOD, FZZW, "Fuzzwork Enterprises")
    assert client.get("/moderation/").status_code == 200
    assert b"Moderation" in client.get(f"/corps/{CORP}").data


@pytest.mark.parametrize("corp_id,verified", [(CORP, True), (FZZW, False)])
def test_others_are_not_moderators(client, advert, corp_id, verified):
    csrf = as_character(client, MOD, corp_id, verified=verified)
    assert client.get("/moderation/").status_code == 403
    assert client.post(f"/moderation/{advert.id}/hide", data={"csrf_token": csrf, "reason": "x"}).status_code == 403
    assert not db.session.get(Advert, advert.id).moderation_hidden


def test_moderator_membership_check_expires(client, advert):
    as_character(client, MOD, FZZW)
    db.session.get(Character, MOD).corp_verified_at -= timedelta(hours=25)
    db.session.commit()
    assert client.get("/moderation/").status_code == 403


def test_moderator_corps_configurable(app, client, advert):
    app.config["MODERATOR_CORP_IDS"] = frozenset({CORP})
    as_character(client, MOD, CORP)
    assert client.get("/moderation/").status_code == 200


def test_hide_and_unhide(client, advert):
    csrf = as_character(client, MOD, FZZW)
    assert client.post(f"/moderation/{advert.id}/hide", data={"csrf_token": csrf, "reason": ""}).status_code == 302
    assert not db.session.get(Advert, advert.id).moderation_hidden  # reason required

    client.post(f"/moderation/{advert.id}/hide", data={"csrf_token": csrf, "reason": "Scam"})
    ad = db.session.get(Advert, advert.id)
    assert ad.moderation_hidden and ad.moderation_reason == "Scam"

    with client.session_transaction() as s:
        s.clear()  # anonymous reader
    assert client.get(f"/corps/{CORP}").status_code == 404
    assert b"Join us" not in client.get("/corps").data

    csrf = as_character(client, MOD, FZZW)
    client.post(f"/moderation/{advert.id}/unhide", data={"csrf_token": csrf})
    assert not db.session.get(Advert, advert.id).moderation_hidden
    actions = [e.action for e in db.session.scalars(select(ModerationLog).order_by(ModerationLog.created_at))]
    assert actions == ["hide", "unhide"]


def test_recruiter_cannot_override_moderator_hide(client, eve, advert):
    advert.moderation_hidden, advert.moderation_reason = True, "Scam"
    db.session.commit()
    csrf = login(client)
    page = client.get("/advert").get_data(as_text=True)
    assert "Hidden by moderators" in page and "Scam" in page
    client.post("/advert", data=form(csrf, active="1"))
    ad = db.session.get(Advert, advert.id)
    assert ad.active and ad.moderation_hidden
    assert client.get(f"/corps/{CORP}").status_code == 200  # recruiter still sees it
    with client.session_transaction() as s:
        s.clear()
    assert client.get(f"/corps/{CORP}").status_code == 404


def test_report_flow(client, advert):
    # Anonymous: must log in.
    with client.session_transaction() as s:
        s["csrf"] = "tok"
    client.post(f"/corps/{CORP}/report", data={"csrf_token": "tok", "reason": "spam"})
    assert db.session.scalar(select(Report)) is None

    csrf = as_character(client, 91000001, 98999999, "Reader Corp")
    assert b"Report this advert" in client.get(f"/corps/{CORP}").data
    client.post(f"/corps/{CORP}/report", data={"csrf_token": csrf, "reason": "Scam corp"})
    client.post(f"/corps/{CORP}/report", data={"csrf_token": csrf, "reason": "again"})
    reports = db.session.scalars(select(Report)).all()
    assert len(reports) == 1 and reports[0].reason == "Scam corp"
    assert b"You've reported this advert" in client.get(f"/corps/{CORP}").data

    csrf = as_character(client, MOD, FZZW)
    queue = client.get("/moderation/").get_data(as_text=True)
    assert "Scam corp" in queue
    assert 'badge text-bg-warning">1<' in queue  # navbar count

    client.post(f"/moderation/{advert.id}/hide", data={"csrf_token": csrf, "reason": "Confirmed scam", "next": "queue"})
    assert db.session.scalars(select(Report)).one().resolved_by_id == MOD
    assert "No open reports" in client.get("/moderation/").get_data(as_text=True)


def test_dismiss_reports(client, advert):
    db.session.add(Character(character_id=91000001, name="Reader"))
    db.session.add(Report(advert_id=advert.id, reporter_id=91000001, reason="meh"))
    db.session.commit()
    csrf = as_character(client, MOD, FZZW)
    client.post(f"/moderation/{advert.id}/dismiss", data={"csrf_token": csrf})
    assert db.session.scalars(select(Report)).one().resolved_at is not None
    assert not db.session.get(Advert, advert.id).moderation_hidden


def test_report_reason_escaped_in_queue(client, advert):
    db.session.add(Character(character_id=91000001, name="Reader"))
    db.session.add(Report(advert_id=advert.id, reporter_id=91000001, reason="<script>alert(1)</script>"))
    db.session.commit()
    as_character(client, MOD, FZZW)
    html = client.get("/moderation/").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


# --- Contact fields -------------------------------------------------------

@pytest.fixture
def names(monkeypatch):
    calls = []
    known = {"steve ronuken": {"id": MOD, "name": "Steve Ronuken"},
             "test pilot": {"id": 90000002, "name": "Test Pilot"}}

    def resolve(ns):
        calls.append(list(ns))
        return {n.lower(): known[n.lower()] for n in ns if n.lower() in known}

    monkeypatch.setattr(esi, "resolve_character_names", resolve)
    return calls


def test_contact_fields_saved_and_shown(client, eve, names):
    csrf = login(client)
    resp = client.post("/advert", data=form(csrf, discord_url="discord.gg/abc123", website_url="corp.example/join",
                                            ingame_channel="TEST Recruits", contacts="steve ronuken\nTest Pilot"))
    assert resp.status_code == 302
    ad = db.session.scalar(select(Advert))
    assert ad.discord_url == "https://discord.gg/abc123"
    assert ad.website_url == "https://corp.example/join"
    assert ad.contacts == [{"id": MOD, "name": "Steve Ronuken"}, {"id": 90000002, "name": "Test Pilot"}]
    html = client.get(f"/corps/{CORP}").get_data(as_text=True)
    assert 'href="https://discord.gg/abc123"' in html and "TEST Recruits" in html
    assert f"https://evewho.com/character/{MOD}" in html and f"characters/{MOD}/portrait" in html

    # Unchanged names: no second ESI lookup.
    client.post("/advert", data=form(csrf, contacts="Steve Ronuken\nTest Pilot"))
    assert len(names) == 1


@pytest.mark.parametrize("field,value,message", [
    ("discord_url", "https://evil.example/abc", "Discord must be an invite link"),
    ("discord_url", "javascript:alert(1)", "Discord must be an invite link"),
    ("website_url", "javascript:alert(1)", "Website must be a web address"),
    ("website_url", "ftp://corp.example", "Website must be a web address"),
    ("website_url", "localhost", "Website must be a web address"),
    ("ingame_channel", "x" * 61, "at most 60 characters"),
    ("contacts", "a\nb\nc\nd", "at most 3 contact"),
    ("contacts", "Steve Ronuken\nNobody Atall", "No EVE character found named: Nobody Atall"),
])
def test_contact_validation(client, eve, names, field, value, message):
    csrf = login(client)
    resp = client.post("/advert", data=form(csrf, **{field: value}))
    assert resp.status_code == 400 and message in resp.get_data(as_text=True)
    assert db.session.scalar(select(Advert)) is None


def test_contact_lookup_esi_failure(client, eve, monkeypatch):
    def boom(ns):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(esi, "resolve_character_names", boom)
    csrf = login(client)
    resp = client.post("/advert", data=form(csrf, contacts="Steve Ronuken"))
    assert resp.status_code == 400 and b"check the contact names with EVE" in resp.data


def test_no_contact_card_when_empty(client, advert):
    assert b"Get in touch" not in client.get(f"/corps/{CORP}").data


# --- Link previews --------------------------------------------------------

def test_corp_page_open_graph(client, advert):
    html = client.get(f"/corps/{CORP}").get_data(as_text=True)
    assert '<meta property="og:title" content="Test Corp [TEST] is recruiting: Join us">' in html
    assert f'content="https://images.evetech.net/corporations/{CORP}/logo?size=256"' in html
    assert f'<meta property="og:url" content="http://localhost/corps/{CORP}">' in html
    assert 'content="EUTZ · PvP · SRP — We fly stuff &amp; things More text"' in html
    assert "**" not in html.split('property="og:description"')[1].split(">")[0]


def test_open_graph_escapes_title(client, advert):
    advert.title = '"><script>alert(1)</script>'
    db.session.commit()
    html = client.get(f"/corps/{CORP}").get_data(as_text=True)
    assert "<script>alert(1)" not in html


def test_default_open_graph(client):
    html = client.get("/").get_data(as_text=True)
    assert 'property="og:title"' in html and "apple-touch-icon.png" in html


# --- Vetting links --------------------------------------------------------

def test_vetting_links(client, advert):
    html = client.get(f"/corps/{CORP}").get_data(as_text=True)
    assert f"https://zkillboard.com/corporation/{CORP}/" in html
    assert f"https://evewho.com/corporation/{CORP}" in html
    assert "https://evemaps.dotlan.net/corp/Test_Corp" in html
    assert "https://zkillboard.com/alliance/99000001/" in html


def test_vetting_links_no_alliance_and_odd_names(client, advert):
    corp = db.session.get(Corporation, CORP)
    corp.alliance_id, corp.name = None, "Café & Co?"
    db.session.commit()
    html = client.get(f"/corps/{CORP}").get_data(as_text=True)
    assert "zkillboard.com/alliance" not in html
    assert "https://evemaps.dotlan.net/corp/Caf%C3%A9_%26_Co%3F" in html
