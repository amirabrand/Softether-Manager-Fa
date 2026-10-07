"""Request dependencies: who is asking.

The token travels in an HttpOnly cookie (what the browser uses) or an
``Authorization: Bearer`` header (what scripts use). Both carry the same
HMAC-signed claims from :mod:`app.security`.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, HTTPException, Request

from .db import get_db
from .security import read_token

SESSION_COOKIE = "sem_session"


def _token_from_request(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def require_user(request: Request) -> dict[str, Any]:
    token = _token_from_request(request)
    claims = read_token(token) if token else None
    if not claims:
        raise HTTPException(status_code=401, detail="Not signed in.")
    if claims.get("scope") == "portal":
        # A customer-portal session is not a panel session, whatever else it
        # claims. (Its uid is outside the PanelUser space anyway; this is the
        # explicit belt to that brace.)
        raise HTTPException(status_code=401, detail="Not signed in.")
    user = get_db().query_one(
        'SELECT "UserID", "Username", "Role" FROM "PanelUser" '
        'WHERE "UserID" = :id AND "IsDeleted" = 0',
        {"id": claims.get("uid")},
    )
    if user is None:
        raise HTTPException(status_code=401, detail="This account no longer exists.")
    user = dict(user)
    # A reseller sign-in reaches exactly one corner of the API: its own
    # reseller desk (and the auth plumbing). Everything else -- hubs, sales,
    # settings, other resellers -- answers 403, here, before any router runs.
    if user["Role"] == "reseller":
        path = request.url.path
        allowed = ("/api/v1/reseller", "/api/v1/auth/me", "/api/v1/auth/logout",
                   "/api/v1/auth/password", "/api/v1/auth/state")
        if not path.startswith(allowed):
            raise HTTPException(status_code=403, detail="Reseller sign-ins only reach the reseller desk.")
    return user


CurrentUser = Depends(require_user)
