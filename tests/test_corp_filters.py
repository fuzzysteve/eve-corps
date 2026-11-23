"""ESI corp facts (war, tax, size, faction warfare, age, closed) and alpha/commitment filters."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from evecorps import auth, esi
from evecorps.extensions import db
from evecorps.models import Advert, AdvertRevision, Corporation
from test_adverts import CORP, eve, form, login  # noqa: F401

NOW = datetime.now(timezone.utc)
FUTURE = NOW + timedelta(hours=1)


# --- Storing ESI facts ------------------------------------------------------

def test_refresh_stores_corp_facts(app, monkeypatch):
    monkeypatch.setattr(esi, "get_corporation", lambda cid: ({
        "name": "Facts Corp", "ticker": "FACT", "member_count": 42, "ceo_id": 1,
        "war_eligible": False, "tax_rates": {"isk": 7.5, "loyalty_point": 0.0},
        "date_founded": "2019-04-01T12:00:00Z", "enlisted_faction_id": 500002, "state": "active",
    }, FUTURE))
    corp = auth.refresh_corporation(5)
    db.session.commit()
    assert corp.war_eligible is False and corp.tax_rate == 7.5
    assert corp.date_founded == datetime(2019, 4, 1, 12, tzinfo=timezone.utc)
    assert corp.enlisted_faction_id == 500002 and corp.state == "active"


def test_refresh_handles_old_tax_shape_and_missing_fields(app, monkeypatch):
    monkeypatch.setattr(esi, "get_corporation", lambda cid: (
        {"name": "Old", "ticker": "OLD", "tax_rate": 0.1}, FUTURE))
    corp = auth.refresh_corporation(6)
    assert corp.tax_rate == pytest.approx(10.0)
    assert corp.war_eligible is None and corp.date_founded is None and corp.enlisted_faction_id is None


# --- Browse filters -------------------------------------------------------

@pytest.fixture
def corps(app):
    rows = [
        # id, title, members, war, tax, founded_days_ago, faction, state, alpha, commitment
        (1, "Tiny Safe", 5, False, 0.0, 30, None, "active", True, "casual"),
        (2, "Medium FW", 30, True, 10.0, 400, 500002, "active", False, "regular"),
        (3, "Large Old", 200, True, 5.0, 3000, None, "active", True, "hardcore"),
        (4, "Huge Pirate", 900, True, 15.0, 2000, 500010, "active", False, None),
        (5, "Closed Corp", 20, False, 0.0, 1000, None, "closed", True, "casual"),
        (6, "Unknown Facts", None, None, None, None, None, None, False, None),
        (7, "Taxing Fifty", 60, True, 50.0, 800, None, "active", False, None),
        (8, "Tax Farm", 3, True, 100.0, 800, None, "active", False, None),
    ]
    for cid, title, members, war, tax, age, fid, state, alpha, commit in rows:
        db.session.add(Corporation(
            corporation_id=cid, name=title, ticker=f"C{cid}", member_count=members, war_eligible=war,
            tax_rate=tax, date_founded=(NOW - timedelta(days=age)) if age is not None else None,
            enlisted_faction_id=fid, state=state))
        db.session.add(Advert(corporation_id=cid, title=title, body="x", alpha_friendly=alpha, commitment=commit))
    db.session.commit()


def titles(client, query=""):
    html = client.get("/corps" + query).get_data(as_text=True)
    names = ["Tiny Safe", "Medium FW", "Large Old", "Huge Pirate", "Closed Corp", "Unknown Facts",
             "Taxing Fifty", "Tax Farm"]
    return {n for n in names if f">{n}</a>" in html}


ALL_OPEN = {"Tiny Safe", "Medium FW", "Large Old", "Huge Pirate", "Unknown Facts", "Taxing Fifty", "Tax Farm"}


@pytest.mark.parametrize("query,expected", [
    ("", ALL_OPEN),                                            # closed corps never listed
    ("?no_war=1", {"Tiny Safe"}),                              # unknown war status excluded
    ("?max_tax=0", {"Tiny Safe"}),
    ("?max_tax=5", {"Tiny Safe", "Large Old"}),
    ("?max_tax=10", {"Tiny Safe", "Medium FW", "Large Old"}),
    ("?max_tax=25", {"Tiny Safe", "Medium FW", "Large Old", "Huge Pirate"}),
    ("?max_tax=50", {"Tiny Safe", "Medium FW", "Large Old", "Huge Pirate", "Taxing Fifty"}),
    ("?max_tax=7", ALL_OPEN),                                  # not an offered option: ignored
    ("?size=small", {"Tiny Safe", "Tax Farm"}),
    ("?size=medium&size=huge", {"Medium FW", "Huge Pirate"}),
    ("?size=large", {"Large Old", "Taxing Fifty"}),
    ("?size=bogus", ALL_OPEN),
    ("?fw=none", {"Tiny Safe", "Large Old", "Unknown Facts", "Taxing Fifty", "Tax Farm"}),
    ("?fw=500002", {"Medium FW"}),
    ("?fw=500010", {"Huge Pirate"}),
    ("?fw=12345", ALL_OPEN),                                   # not an FW faction: ignored
    ("?min_age=6m", {"Medium FW", "Large Old", "Huge Pirate", "Taxing Fifty", "Tax Farm"}),
    ("?min_age=1y", {"Medium FW", "Large Old", "Huge Pirate", "Taxing Fifty", "Tax Farm"}),
    ("?min_age=5y", {"Large Old", "Huge Pirate"}),
    ("?alpha=1", {"Tiny Safe", "Large Old"}),
    ("?commitment=casual", {"Tiny Safe"}),
    ("?commitment=regular&commitment=hardcore", {"Medium FW", "Large Old"}),
    ("?alpha=1&commitment=hardcore&max_tax=5&min_age=5y", {"Large Old"}),
])
def test_corp_filters(client, corps, query, expected):
    assert titles(client, query) == expected


def test_filter_state_kept_in_form(client, corps):
    html = client.get("/corps?no_war=1&max_tax=5&size=small&fw=500002&min_age=1y&alpha=1&commitment=casual").get_data(as_text=True)
    assert 'id="f-nowar" checked' in html
    assert '<option value="5" selected>' in html
    assert 'id="f-size-small" checked' in html
    assert '<option value="500002" selected>' in html
    assert '<option value="1y" selected>' in html
    assert 'id="f-alpha" checked' in html and 'id="f-commit-casual" checked' in html


def test_facts_shown_on_card_and_page(client, corps):
    card = client.get("/corps").get_data(as_text=True)
    assert "10% tax · war-declarable · 1 year old · FW: Minmatar Republic" in card
    assert "0% tax · can&#39;t be war-declared · 1 month old" in card
    assert "Alpha clones welcome" in card and "Hardcore" in card
    page = client.get("/corps/2").get_data(as_text=True)
    assert "At a glance" in page and "Can be war-declared" in page
    assert "Minmatar Republic" in page and "Regular" in page
    assert "Some expected participation" in page


def test_closed_corp_page_warns(client, corps):
    assert "This corporation has closed." in client.get("/corps/5").get_data(as_text=True)


# --- Editor ---------------------------------------------------------------

def test_editor_saves_alpha_and_commitment(client, eve):
    csrf = login(client)
    client.post("/advert", data=form(csrf, alpha="1", commitment="hardcore"))
    ad = db.session.scalar(select(Advert))
    assert ad.alpha_friendly is True and ad.commitment == "hardcore"
    page = client.get("/advert").get_data(as_text=True)
    assert 'id="e-alpha" checked' in page and 'id="e-commit-hardcore" checked' in page

    client.post("/advert", data=form(csrf, commitment="bogus"))
    ad = db.session.scalar(select(Advert))
    assert ad.alpha_friendly is False and ad.commitment is None
    rev = db.session.scalars(select(AdvertRevision).order_by(AdvertRevision.created_at)).all()[-1]
    assert "alpha_friendly" in rev.changed and "commitment" in rev.changed
    history = client.get(f"/corps/{CORP}/history").get_data(as_text=True)
    assert "Commitment" in history and "Hardcore" in history and "(none)" in history



def test_high_tax_badge(client, corps):
    card = client.get("/corps").get_data(as_text=True)
    assert "High tax: 100%" in card and "High tax: 50%" in card
    assert "High tax: 15%" not in card and "15% tax" in card       # below threshold: plain text
    assert "100% tax" not in card                                  # badge replaces the plain text
    page = client.get("/corps/8").get_data(as_text=True)
    assert '<span class="badge text-bg-warning">100%</span>' in page
    assert '<option value="50"' in client.get("/corps").get_data(as_text=True)
