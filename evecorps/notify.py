"""Discord alerts to the admin channel."""

import re
from pathlib import Path

import requests
from flask import current_app, url_for

from .models import Advert, Report

TIMEOUT = 5
MAX_MESSAGE = 1900  # Discord's limit is 2000
SUPPRESS_EMBEDS = 1 << 2


def _escape(text: str, limit: int) -> str:
    """Neutralise Discord markdown in user-written text and keep it to one line."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return re.sub(r"([\\*_~`|>#\[\]()-])", r"\\\1", text)


def _webhook() -> str | None:
    path = current_app.config.get("DISCORD_WEBHOOK_FILE")
    if not path:
        return None
    try:
        url = Path(path).read_text().strip()
    except OSError:
        current_app.logger.warning("Discord webhook file %s not readable; report alert skipped", path)
        return None
    return url if url.startswith("https://") else None


def report_message(advert: Advert, report: Report) -> str:
    corp = advert.corporation
    lines = [
        "**Advert reported on eve-corps.online**",
        f"Corp: {_escape(corp.name, 100)} \\[{_escape(corp.ticker, 10)}\\]",
        f"Title: {_escape(advert.title, 150)}",
        f"Reported by: {_escape(report.reporter.name, 100)}",
        f"Reason: {_escape(report.reason, 800)}",
        f"Advert: {url_for('main.corp', corporation_id=corp.corporation_id, _external=True)}",
        f"Queue: {url_for('moderation.queue', _external=True)}",
        "_Further reports on this advert won't be posted here._",
    ]
    return "\n".join(lines)[:MAX_MESSAGE]


def post_report_alert(advert: Advert, report: Report) -> bool:
    """Post to the admin channel. Returns True if Discord accepted it."""
    webhook = _webhook()
    if webhook is None:
        return False
    try:
        resp = requests.post(
            webhook,
            json={
                "content": report_message(advert, report),
                "allowed_mentions": {"parse": []},  # never ping, whatever the reporter wrote
                "flags": SUPPRESS_EMBEDS,
            },
            headers={"User-Agent": "EveCorpsOnline/0.1 (+https://eve-corps.online)"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        # Log the type only: the exception text can include the webhook URL.
        current_app.logger.warning("Report alert to Discord failed: %s", type(e).__name__)
        return False
    if resp.status_code >= 300:
        current_app.logger.warning("Discord rejected report alert: HTTP %s", resp.status_code)
        return False
    return True
