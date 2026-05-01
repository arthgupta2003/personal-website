"""Google OAuth sign-in for Calyx.

Handles the OAuth 2.0 / OpenID Connect dance: build the consent URL, exchange
the auth code on return, verify the ID token, and surface the user profile.
"""

from __future__ import annotations

from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests


SIGNIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _client_config(client_id: str, client_secret: str, redirect_uri: str) -> dict:
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def build_login_url(client_id: str, client_secret: str, redirect_uri: str, state: str) -> tuple[str, str]:
    """Returns (authorization_url, code_verifier). The verifier must be persisted
    across the redirect so the callback can pass it to fetch_token (PKCE)."""
    flow = Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri),
        scopes=SIGNIN_SCOPES,
    )
    flow.redirect_uri = redirect_uri
    auth_url, _ = flow.authorization_url(
        access_type="online",
        prompt="select_account",
        state=state,
        include_granted_scopes="true",
    )
    return auth_url, flow.code_verifier or ""


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str, code_verifier: str = "") -> dict:
    """Exchange auth code for tokens, verify the ID token, return user profile."""
    flow = Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri),
        scopes=SIGNIN_SCOPES,
    )
    flow.redirect_uri = redirect_uri
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    info = id_token.verify_oauth2_token(
        creds.id_token, google_requests.Request(), client_id
    )
    return {
        "email": (info.get("email") or "").lower(),
        "name": info.get("name", ""),
        "picture": info.get("picture", ""),
        "google_id": info.get("sub", ""),
        "email_verified": bool(info.get("email_verified", False)),
    }
