"""Rate limits, view counts, robots.txt/sitemap, and the maintenance commands."""
from datetime import datetime, timedelta, timezone

import pytest
import requests
from sqlalchemy import func, select

from evecorps import cli, esi, limits
from evecorps.extensions import db
from evecorps.models import Advert, AdvertViewDaily, Corporation, RateLimit, Report
from test_adverts import CORP, eve, form, login  # noqa: F401
from test_features import FZZW, MOD, advert, as_character  # noqa: F401

NOW = datetime.now(timezone.utc)
BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"}


# --- Rate limits ------------------------------------------------------------

@pytest.fixture
def tight_limits(monkeypatch):
    monkeypatch.setitem(limits.LIMITS, "report", (2, 3600))
    monkeypatch.setitem(limits.LIMITS, "preview", (3, 600))
    monkeypatch.setitem(limits.LIMITS, "convert", (2, 600))
    monkeypatch.setitem(limits.LIMITS, "import", (2, 600))


def test_report_rate_limit(client, app, tight_limits):
    for cid in (1, 2, 3):
        db.session.add(Corporation(corporation_id=cid, name=f"C{cid}", ticker=f"C{cid}"))
        db.session.add(Advert(corporation_id=cid, title=f"Ad {cid}", body="x"))
    db.session.commit()
    csrf = as_character(client, 91000001, 98999999, "Reader Corp")
    for cid in (1, 2, 3):
        client.post(f"/corps/{cid}/report", data={"csrf_token": csrf, "reason": "spam"})
    assert db.session.scalar(select(func.count()).select_from(Report)) == 2
    assert b"sent a lot of reports" in client.get("/corps/3").data


