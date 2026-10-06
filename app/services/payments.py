"""Payments: the money side of the shop, in two methods.

* **card-to-card** -- the buyer transfers money by hand and reports the
  tracking reference; the row sits *pending* until an operator confirms it
  (panel buttons, or the bot's inline keys on the admin's copy of the
  message). Confirmation is the whole point: until then it is a promise,
  not money.
* **oxapay** -- an invoice on the Oxapay crypto gateway. Amounts arrive in
  the shop's own currency and convert through ``oxapay_rate`` (one gateway
  unit costs that many); the gateway's webhook confirms the payment, and
  the operator's "check status" button covers installs without a public
  callback URL. The API base is overridable with ``SEM_OXAPAY_API`` so
  tests can point the client at a local mock.

An approved payment does its job through whichever link it carries: an
``OrderID`` auto-approves the linked Telegram order (the sale core runs,
the account is delivered in chat), a ``UserID`` credits the linked
reseller's wallet, and a row with neither is pure bookkeeping. Rejection
only answers the buyer, if the bot knows their chat.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from ..audit import record
from ..db import get_db, utc_now
from ..settings_store import get_setting
from . import wallet

logger = logging.getLogger(__name__)

#: Overridable for tests: a local mock speaks the same merchant API.
OXAPAY_BASE = os.environ.get("SEM_OXAPAY_API", "https://api.oxapay.com")

Wire = dict[str, Any]

KINDS = ("card2card", "oxapay")
STATUSES = ("pending", "approved", "rejected")


# ── the Oxapay merchant client ───────────────────────────────────────────────
def _oxa_api(path: str, payload: Wire, api_key: str) -> Wire:
    req = urllib.request.Request(
        f"{OXAPAY_BASE.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "merchant": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"oxapay HTTP {e.code}") from None
    if str(out.get("status")) != "success":
        raise RuntimeError(str(out.get("message") or out.get("status") or "oxapay failed"))
    return out


def _rate() -> float:
    """Shop-currency price of one gateway unit (0 = not configured)."""
    try:
        return float(get_setting("oxapay_rate") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def oxapay_ready() -> bool:
    return bool(get_setting("oxapay_enabled")) and bool(str(get_setting("oxapay_api_key") or ""))


def _callback_url() -> str:
    """The webhook address the gateway calls back -- only when the panel
    knows its own public URL. Empty means 'no webhook'; the operator's
    status check (and the poll-friendly inquiry) still confirm payments."""
    base = str(get_setting("public_base_url") or "").strip().rstrip("/")
    if not base:
        return ""
    try:
        web_path = str(get_setting("web_path") or "").strip().strip("/")
    except Exception:  # noqa: BLE001
        web_path = ""
    return f"{base}/{'/'.join(p for p in (web_path, 'api/v1') if p)}/payments/oxapay/callback"


# ── the Telegram side (late import: the bot module imports this one) ────────
def _tg():
    from . import telegram

    return telegram


def _notify_admin_payment(pay: Wire) -> None:
    """The operator's copy of a new payment, with confirm/reject keys."""
    try:
        tg = _tg()
        token = str(get_setting("telegram_bot_token") or "")
        admin = tg._admin_chat()
        if not token or not admin:
            return
        text = tg.T_PAY_ADMIN.format(
            pid=pay["PayReqID"], kind=tg._pay_kind_label(pay["Kind"]),
            buyer=pay["Buyer"] or "—", amount=f"{float(pay['Amount']):g}",
            currency=pay["Currency"], ref=pay["Ref"] or pay["TrackID"] or "—",
            note=(f"\n📝 {pay['Note']}" if pay["Note"] else ""),
        )
        kb = tg._inline([(tg.T_BTN_PAY_OK, f"payok:{pay['PayReqID']}"),
                         (tg.T_BTN_PAY_NO, f"payno:{pay['PayReqID']}")])
        msg = tg._send(token, admin, text, kb)
        get_db().execute(
            'UPDATE "PayReq" SET "MessageID" = :m WHERE "PayReqID" = :i',
            {"m": str(msg.get("message_id", "")), "i": pay["PayReqID"]},
        )
    except Exception as e:  # noqa: BLE001 -- a missed ping never blocks a payment
        logger.warning("payments: admin notify failed: %s", e)


