"""The panel's view of the Telegram bot: configuration, health, the link
book and the order book.

The bot itself lives in :mod:`app.services.telegram`; this router only
talks to it -- set config (which restarts the poller), read its status,
list connected accounts and pending orders, and decide orders from the
panel. Approving runs the same sale core the till uses, so a bot sale and
a till sale land on the server and in the ledger identically.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from ..audit import record
from ..db import get_db
from ..deps import CurrentUser
from ..settings_store import get_setting, set_setting
from ..services import telegram

router = APIRouter(tags=["telegram"])

Wire = dict[str, Any]

_MASK = "\u2022\u2022\u2022\u2022"


def _masked(token: str) -> str:
    if not token:
        return ""
    tail = token[-6:] if len(token) > 12 else token
    return f"{_MASK}{tail}"


class TgConfigIn(BaseModel):
    bot_token: Optional[str] = Field(default=None, max_length=64)
    admin_chat: Optional[str] = Field(default=None, max_length=32)
    pay_note: Optional[str] = Field(default=None, max_length=512)
    reminder_days: Optional[str] = Field(default=None, max_length=64)
    enabled: Optional[bool] = None


@router.get("/telegram/config")
def telegram_config(user: dict = CurrentUser) -> Wire:
    """Config (token masked) plus the poller's live status."""
    st = telegram.status()
    return {
        "BotUsername_str": st.get("bot_username", ""),
        "Running_b": bool(st.get("running")),
        "Error_utf": st.get("error", ""),
        "BotToken_mask": _masked(str(get_setting("telegram_bot_token") or "")),
        "AdminChat_str": str(get_setting("telegram_admin_chat") or ""),
        "PayNote_utf": str(get_setting("telegram_pay_note") or ""),
        "ReminderDays_str": str(get_setting("telegram_reminder_days") or ""),
        "Enabled_b": bool(get_setting("telegram_enabled")),
    }


@router.put("/telegram/config")
def put_telegram_config(body: TgConfigIn, user: dict = CurrentUser) -> Wire:
    changed: list[str] = []
    if body.bot_token is not None:
        token = body.bot_token.strip()
        if token and not token.isdigit() and ":" not in token:
            raise HTTPException(
                status_code=422,
                detail="A bot token comes from @BotFather and looks like 123456:ABC-DEF...",
            )
        set_setting("telegram_bot_token", token)
        changed.append("telegram_bot_token")
    if body.admin_chat is not None:
        chat = body.admin_chat.strip()
        if chat and not chat.lstrip("-").isdigit():
            raise HTTPException(
                status_code=422,
                detail="The admin chat id is a number (forward a message to @userinfobot to see it).",
            )
        set_setting("telegram_admin_chat", chat)
        changed.append("telegram_admin_chat")
    if body.pay_note is not None:
        set_setting("telegram_pay_note", body.pay_note.strip())
        changed.append("telegram_pay_note")
    if body.reminder_days is not None:
        days = body.reminder_days.strip()
        if days and not all(part.strip().lstrip("-").isdigit() and int(part) >= 0
                            for part in days.split(",") if part.strip()):
            raise HTTPException(
                status_code=422,
                detail="Reminder days is a comma list of numbers, like 7,3,1 (0 = expired notice).",
            )
        set_setting("telegram_reminder_days", days)
        changed.append("telegram_reminder_days")
    if body.enabled is not None:
        set_setting("telegram_enabled", body.enabled)
        changed.append("telegram_enabled")
    if changed:
        telegram.restart()
        record(user, "telegram.configured", "telegram", "", ", ".join(changed))
    return telegram_config(user)


@router.post("/telegram/reminders/run")
def run_telegram_reminders(user: dict = CurrentUser) -> Wire:
    """One reminder pass right now, regardless of the 30-minute clock."""
    sent = telegram.run_reminders_now()
    record(user, "telegram.reminders_ran", "telegram", "", f"{sent} sent")
    return {"Sent_u32": sent}


@router.get("/telegram/links")
def telegram_links(user: dict = CurrentUser) -> Wire:
    rows = get_db().query_all('SELECT * FROM "TgLink" ORDER BY "TgLinkID" DESC')
    return {"LinkList": [dict(r) for r in rows], "Count_u32": len(rows)}


@router.delete("/telegram/links/{link_id}")
def delete_telegram_link(link_id: int, user: dict = CurrentUser) -> Wire:
    db = get_db()
    row = db.query_one('SELECT * FROM "TgLink" WHERE "TgLinkID" = :i', {"i": link_id})
    if not row:
        raise HTTPException(status_code=404, detail="The link was not found.")
    db.execute('DELETE FROM "TgLink" WHERE "TgLinkID" = :i', {"i": link_id})
    record(user, "telegram.link_removed", "telegram_chat", str(row["ChatID"]),
           f"{row['HubName']}/{row['UserName']}")
    return {"OK": True}


@router.get("/telegram/orders")
def telegram_orders(status: str = Query(default="", max_length=16),
                    limit: int = Query(default=100, ge=1, le=500),
                    user: dict = CurrentUser) -> Wire:
    if status and status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=422, detail="Unknown order status.")
    sql = 'SELECT * FROM "TgOrder"'
    params: dict[str, Any] = {"n": limit}
    if status:
        sql += ' WHERE "Status" = :s'
        params["s"] = status
    sql += ' ORDER BY "TgOrderID" DESC LIMIT :n'
    rows = get_db().query_all(sql, params)
    pending = get_db().query_one(
        'SELECT COUNT(*) AS n FROM "TgOrder" WHERE "Status" = \'pending\''
    )
    return {"OrderList": [dict(r) for r in rows],
            "Pending_u32": int(pending["n"]) if pending else 0}


@router.post("/telegram/orders/{oid}/approve")
def approve_telegram_order(oid: int, user: dict = CurrentUser) -> Wire:
    try:
        row = telegram.approve_order(oid, actor=user, source="panel")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    record(user, "telegram.order_approved", "telegram_order", str(oid),
           f"{row.get('UserName', '')} {row.get('GroupName', '')}")
    return {"Order": row}


@router.post("/telegram/orders/{oid}/reject")
def reject_telegram_order(oid: int, body: dict = Body(default={}),
                          user: dict = CurrentUser) -> Wire:
    note = str((body or {}).get("note") or "")[:256]
    try:
        row = telegram.reject_order(oid, note=note, actor=user, source="panel")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    record(user, "telegram.order_rejected", "telegram_order", str(oid), note)
    return {"Order": row}
