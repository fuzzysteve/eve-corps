import difflib
import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import choices, esi
from .auth import is_moderator, is_recruiter, refresh_corporation
from .extensions import db
from . import limits
from .formatting import eve_description_to_markdown, pasted_advert_to_markdown, render_markdown
from .notify import post_report_alert
from .models import Advert, AdvertRevision, AdvertViewDaily, Alliance, Corporation, ModerationLog, Report

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


@bp.route("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Disallow: /advert",
        "Disallow: /moderation/",
        "Disallow: /sso/",
        "Disallow: /logout",
        "Disallow: /corps/*/history",
        "Disallow: /corps?",  # filtered/search listings: endless combinations
        f"Sitemap: {url_for('main.sitemap', _external=True)}",
        "",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.route("/sitemap.xml")
def sitemap():
    adverts = db.session.scalars(
        select(Advert)
        .join(Advert.corporation)
        .where(Advert.active.is_(True), Advert.moderation_hidden.is_(False),
               or_(Corporation.state.is_(None), Corporation.state != "closed"))
        .order_by(Advert.last_confirmed_at.desc())
    ).all()
    xml = render_template("sitemap.xml", adverts=adverts)
    return xml, 200, {"Content-Type": "application/xml; charset=utf-8"}


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


SORTS = {
    "updated": "Recently updated",
    "members": "Most members",
    "newest": "Newest adverts",
}
Q_MAX = 200


def _like(term: str) -> str:
    """ILIKE pattern matching term anywhere, with % and _ taken literally."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@bp.route("/corps")
def browse():
    stmt = (
        select(Advert)
        .join(Advert.corporation)
        .outerjoin(Alliance, Alliance.alliance_id == Corporation.alliance_id)
        .where(Advert.active.is_(True), Advert.moderation_hidden.is_(False))
    )

    q = request.args.get("q", "").strip()[:Q_MAX]
    rank = None
    if q:
        tsq = func.websearch_to_tsquery("english", q)
        pattern = _like(q)
        stmt = stmt.where(or_(
            Advert.search_vector.op("@@")(tsq),
            Corporation.name.ilike(pattern, escape="\\"),
            Corporation.ticker.ilike(pattern, escape="\\"),
            Alliance.name.ilike(pattern, escape="\\"),
            Alliance.ticker.ilike(pattern, escape="\\"),
        ))
        rank = func.ts_rank(Advert.search_vector, tsq)

    sort = request.args.get("sort", "")
    if sort not in SORTS:
        sort = "relevance" if q else "updated"
    if sort == "relevance" and rank is not None:
        stmt = stmt.order_by(rank.desc(), Advert.last_confirmed_at.desc())
    elif sort == "members":
        stmt = stmt.order_by(Corporation.member_count.desc().nulls_last(), Advert.last_confirmed_at.desc())
    elif sort == "newest":
        stmt = stmt.order_by(Advert.created_at.desc())
    else:
        sort = "updated"
        stmt = stmt.order_by(Advert.last_confirmed_at.desc())
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

    # Closed corporations never show in search.
    stmt = stmt.where(or_(Corporation.state.is_(None), Corporation.state != "closed"))

    # Verified corp facts from ESI.
    corp_filters = {}
    if request.args.get("no_war"):
        stmt = stmt.where(Corporation.war_eligible.is_(False))
        corp_filters["no_war"] = True
    max_tax = request.args.get("max_tax", type=int)
    if max_tax in choices.MAX_TAX:
        stmt = stmt.where(Corporation.tax_rate <= max_tax)
        corp_filters["max_tax"] = max_tax
    sizes = [s for s in dict.fromkeys(request.args.getlist("size")) if s in choices.SIZES]
    if sizes:
        ranges = []
        for key in sizes:
            _, low, high = choices.SIZES[key]
            cond = Corporation.member_count >= low
            if high is not None:
                cond = cond & (Corporation.member_count <= high)
            ranges.append(cond)
        stmt = stmt.where(or_(*ranges))
    corp_filters["sizes"] = sizes
    fw = request.args.get("fw", "")
    if fw == "none":
        stmt = stmt.where(Corporation.enlisted_faction_id.is_(None))
    elif fw.isdigit() and int(fw) in choices.FW_FACTIONS:
        stmt = stmt.where(Corporation.enlisted_faction_id == int(fw))
    else:
        fw = ""
    corp_filters["fw"] = fw
    min_age = request.args.get("min_age", "")
    if min_age in choices.MIN_AGE:
        cutoff = datetime.now(timezone.utc) - timedelta(days=choices.MIN_AGE[min_age][1])
        stmt = stmt.where(Corporation.date_founded <= cutoff)
    else:
        min_age = ""
    corp_filters["min_age"] = min_age

    # Recruiter-set extras.
    if request.args.get("alpha"):
        stmt = stmt.where(Advert.alpha_friendly.is_(True))
    commitment = [c for c in dict.fromkeys(request.args.getlist("commitment")) if c in choices.COMMITMENT]
    if commitment:
        stmt = stmt.where(Advert.commitment.in_(commitment))
    corp_filters["commitment"] = commitment

    page = max(request.args.get("page", 1, type=int), 1)
    adverts = db.paginate(stmt, page=page, per_page=PER_PAGE, error_out=False)
    return render_template("browse.html", adverts=adverts, filters=filters, alliance=alliance,
                           q=q, sort=sort, sorts=SORTS, cf=corp_filters)


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
    if advert.public and not (can_edit or moderator):
        _count_view(advert)
    stats = view_stats(advert) if (can_edit or moderator) else None
    return render_template(
        "corp.html", advert=advert, can_edit=can_edit, moderator=moderator,
        already_reported=already_reported, stats=stats,
    )


# Crawlers, link-preview fetchers and scripts don't count as views.
BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|preview|facebookexternalhit|embedly|discord|telegram|whatsapp|"
    r"slack|skype|curl|wget|python|httpx|go-http|java/|headless|lighthouse|monitor",
    re.IGNORECASE,
)


def _count_view(advert: Advert) -> None:
    """Add one to today's count. Stores no IP, cookie or anything about the visitor."""
    ua = request.headers.get("User-Agent", "")
    if request.method != "GET" or not ua or BOT_UA.search(ua):  # real browsers always send a UA
        return
    today = datetime.now(timezone.utc).date()
    db.session.execute(
        pg_insert(AdvertViewDaily)
        .values(advert_id=advert.id, day=today, views=1)
        .on_conflict_do_update(
            index_elements=[AdvertViewDaily.advert_id, AdvertViewDaily.day],
            set_={"views": AdvertViewDaily.views + 1},
        )
    )
    db.session.commit()


def view_stats(advert: Advert) -> dict:
    today = datetime.now(timezone.utc).date()

    def since(days: int) -> int:
        return db.session.scalar(
            select(func.coalesce(func.sum(AdvertViewDaily.views), 0))
            .where(AdvertViewDaily.advert_id == advert.id, AdvertViewDaily.day > today - timedelta(days=days))
        )

    return {"week": since(7), "month": since(30)}


def _display(field: str, value) -> str:
    if value is None or value == [] or value == "":
        return "(none)"
    if field in choices.MULTI:
        labels = choices.MULTI[field]
        return ", ".join(labels.get(v, v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if field == "min_sp":
        return f"{value:,}"
    if field == "commitment":
        return choices.COMMITMENT.get(value, value)
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def _text_diff(old: str | None, new: str | None) -> list[tuple[str, str]]:
    """Unified diff lines tagged add/del/ctx, without the file headers."""
    lines = difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(), lineterm="", n=1)
    out = []
    for line in list(lines)[2:]:
        kind = {"+": "add", "-": "del", "@": "hunk"}.get(line[:1], "ctx")
        out.append((kind, line))
    return out


@bp.route("/corps/<int:corporation_id>/history")
def history(corporation_id: int):
    advert = db.session.scalar(select(Advert).where(Advert.corporation_id == corporation_id))
    if advert is None:
        abort(404)
    can_edit = is_recruiter(g.character) and g.character.recruiter_corp_id == corporation_id
    if not (can_edit or is_moderator(g.character)):
        abort(403)

    entries = []
    previous = None
    for rev in advert.revisions:
        changes = []
        for field in rev.changed:
            if field == "created" or previous is None:
                continue
            old, new = previous.snapshot.get(field), rev.snapshot.get(field)
            if field in ("title", "body"):
                changes.append({"label": REVISION_FIELDS[field], "diff": _text_diff(old, new)})
            else:
                changes.append({"label": REVISION_FIELDS[field],
                                "old": _display(field, old), "new": _display(field, new)})
        entries.append({"kind": "revision", "at": rev.created_at, "who": rev.editor,
                        "created": "created" in rev.changed, "changes": changes, "rev": rev})
        previous = rev
    for log in db.session.scalars(select(ModerationLog).where(ModerationLog.advert_id == advert.id)):
        entries.append({"kind": "moderation", "at": log.created_at, "who": log.moderator,
                        "action": log.action, "reason": log.reason})
    entries.sort(key=lambda e: e["at"], reverse=True)
    return render_template("history.html", advert=advert, entries=entries, labels=REVISION_FIELDS)


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
    if not limits.allow("report", g.character.character_id):
        flash("You've sent a lot of reports recently. Please try again later.", "warning")
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


# Fields recorded in each revision, in display order.
REVISION_FIELDS = {
    "title": "Title", "body": "Description", "timezones": "Timezones", "languages": "Languages",
    "activities": "Activities", "space": "Space", "newbro_friendly": "New-player friendly",
    "alpha_friendly": "Alpha clones welcome", "commitment": "Commitment",
    "srp": "SRP", "min_sp": "Minimum SP", "discord_url": "Discord", "website_url": "Website",
    "ingame_channel": "In-game channel", "contacts": "Contacts", "active": "Shown in search",
}


def _snapshot(advert: Advert) -> dict:
    snap = {f: getattr(advert, f) for f in REVISION_FIELDS if f != "contacts"}
    snap["contacts"] = [c["name"] for c in (advert.contacts or [])]
    return snap


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
        stats = view_stats(advert) if advert else None
        return render_template("edit.html", advert=advert, form=None, errors=[], stats=stats)

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

    before = _snapshot(advert) if advert is not None else None
    if advert is None:
        advert = Advert(corporation_id=corp.corporation_id)
        db.session.add(advert)
    advert.title = title
    advert.body = body
    for name in choices.MULTI:
        setattr(advert, name, _selected(form, name))
    advert.newbro_friendly = bool(form.get("newbro"))
    advert.alpha_friendly = bool(form.get("alpha"))
    commitment = form.get("commitment", "")
    advert.commitment = commitment if commitment in choices.COMMITMENT else None
    advert.srp = bool(form.get("srp"))
    advert.min_sp = min_sp
    advert.discord_url = discord_url
    advert.website_url = website_url
    advert.ingame_channel = ingame_channel
    advert.contacts = contacts
    advert.active = bool(form.get("active"))
    advert.updated_by = g.character
    advert.last_confirmed_at = datetime.now(timezone.utc)
    after = _snapshot(advert)
    changed = [f for f in REVISION_FIELDS if before is None or before[f] != after[f]]
    if changed:
        db.session.add(AdvertRevision(
            advert=advert, editor_id=g.character.character_id,
            changed=["created"] if before is None else changed, snapshot=after,
        ))
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
    if not limits.allow("preview", g.character.character_id):
        return "Too many previews. Wait a few minutes and try again.", 429, {"Content-Type": "text/plain; charset=utf-8"}
    body = request.form.get("body", "")[:BODY_MAX]
    return render_markdown(body), 200, {"Content-Type": "text/html; charset=utf-8"}


@bp.route("/advert/ingame-description")
def ingame_description():
    """The corp's in-game description converted to Markdown, for the editor."""
    if not is_recruiter(g.character):
        abort(403)
    if not limits.allow("import", g.character.character_id):
        return jsonify(error="Too many imports. Wait a few minutes and try again."), 429
    try:
        # Only re-fetched from ESI once the cached copy has expired.
        corp = refresh_corporation(g.character.corporation_id)
        db.session.commit()
    except requests.RequestException:
        return jsonify(error="Couldn't reach EVE to fetch your in-game description, try again shortly."), 502
    return jsonify(markdown=eve_description_to_markdown(corp.description))


PASTE_MAX = 20_000


@bp.route("/advert/convert-paste", methods=["POST"])
def convert_paste():
    """Advert text pasted from the game client, converted to Markdown for the editor."""
    if not is_recruiter(g.character):
        abort(403)
    if not limits.allow("convert", g.character.character_id):
        return jsonify(error="Too many conversions. Wait a few minutes and try again."), 429
    raw = request.form.get("text", "")
    if len(raw) > PASTE_MAX:
        return jsonify(error=f"That's too long to convert (over {PASTE_MAX:,} characters)."), 400
    markdown, fmt = pasted_advert_to_markdown(raw)
    return jsonify(markdown=markdown, format=fmt)
