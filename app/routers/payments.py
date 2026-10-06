"""The payments router: the operator's till-side of the money, the buyer's
way in, and the gateway's webhook.

Three corners live here:

* ``/payments/...`` (operator) -- the payment book with its status filter,
  recording a card-to-card receipt on a buyer's behalf, creating an Oxapay
  invoice, deciding pending ones, poking the gateway for an answer, and the
  configuration (card facts + the gateway's key/rate/currency). The API key
  reads back masked, exactly like the bot token.
* ``/reseller/...`` (the reseller's own desk) -- request a wallet top-up by
  card-to-card, buy an Oxapay invoice for one, and read back their own
  payment history. The path gate in :mod:`app.deps` already confines a
  reseller sign-in to this prefix.
* ``POST /payments/oxapay/callback`` -- deliberately anonymous: the
  gateway cannot carry the operator's cookie. It only flips an invoice the
  gateway itself created (matched by track id) and only on a *paid* report;
  anything looser is ignored.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from ..audit import record
from ..db import get_db
from ..deps import CurrentUser
from ..settings_store import get_setting, set_setting
from ..services import payments

self_router = APIRouter(tags=["payments"])
admin_router = APIRouter(tags=["payments"])

Wire = dict[str, Any]

_MASK = "\u2022\u2022\u2022\u2022"


def _masked(key: str) -> str:
    if not key:
        return ""
    tail = key[-6:] if len(key) > 12 else key
    return f"{_MASK}{tail}"


def _fail(e: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


# ── the operator's book ──────────────────────────────────────────────────────
@admin_router.get("/payments")
def list_payments(status: str = Query(default="", max_length=16),
                  kind: str = Query(default="", max_length=16),
                  limit: int = Query(default=100, ge=1, le=500),
                  user: dict = CurrentUser) -> Wire:
    try:
        return payments.list_payments(status, kind, limit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


class Card2CardIn(BaseModel):
    amount: float = Field(gt=0, le=10**15)
    buyer: str = Field(default="", max_length=128)
    ref: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=256)


@admin_router.post("/payments/card2card")
def create_card2card(body: Card2CardIn, user: dict = CurrentUser) -> Wire:
    """Record a card-to-card receipt on a buyer's behalf -- the operator
    transcribing what the buyer reported on the phone."""
    try:
        pay = payments.create_card2card(body.amount, buyer=body.buyer, ref=body.ref,
                                        note=body.note, actor=user, source="panel")
    except ValueError as e:
        raise _fail(e) from None
    return {"PayReq": pay}


class OxapayIn(BaseModel):
    amount: float = Field(gt=0, le=10**15)
    buyer: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=256)


@admin_router.post("/payments/oxapay")
def create_oxapay(body: OxapayIn, user: dict = CurrentUser) -> Wire:
    try:
        pay = payments.create_oxapay(body.amount, buyer=body.buyer, note=body.note,
                                     actor=user, source="panel")
    except ValueError as e:
        raise _fail(e) from None
    return {"PayReq": pay}


@admin_router.post("/payments/{pid}/approve")
def approve_payment(pid: int, user: dict = CurrentUser) -> Wire:
    try:
        pay = payments.decide(pid, True, actor=user, source="panel")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"PayReq": pay}


@admin_router.post("/payments/{pid}/reject")
def reject_payment(pid: int, body: dict = Body(default={}), user: dict = CurrentUser) -> Wire:
    note = str((body or {}).get("note") or "")[:256]
    try:
        pay = payments.decide(pid, False, note=note, actor=user, source="panel")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"PayReq": pay}


@admin_router.post("/payments/{pid}/check")
def check_payment(pid: int, user: dict = CurrentUser) -> Wire:
    """Ask the Oxapay gateway where this invoice stands."""
    try:
        out = payments.check_oxapay(pid, actor=user)
    except ValueError as e:
        detail = str(e)
        raise HTTPException(status_code=409 if "already" in detail else 422,
                            detail=detail) from None
    record(user, "payment.checked", "payreq", str(pid), f"gateway says {out['GatewayStatus_utf']}")
    return out


@admin_router.get("/payments/config")
def payments_config(user: dict = CurrentUser) -> Wire:
    return {
        "CardNumber_str": str(get_setting("pay_card_number") or ""),
        "CardHolder_str": str(get_setting("pay_card_holder") or ""),
        "CardBank_str": str(get_setting("pay_card_bank") or ""),
        "OxapayEnabled_b": bool(get_setting("oxapay_enabled")),
        "OxapayApiKey_mask": _masked(str(get_setting("oxapay_api_key") or "")),
        "OxapayCurrency_str": str(get_setting("oxapay_currency") or ""),
        "OxapayRate_f64": payments._rate(),
        "PublicBaseUrl_str": str(get_setting("public_base_url") or ""),
    }


class PayConfigIn(BaseModel):
    card_number: Optional[str] = Field(default=None, max_length=64)
    card_holder: Optional[str] = Field(default=None, max_length=128)
    card_bank: Optional[str] = Field(default=None, max_length=64)
    oxapay_enabled: Optional[bool] = None
    oxapay_api_key: Optional[str] = Field(default=None, max_length=128)
    oxapay_currency: Optional[str] = Field(default=None, max_length=16)
    oxapay_rate: Optional[float] = Field(default=None, ge=0, le=10**12)
    public_base_url: Optional[str] = Field(default=None, max_length=256)


@admin_router.put("/payments/config")
def put_payments_config(body: PayConfigIn, user: dict = CurrentUser) -> Wire:
    changed: list[str] = []
    if body.card_number is not None:
        num = body.card_number.strip()
        if num and not all(c.isdigit() or c == "-" for c in num):
            raise HTTPException(status_code=422,
                                detail="Card number looks wrong — use digits and dashes.")
        set_setting("pay_card_number", num)
        changed.append("pay_card_number")
    for key, val in (("pay_card_holder", body.card_holder), ("pay_card_bank", body.card_bank)):
        if val is not None:
            set_setting(key, val.strip())
            changed.append(key)
    if body.oxapay_enabled is not None:
        set_setting("oxapay_enabled", body.oxapay_enabled)
        changed.append("oxapay_enabled")
    if body.oxapay_api_key is not None:
        key = body.oxapay_api_key.strip()
        if key and len(key) < 8:
            raise HTTPException(status_code=422,
                                detail="An Oxapay merchant API key is longer than that.")
        set_setting("oxapay_api_key", key)
        changed.append("oxapay_api_key")
    if body.oxapay_currency is not None:
        cur = body.oxapay_currency.strip().upper()
        if cur and (len(cur) > 8 or not cur.isalnum()):
            raise HTTPException(status_code=422,
                                detail="Gateway currency is a short code like USDT.")
        set_setting("oxapay_currency", cur or "USDT")
        changed.append("oxapay_currency")
    if body.oxapay_rate is not None:
        if body.oxapay_rate < 0:
            raise HTTPException(status_code=422,
                                detail="The rate is a positive number -- what one gateway unit costs.")
        set_setting("oxapay_rate", body.oxapay_rate)
        changed.append("oxapay_rate")
    if body.public_base_url is not None:
        url = body.public_base_url.strip().rstrip("/")
        if url and not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=422,
                                detail="The public URL starts with http:// or https://.")
        set_setting("public_base_url", url)
        changed.append("public_base_url")
    if changed:
        record(user, "payments.configured", "payments", "", ", ".join(changed))
    return payments_config(user)


# ── the gateway's webhook (deliberately anonymous) ───────────────────────────
@admin_router.post("/payments/oxapay/callback")
def oxapay_callback(body: dict = Body(default={})) -> Wire:
    out = payments.oxapay_callback(body or {})
    return {"OK": True, **out}


# ── the reseller's own corner of the desk ────────────────────────────────────
@self_router.get("/reseller/payconfig")
def reseller_payconfig(user: dict = CurrentUser) -> Wire:
    """What a reseller needs to move money: the card facts, the payment
    note, and whether the Oxapay door is open (no secrets here)."""
    return {
        "CardNumber_str": str(get_setting("pay_card_number") or ""),
        "CardHolder_str": str(get_setting("pay_card_holder") or ""),
        "CardBank_str": str(get_setting("pay_card_bank") or ""),
        "PayNote_utf": str(get_setting("telegram_pay_note") or ""),
        "OxapayEnabled_b": payments.oxapay_ready() and payments._rate() > 0,
        "OxapayCurrency_str": str(get_setting("oxapay_currency") or ""),
        "Currency_str": str(get_setting("sale_currency") or ""),
    }


class TopupCardIn(BaseModel):
    amount: float = Field(gt=0, le=10**15)
    ref: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=256)


@self_router.post("/reseller/topup/card2card")
def reseller_topup_card(body: TopupCardIn, user: dict = CurrentUser) -> Wire:
    try:
        pay = payments.create_card2card(
            body.amount, buyer=user["Username"], user_id=user["UserID"],
            ref=body.ref, note=body.note, actor=user, source="reseller",
        )
    except ValueError as e:
        raise _fail(e) from None
    return {"PayReq": pay}


class TopupOxaIn(BaseModel):
    amount: float = Field(gt=0, le=10**15)


@self_router.post("/reseller/topup/oxapay")
def reseller_topup_oxapay(body: TopupOxaIn, user: dict = CurrentUser) -> Wire:
    try:
        pay = payments.create_oxapay(
            body.amount, buyer=user["Username"], user_id=user["UserID"],
            note=f"wallet top-up by {user['Username']}", actor=user, source="reseller",
        )
    except ValueError as e:
        raise _fail(e) from None
    return {"PayReq": pay}


@self_router.get("/reseller/payments")
def reseller_payments(limit: int = Query(default=50, ge=1, le=200),
                      user: dict = CurrentUser) -> Wire:
    rows = [dict(r) for r in get_db().query_all(
        'SELECT * FROM "PayReq" WHERE "UserID" = :u ORDER BY "PayReqID" DESC LIMIT :n',
        {"u": user["UserID"], "n": limit},
    )]
    pending = sum(1 for r in rows if r["Status"] == "pending")
    return {"PayReqList": rows, "Pending_u32": pending}
