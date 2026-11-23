"""Maintenance commands, run by evecorps-maintenance.timer.

    flask refresh-corps   # re-fetch ESI data for corps with adverts, once their cache expires
    flask prune           # drop old rate-limit counters and year-old view counts
"""

import sys
import time
from datetime import datetime, timedelta, timezone

import click
import requests
from flask import current_app
from sqlalchemy import select

from . import limits
from .auth import refresh_corporation
from .extensions import db
from .models import Advert, AdvertViewDaily, Corporation

PAUSE = 0.25  # seconds between ESI calls; plenty polite for a few hundred corps


@click.command("refresh-corps")
def refresh_corps():
    """Refresh corp (and alliance) facts from ESI for every corp with an advert."""
    now = datetime.now(timezone.utc)
    corp_ids = db.session.scalars(
        select(Corporation.corporation_id)
        .join(Advert, Advert.corporation_id == Corporation.corporation_id)
        .where((Corporation.esi_expires.is_(None)) | (Corporation.esi_expires <= now))
        .order_by(Corporation.esi_expires.nulls_first())
    ).all()

    refreshed, failed = 0, 0
    for corp_id in corp_ids:
        try:
            corp = refresh_corporation(corp_id)  # honours esi_expires itself
            db.session.commit()
            refreshed += 1
            if corp.state == "closed":
                current_app.logger.info("Corp %s (%s) has closed; its advert is now hidden", corp_id, corp.name)
        except requests.HTTPError as e:
            db.session.rollback()
            status = e.response.status_code if e.response is not None else None
            if status in (420, 429):
                # Error-limited or rate-limited: stop now rather than make it worse.
                click.echo(f"ESI rate limit ({status}) after {refreshed} corps; stopping.", err=True)
                sys.exit(1)
            failed += 1
            click.echo(f"Corp {corp_id}: ESI HTTP {status}", err=True)
        except requests.RequestException as e:
            db.session.rollback()
            failed += 1
            click.echo(f"Corp {corp_id}: {type(e).__name__}", err=True)
        time.sleep(PAUSE)

    click.echo(f"Refreshed {refreshed} of {len(corp_ids)} corps due; {failed} failed.")
    if failed:
        sys.exit(1)


@click.command("prune")
def prune():
    """Delete rate-limit counters older than a day and view counts older than a year."""
    rate_rows = limits.prune()
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=365)
    view_rows = db.session.query(AdvertViewDaily).filter(AdvertViewDaily.day < cutoff).delete()
    db.session.commit()
    click.echo(f"Pruned {rate_rows} rate-limit rows and {view_rows} view-count rows.")