def _tell_buyer(chat: str, text: str) -> None:
    if not chat:
        return
    try:
        token = str(get_setting("telegram_bot_token") or "")
        if token:
            _tg()._send(token, int(chat), text)
    except Exception as e:  # noqa: BLE001
        logger.warning("payments: buyer notify failed: %s", e)


# ── creation ─────────────────────────────────────────────────────────────────
def _insert(kind: str, amount: float, currency: str, *, buyer: str = "",
            chat_id: str = "", user_id: int = 0, order_id: int = 0,
            ref: str = "", note: str = "", actor=None, source: str = "panel") -> Wire:
    db = get_db()
    pid = db.execute(
        'INSERT INTO "PayReq"("Kind", "Amount", "Currency", "Status", "Buyer", "ChatID", '
        '"UserID", "OrderID", "Ref", "Note", "CreatedDate") '
        "VALUES (:k, :a, :c, 'pending', :b, :ch, :u, :o, :r, :n, :now)",
        {"k": kind, "a": round(float(amount), 2), "c": currency, "b": buyer,
         "ch": str(chat_id), "u": int(user_id), "o": int(order_id), "r": ref,
         "n": note, "now": utc_now()},
    )
    row = db.query_one('SELECT * FROM "PayReq" WHERE "PayReqID" = :i', {"i": pid})
    pay = dict(row) if row else {}
    record(actor, f"payment.created_{kind}", "payreq", str(pid),
           f"{amount:g} {currency} buyer={buyer or '—'} "
           f"order={order_id or '—'} user={user_id or '—'} [{source}]")
    _notify_admin_payment(pay)
    return pay


def create_card2card(amount: float, currency: str = "", *, buyer: str = "",
                     chat_id: Any = "", user_id: int = 0, order_id: int = 0,
                     ref: str = "", note: str = "", actor=None,
                     source: str = "panel") -> Wire:
    amount = float(amount or 0)
    if amount <= 0:
        raise ValueError("The amount must be a positive number.")
    return _insert("card2card", amount, currency or str(get_setting("sale_currency") or ""),
                   buyer=buyer, chat_id=str(chat_id or ""), user_id=user_id,
                   order_id=order_id, ref=str(ref or "").strip()[:64], note=note,
                   actor=actor, source=source)


def create_oxapay(amount: float, currency: str = "", *, buyer: str = "",
                  chat_id: Any = "", user_id: int = 0, order_id: int = 0,
                  note: str = "", actor=None, source: str = "panel") -> Wire:
    """An invoice on the gateway. ``amount`` is in the shop's currency; the
    gateway sees it divided by ``oxapay_rate``. Both figures freeze onto the
    row, so a later rate change never rewrites history."""
    if not oxapay_ready():
        raise ValueError("The Oxapay gateway is not enabled.")
    amount = float(amount or 0)
    if amount <= 0:
        raise ValueError("The amount must be a positive number.")
    rate = _rate()
    if rate <= 0:
        raise ValueError("The Oxapay rate is not configured — set it in payment settings.")
    asset = str(get_setting("oxapay_currency") or "USDT")
    gw_amount = round(amount / rate, 2)
    if gw_amount <= 0:
        raise ValueError("The amount must be a positive number.")
    try:
        out = _oxa_api("/merchants/request/invoice", {
            "merchant": str(get_setting("oxapay_api_key") or ""),
            "amount": gw_amount,
            "currency": asset,
            "order_id": f"pay-{int(user_id or 0)}-{int(order_id or 0)}",
            "callback_url": _callback_url(),
            "description": (note or f"VPN shop payment by {buyer}" if buyer else "VPN shop payment")[:200],
        }, str(get_setting("oxapay_api_key") or ""))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"The Oxapay gateway refused the invoice: {e}") from None
    track = str(out.get("trackId") or "")
    link = str(out.get("payLink") or out.get("link") or "")
    if not track:
        raise ValueError("The Oxapay gateway returned no track id.")
    pay = _insert("oxapay", amount, currency or str(get_setting("sale_currency") or ""),
                  buyer=buyer, chat_id=str(chat_id or ""), user_id=user_id,
                  order_id=order_id, note=note, actor=actor, source=source)
    db = get_db()
    db.execute(
        'UPDATE "PayReq" SET "TrackID" = :t, "PayLink" = :l, "GwAmount" = :g, '
        '"Asset" = :a WHERE "PayReqID" = :i',
        {"t": track, "l": link, "g": gw_amount, "a": asset, "i": pay["PayReqID"]},
    )
    pay.update({"TrackID": track, "PayLink": link, "GwAmount": gw_amount, "Asset": asset})
    return pay


