import math
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from . import choices
from .auth import is_recruiter
from .extensions import db
from .models import Advert, Corporation

bp = Blueprint("main", __name__)

PER_PAGE = 25
TITLE_MAX = 120
BODY_MAX = 10_000
SP_MAX = 1_000_000_000


@bp.route("/")
def index():
    return render_template("index.html")


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


@bp.route("/corps")
def browse():
    stmt = (
        select(Advert)
        .join(Advert.corporation)
        .where(Advert.active.is_(True))
        .order_by(Advert.updated_at.desc())
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
    if not advert.active and not can_edit:
        abort(404)
    return render_template("corp.html", advert=advert, can_edit=can_edit)


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
    advert.active = bool(form.get("active"))
    advert.updated_by = g.character
    db.session.commit()

    flash("Advert saved." if advert.active else "Advert saved (hidden from search).", "success")
    return redirect(url_for("main.corp", corporation_id=corp.corporation_id))
