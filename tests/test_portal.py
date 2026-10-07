"""The portal's pure logic, without a server: tokens, throttle, hashing.

The portal's server-side safety rests on four small pieces -- a portal-scope
token the panel auth refuses, a per-address failure window, SoftEther's
SHA-0 credential check, and the public-hosts filter. These tests pin them.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers.portal import _FailureWindow, _public_hosts  # noqa: E402
from app.security import make_token, read_token  # noqa: E402
from app.vpnfile import hashed_password  # noqa: E402


# --- tokens -----------------------------------------------------------------


def test_portal_token_carries_scope_and_roundtrips():
    token = make_token(0, "AMIRIT", 5, extra={"scope": "portal", "hub": "DEFAULT"})
    claims = read_token(token)
    assert claims is not None
    assert claims["sub"] == "AMIRIT"
    assert claims["uid"] == 0
    assert claims["scope"] == "portal"
    assert claims["hub"] == "DEFAULT"


def test_panel_tokens_have_no_scope():
    claims = read_token(make_token(1, "admin", 5))
    assert claims is not None
    assert "scope" not in claims


def test_tampered_portal_token_is_rejected():
    token = make_token(0, "AMIRIT", 5, extra={"scope": "portal"})
    body, sig = token.split(".")
    assert read_token(f"{body}.{'A' * len(sig)}") is None
    assert read_token(f"{body[:-1]}x.{sig}") is None


# --- the failure window ------------------------------------------------------


def test_failure_window_blocks_after_limit():
    w = _FailureWindow(limit=3, window_seconds=100)
    assert not w.blocked("1.2.3.4", now=0)
    for tick in (1, 2, 3):
        w.record("1.2.3.4", now=tick)
    assert w.blocked("1.2.3.4", now=4)


def test_failure_window_forgets_after_the_window():
    w = _FailureWindow(limit=3, window_seconds=100)
    for tick in (1, 2, 3):
        w.record("1.2.3.4", now=tick)
    assert w.blocked("1.2.3.4", now=50)
    assert not w.blocked("1.2.3.4", now=1000)


def test_failure_window_is_per_address():
    w = _FailureWindow(limit=2, window_seconds=100)
    w.record("1.1.1.1", now=1)
    w.record("1.1.1.1", now=2)
    assert w.blocked("1.1.1.1", now=3)
    assert not w.blocked("2.2.2.2", now=3)


# --- the credential check ----------------------------------------------------


def test_hashed_password_matches_softether_convention():
    # SHA-0(password + UPPER(username)); the same value the server stores and
    # a .vpn file embeds. Case of the username must not matter.
    assert hashed_password("s3cret", "AMIRIT") == hashed_password("s3cret", "amirit")
    assert hashed_password("s3cret", "AMIRIT") != hashed_password("S3cret", "AMIRIT")


def test_stale_cache_can_be_overruled_by_fresh_config(monkeypatch):
    """When the panel's cached hash disagrees with the typed password, the
    portal re-reads the server's config before deciding -- a password changed
    at the console still logs in; a genuinely wrong one still fails."""
    import app.routers.portal as portal

    calls = {"fresh": 0}

    def fake_obtain(hub, name):
        return "stale-hash"

    def fake_fresh(hub, name):
        calls["fresh"] += 1
        return hashed_password("the-real-password", name)

    monkeypatch.setattr("app.credentials.obtain_credential", fake_obtain)
    monkeypatch.setattr("app.credentials.credential_from_server_config", fake_fresh)

    assert portal._verified("DEFAULT", "AMIRIT", "the-real-password") is True
    assert calls["fresh"] == 1
    assert portal._verified("DEFAULT", "AMIRIT", "wrong-password") is False


# --- username resolution -----------------------------------------------------


def test_missing_user_is_not_distinguishable(monkeypatch):
    """A username the server never heard of must reach the same generic
    answer as a wrong password -- GetUser failing (as 400 or 404) only ever
    falls through to the roster scan, never out as its own error."""
    import fastapi

    import app.routers.portal as portal

    def fake_rpc(method, params=None):
        if method == "GetUser":
            raise fastapi.HTTPException(status_code=400, detail="server's odd shape for 'missing'")
        if method == "EnumUser":
            return {"UserList": [{"Name_str": "OTHER"}, {"Name_str": "AMIRIT"}]}
        raise AssertionError(method)

    monkeypatch.setattr(portal, "rpc", fake_rpc)
    assert portal._canonical_name("DEFAULT", "Ghost") is None
    assert portal._canonical_name("DEFAULT", "amirit") == "AMIRIT"


def test_server_outage_is_not_swallowed(monkeypatch):
    """A dead server is a 502 the client can show, not a fake 'wrong password'."""
    import fastapi

    import app.routers.portal as portal

    def fake_rpc(method, params=None):
        raise fastapi.HTTPException(status_code=502, detail="down")

    monkeypatch.setattr(portal, "rpc", fake_rpc)
    try:
        portal._canonical_name("DEFAULT", "AMIRIT")
        raised = False
    except fastapi.HTTPException as exc:
        raised = exc.status_code == 502
    assert raised