# ── the decision ─────────────────────────────────────────────────────────────
def decide(req_id: int, ok: bool, note: str = "", actor=None,
           source: str = "panel") -> Wire:
    """Confirm or refuse one pending payment, then let it do its job."""
    db = get_db()
    row = db.query_one('SELECT * FROM "PayReq" WHERE "PayReqID" = :i', {"i": req_id})
    if not row:
        raise ValueError("The payment was not found.")
    pay = dict(row)
    if pay["Status"] != "pending":
        raise ValueError("This payment has already been decided.")
    db.execute(
        'UPDATE "PayReq" SET "Status" = :s, "DecidedDate" = :now, "DecidedBy" = :by, '
        '"Note" = CASE WHEN :n != \'\' THEN :n ELSE "Note" END WHERE "PayReqID" = :i',
        {"s": "approved" if ok else "rejected",
         "now": utc_now(),
         "by": (actor or {}).get("Username") or source,
         "n": note, "i": req_id},
    )
    pay["Status"] = "approved" if ok else "rejected"
    record(actor, "payment.approved" if ok else "payment.rejected",
           "payreq", str(req_id),
           f"{float(pay['Amount']):g} {pay['Currency']} order={pay['OrderID'] or '—'} "
           f"user={pay['UserID'] or '—'} [{source}]" + (f" note={note}" if note else ""))

    tg = _tg()
    if ok:
        # the linked job runs first, then the buyer hears about it
        if int(pay["OrderID"] or 0):
            try:
                tg.approve_order(int(pay["OrderID"]), actor=actor, source=source)
            except ValueError as e:
                # the order may already have been decided by hand -- the
                # payment still stands, the ledger still balances
                logger.info("payments: linked order #%s: %s", pay["OrderID"], e)
        if int(pay["UserID"] or 0):
            new_balance = wallet.apply(
                int(pay["UserID"]), float(pay["Amount"]),
                note=("شارژ کیف پول — " +
                      ("کارت به کارت" if pay["Kind"] == "card2card" else "درگاه آکساپی") +
                      f" (رسید #{req_id})" + (f" پیگیری {pay['Ref']}" if pay["Ref"] else "")),
                by=(actor or {}).get("Username") or source, actor=actor,
                action="payment.credited",
            )
            pay["BalanceAfter_f64"] = new_balance
    if pay["ChatID"]:
        amount_txt = f"{float(pay['Amount']):g} {pay['Currency']}"
        if ok:
            _tell_buyer(pay["ChatID"], tg.T_PAY_APPROVED.format(amount=amount_txt, pid=req_id))
        else:
            _tell_buyer(pay["ChatID"], tg.T_PAY_REJECTED.format(pid=req_id, amount=amount_txt,
                                                                note=f"\nدلیل: {note}" if note else ""))
    # retire the operator's copy of the message, if it went out
    if pay["MessageID"]:
        try:
            token = str(get_setting("telegram_bot_token") or "")
            admin = tg._admin_chat()
            if token and admin:
                mark = "✅ تأیید شد" if ok else "❌ رد شد"
                tg._edit(token, admin, int(pay["MessageID"] or 0),
                         f"{mark} — پرداخت #{req_id} ({pay['Buyer'] or '—'})")
        except Exception as e:  # noqa: BLE001
            logger.warning("payments: admin message edit failed: %s", e)
    fresh = db.query_one('SELECT * FROM "PayReq" WHERE "PayReqID" = :i', {"i": req_id})
    return dict(fresh) if fresh else pay


# ── gateway confirmations ────────────────────────────────────────────────────
def _paid_status(s: Any) -> bool:
    return str(s or "").strip().lower() in ("paid", "successful", "success", "confirmed")


