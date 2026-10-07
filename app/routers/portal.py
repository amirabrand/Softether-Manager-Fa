"""The customer portal: /api/v1/portal/...

A shop window, not a second panel. VPN users sign in here with the very
credentials their VPN client uses -- verified against SoftEther's own stored
hash, which the panel already keeps for embedding into connection files --
and see exactly three things: that their account exists, what it has done
(traffic, expiry, last login), and how to connect. Nothing else. No hub
roster, no sales ledger, no server facts, and never an address the operator
has marked private (the same visibility rule the download dialog and the
bot obey).

The session is a signed token with ``scope: "portal"``. The panel's own
``require_user`` refuses that scope on sight, and the portal's uid lives
outside the PanelUser key space, so neither side can wander into the other.
Login failures are rate-limited per source address, and every answer for a
bad credential is the same generic line -- no oracle for which usernames
exist.
"""
from __future__ import annotations

import hmac
import threading
import time
from collections import deque
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..audit import record
from ..security import make_token, read_token
from ..se import rpc
from ..settings_store import get_setting
from ..version import get_version

router = APIRouter(prefix="/portal", tags=["portal"])

Wire = dict[str, Any]

#: A customer session lasts a working day, not a browser lifetime.
PORTAL_TOKEN_MINUTES = 60 * 8


def _enabled() -> bool:
    """The operator's switch; the seed is on, and a missing row seeds it."""
    try:
        value = get_setting("user_portal_enabled")
    except Exception:  # noqa: BLE001 - a closed shop is the safe default
        return False
    return value is None or bool(value)


def _hub() -> str:
    try:
        return str(get_setting("portal_hub") or "DEFAULT")
    except Exception:  # noqa: BLE001
        return "DEFAULT"


# ---------------------------------------------------------------------------
# the portal session
# ---------------------------------------------------------------------------


def require_portal_user(request: Request) -> dict[str, Any]:
    """The signed-in VPN user a portal request belongs to."""
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    claims = read_token(token) if token else None
    if not claims or claims.get("scope") != "portal" or not str(claims.get("sub") or "").strip():
        raise HTTPException(status_code=401, detail="Not signed in.")
    return {"hub": str(claims.get("hub") or _hub()), "username": str(claims["sub"]).strip()}


PortalUser = Depends(require_portal_user)


# ---------------------------------------------------------------------------
# brute-force throttle
# ---------------------------------------------------------------------------


