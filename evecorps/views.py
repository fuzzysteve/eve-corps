import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)
from sqlalchemy import select, update

from . import choices, esi
from .auth import is_moderator, is_recruiter, refresh_corporation
from .extensions import db
from .formatting import eve_description_to_markdown, render_markdown
from .notify import post_report_alert
from .models import Advert, Corporation, Report

bp = Blueprint("main", __name__)

PER_PAGE = 25
TITLE_MAX = 120
BODY_MAX = 10_000
SP_MAX = 1_000_000_000
MAX_CONTACTS = 3
DISCORD_INVITE = re.compile(r"^https://(?:www\.)?(?:discord\.gg|discord\.com/invite)/[A-Za-z0-9-]{2,32}/?$")


@bp.route("/")
def index():
    return render_template("index.html")


# Browsers and crawlers ask for these at the site root regardless of <link> tags.
@bp.route("/favicon.ico")
@bp.route("/apple-touch-icon.png")
def root_icon():
    name = request.path.lstrip("/")
    return send_from_directory(current_app.static_folder, name, max_age=7 * 86400)


def _selected(source, name: str) -> list[str]:
    """Checked values for a multi-select field, de-duplicated and limited to known keys."""
    allowed = choices.MULTI[name]
    return [v for v in dict.fromkeys(source.getlist(name)) if v in allowed]


def _parse_sp(raw: str) -> int | None:
    """Skill points as whole SP. Accepts '25000000', '25,000,000' or '25m'. Floors fractions.

    Returns None if unparseable or out of range."""
    raw = raw.strip().lower().replace(",", "").replace("_", "")
    if not raw:
        return 0
    mult = 1
    if raw.endswith("m"):
        raw, mult = raw[:-1], 1_000_000
    try:
        value = Decimal(raw) * mult
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    sp = math.floor(value)
    return sp if 0 <= sp <= SP_MAX else None


def _clean_discord(raw: str) -> tuple[str | None, str | None]:
    """(value, error). Only discord.gg / discord.com/invite links."""
    raw = raw.strip()
    if not raw:
        return None, None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    raw = raw.replace("http://", "https://", 1)
    if not DISCORD_INVITE.match(raw):
        return None, "Discord must be an invite link like https://discord.gg/abc123."
    return raw.rstrip("/"), None


