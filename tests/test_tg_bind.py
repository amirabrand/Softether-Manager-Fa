"""The Telegram deep-link bind codes: one code, one chat, one account.

The portal and the operator hand out ``t.me?start=b<code>`` links instead
of asking customers to type their username into the bot. These tests pin
the properties that make such a link safe to drop in a chat window: an
unguessable code, one-time redemption, a day's life, an url that fits
Telegram's 64-character start payload, and silence when the bot is off.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.sqlite_db import SQLiteDatabase  # noqa: E402
from app.services import telegram as tg  # noqa: E402


@pytest.fixture()
def tdb(tmp_path, monkeypatch):
    """A throwaway database wired into the telegram service's get_db."""
    db = SQLiteDatabase(str(tmp_path / "bind.db"))
    db.init_schema()
    monkeypatch.setattr(tg, "get_db", lambda: db)
    return db


def _expired_row(db, code: str) -> None:
    from app.db import utc_now

    db.execute(
        'INSERT INTO "TgBindCode"("Code", "HubName", "UserName", "Source", '
        '"CreatedDate", "ExpiresAt") VALUES (:c, :h, :u, :s, :now, :exp)',
        {"c": code, "h": "DEFAULT", "u": "someone", "s": "test",
         "now": utc_now(), "exp": "2000-01-01T00:00:00+00:00"},
    )


# --- the code lifecycle ------------------------------------------------------


def test_mint_then_redeem_roundtrip_once(tdb):
    code = tg.mint_bind_code("DEFAULT", "AMIRIT", source="portal")
    assert tg._BIND_CODE_RE.match(code)  # 12 urlsafe characters
    row = tg.redeem_bind_code(code)
    assert row == {"HubName": "DEFAULT", "UserName": "AMIRIT"}
    assert tg.redeem_bind_code(code) is None  # one-time by deletion


def test_redeemed_code_is_gone_from_the_table(tdb):
    code = tg.mint_bind_code("DEFAULT", "AMIRIT")
    assert tg.redeem_bind_code(code) is not None
    assert tdb.query_one('SELECT COUNT(*) AS n FROM "TgBindCode"')["n"] == 0


def test_other_codes_survive_a_redemption(tdb):
    a = tg.mint_bind_code("DEFAULT", "AMIRIT")
    b = tg.mint_bind_code("DEFAULT", "SARA")
    assert tg.redeem_bind_code(a) is not None
    assert tg.redeem_bind_code(b) == {"HubName": "DEFAULT", "UserName": "SARA"}


def test_expired_code_redeems_nothing(tdb):
    _expired_row(tdb, "oldcode0000001")
    assert tg.redeem_bind_code("oldcode0000001") is None


def test_mint_sweeps_expired_rows(tdb):
    _expired_row(tdb, "oldcode0000002")
    tg.mint_bind_code("DEFAULT", "AMIRIT")
    assert tdb.query_one(
        "SELECT 1 FROM \"TgBindCode\" WHERE \"Code\" = 'oldcode0000002'"
    ) is None


def test_unknown_and_malformed_codes_are_dead(tdb):
    assert tg.redeem_bind_code("aaaaaaaaaaaa") is None  # right shape, no row
    assert tg.redeem_bind_code("no-such-code00") is None  # wrong shape
    assert tg.redeem_bind_code("") is None
    assert tg.redeem_bind_code("b" + "x" * 40) is None  # a payload, not a code
    assert tg.redeem_bind_code("../../etc/passwd") is None


# --- the link the customer actually opens ------------------------------------


def test_bind_link_url_fits_telegram_start_payload(monkeypatch, tdb):
    monkeypatch.setattr(
        tg, "_status",
        {"running": True, "bot_username": "AbarOnlineBot", "error": ""},
    )
    link = tg.mint_bind_link("DEFAULT", "AMIRIT", source="portal")
    assert link["url"].startswith("https://t.me/AbarOnlineBot?start=b")
    payload = link["url"].split("start=", 1)[1]
    assert 1 <= len(payload) <= 64  # the Bot API's hard cap
    assert link["expires_minutes"] == tg.BIND_TTL_MINUTES


def test_bind_link_is_empty_while_the_bot_is_off(monkeypatch, tdb):
    for st in ({"running": False, "bot_username": "Bot", "error": ""},
               {"running": True, "bot_username": "", "error": ""}):
        monkeypatch.setattr(tg, "_status", st)
        assert tg.mint_bind_link("DEFAULT", "AMIRIT")["url"] == ""


def test_name_shape_is_the_bots_rule():
    assert tg.name_ok("AMIRIT")
    assert tg.name_ok("a.b-c_d")
    assert not tg.name_ok("has space")
    assert not tg.name_ok("«فارسی»")
    assert not tg.name_ok("")
