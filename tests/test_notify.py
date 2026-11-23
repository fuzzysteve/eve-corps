"""Report alerts to the admin Discord channel: one post per advert, safe content."""
import pytest
import requests
from sqlalchemy import select

from evecorps import notify
from evecorps.extensions import db
from evecorps.models import Advert, Report
from test_features import CORP, FZZW, MOD, advert, as_character  # noqa: F401  (advert is a fixture)

FAKE_WEBHOOK = "https://discord.example/api/webhooks/123/fake"


@pytest.fixture
def discord(app, tmp_path, monkeypatch):
    hook = tmp_path / "webhook.txt"
    hook.write_text(FAKE_WEBHOOK + "\n")
    app.config["DISCORD_WEBHOOK_FILE"] = str(hook)
    state = {"posts": [], "status": 204, "raise": False}

    class Resp:
        def __init__(self, code):
            self.status_code = code

    def fake_post(url, json=None, headers=None, timeout=None):
        if state["raise"]:
            raise requests.ConnectionError(f"failed to reach {url}")
        state["posts"].append({"url": url, "json": json, "headers": headers})
        return Resp(state["status"])

    monkeypatch.setattr(notify.requests, "post", fake_post)
    return state


def report_as(client, char_id, reason="Looks like a scam"):
    csrf = as_character(client, char_id, 98999999, "Reader Corp")
    return client.post(f"/corps/{CORP}/report", data={"csrf_token": csrf, "reason": reason})


def test_first_report_posts_once(client, advert, discord):
    report_as(client, 91000001)
    report_as(client, 91000002, "Me too")
    report_as(client, 91000003, "And me")
    assert len(discord["posts"]) == 1
    assert len(db.session.scalars(select(Report)).all()) == 3
    post = discord["posts"][0]
    assert post["url"] == FAKE_WEBHOOK
    content = post["json"]["content"]
    assert "Test Corp" in content and "Looks like a scam" in content
    assert f"http://localhost/corps/{CORP}" in content and "/moderation/" in content
    assert db.session.get(Advert, advert.id).report_notified_at is not None


def test_no_repost_after_reports_dismissed(client, advert, discord):
    report_as(client, 91000001)
    csrf = as_character(client, MOD, FZZW)
    client.post(f"/moderation/{advert.id}/dismiss", data={"csrf_token": csrf})
    report_as(client, 91000002, "New problem")
    assert len(discord["posts"]) == 1


def test_duplicate_report_from_same_pilot_doesnt_post(client, advert, discord):
    discord["status"] = 500  # first post fails, so the claim is released
    report_as(client, 91000001)
    discord["status"] = 204
    report_as(client, 91000001, "again")  # same pilot, report still open: not a new report
    assert len(discord["posts"]) == 1  # only the failed attempt


@pytest.mark.parametrize("failure", ["status", "raise"])
def test_failed_post_lets_next_report_retry(client, advert, discord, failure):
    if failure == "status":
        discord["status"] = 500
    else:
        discord["raise"] = True
    report_as(client, 91000001)
    assert db.session.get(Advert, advert.id).report_notified_at is None
    discord["status"], discord["raise"] = 204, False
    report_as(client, 91000002)
    assert db.session.get(Advert, advert.id).report_notified_at is not None
    assert discord["posts"][-1]["json"]["content"]


def test_webhook_url_not_logged_on_failure(client, advert, discord, caplog):
    discord["raise"] = True
    report_as(client, 91000001)
    assert "ConnectionError" in caplog.text
    assert FAKE_WEBHOOK not in caplog.text


def test_mentions_and_markdown_neutralised(client, advert, discord):
    report_as(client, 91000001, "@everyone **free isk** [click](https://evil.example) <@123>\n\nline2")
    post = discord["posts"][0]["json"]
    assert post["allowed_mentions"] == {"parse": []}
    assert post["flags"] & notify.SUPPRESS_EMBEDS
    content = post["content"]
    assert "**free isk**" not in content and "\\*\\*free isk\\*\\*" in content
    assert "[click](" not in content
    assert "line2" in content and "\n\nline2" not in content  # reason kept on one line


def test_long_reason_truncated(client, advert, discord):
    report_as(client, 91000001, "x" * 1000)
    assert len(discord["posts"][0]["json"]["content"]) <= 1900


@pytest.mark.parametrize("setting", ["", "/nonexistent/webhook.txt"])
def test_alerts_off_when_not_configured(app, client, advert, monkeypatch, setting):
    app.config["DISCORD_WEBHOOK_FILE"] = setting
    calls = []
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: calls.append(1))
    resp = report_as(client, 91000001)
    assert resp.status_code == 302 and not calls
    assert db.session.scalars(select(Report)).one()  # report still recorded
    assert db.session.get(Advert, advert.id).report_notified_at is None
