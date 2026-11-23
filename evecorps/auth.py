import secrets
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, abort, current_app, flash, g, redirect, request, session, url_for
from jwt import PyJWTError
from sqlalchemy import select

from . import esi, sso
from .extensions import db
from .models import Advert, Alliance, Character, Corporation

bp = Blueprint("auth", __name__)

ROLES_SCOPE = "esi-characters.read_corporation_roles.v1"
# How long a roles check stays good before we ask the recruiter to log in again.
RECRUITER_TTL = timedelta(hours=24)
# How long a corp-membership check (for moderators) stays good.
MODERATOR_TTL = timedelta(hours=24)


def load_current_character() -> None:
    char_id = session.get("character_id")
    g.character = db.session.get(Character, char_id) if char_id else None


@bp.route("/sso/login")
def login():
    state = secrets.token_urlsafe(24)
    session["sso_state"] = state
    session["sso_next"] = request.args.get("next", "")
    # Plain login is identity only. Recruiters additionally grant the roles scope,
    # used once at login to check Director/Personnel_Manager; the token is not kept.
    recruiter = request.args.get("purpose") == "recruiter"
    session["sso_recruiter"] = recruiter
    return redirect(sso.authorize_url(state, [ROLES_SCOPE] if recruiter else []))


@bp.route("/sso/callback")
def callback():
    expected = session.pop("sso_state", None)
    wants_recruiter = session.pop("sso_recruiter", False)
    back = _safe_next(session.pop("sso_next", ""))
    state = request.args.get("state", "")
    if not expected or not secrets.compare_digest(state, expected):
        # Stale tab, back button, or a replayed/forged callback: never log in,
        # but send the user somewhere useful rather than a bare 400.
        flash("That login link has expired, please log in again.", "warning")
        return redirect(back)
    if request.args.get("error") or not request.args.get("code"):
        # The user cancelled or declined at EVE's login page.
        flash("Login cancelled.", "info")
        return redirect(back)
    code = request.args["code"]

    try:
        tokens = sso.exchange_code(code)
        claims = sso.validate_token(tokens["access_token"])
        char_id = sso.character_id_from_claims(claims)
    except (requests.RequestException, PyJWTError, KeyError, ValueError):
        current_app.logger.exception("SSO login failed")
        flash("Login with EVE Online failed, please try again.", "danger")
        return redirect(back)

    character = db.session.get(Character, char_id)
    if character is None:
        character = Character(character_id=char_id, name=claims["name"])
        db.session.add(character)
    character.name = claims["name"]
    character.owner_hash = claims.get("owner")
    character.last_login = datetime.now(timezone.utc)

    corp_ok = True
    try:
        affiliation = esi.get_affiliation(char_id)
        character.corporation = refresh_corporation(affiliation["corporation_id"])
        character.corp_verified_at = datetime.now(timezone.utc)
    except requests.RequestException:
        # Not fatal for a plain login, but the stored corp may now be stale.
        corp_ok = False
        current_app.logger.warning("ESI affiliation lookup failed for %s", char_id, exc_info=True)

    if wants_recruiter:
        if corp_ok:
            check_recruiter(character, tokens["access_token"], claims)
        else:
            character.recruiter_corp_id = character.recruiter_verified_at = None
            flash("Couldn't look up your corporation with EVE, please try again.", "warning")

    db.session.commit()

    flashes = session.get("_flashes")
    session.clear()  # fresh session on login
    session["character_id"] = char_id
    if flashes:
        session["_flashes"] = flashes
    return redirect(back)


def _safe_next(nxt: str) -> str:
    """Only allow redirects to paths on this site."""
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return url_for("main.index")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.index"))


def refresh_corporation(corporation_id: int) -> Corporation:
    """Return the corp row, re-fetching from ESI only once its cache has expired."""
    corp = db.session.get(Corporation, corporation_id)
    now = datetime.now(timezone.utc)
    if corp is not None and corp.esi_expires and corp.esi_expires > now:
        _refresh_alliance_quietly(corp.alliance_id)
        return corp

    data, expires = esi.get_corporation(corporation_id)
    if corp is None:
        corp = Corporation(corporation_id=corporation_id)
        db.session.add(corp)
    corp.name = data["name"]
    corp.ticker = data["ticker"]
    corp.alliance_id = data.get("alliance_id")
    corp.member_count = data.get("member_count")
    corp.ceo_id = data.get("ceo_id")
    corp.description = data.get("description")
    corp.esi_expires = expires
    _refresh_alliance_quietly(corp.alliance_id)
    return corp


def refresh_alliance(alliance_id: int) -> Alliance:
    """Return the alliance row, re-fetching from ESI only once its cache has expired."""
    alliance = db.session.get(Alliance, alliance_id)
    if alliance is not None and alliance.esi_expires and alliance.esi_expires > datetime.now(timezone.utc):
        return alliance
    data, expires = esi.get_alliance(alliance_id)
    if alliance is None:
        alliance = Alliance(alliance_id=alliance_id)
        db.session.add(alliance)
    alliance.name = data["name"]
    alliance.ticker = data["ticker"]
    alliance.esi_expires = expires
    return alliance


def _refresh_alliance_quietly(alliance_id: int | None) -> None:
    """Alliance names are nice to have; never let a lookup failure break the caller."""
    if alliance_id is None:
        return
    try:
        refresh_alliance(alliance_id)
    except requests.RequestException:
        current_app.logger.warning("ESI alliance lookup failed for %s", alliance_id, exc_info=True)


def check_recruiter(character: Character, access_token: str, claims: dict) -> None:
    """Record whether this character may manage its corp's advert."""
    character.recruiter_corp_id = None
    character.recruiter_verified_at = None
    corp = character.corporation
    assert corp is not None
    scopes = claims.get("scp") or []
    if isinstance(scopes, str):
        scopes = [scopes]

    allowed = corp.ceo_id == character.character_id
    if not allowed and ROLES_SCOPE in scopes:
        try:
            roles = esi.get_roles(character.character_id, access_token)
            allowed = bool(esi.RECRUITER_ROLES & set(roles.get("roles", [])))
        except requests.RequestException:
            current_app.logger.warning("ESI roles lookup failed for %s", character.character_id, exc_info=True)
            flash("Couldn't check your corporation roles with EVE, please try again.", "warning")
            return

    if allowed:
        now = datetime.now(timezone.utc)
        character.recruiter_corp_id = corp.corporation_id
        character.recruiter_verified_at = now
        # A verified recruiter logging in shows the corp is still active.
        advert = db.session.scalar(select(Advert).where(Advert.corporation_id == corp.corporation_id))
        if advert is not None:
            advert.last_confirmed_at = now
    else:
        flash(f"Only directors and recruiters (Personnel Manager role) of {corp.name} can manage its advert.", "warning")


def is_recruiter(character: Character | None) -> bool:
    if character is None or character.recruiter_verified_at is None:
        return False
    if character.recruiter_corp_id != character.corporation_id:
        return False
    return datetime.now(timezone.utc) - character.recruiter_verified_at < RECRUITER_TTL


def is_moderator(character: Character | None) -> bool:
    """Member of a moderator corp, with membership confirmed by ESI in the last day."""
    if character is None or character.corp_verified_at is None:
        return False
    if character.corporation_id not in current_app.config["MODERATOR_CORP_IDS"]:
        return False
    return datetime.now(timezone.utc) - character.corp_verified_at < MODERATOR_TTL


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def check_csrf() -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, "Invalid or missing form token, please reload the page and try again.")
