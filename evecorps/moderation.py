"""Moderation: members of MODERATOR_CORP_IDS can hide adverts and handle reports."""

import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from .auth import is_moderator
from .extensions import db
from .models import Advert, ModerationLog, Report

bp = Blueprint("moderation", __name__, url_prefix="/moderation")


@bp.before_request
def require_moderator():
    if not is_moderator(g.character):
        abort(403)


def open_report_count() -> int:
    return db.session.scalar(select(func.count()).select_from(Report).where(Report.resolved_at.is_(None))) or 0


@bp.route("/")
def queue():
    reported = db.session.scalars(
        select(Advert)
        .join(Report)
        .where(Report.resolved_at.is_(None))
        .group_by(Advert.id)
        .order_by(func.min(Report.created_at))
    ).all()
    hidden = db.session.scalars(
        select(Advert).where(Advert.moderation_hidden.is_(True)).order_by(Advert.updated_at.desc())
    ).all()
    log = db.session.scalars(select(ModerationLog).order_by(ModerationLog.created_at.desc()).limit(50)).all()
    return render_template("moderation.html", reported=reported, hidden=hidden, log=log)


def _advert(advert_id: uuid.UUID) -> Advert:
    advert = db.session.get(Advert, advert_id)
    if advert is None:
        abort(404)
    return advert


def _resolve_reports(advert: Advert) -> None:
    now = datetime.now(timezone.utc)
    for r in advert.reports:
        if r.resolved_at is None:
            r.resolved_at = now
            r.resolved_by_id = g.character.character_id


def _back(advert: Advert):
    target = request.form.get("next")
    if target == "queue":
        return redirect(url_for("moderation.queue"))
    return redirect(url_for("main.corp", corporation_id=advert.corporation_id))


@bp.route("/<uuid:advert_id>/hide", methods=["POST"])
def hide(advert_id):
    advert = _advert(advert_id)
    reason = request.form.get("reason", "").strip()
    if not reason or len(reason) > 1000:
        flash("Give a reason for hiding (the corp's recruiters will see it).", "warning")
        return _back(advert)
    advert.moderation_hidden = True
    advert.moderation_reason = reason
    _resolve_reports(advert)
    db.session.add(ModerationLog(advert=advert, moderator_id=g.character.character_id, action="hide", reason=reason))
    db.session.commit()
    flash(f"Advert for {advert.corporation.name} hidden.", "success")
    return _back(advert)


@bp.route("/<uuid:advert_id>/unhide", methods=["POST"])
def unhide(advert_id):
    advert = _advert(advert_id)
    advert.moderation_hidden = False
    advert.moderation_reason = None
    db.session.add(ModerationLog(advert=advert, moderator_id=g.character.character_id, action="unhide"))
    db.session.commit()
    flash(f"Advert for {advert.corporation.name} is visible again.", "success")
    return _back(advert)


@bp.route("/<uuid:advert_id>/dismiss", methods=["POST"])
def dismiss(advert_id):
    advert = _advert(advert_id)
    _resolve_reports(advert)
    db.session.add(ModerationLog(advert=advert, moderator_id=g.character.character_id, action="dismiss",
                                 reason=request.form.get("reason", "").strip() or None))
    db.session.commit()
    flash(f"Reports on {advert.corporation.name} dismissed.", "success")
    return _back(advert)
