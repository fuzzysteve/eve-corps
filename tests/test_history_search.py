"""Edit history, full-text search and sorting, alliance names and logos."""
from datetime import datetime, timedelta, timezone

import pytest
import requests
from sqlalchemy import select

from evecorps import auth, esi
from evecorps.extensions import db
from evecorps.models import Advert, AdvertRevision, Alliance, Corporation, ModerationLog
from test_adverts import CHAR, CORP, eve, form, login  # noqa: F401
from test_features import FZZW, MOD, as_character

FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)


# --- Edit history ---------------------------------------------------------

def revisions():
    return db.session.scalars(select(AdvertRevision).order_by(AdvertRevision.created_at)).all()


def test_revisions_recorded(client, eve):
    csrf = login(client)
    client.post("/advert", data=form(csrf))
    client.post("/advert", data=form(csrf))  # identical: no new revision
    client.post("/advert", data=form(csrf, title="New title", body="Line one\nLine 2 changed",
                                     timezones=["eu"], srp=""))
    revs = revisions()
    assert [r.changed for r in revs] == [["created"], ["title", "body", "timezones", "srp"]]
    assert revs[1].editor_id == CHAR and revs[1].snapshot["title"] == "New title"


def test_history_page_shows_changes(client, eve):
    csrf = login(client)
    client.post("/advert", data=form(csrf))
    client.post("/advert", data=form(csrf, body="Line one\nLine 2 changed", timezones=["eu"], min_sp="10m"))
    html = client.get(f"/corps/{CORP}/history").get_data(as_text=True)
    assert "Created the advert." in html
    assert "Timezones" in html and "EUTZ, USTZ" in html and "→ EUTZ" in html
    assert "5,000,000" in html and "10,000,000" in html
    assert '<span class="diff-del">-Line two</span>' in html
    assert '<span class="diff-add">+Line 2 changed</span>' in html
    assert "Recruiter Pilot" in html


def test_history_includes_moderation(client, eve):
    csrf = login(client)
    client.post("/advert", data=form(csrf))
    advert = db.session.scalar(select(Advert))
    db.session.add(Corporation(corporation_id=FZZW, name="Fuzzwork Enterprises", ticker="FZZW"))
    db.session.flush()
    from evecorps.models import Character
    db.session.add(Character(character_id=MOD, name="Steve Ronuken", corporation_id=FZZW))
    db.session.add(ModerationLog(advert_id=advert.id, moderator_id=MOD, action="hide", reason="Too spicy"))
    db.session.commit()
    html = client.get(f"/corps/{CORP}/history").get_data(as_text=True)
    assert "Hid the advert: Too spicy" in html and "Moderator" in html


def test_history_escapes_content(client, eve):
    csrf = login(client)
    client.post("/advert", data=form(csrf))
    client.post("/advert", data=form(csrf, body="<script>alert(1)</script>"))
    html = client.get(f"/corps/{CORP}/history").get_data(as_text=True)
    assert "<script>alert(1)" not in html


def test_history_access(client, eve):
    csrf = login(client)
    client.post("/advert", data=form(csrf))
    assert client.get(f"/corps/{CORP}/history").status_code == 200
    assert b"Edit history" in client.get(f"/corps/{CORP}").data

    as_character(client, 91000001, 98999999, "Other Corp")
    assert client.get(f"/corps/{CORP}/history").status_code == 403
    assert b"Edit history" not in client.get(f"/corps/{CORP}").data

    as_character(client, MOD, FZZW, "Fuzzwork Enterprises")
    assert client.get(f"/corps/{CORP}/history").status_code == 200

    with client.session_transaction() as s:
        s.clear()
    assert client.get(f"/corps/{CORP}/history").status_code == 403
    assert client.get("/corps/123/history").status_code == 404


# --- Search and sort ------------------------------------------------------

@pytest.fixture
def listings(app):
    db.session.add(Alliance(alliance_id=99000001, name="Goonswarm Federation", ticker="CONDI"))
    rows = [
        (1, "Rock Breakers", "ROCKS", None, 10, "Mining in highsec", "We run mining fleets every day and support new miners."),
        (2, "Blap Squad", "BLAP", 99000001, 300, "Nullsec PvP", "Roams, structure bashes and small gang PvP."),
        (3, "100% Pure_Indy", "PURE", None, 50, "Industry", "Manufacturing and invention, 100% discount on BPCs."),
    ]
    for i, (cid, name, ticker, aid, members, title, body) in enumerate(rows):
        db.session.add(Corporation(corporation_id=cid, name=name, ticker=ticker, alliance_id=aid, member_count=members))
        db.session.add(Advert(corporation_id=cid, title=title, body=body,
                              created_at=datetime(2026, 1, 1 + i, tzinfo=timezone.utc),
                              last_confirmed_at=datetime(2026, 6, 3 - i, tzinfo=timezone.utc)))
    db.session.commit()


def order(client, query=""):
    html = client.get("/corps" + query).get_data(as_text=True)
    titles = ["Mining in highsec", "Nullsec PvP", "Industry"]
    found = [(html.index(f">{t}</a>"), t) for t in titles if f">{t}</a>" in html]
    return [t for _, t in sorted(found)]


