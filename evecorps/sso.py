"""EVE SSO (OAuth2 authorization code flow) helpers."""

import time
from urllib.parse import urlencode

import jwt
import requests
from flask import current_app

METADATA_URL = "https://login.eveonline.com/.well-known/oauth-authorization-server"
ACCEPTED_ISSUERS = ("https://login.eveonline.com", "https://login.eveonline.com/", "login.eveonline.com")
METADATA_TTL = 3600
TIMEOUT = 10

_metadata: dict | None = None
_metadata_expires = 0.0
_jwk_clients: dict[str, jwt.PyJWKClient] = {}


def metadata() -> dict:
    global _metadata, _metadata_expires
    if _metadata is None or time.time() > _metadata_expires:
        resp = requests.get(
            METADATA_URL,
            headers={"User-Agent": current_app.config["ESI_USER_AGENT"]},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        _metadata = resp.json()
        _metadata_expires = time.time() + METADATA_TTL
    return _metadata


def authorize_url(state: str, scopes: list[str] | None = None) -> str:
    cfg = current_app.config
    query = urlencode({
        "response_type": "code",
        "client_id": cfg["EVE_CLIENT_ID"],
        "redirect_uri": cfg["EVE_CALLBACK_URL"],
        "scope": " ".join(scopes or []),
        "state": state,
    })
    return f"{metadata()['authorization_endpoint']}?{query}"


def exchange_code(code: str) -> dict:
    cfg = current_app.config
    resp = requests.post(
        metadata()["token_endpoint"],
        auth=(cfg["EVE_CLIENT_ID"], cfg["EVE_CLIENT_SECRET"]),
        data={"grant_type": "authorization_code", "code": code},
        headers={"User-Agent": cfg["ESI_USER_AGENT"]},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def validate_token(access_token: str) -> dict:
    """Verify signature, issuer, audience and expiry; return the claims."""
    jwks_uri = metadata()["jwks_uri"]
    client = _jwk_clients.get(jwks_uri)
    if client is None:
        client = _jwk_clients[jwks_uri] = jwt.PyJWKClient(
            jwks_uri, headers={"User-Agent": current_app.config["ESI_USER_AGENT"]}
        )
    key = client.get_signing_key_from_jwt(access_token)
    claims = jwt.decode(
        access_token,
        key=key.key,
        algorithms=["RS256"],
        audience="EVE Online",
        issuer=ACCEPTED_ISSUERS,
        options={"require": ["exp", "sub", "iss", "aud"]},
    )
    aud = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    if current_app.config["EVE_CLIENT_ID"] not in aud:
        raise jwt.InvalidAudienceError("token not issued for this client")
    return claims


def character_id_from_claims(claims: dict) -> int:
    # sub is "CHARACTER:EVE:<id>"
    kind, _, char_id = claims["sub"].rpartition(":")
    if kind != "CHARACTER:EVE":
        raise ValueError(f"unexpected sub claim: {claims['sub']!r}")
    return int(char_id)