def oxapay_callback(payload: Wire) -> Wire:
    """The gateway's webhook: find the invoice by its track id and, when the
    network reports it paid, approve it exactly as the operator would."""
    track = str(payload.get("trackId") or payload.get("track_id") or "").strip()
    row = get_db().query_one(
        'SELECT * FROM "PayReq" WHERE "Kind" = \'oxapay\' AND "TrackID" = :t', {"t": track}
    ) if track else None
    if not row:
        return {"Found_b": False}
    pay = dict(row)
    if pay["Status"] != "pending":
        return {"Found_b": True, "Decided_b": False, "PayReq": pay}
    if not _paid_status(payload.get("status") or (payload.get("payment") or {}).get("status")):
        return {"Found_b": True, "Decided_b": False, "PayReq": pay}
    out = decide(pay["PayReqID"], True, note="", actor={"Username": "oxapay"}, source="webhook")
    return {"Found_b": True, "Decided_b": True, "PayReq": out}


def check_oxapay(req_id: int, actor=None) -> Wire:
    """Ask the gateway where the invoice stands; approve it when it says paid."""
    row = get_db().query_one(
        'SELECT * FROM "PayReq" WHERE "PayReqID" = :i AND "Kind" = \'oxapay\'', {"i": req_id}
    )
    if not row:
        raise ValueError("The payment was not found.")
    pay = dict(row)
    if pay["Status"] != "pending":
        return {"PayReq": pay, "GatewayStatus_utf": pay["Status"], "Decided_b": False}
    api_key = str(get_setting("oxapay_api_key") or "")
    try:
        out = _oxa_api(f"/merchants/inquiry/{pay['TrackID']}", {}, api_key)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"The Oxapay gateway refused the inquiry: {e}") from None
    data = out.get("payment") if isinstance(out.get("payment"), dict) else out
    status = str(data.get("status") or "unknown")
    if _paid_status(status):
        return {"PayReq": decide(req_id, True, actor=actor or {"Username": "panel"}, source="inquiry"),
                "GatewayStatus_utf": status, "Decided_b": True}
    return {"PayReq": pay, "GatewayStatus_utf": status, "Decided_b": False}


# ── reads ────────────────────────────────────────────────────────────────────
def list_payments(status: str = "", kind: str = "", limit: int = 100) -> Wire:
    if status and status not in STATUSES:
        raise ValueError("Unknown payment status.")
    if kind and kind not in KINDS:
        raise ValueError("Unknown payment kind.")
    sql = 'SELECT * FROM "PayReq"'
    where: list[str] = []
    params: dict[str, Any] = {"n": limit}
    if status:
        where.append('"Status" = :s')
        params["s"] = status
    if kind:
        where.append('"Kind" = :k')
        params["k"] = kind
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ' ORDER BY "PayReqID" DESC LIMIT :n'
    rows = [dict(r) for r in get_db().query_all(sql, params)]
    # name the linked wallet so the operator does not have to look it up
    names: dict[int, str] = {}
    for r in rows:
        uid = int(r["UserID"] or 0)
        if uid and uid not in names:
            u = get_db().query_one('SELECT "Username" FROM "PanelUser" WHERE "UserID" = :i', {"i": uid})
            names[uid] = u["Username"] if u else ""
        r["ResellerName_utf"] = names.get(uid, "")
    pending = get_db().query_one(
        'SELECT COUNT(*) AS n FROM "PayReq" WHERE "Status" = \'pending\''
    )
    return {"PayReqList": rows,
            "Count_u32": len(rows),
            "Pending_u32": int(pending["n"]) if pending else 0}


def get_payreq(req_id: int) -> Optional[Wire]:
    row = get_db().query_one('SELECT * FROM "PayReq" WHERE "PayReqID" = :i', {"i": req_id})
    return dict(row) if row else None


def card_text() -> str:
    """The card-to-card facts buyers transfer against, one line each."""
    lines = []
    number = str(get_setting("pay_card_number") or "")
    holder = str(get_setting("pay_card_holder") or "")
    bank = str(get_setting("pay_card_bank") or "")
    if number:
        lines.append(f"💳 شماره کارت: <code>{number}</code>")
    if holder:
        lines.append(f"👤 به نام: {holder}")
    if bank:
        lines.append(f"🏦 بانک: {bank}")
    extra = str(get_setting("telegram_pay_note") or "")
    if extra:
        lines.append(f"📝 {extra}")
    return "\n".join(lines)