class _FailureWindow:
    """A sliding window of failed logins per source address.

    Pure wall-clock, no daemon: an entry is a timestamp, the window is a
    deque, and memory stays bounded because addresses with only stale hits
    are dropped on the next write once the table grows past a page.
    """

    def __init__(self, limit: int = 8, window_seconds: float = 15 * 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] > self.window:
            hits.popleft()

    def blocked(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return False
            self._prune(hits, now)
            if not hits:
                del self._hits[key]
                return False
            return len(hits) >= self.limit

    def record(self, key: str, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            hits.append(now)
            self._prune(hits, now)
            if len(self._hits) > 4096:
                stale = [k for k, d in self._hits.items() if not d or now - d[-1] > self.window]
                for k in stale:
                    del self._hits[k]


_FAILURES = _FailureWindow()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_WRONG = "Wrong username or password."


def _bad_credentials() -> HTTPException:
    """One answer for every way in. No oracle for which usernames exist."""
    return HTTPException(status_code=401, detail=_WRONG)


def _public_hosts() -> list[Wire]:
    """The shop window: enabled, customer-facing addresses only -- the exact
    filter the public endpoint, the download dialog and the bot obey."""
    items = get_setting("connection_subdomains") or []
    out: list[Wire] = []
    for item in items:
        if item.get("enabled", True) and item.get("public", True):
            out.append(
                {
                    "host": str(item.get("host") or "").strip(),
                    "port": int(item.get("port") or 443),
                    "note": str(item.get("note") or ""),
                }
            )
    return [h for h in out if h["host"]]


def _canonical_name(hub: str, username: str) -> Optional[str]:
    """The exact spelling of this user as the server stores it, or None.

    A login typed ``amirit`` must reach the account ``AMIRIT``: the exact
    name is tried first, and a case-folded scan of the roster settles the
    rest. Nothing else about the roster leaks -- the caller answers None
    with the same generic line as a wrong password.
    """
    try:
        got = rpc("GetUser", {"HubName_str": hub, "Name_str": username})
        return str(got.get("Name_str") or username)
    except HTTPException as exc:
        # "No such user" arrives as 404 -- and, depending on which error the
        # server picks, as 400/422 too. All of them mean "ask the roster";
        # anything else (server down, not configured) is a real failure and
        # keeps its own shape.
        if exc.status_code not in (400, 404, 422):
            raise
    listing = rpc("EnumUser", {"HubName_str": hub})
    wanted = username.strip().casefold()
    for item in listing.get("UserList", []):
        if str(item.get("Name_str", "")).strip().casefold() == wanted:
            return str(item.get("Name_str")).strip()
    return None


def _verified(hub: str, name: str, password: str) -> bool:
    """SoftEther's own test: SHA-0(password + UPPER(name)) against the stored
    credential -- refreshed once from the server's configuration when the
    cache disagrees, exactly like the admin download dialog does."""
    from ..credentials import credential_from_server_config, obtain_credential, save_credential
    from ..vpnfile import hashed_password

    typed = hashed_password(password, name)
    try:
        stored = obtain_credential(hub, name)
    except Exception:  # noqa: BLE001 - a config read failing must not leak shapes
        stored = None
    if not stored:
        return False
    if hmac.compare_digest(stored, typed):
        return True
    try:
        fresh = credential_from_server_config(hub, name)
    except Exception:  # noqa: BLE001
        return False
    if fresh:
        save_credential(hub, name, fresh)
        return hmac.compare_digest(fresh, typed)
    return False


def _clean_dt(value: Any) -> Optional[str]:
    """SoftEther's ``never`` stamps (year 1 / 1970, zero) become null."""
    text = str(value or "").strip()
    if not text or text.startswith("0001-") or text.startswith("1970-01-01T00:00:00"):
        return None
    return text


def _bot_username() -> str:
    try:
        from ..services import telegram as tg

        return str((getattr(tg, "_status", None) or {}).get("bot_username") or "")
    except Exception:  # noqa: BLE001 - the bot is optional here
        return ""


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


class PortalLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def portal_login(body: PortalLogin, request: Request) -> Wire:
    if not _enabled():
        raise HTTPException(status_code=404, detail="The portal is turned off.")
    ip = _client_ip(request)
    if _FAILURES.blocked(ip):
        raise HTTPException(
            status_code=429, detail="Too many attempts. Wait a few minutes and try again."
        )
    hub = _hub()
    name = _canonical_name(hub, body.username.strip())
    if not name or not _verified(hub, name, body.password):
        _FAILURES.record(ip)
        raise _bad_credentials()
    token = make_token(0, name, PORTAL_TOKEN_MINUTES, extra={"scope": "portal", "hub": hub})
    record({"UserID": None, "Username": name}, "portal.login", "vpn_user", name, f"hub {hub}")
    return {"token": token, "username": name, "hub": hub, "expires_minutes": PORTAL_TOKEN_MINUTES}


@router.get("/me")
def portal_me(user: dict = PortalUser) -> Wire:
    """One customer's own shop window: status, usage, expiry, addresses."""
    if not _enabled():
        raise HTTPException(status_code=404, detail="The portal is turned off.")
    hub, name = user["hub"], user["username"]

    record_user = rpc("GetUser", {"HubName_str": hub, "Name_str": name})
    from ..routers.se_hub import online_usernames

    try:
        online = name.casefold() in online_usernames(hub)
    except Exception:  # noqa: BLE001 - an unreachable hub is not an error page
        online = False

    quota_wire: Optional[Wire] = None
    try:
        from ..services import quota as quota_service

        quota_service.refresh("user", hub, name)
        quota_wire = quota_service.get("user", hub, name)
    except Exception:  # noqa: BLE001 - usage is a nice-to-have, not a gate
        quota_wire = None

    return {
        "username": name,
        "hub": hub,
        "online": online,
        "auth_type": int(record_user.get("AuthType_u32", 1)),
        "created": _clean_dt(record_user.get("CreatedTime_dt")),
        "expire": _clean_dt(record_user.get("ExpireTime_dt")),
        "last_login": _clean_dt(record_user.get("LastLoginTime_dt")),
        "note": str(record_user.get("Note_utf") or ""),
        "group": str(record_user.get("GroupName_str") or ""),
        "num_login": int(record_user.get("NumLogin_u32") or 0),
        "quota": quota_wire,
        "hosts": _public_hosts(),
        "bot_username": _bot_username(),
        "panel_version": get_version(),
    }


@router.get("/connection-file")
def portal_connection_file(
    kind: str = "ovpn",
    host: str = "",
    user: dict = PortalUser,
) -> Wire:
    """A ready-to-import connection file for the signed-in customer.

    Only customer-facing addresses are ever written into it, and only one the
    shop window already advertises -- a private hop can be neither picked by
    accident nor smuggled in by parameter. The SoftEther file embeds the
    stored hash (no plaintext exists anywhere in this exchange); the OpenVPN
    profile cannot use a hash, so it ships with a bare auth-user-pass and the
    client asks once on first connect.
    """
    if not _enabled():
        raise HTTPException(status_code=404, detail="The portal is turned off.")
    hub, name = user["hub"], user["username"]

    kind = kind.strip().lower().lstrip(".")
    if kind not in ("ovpn", "vpn"):
        raise HTTPException(status_code=422, detail="Unknown file kind; use ovpn or vpn.")

    hosts = _public_hosts()
    if not hosts:
        raise HTTPException(
            status_code=409, detail="No connection address is published for the portal yet."
        )
    wanted = host.strip()
    chosen = next((h for h in hosts if h["host"] == wanted), None)
    if chosen is None:
        if wanted:
            raise HTTPException(status_code=403, detail="That connection address is not offered.")
        chosen = hosts[0]

    record_user = rpc("GetUser", {"HubName_str": hub, "Name_str": name})
    auth_type = int(record_user.get("AuthType_u32", 1))

    from ..settings_store import get_setting as gs
    from ..vpnfile import (
        DEFAULT_ACCOUNT_NAME_TEMPLATE,
        DEFAULT_FILENAME_TEMPLATE,
        normalize_options,
        render_name,
        safe_filename,
    )

    names = {"hub": hub, "username": name, "host": chosen["host"], "port": chosen["port"]}
    account = render_name(
        str(gs("vpn_account_name_template") or DEFAULT_ACCOUNT_NAME_TEMPLATE), **names
    )
    file_base = render_name(
        str(gs("vpn_filename_template") or DEFAULT_FILENAME_TEMPLATE), **names
    )

    if kind == "vpn":
        from ..credentials import obtain_credential
        from ..vpnfile import build_vpn_file

        password_hash = obtain_credential(hub, name) or ""
        if not password_hash:
            raise HTTPException(
                status_code=409,
                detail="This account cannot be packed into a SoftEther file automatically; "
                "ask support for your connection file.",
            )
        content = build_vpn_file(
            host=chosen["host"],
            port=chosen["port"],
            hub=hub,
            username=name,
            user_auth_type=auth_type,
            account_name=account,
            password_hash=password_hash,
            options=normalize_options(gs("vpn_template")),
        )
        embedded = True
    else:
        import base64 as _b64

        from ..vpnfile import build_ovpn_file, extract_remote_access_config

        raw = rpc("MakeOpenVpnConfigFile", {"ServerName_str": chosen["host"]})
        base = extract_remote_access_config(_b64.b64decode(raw.get("Buffer_bin", "") or ""))
        content = build_ovpn_file(
            base=base,
            host=chosen["host"],
            port=chosen["port"],
            hub=hub,
            username=name,
            embed=False,
        )
        embedded = False

    record(
        {"UserID": None, "Username": name},
        "portal.vpn_file_generated",
        "vpn_user",
        name,
        f"hub {hub} -> {chosen['host']}:{chosen['port']} {kind}"
        + (" (credential embedded)" if embedded else " (credential prompted)"),
    )
    return {
        "filename": safe_filename(file_base, f".{kind}"),
        "content": content,
        "embedded": embedded,
        "host": chosen["host"],
        "port": chosen["port"],
    }