@pytest.mark.parametrize("query,expected", [
    ("?q=miner", ["Mining in highsec"]),              # stemming: miner ~ mining/miners
    ("?q=fleets", ["Mining in highsec"]),
    ("?q=pvp", ["Nullsec PvP"]),
    ("?q=blap", ["Nullsec PvP"]),                     # corp name, partial
    ("?q=ROCK", ["Mining in highsec"]),               # ticker / name, case-insensitive
    ("?q=goonswarm", ["Nullsec PvP"]),                # alliance name
    ("?q=CONDI", ["Nullsec PvP"]),                    # alliance ticker
    ("?q=100%25", ["Industry"]),                      # % taken literally
    ("?q=k_b", []),                                   # _ literal: as a wildcard it would match "Rock Breakers"
    ("?q=e_indy", ["Industry"]),                      # ...and a literal _ still matches
    ("?q=mining+-highsec", []),                       # websearch syntax: exclusion
    ("?q=nothing+matches+this", []),
])
def test_search(client, listings, query, expected):
    assert order(client, query) == expected


@pytest.mark.parametrize("q", ['"unclosed', "a:b & | !", "'; drop table advert; --", "<script>", "x" * 500, "&&&"])
def test_odd_search_input_is_safe(client, listings, q):
    resp = client.get("/corps", query_string={"q": q})
    assert resp.status_code == 200
    assert "<script>" not in resp.get_data(as_text=True).split("<main")[1].replace("&lt;script&gt;", "")


def test_search_keeps_filters(client, listings):
    db.session.get(Advert, db.session.scalar(select(Advert.id).where(Advert.corporation_id == 2))).timezones = ["us"]
    db.session.commit()
    assert order(client, "?q=pvp&timezones=us") == ["Nullsec PvP"]
    assert order(client, "?q=pvp&timezones=eu") == []


@pytest.mark.parametrize("query,expected", [
    ("", ["Mining in highsec", "Nullsec PvP", "Industry"]),             # default: recently updated
    ("?sort=updated", ["Mining in highsec", "Nullsec PvP", "Industry"]),
    ("?sort=members", ["Nullsec PvP", "Industry", "Mining in highsec"]),
    ("?sort=newest", ["Industry", "Nullsec PvP", "Mining in highsec"]),
    ("?sort=bogus", ["Mining in highsec", "Nullsec PvP", "Industry"]),
])
def test_sorting(client, listings, query, expected):
    assert order(client, query) == expected


def test_relevance_ranking(client, listings):
    # "pvp" appears in both title and body of one advert; add a weaker match.
    db.session.get(Advert, db.session.scalar(select(Advert.id).where(Advert.corporation_id == 1))).body += " Some pvp too."
    db.session.commit()
    assert order(client, "?q=pvp") == ["Nullsec PvP", "Mining in highsec"]
    html = client.get("/corps?q=pvp").get_data(as_text=True)
    assert 'value="relevance" selected' in html and "2 results for" in html


# --- Alliances ------------------------------------------------------------

@pytest.fixture
def esi_corp(monkeypatch):
    calls = {"alliance": 0, "fail": False}
    monkeypatch.setattr(esi, "get_corporation", lambda cid: (
        {"name": "Blap Squad", "ticker": "BLAP", "alliance_id": 99000001, "ceo_id": 1}, FUTURE))

    def get_alliance(aid):
        calls["alliance"] += 1
        if calls["fail"]:
            raise requests.ConnectionError("down")
        return {"name": "Goonswarm Federation", "ticker": "CONDI"}, FUTURE

    monkeypatch.setattr(esi, "get_alliance", get_alliance)
    return calls


def test_alliance_fetched_and_cached(app, esi_corp):
    corp = auth.refresh_corporation(2)
    db.session.commit()
    assert corp.alliance.name == "Goonswarm Federation" and corp.alliance.ticker == "CONDI"
    auth.refresh_corporation(2)
    assert esi_corp["alliance"] == 1

    db.session.get(Alliance, 99000001).esi_expires = datetime.now(timezone.utc) - timedelta(seconds=1)
    auth.refresh_corporation(2)
    assert esi_corp["alliance"] == 2


def test_alliance_failure_does_not_break_corp(app, esi_corp):
    esi_corp["fail"] = True
    corp = auth.refresh_corporation(2)
    db.session.commit()
    assert corp.name == "Blap Squad" and corp.alliance is None


def test_alliance_shown(client, listings):
    browse = client.get("/corps").get_data(as_text=True)
    assert "Goonswarm Federation &lt;CONDI&gt;" in browse
    assert "images.evetech.net/alliances/99000001/logo" in browse
    page = client.get("/corps/2").get_data(as_text=True)
    assert "Goonswarm Federation &lt;CONDI&gt;" in page
    assert "https://evemaps.dotlan.net/alliance/Goonswarm_Federation" in page
    assert "images.evetech.net/alliances" not in client.get("/corps/1").get_data(as_text=True)