def _clean_website(raw: str) -> tuple[str | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or "." not in (parts.hostname or "") or len(raw) > 300:
        return None, "Website must be a web address like https://example.com (at most 300 characters)."
    return raw, None


def _contact_names(raw: str) -> list[str]:
    """One character name per line (commas also accepted), de-duplicated."""
    names = [n.strip() for n in re.split(r"[\n,]", raw) if n.strip()]
    return list(dict.fromkeys(names))


@bp.route("/corps")
def browse():
    stmt = (
        select(Advert)
        .join(Advert.corporation)
        .where(Advert.active.is_(True), Advert.moderation_hidden.is_(False))
        .order_by(Advert.last_confirmed_at.desc())
    )
    filters = {name: _selected(request.args, name) for name in choices.MULTI}
    for name, values in filters.items():
        if values:
            # Within one attribute: any of the ticked values. Across attributes: all.
            stmt = stmt.where(getattr(Advert, name).overlap(values))

    if request.args.get("newbro"):
        stmt = stmt.where(Advert.newbro_friendly.is_(True))
    if request.args.get("srp"):
        stmt = stmt.where(Advert.srp.is_(True))

    alliance = request.args.get("alliance", "")
    if alliance == "yes":
        stmt = stmt.where(Corporation.alliance_id.is_not(None))
    elif alliance == "no":
        stmt = stmt.where(Corporation.alliance_id.is_(None))

    my_sp = _parse_sp(request.args.get("my_sp", ""))
    if my_sp:
        stmt = stmt.where(Advert.min_sp <= my_sp)

    page = max(request.args.get("page", 1, type=int), 1)
    adverts = db.paginate(stmt, page=page, per_page=PER_PAGE, error_out=False)
    return render_template("browse.html", adverts=adverts, filters=filters, alliance=alliance)


@bp.route("/corps/<int:corporation_id>")
def corp(corporation_id: int):
    advert = db.session.scalar(
        select(Advert).where(Advert.corporation_id == corporation_id)
    )
    if advert is None:
        abort(404)
    can_edit = is_recruiter(g.character) and g.character.recruiter_corp_id == corporation_id
    moderator = is_moderator(g.character)
    if not advert.public and not (can_edit or moderator):
        abort(404)
    already_reported = g.character is not None and any(
        r.reporter_id == g.character.character_id and r.resolved_at is None for r in advert.reports
    )
    return render_template(
        "corp.html", advert=advert, can_edit=can_edit, moderator=moderator,
        already_reported=already_reported,
    )


@bp.route("/corps/<int:corporation_id>/report", methods=["POST"])
def report(corporation_id: int):
    if g.character is None:
        flash("Log in with EVE Online to report an advert.", "warning")
        return redirect(url_for("main.corp", corporation_id=corporation_id))
    advert = db.session.scalar(select(Advert).where(Advert.corporation_id == corporation_id))
    if advert is None or not advert.public:
        abort(404)
    reason = request.form.get("reason", "").strip()
    if not reason or len(reason) > 1000:
        flash("Please say briefly what's wrong (up to 1000 characters).", "warning")
        return redirect(url_for("main.corp", corporation_id=corporation_id))
    open_report = any(
        r.reporter_id == g.character.character_id and r.resolved_at is None for r in advert.reports
    )
    if not open_report:
        new_report = Report(advert=advert, reporter_id=g.character.character_id, reason=reason)
        db.session.add(new_report)
        db.session.commit()
        _alert_once(advert, new_report)
    flash("Thanks, the moderators will take a look.", "success")
    return redirect(url_for("main.corp", corporation_id=corporation_id))


def _alert_once(advert: Advert, new_report: Report) -> None:
    """Post the first report on an advert to the admin channel; never post again for it."""
    # Atomic claim, so two reports arriving together can't both post.
    claimed = db.session.execute(
        update(Advert)
        .where(Advert.id == advert.id, Advert.report_notified_at.is_(None))
        .values(report_notified_at=datetime.now(timezone.utc))
        .returning(Advert.id)
    ).first()
    db.session.commit()
    if claimed is None:
        return
    if not post_report_alert(advert, new_report):
        # Didn't go out (or alerts are off): release the claim so a later report can post.
        db.session.execute(update(Advert).where(Advert.id == advert.id).values(report_notified_at=None))
        db.session.commit()


@bp.route("/advert", methods=["GET", "POST"])
def edit_advert():
    if g.character is None or not is_recruiter(g.character):
        return render_template("recruiter_login.html"), 403

    corp = g.character.corporation
    advert = db.session.scalar(select(Advert).where(Advert.corporation_id == corp.corporation_id))

    if request.method == "GET":
        return render_template("edit.html", advert=advert, form=None, errors=[])

    form = request.form
    errors = []
    title = form.get("title", "").strip()
    body = form.get("body", "").strip()
    min_sp = _parse_sp(form.get("min_sp", ""))
    if not title or len(title) > TITLE_MAX:
        errors.append(f"Title is required and must be at most {TITLE_MAX} characters.")
    if not body or len(body) > BODY_MAX:
        errors.append(f"Description is required and must be at most {BODY_MAX:,} characters.")
    if min_sp is None:
        errors.append("Minimum skill points must be a whole number like 5000000 or 5m.")

    discord_url, err = _clean_discord(form.get("discord_url", ""))
    if err:
        errors.append(err)
    website_url, err = _clean_website(form.get("website_url", ""))
    if err:
        errors.append(err)
    ingame_channel = form.get("ingame_channel", "").strip() or None
    if ingame_channel and len(ingame_channel) > 60:
        errors.append("In-game channel name must be at most 60 characters.")

    names = _contact_names(form.get("contacts", ""))
    contacts = advert.contacts if advert else []
    if len(names) > MAX_CONTACTS:
        errors.append(f"List at most {MAX_CONTACTS} contact characters.")
    elif [n.lower() for n in names] != [c["name"].lower() for c in contacts]:
        # Names changed: check they're real characters (exact name match).
        try:
            found = esi.resolve_character_names(names)
        except requests.RequestException:
            found = None
            errors.append("Couldn't check the contact names with EVE just now, please try again.")
        if found is not None:
            missing = [n for n in names if n.lower() not in found]
            if missing:
                errors.append("No EVE character found named: " + ", ".join(missing) + ". Names must match exactly.")
            contacts = [found[n.lower()] for n in names if n.lower() in found]

    if errors:
        return render_template("edit.html", advert=advert, form=form, errors=errors), 400

    if advert is None:
        advert = Advert(corporation_id=corp.corporation_id)
        db.session.add(advert)
    advert.title = title
    advert.body = body
    for name in choices.MULTI:
        setattr(advert, name, _selected(form, name))
    advert.newbro_friendly = bool(form.get("newbro"))
    advert.srp = bool(form.get("srp"))
    advert.min_sp = min_sp
    advert.discord_url = discord_url
    advert.website_url = website_url
    advert.ingame_channel = ingame_channel
    advert.contacts = contacts
    advert.active = bool(form.get("active"))
    advert.updated_by = g.character
    advert.last_confirmed_at = datetime.now(timezone.utc)
    db.session.commit()

    if advert.moderation_hidden:
        flash("Advert saved, but it stays hidden until a moderator reviews it.", "warning")
    else:
        flash("Advert saved." if advert.active else "Advert saved (hidden from search).", "success")
    return redirect(url_for("main.corp", corporation_id=corp.corporation_id))


@bp.route("/advert/preview", methods=["POST"])
def preview_advert():
    if not is_recruiter(g.character):
        abort(403)
    body = request.form.get("body", "")[:BODY_MAX]
    return render_markdown(body), 200, {"Content-Type": "text/html; charset=utf-8"}


@bp.route("/advert/ingame-description")
def ingame_description():
    """The corp's in-game description converted to Markdown, for the editor."""
    if not is_recruiter(g.character):
        abort(403)
    try:
        # Only re-fetched from ESI once the cached copy has expired.
        corp = refresh_corporation(g.character.corporation_id)
        db.session.commit()
    except requests.RequestException:
        return jsonify(error="Couldn't reach EVE to fetch your in-game description, try again shortly."), 502
    return jsonify(markdown=eve_description_to_markdown(corp.description))
