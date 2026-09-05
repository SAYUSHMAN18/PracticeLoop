"""Google Sign-In: a plain OAuth 2.0 authorization-code exchange over
httpx, not a dependency on authlib or similar for one provider and three
HTTP calls. build_authorize_url/exchange_code_for_userinfo are the only
two functions that talk to Google -- tests monkeypatch the latter the same
way every other outbound-call service in this app gets faked.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.config import settings

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class OAuthExchangeFailed(Exception):
    """The code-for-token exchange or the userinfo call failed, or Google
    returned an email address it hasn't itself verified."""


def is_configured() -> bool:
    return bool(settings.google_oauth_client_id.strip() and settings.google_oauth_client_secret.strip())


def redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/auth/google/callback"


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_userinfo(code: str) -> dict:
    """The authorization code from Google's redirect -> {"email", "name"}
    for a Google-verified identity. Raises OAuthExchangeFailed for
    anything short of that -- a bad/expired code, a provider hiccup, or
    (defense in depth; Google's own userinfo endpoint only exists for
    accounts it has already verified) an unverified email."""
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        try:
            token_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OAuthExchangeFailed("Google rejected the sign-in code.") from exc

        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise OAuthExchangeFailed("Google didn't return an access token.")

        userinfo_resp = await client.get(_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        try:
            userinfo_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OAuthExchangeFailed("Couldn't read the Google account's profile.") from exc
        userinfo = userinfo_resp.json()

    if not userinfo.get("email_verified"):
        raise OAuthExchangeFailed("That Google account's email isn't verified.")
    email = str(userinfo.get("email") or "").strip().lower()
    if not email:
        raise OAuthExchangeFailed("Google didn't return an email address.")
    name = str(userinfo.get("name") or email.split("@")[0]).strip()
    return {"email": email, "name": name}