def test_preview_convert_import_limits(client, eve, tight_limits):
    csrf = login(client)
    codes = [client.post("/advert/preview", data={"csrf_token": csrf, "body": "x"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    codes = [client.post("/advert/convert-paste", data={"csrf_token": csrf, "text": "x"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    resp = client.post("/advert/convert-paste", data={"csrf_token": csrf, "text": "x"})
    assert "Too many" in resp.get_json()["error"]
    db.session.get(Corporation, CORP).esi_expires = NOW + timedelta(hours=1)
    db.session.commit()
    codes = [client.get("/advert/ingame-description").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_limits_are_per_character(client, eve, tight_limits):
    for _ in range(3):
        limits.allow("preview", 111)
    assert limits.allow("preview", 111) is False
    assert limits.allow("preview", 222) is True


def test_prune_rate_limits(app):
    db.session.add(RateLimit(key="preview:1", window_start=NOW - timedelta(days=2), count=5))
    db.session.add(RateLimit(key="preview:1", window_start=NOW, count=1))
    db.session.commit()
    assert limits.prune() == 1
    assert db.session.scalar(select(func.count()).select_from(RateLimit)) == 1


# --- View counts ------------------------------------------------------------

def views(advert_id):
    return db.session.scalar(select(func.coalesce(func.sum(AdvertViewDaily.views), 0))
                             .where(AdvertViewDaily.advert_id == advert_id))


def test_public_views_counted(client, advert):
    for _ in range(3):
        client.get(f"/corps/{CORP}", headers=BROWSER)
    assert views(advert.id) == 3
    rows = db.session.scalars(select(AdvertViewDaily)).all()
    assert len(rows) == 1 and rows[0].day == NOW.date()


@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (compatible; Googlebot/2.1)", "Mozilla/5.0 (compatible; Discordbot/2.0)",
    "facebookexternalhit/1.1", "curl/8.5.0", "python-requests/2.32", "Slackbot-LinkExpanding 1.0", "",
])
def test_bots_not_counted(client, advert, ua):
    client.get(f"/corps/{CORP}", headers={"User-Agent": ua})
    assert views(advert.id) == 0


def test_own_recruiters_and_moderators_not_counted(client, eve, advert):
    login(client)
    client.get(f"/corps/{CORP}", headers=BROWSER)
    as_character(client, MOD, FZZW, "Fuzzwork Enterprises")
    client.get(f"/corps/{CORP}", headers=BROWSER)
    assert views(advert.id) == 0


def test_hidden_advert_not_counted(client, advert):
    advert.active = False
    db.session.commit()
    client.get(f"/corps/{CORP}", headers=BROWSER)
    assert views(advert.id) == 0


def test_stats_shown_only_to_recruiters_and_moderators(client, eve, advert):
    db.session.add(AdvertViewDaily(advert_id=advert.id, day=NOW.date(), views=5))
    db.session.add(AdvertViewDaily(advert_id=advert.id, day=NOW.date() - timedelta(days=10), views=7))
    db.session.add(AdvertViewDaily(advert_id=advert.id, day=NOW.date() - timedelta(days=40), views=100))
    db.session.commit()
    assert b"Views:" not in client.get(f"/corps/{CORP}", headers=BROWSER).data  # this visit counts too
    login(client)
    page = client.get(f"/corps/{CORP}").get_data(as_text=True)
    assert "Views: 6 in the last 7 days, 13 in the last 30" in page
    assert "viewed 6 times in the last 7 days and 13 in the last 30" in client.get("/advert").get_data(as_text=True)


# --- robots.txt / sitemap.xml -------------------------------------------------

def test_robots(client):
    resp = client.get("/robots.txt")
    text = resp.get_data(as_text=True)
    assert resp.content_type.startswith("text/plain")
    assert "Disallow: /advert" in text and "Disallow: /moderation/" in text
    assert "Sitemap: http://localhost/sitemap.xml" in text


def test_sitemap_lists_only_public(client, app):
    rows = [(1, True, False, "active"), (2, False, False, "active"), (3, True, True, "active"), (4, True, False, "closed")]
    for cid, active, hidden, state in rows:
        db.session.add(Corporation(corporation_id=cid, name=f"C{cid}", ticker=f"C{cid}", state=state))
        db.session.add(Advert(corporation_id=cid, title="t", body="b", active=active, moderation_hidden=hidden,
                              last_confirmed_at=datetime(2026, 11, 1, tzinfo=timezone.utc)))
    db.session.commit()
    resp = client.get("/sitemap.xml")
    xml = resp.get_data(as_text=True)
    assert resp.content_type.startswith("application/xml")
    assert "<loc>http://localhost/corps/1</loc><lastmod>2026-11-01</lastmod>" in xml
    for cid in (2, 3, 4):
        assert f"/corps/{cid}<" not in xml


# --- Maintenance commands -----------------------------------------------------

@pytest.fixture
def fake_esi(monkeypatch):
    state = {"calls": [], "fail": {}}
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    def get_corporation(cid):
        state["calls"].append(cid)
        if cid in state["fail"]:
            code = state["fail"][cid]
            if code == "conn":
                raise requests.ConnectionError("down")
            resp = requests.Response()
            resp.status_code = code
            raise requests.HTTPError(response=resp)
        return ({"name": f"Corp {cid}", "ticker": "T", "member_count": 9, "war_eligible": False,
                 "tax_rates": {"isk": 3.0}, "state": "closed" if cid == 3 else "active"},
                NOW + timedelta(hours=1))

    monkeypatch.setattr(esi, "get_corporation", get_corporation)
    return state


@pytest.fixture
def due_corps(app):
    for cid, expires, has_advert in [(1, NOW - timedelta(minutes=1), True), (2, NOW + timedelta(hours=1), True),
                                     (3, None, True), (4, NOW - timedelta(days=1), False)]:
        db.session.add(Corporation(corporation_id=cid, name=f"Old {cid}", ticker="O", esi_expires=expires))
        if has_advert:
            db.session.add(Advert(corporation_id=cid, title="t", body="b"))
    db.session.commit()


def test_refresh_corps(app, due_corps, fake_esi):
    result = app.test_cli_runner().invoke(cli.refresh_corps)
    assert result.exit_code == 0, result.output
    assert sorted(fake_esi["calls"]) == [1, 3]  # not cached-and-fresh (2), not advert-less (4)
    assert db.session.get(Corporation, 1).tax_rate == 3.0
    assert db.session.get(Corporation, 3).state == "closed"
    assert "Refreshed 2 of 2" in result.output


def test_refresh_corps_stops_on_rate_limit(app, due_corps, fake_esi):
    fake_esi["fail"] = {3: 420, 1: 420}
    result = app.test_cli_runner().invoke(cli.refresh_corps)
    assert result.exit_code == 1 and "rate limit (420)" in result.output
    assert len(fake_esi["calls"]) == 1  # stopped at the first one


def test_refresh_corps_reports_other_failures(app, due_corps, fake_esi):
    fake_esi["fail"] = {3: "conn"}
    result = app.test_cli_runner().invoke(cli.refresh_corps)
    assert result.exit_code == 1 and "1 failed" in result.output
    assert db.session.get(Corporation, 1).tax_rate == 3.0  # carried on with the rest


def test_prune_command(app, advert):
    db.session.add(AdvertViewDaily(advert_id=advert.id, day=NOW.date() - timedelta(days=400), views=3))
    db.session.add(AdvertViewDaily(advert_id=advert.id, day=NOW.date(), views=1))
    db.session.commit()
    result = app.test_cli_runner().invoke(cli.prune)
    assert result.exit_code == 0 and "1 view-count rows" in result.output
