"""The Telegram shopfront: sell subscriptions from a bot, deliver the
account on approval, and remind customers before their subscription ends.

Three moving parts live here, all on one daemon thread:

* the poller -- Bot API ``getUpdates`` long polling; customer commands
  (``/start``, my subscription, buy, link account) and the operator's
  inline approve/reject buttons on order messages;
* the order book -- a buy request inserts a pending ``TgOrder`` row and
  pings the operator's chat; approval runs the same sale core the panel's
  till uses (:func:`app.services.selling.perform_sale`), so the account,
  the expiry, the plan limits and the ``Sale`` ledger row all happen in
  one motion -- the books never disagree with the server;
* the reminder pass -- every 30 minutes, every account a chat is linked
  to and whose expiry falls inside one of the configured thresholds
  (``7,3,1`` days; ``0`` adds an expired notice) gets exactly one message
  per threshold, remembered in ``TgNotify`` so the scan never nags twice.

Configuration lives in the panel settings (token, operator chat id, the
payment instructions buyers see, reminder days, on/off) and is edited from
the panel's Telegram page; changing it restarts the poller. The Bot API
base URL is overridable with ``SEM_TG_API`` so tests can point the bot at
a local mock.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from typing import Any, Optional

from ..db import get_db, utc_now
from ..se import rpc
from ..settings_store import get_setting
from . import payments
from . import quota
from .selling import perform_sale, parse_expire, suggest_password

logger = logging.getLogger(__name__)

#: Overridable for tests: a local mock speaks the same Bot API.
API_BASE = os.environ.get("SEM_TG_API", "https://api.telegram.org")

Wire = dict[str, Any]

_lock = threading.Lock()
_status: Wire = {"running": False, "bot_username": "", "error": ""}
_offset = 0
#: chats that asked to link (or buy) and owe the next message
_awaiting: dict[int, str] = {}

#: the username the customer may type, and nothing looser
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REMINDER_EVERY = 1800.0  # seconds between expiry scans

# ── Persian texts ────────────────────────────────────────────────────────────
T_WELCOME = (
    "👋 به فروشگاه VPN خوش آمدید!\n\n"
    "از دکمه‌های زیر استفاده کنید:\n"
    "🛒 خرید اشتراک — انتخاب پلن و ثبت سفارش\n"
    "📦 اشتراک من — تاریخ انقضا و مصرف\n"
    "🔗 اتصال حساب — وصل کردن اکانت به تلگرام برای یادآوری"
)
T_LINK_ASK = "🔗 نام کاربری (username) اکانت VPN خود را بفرستید."
T_LINKED = "✅ حساب «{name}» به تلگرام شما وصل شد. پیش از پایان اشتراک یادآوری می‌گیرید."
T_LINK_NOTFOUND = "❌ کاربری به این نام روی سرور پیدا نشد. نام کاربری را دقیق بفرستید."
T_LINK_BAD = "❌ نام کاربری فقط می‌تواند حرف، رقم، نقطه، خط تیره و زیرخط داشته باشد."
T_LINKED_ALREADY = "ℹ️ این حساب قبلاً به تلگرام شما وصل شده است."
T_MY_HEAD = "📦 اشتراک‌های شما:\n"
T_MY_ROW = (
    "\n🔹 <b>{name}</b> ({hub})\n"
    "   انقضا: <b>{expire}</b> ({left})\n"
    "   حجم: <b>{used}{limit}</b>\n"
    "   وضعیت: {state}"
)
T_MY_NONE = "📭 حسابی به تلگرام شما وصل نیست. با «🔗 اتصال حساب» اکانت خود را وصل کنید."
T_PLANS_HEAD = "🛒 پلن‌ها — یکی را انتخاب کنید:"
T_PLANS_NONE = "ℹ️ فعلاً پلنی ثبت نشده است."
T_BUY_ASK = "🛒 پلن «{title}».\nنام کاربری موردنظرتان را بفرستید (انگلیسی)."
T_ORDERED = (
    "🧾 سفارش شما ثبت شد و در انتظار تأیید پرداخت است.\n\n"
    "👤 نام کاربری: <b>{name}</b>\n"
    "📅 مدت: <b>{title}</b>\n"
    "💰 مبلغ: <b>{price} {currency}</b>\n\n"
    "{pay_note}\n\n"
    "پس از تأیید ادمین، اکانت همین‌جا تحویل داده می‌شود."
)
T_ORDERED_TO_ADMIN = (
    "🧾 <b>سفارش جدید</b> #{oid}\n"
    "👤 خریدار: {tgname} (<code>{chat}</code>)\n"
    "🔹 نام کاربری: <b>{name}</b> ({kind})\n"
    "📅 پلن: {title}\n"
    "💰 مبلغ: {price} {currency}\n"
    "💾 حجم: {volume} | 🌐 آنلاین: {online}"
)
T_APPROVED_NEW = (
    "✅ پرداخت شما تأیید شد — اکانت آماده است!\n\n"
    "🌐 سرور: <code>{host}</code>\n"
    "👤 نام کاربری: <code>{name}</code>\n"
    "🔑 گذرواژه: <code>{password}</code>\n"
    "📅 انقضا: <b>{expire}</b>\n"
    "💾 حجم: {volume} | 🌐 آنلاین: {online}\n\n"
    "برای اتصال، از فایل‌های کانفیگ پنل یا آموزش ادمین استفاده کنید."
)
T_APPROVED_RENEW = (
    "✅ پرداخت تأیید و اشتراک تمدید شد!\n\n"
    "👤 <b>{name}</b>\n"
    "📅 انقضای جدید: <b>{expire}</b>\n"
    "💾 حجم از نو شروع شد: {volume} | 🌐 آنلاین: {online}"
)
T_REJECTED = "❌ سفارش #{oid} رد شد.{note}"
T_NO_PENDING = "ℹ️ این سفارش قبلاً تصمیم‌گیری شده است."

# -- payments: the two ways money arrives -----------------------------------
T_PAY_MENU = (
    "💳 روش پرداخت را انتخاب کنید:\n"
    "💰 مبلغ سفارش: <b>{amount} {currency}</b>"
)
T_BTN_PAY_C2C = "💳 کارت به کارت"
T_BTN_PAY_OXA = "🪙 پرداخت با آکساپی"
T_BTN_PAY_OK = "✅ تأیید پرداخت"
T_BTN_PAY_NO = "❌ رد پرداخت"
T_BTN_PAY_OPEN = "🌐 باز کردن صفحه پرداخت"
T_PAY_C2C_INFO = (
    "💳 به کارت زیر واریز کنید:\n\n{card_text}\n\n"
    "سپس کد پیگیری (شماره مرجع) تراکنش را بفرستید تا ادمین تأیید کند."
)
T_PAY_C2C_WAIT = (
    "🧾 رسید شما ثبت شد و در انتظار تأیید ادمین است.\n"
    "پس از تأیید، سفارش همین‌جا نهایی می‌شود."
)
T_PAY_REF_BAD = "❌ کد پیگیری را همین حالا بفرستید (متن ساده، حداکثر ۶۴ کاراکتر)."
T_PAY_OXA_SENT = (
    "🪙 فاکتور پرداخت ساخته شد ({gwa} {asset}).\n"
    "با دکمهٔ زیر بپردازید؛ پس از تأیید شبکه، سفارش خودکار نهایی می‌شود."
)
T_PAY_OXA_FAIL = "❌ ساخت فاکتور پرداخت ناموفق بود. کارت به کارت را انتخاب کنید یا بعداً امتحان کنید."
T_PAY_APPROVED = "✅ پرداخت #{pid} شما به مبلغ {amount} تأیید شد. 🎉"
T_PAY_REJECTED = "❌ پرداخت #{pid} ({amount}) تأیید نشد.{note}"
T_PAY_ADMIN = (
    "💳 <b>پرداخت جدید</b> #{pid} — {kind}\n"
    "👤 خریدار: {buyer}\n"
    "💰 مبلغ: {amount} {currency}\n"
    "🔖 پیگیری: {ref}{note}"
)
T_PAY_NO_ORDER = "ℹ️ این سفارش پیدا نشد."
T_REMIND = (
    "⏳ یادآوری: اشتراک «{name}» {rel} تمام می‌شود ({date}).\n"
    "برای تمدید همین حالا بزنید 👇"
)
T_REMIND_EXPIRED = "🛑 اشتراک «{name}» در {date} تمام شده است. برای وصل‌شدن دوباره تمدید کنید 👇"
T_BTN_MY = "📦 اشتراک من"
T_BTN_BUY = "🛒 خرید اشتراک"
T_BTN_LINK = "🔗 اتصال حساب"
T_BTN_RENEW = "🔁 تمدید / خرید"
T_BTN_OK = "✅ تأیید"
T_BTN_NO = "❌ رد"
T_FALLBACK = "برای شروع از /start استفاده کنید."
T_STATS = (
    "📊 فروش امروز: {today} مورد / {today_net} {cur}\n"
    "📈 فروش کل: {total} مورد / {total_net} {cur}\n"
    "🧾 سفارش‌های در انتظار: {pending}\n"
    "🔗 حساب‌های متصل: {links}"
)


# ── Bot API plumbing ─────────────────────────────────────────────────────────
def _api(token: str, method: str, payload: Optional[Wire] = None,
         timeout: float = 40.0) -> Any:
    req = urllib.request.Request(
        f"{API_BASE}/bot{token}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"telegram {method} HTTP {e.code}") from None
    if not out.get("ok"):
        raise RuntimeError(str(out.get("description") or f"telegram {method} failed"))
    return out.get("result")


def _inline(*rows: list[tuple[str, str]]) -> Wire:
    """An inline keyboard from rows of (label, callback_data)."""
    return {"inline_keyboard": [
        [{"text": t, "callback_data": d} for t, d in row] for row in rows
    ]}


def _send(token: str, chat: int | str, text: str,
          keyboard: Optional[Wire] = None) -> Wire:
    payload: Wire = {"chat_id": chat, "text": text, "parse_mode": "HTML",
                     "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = keyboard
    return _api(token, "sendMessage", payload)


def _answer(token: str, cb_id: str, text: str = "") -> None:
    try:
        _api(token, "answerCallbackQuery", {"callback_query_id": cb_id, "text": text})
    except Exception:  # noqa: BLE001 -- cosmetic
        pass


def _edit(token: str, chat: int | str, message_id: int, text: str,
          keyboard: Optional[Wire] = None) -> None:
    payload: Wire = {"chat_id": chat, "message_id": message_id, "text": text,
                     "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        _api(token, "editMessageText", payload)
    except Exception:  # noqa: BLE001 -- the message may already be edited
        pass


# ── helpers ──────────────────────────────────────────────────────────────────
def _admin_chat() -> str:
    return str(get_setting("telegram_admin_chat") or "")


def _is_admin(chat: Any) -> bool:
    return _admin_chat() and str(chat) == _admin_chat()


def _plans() -> dict[str, Wire]:
    raw = get_setting("sale_pricing") or {}
    plans: dict[str, Wire] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            plans[k] = {"price": float(v.get("price") or 0),
                        "volume_gb": float(v.get("volume_gb") or 0),
                        "max_online": int(v.get("max_online") or 0)}
        else:
            plans[k] = {"price": float(v or 0), "volume_gb": 0.0, "max_online": 0}
    return plans


_PLAN_TITLES = {
    "1month": "۱ ماهه", "2months": "۲ ماهه", "3months": "۳ ماهه",
    "6months": "۶ ماهه", "9months": "۹ ماهه", "1year": "۱ ساله",
}


def _plan_title(group: str, months: int) -> str:
    if group in _PLAN_TITLES:
        return _PLAN_TITLES[group]
    return f"{months} ماهه"


def _fmt_gb(n: float) -> str:
    return f"{n:g} GB" if n > 0 else "نامحدود"


def _default_hub() -> str:
    try:
        hubs = rpc("EnumHub", {}).get("HubList", []) or []
    except Exception:  # noqa: BLE001
        return ""
    return str(hubs[0].get("HubName_str", "")) if hubs else ""


def _user_exists(hub: str, name: str) -> bool:
    try:
        return bool(rpc("GetUser", {"HubName_str": hub, "Name_str": name}).get("Name_str"))
    except Exception:  # noqa: BLE001
        return False


def _host_display() -> str:
    return str(get_setting("se_host") or "") or "سرور"


def _pay_kind_label(kind: str) -> str:
    return "کارت به کارت" if kind == "card2card" else "آکساپی"


# ── payments: the menu a fresh order ends with ────────────────────────────
def _pay_menu_kb(oid: int) -> Optional[Wire]:
    rows = [[(T_BTN_PAY_C2C, f"payc2c:{oid}")]]
    if payments.oxapay_ready() and payments._rate() > 0:
        rows.append([(T_BTN_PAY_OXA, f"payoxa:{oid}")])
    return _inline(*rows)


def _send_pay_menu(token: str, chat: int, order: Wire) -> None:
    kb = _pay_menu_kb(int(order["TgOrderID"]))
    if kb is None:
        return
    _send(token, chat, T_PAY_MENU.format(
        amount=f"{float(order['Price'] or 0):g}", currency=order["Currency"]), kb)


def _pay_oxa_for_order(token: str, chat: int, oid: int) -> None:
    """Invoice the order's price on the gateway and hand the buyer the link."""
    order = get_db().query_one('SELECT * FROM "TgOrder" WHERE "TgOrderID" = :i', {"i": oid})
    if not order:
        _send(token, chat, T_PAY_NO_ORDER)
        return
    try:
        pay = payments.create_oxapay(
            float(order["Price"] or 0), currency=order["Currency"],
            buyer=str(order["TgName"] or ""), chat_id=chat, order_id=oid,
            note=f"telegram order #{oid}", source="telegram",
        )
    except Exception as e:  # noqa: BLE001 -- the buyer picks another way
        logger.warning("telegram: oxapay invoice for order #%s failed: %s", oid, e)
        _send(token, chat, T_PAY_OXA_FAIL)
        return
    kb = {"inline_keyboard": [[{"text": T_BTN_PAY_OPEN, "url": pay["PayLink"]}]]} \
        if pay["PayLink"] else None
    _send(token, chat, T_PAY_OXA_SENT.format(
        gwa=f"{float(pay['GwAmount']):g}", asset=pay["Asset"]), kb)


# ── customer flows ───────────────────────────────────────────────────────────
def _main_menu() -> Wire:
    return _inline(
        [(T_BTN_BUY, "buy"), (T_BTN_MY, "my")],
        [(T_BTN_LINK, "link")],
    )


def _send_my(token: str, chat: int) -> None:
    links = get_db().query_all(
        'SELECT * FROM "TgLink" WHERE "ChatID" = :c ORDER BY "TgLinkID"', {"c": str(chat)}
    )
    if not links:
        _send(token, chat, T_MY_NONE, _inline([(T_BTN_LINK, "link")]))
        return
    online: dict[str, int] = {}
    try:
        for hw in rpc("EnumHub", {}).get("HubList", []) or []:
            hub = str(hw.get("HubName_str", ""))
            for s in rpc("EnumSession", {"HubName_str": hub}).get("SessionList", []) or []:
                n = str(s.get("Username_str", "")).casefold()
                if n:
                    online[f"{hub}/{n}"] = online.get(f"{hub}/{n}", 0) + 1
    except Exception:  # noqa: BLE001 -- decoration, never a failure
        pass

    body = T_MY_HEAD
    for link in links:
        hub, name = link["HubName"], link["UserName"]
        expire = None
        try:
            expire = parse_expire(
                rpc("GetUser", {"HubName_str": hub, "Name_str": name}).get("ExpireTime_dt")
            )
        except Exception:  # noqa: BLE001
            pass
        if expire is None or expire.year <= 1970:
            left, expire_txt = "نامحدود", "—"
        else:
            days = (expire - datetime.now()).total_seconds() / 86400.0
            left = f"{max(0, round(days))} روز دیگر" if days >= 0 else f"{abs(round(days))} روز پیش"
            expire_txt = expire.date().isoformat()
        q = quota.get("user", hub, name) or {}
        used = int(q.get("used_bytes") or 0)
        limit_b = int(q.get("limit_bytes") or 0) if q.get("has_limit") else 0
        used_txt = f"{used / 1024 ** 3:.1f} GB" if used else "۰"
        limit_txt = f" / {limit_b / 1024 ** 3:g} GB" if limit_b else ""
        blocked = bool(q.get("blocked"))
        state = "⛔ قطع شده (حجم)" if blocked else "✅ فعال"
        body += T_MY_ROW.format(name=name, hub=hub, expire=expire_txt, left=left,
                                used=used_txt, limit=limit_txt, state=state)
    _send(token, chat, body, _inline([(T_BTN_RENEW, "buy")]))


def _send_plans(token: str, chat: int) -> None:
    plans = _plans()
    if not plans:
        _send(token, chat, T_PLANS_NONE)
        return
    currency = str(get_setting("sale_currency") or "")
    rows = []
    for group, p in plans.items():
        label = (f"{_plan_title(group, 0)} — {p['price']:g} {currency}"
                 f" ({_fmt_gb(p['volume_gb'])})")
        rows.append([(label, f"pick:{group}")])
    _send(token, chat, T_PLANS_HEAD, _inline(*rows))


def _order_text(order: Wire) -> str:
    return T_ORDERED.format(
        name=order["UserName"], title=_plan_title(order["GroupName"], order["Months"]),
        price=f"{order['Price']:g}", currency=order["Currency"],
        pay_note=str(get_setting("telegram_pay_note") or "پس از واریز، ادمین سفارش را تأیید می‌کند."),
    )


def _notify_admin_order(token: str, order: Wire) -> None:
    admin = _admin_chat()
    if not admin:
        return
    text = T_ORDERED_TO_ADMIN.format(
        oid=order["TgOrderID"], tgname=order["TgName"] or "—", chat=order["ChatID"],
        name=order["UserName"], kind="تمدید" if order["Kind"] == "renew" else "اکانت جدید",
        title=_plan_title(order["GroupName"], order["Months"]),
        price=f"{order['Price']:g}", currency=order["Currency"],
        volume=_fmt_gb(float(order["VolumeGB"] or 0)),
        online=order["MaxOnline"] if order["MaxOnline"] else "نامحدود",
    )
    kb = _inline([(T_BTN_OK, f"ok:{order['TgOrderID']}"), (T_BTN_NO, f"no:{order['TgOrderID']}")])
    try:
        msg = _send(token, admin, text, kb)
        db = get_db()
        db.execute('UPDATE "TgOrder" SET "MessageID" = :m WHERE "TgOrderID" = :i',
                   {"m": str(msg.get("message_id", "")), "i": order["TgOrderID"]})
        order["MessageID"] = str(msg.get("message_id", ""))
    except Exception as e:  # noqa: BLE001 -- admin chat may be unset/misconfigured
        logger.warning("telegram: admin order notify failed: %s", e)


def _create_order(chat: int, tgname: str, group: str, username: str) -> Optional[Wire]:
    plans = _plans()
    plan = plans.get(group)
    if not plan:
        return None
    hub = _default_hub()
    if not hub:
        return None
    kind = "renew" if _user_exists(hub, username) else "new"
    db = get_db()
    oid = db.execute(
        'INSERT INTO "TgOrder"("ChatID", "TgName", "HubName", "UserName", "GroupName", '
        '"Months", "Price", "Currency", "VolumeGB", "MaxOnline", "Kind", "Status", '
        '"CreatedDate") VALUES (:c, :n, :h, :u, :g, :mo, :p, :cur, :v, :o, :k, '
        "'pending', :now)",
        {"c": str(chat), "n": tgname, "h": hub, "u": username, "g": group,
         "mo": {"1month": 1, "2months": 2, "3months": 3, "6months": 6,
                "9months": 9, "1year": 12}.get(group, 1),
         "p": plan["price"], "cur": str(get_setting("sale_currency") or ""),
         "v": plan["volume_gb"], "o": plan["max_online"], "k": kind, "now": utc_now()},
    )
    row = db.query_one('SELECT * FROM "TgOrder" WHERE "TgOrderID" = :i', {"i": oid})
    return dict(row) if row else None


def _handle_message(token: str, msg: Wire) -> None:
    chat = int(msg["chat"]["id"])
    text = str(msg.get("text") or "").strip()
    frm = msg.get("from") or {}
    tgname = str(frm.get("first_name") or frm.get("username") or "")

    mode = _awaiting.pop(chat, None)
    if mode == "link":
        if not _NAME_RE.match(text):
            _send(token, chat, T_LINK_BAD)
            return
        hub = _default_hub()
        if not hub or not _user_exists(hub, text):
            _send(token, chat, T_LINK_NOTFOUND)
            return
        db = get_db()
        if db.query_one('SELECT 1 FROM "TgLink" WHERE "ChatID" = :c AND "HubName" = :h '
                        'AND "UserName" = :u', {"c": str(chat), "h": hub, "u": text}):
            _send(token, chat, T_LINKED_ALREADY)
            return
        db.execute(
            'INSERT INTO "TgLink"("ChatID", "HubName", "UserName", "TgName", "CreatedDate") '
            "VALUES (:c, :h, :u, :n, :now)",
            {"c": str(chat), "h": hub, "u": text, "n": tgname, "now": utc_now()},
        )
        _send(token, chat, T_LINKED.format(name=text))
        return

    if mode and mode.startswith("buy:"):
        group = mode.split(":", 1)[1]
        if not _NAME_RE.match(text):
            _send(token, chat, T_LINK_BAD)
            return
        order = _create_order(chat, tgname, group, text)
        if not order:
            _send(token, chat, T_PLANS_NONE)
            return
        _send(token, chat, _order_text(order))
        _notify_admin_order(token, order)
        _send_pay_menu(token, chat, order)
        return

    if mode and mode.startswith("c2c:"):
        # the buyer owes this order its card-to-card tracking reference
        oid = int(mode.split(":", 1)[1])
        order = get_db().query_one('SELECT * FROM "TgOrder" WHERE "TgOrderID" = :i', {"i": oid})
        if not order or str(order["ChatID"]) != str(chat):
            _send(token, chat, T_PAY_NO_ORDER)
            return
        ref = text.strip()[:64]
        if not ref:
            _send(token, chat, T_PAY_REF_BAD)
            _awaiting[chat] = mode  # still owed
            return
        try:
            payments.create_card2card(
                float(order["Price"] or 0), currency=order["Currency"],
                buyer=tgname or str(chat), chat_id=chat, order_id=oid,
                ref=ref, note=f"telegram order #{oid}", source="telegram",
            )
        except ValueError as e:
            _send(token, chat, f"❌ {e}")
            _awaiting[chat] = mode
            return
        _send(token, chat, T_PAY_C2C_WAIT)
        return

    if text.startswith("/start"):
        _awaiting.pop(chat, None)
        _send(token, chat, T_WELCOME, _main_menu())
    elif text.startswith("/stats") and _is_admin(chat):
        _send(token, chat, _admin_stats())
    elif text in ("اشتراک من", "/my"):
        _send_my(token, chat)
    elif text in ("خرید اشتراک", "/buy"):
        _send_plans(token, chat)
    elif text in ("اتصال حساب", "/link"):
        _awaiting[chat] = "link"
        _send(token, chat, T_LINK_ASK)
    elif text.startswith("/"):
        _send(token, chat, T_FALLBACK, _main_menu())


def _admin_stats() -> str:
    db = get_db()
    cur = str(get_setting("sale_currency") or "")
    today = date.today().isoformat()
    t = db.query_one(
        'SELECT COUNT(*) AS n, COALESCE(SUM("Price" - "Discount"), 0) AS s FROM "Sale" '
        'WHERE "IsDeleted" = 0 AND substr("CreatedDate", 1, 10) = :d', {"d": today}
    ) or {}
    a = db.query_one(
        'SELECT COUNT(*) AS n, COALESCE(SUM("Price" - "Discount"), 0) AS s FROM "Sale" '
        'WHERE "IsDeleted" = 0'
    ) or {}
    p = db.query_one('SELECT COUNT(*) AS n FROM "TgOrder" WHERE "Status" = \'pending\'') or {}
    l = db.query_one('SELECT COUNT(*) AS n FROM "TgLink"') or {}
    return T_STATS.format(today=t.get("n", 0), today_net=f"{float(t.get('s') or 0):g}",
                          total=a.get("n", 0), total_net=f"{float(a.get('s') or 0):g}",
                          cur=cur, pending=p.get("n", 0), links=l.get("n", 0))


# ── orders: decide from the bot or the panel, same code path ─────────────────
def _customer_kb() -> Wire:
    return _inline([(T_BTN_RENEW, "buy")])


def approve_order(oid: int, actor: Optional[dict] = None,
                  source: str = "panel") -> Wire:
    """Approve one pending order: run the sale, mark the row, tell both sides.

    A *new* account gets a generated password (delivered to the customer in
    the bot message); a *renew* keeps the account's existing credential and
    extends from its current expiry, with the volume meter restarted --
    exactly what the panel's renewal does.
    """
    db = get_db()
    order = db.query_one('SELECT * FROM "TgOrder" WHERE "TgOrderID" = :i', {"i": oid})
    if not order:
        raise ValueError("order not found")
    if order["Status"] != "pending":
        raise ValueError("already decided")
    out = perform_sale(
        order["HubName"], order["UserName"], months=int(order["Months"]),
        group=order["GroupName"], realname=order["TgName"] or "",
        note=f"telegram order #{oid}", password="" if order["Kind"] == "renew" else suggest_password(),
        volume_gb=float(order["VolumeGB"] or 0), max_online=int(order["MaxOnline"] or 0),
        price=float(order["Price"] or 0), currency=order["Currency"],
        buyer=f"telegram: {order['TgName']}".strip(),
        extend_if_exists=True, actor=actor, source=source,
    )
    password = out["password"]
    if not password and not out["created"]:
        password = "(همان گذرواژه قبلی)"
    db.execute(
        'UPDATE "TgOrder" SET "Status" = \'approved\', "DecidedDate" = :now, '
        '"DecidedBy" = :by WHERE "TgOrderID" = :i',
        {"now": utc_now(), "by": (actor or {}).get("Username") or source, "i": oid},
    )

    # tell the customer, then annotate the operator's copy
    token = str(get_setting("telegram_bot_token") or "")
    if order["Kind"] == "renew":
        text = T_APPROVED_RENEW.format(
            name=order["UserName"], expire=out["expire"][:10],
            volume=_fmt_gb(float(order["VolumeGB"] or 0)),
            online=order["MaxOnline"] if order["MaxOnline"] else "نامحدود",
        )
    else:
        text = T_APPROVED_NEW.format(
            host=_host_display(), name=order["UserName"], password=password,
            expire=out["expire"][:10], volume=_fmt_gb(float(order["VolumeGB"] or 0)),
            online=order["MaxOnline"] if order["MaxOnline"] else "نامحدود",
        )
    if token:
        try:
            _send(token, order["ChatID"], text, _customer_kb())
            if order["MessageID"]:
                _edit(token, _admin_chat(), int(order["MessageID"] or 0),
                      f"✅ تأیید شد — سفارش #{oid} ({order['UserName']})")
        except Exception as e:  # noqa: BLE001 -- the sale stands even if delivery hiccups
            logger.warning("telegram: approval delivery failed: %s", e)
    return dict(db.query_one('SELECT * FROM "TgOrder" WHERE "TgOrderID" = :i', {"i": oid}) or {})


def reject_order(oid: int, note: str = "", actor: Optional[dict] = None,
                 source: str = "panel") -> Wire:
    db = get_db()
    order = db.query_one('SELECT * FROM "TgOrder" WHERE "TgOrderID" = :i', {"i": oid})
    if not order:
        raise ValueError("order not found")
    if order["Status"] != "pending":
        raise ValueError("already decided")
    db.execute(
        'UPDATE "TgOrder" SET "Status" = \'rejected\', "DecidedDate" = :now, '
        '"DecidedBy" = :by, "Note" = :n WHERE "TgOrderID" = :i',
        {"now": utc_now(), "by": (actor or {}).get("Username") or source,
         "n": note, "i": oid},
    )
    token = str(get_setting("telegram_bot_token") or "")
    if token:
        try:
            _send(token, order["ChatID"],
                  T_REJECTED.format(oid=oid, note=f"\nدلیل: {note}" if note else ""))
            if order["MessageID"]:
                _edit(token, _admin_chat(), int(order["MessageID"] or 0),
                      f"❌ رد شد — سفارش #{oid} ({order['UserName']})")
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram: rejection delivery failed: %s", e)
    return dict(db.query_one('SELECT * FROM "TgOrder" WHERE "TgOrderID" = :i', {"i": oid}) or {})


def _handle_callback(token: str, cb: Wire) -> None:
    data = str(cb.get("data") or "")
    chat = int((cb.get("message") or {}).get("chat", {}).get("id", 0))
    cb_id = str(cb.get("id", ""))
    frm = cb.get("from") or {}
    tgname = str(frm.get("first_name") or frm.get("username") or "")

    if data == "my":
        _answer(token, cb_id)
        _send_my(token, chat)
        return
    if data == "buy":
        _answer(token, cb_id)
        _send_plans(token, chat)
        return
    if data == "link":
        _awaiting[chat] = "link"
        _answer(token, cb_id)
        _send(token, chat, T_LINK_ASK)
        return
    if data.startswith("pick:"):
        group = data.split(":", 1)[1]
        if group not in _plans():
            _answer(token, cb_id, "پلن یافت نشد")
            return
        _awaiting[chat] = f"buy:{group}"
        _answer(token, cb_id)
        currency = str(get_setting("sale_currency") or "")
        p = _plans()[group]
        _send(token, chat, T_BUY_ASK.format(
            title=_plan_title(group, 0) + f" — {p['price']:g} {currency}"))
        return
    if data.startswith(("ok:", "no:")):
        decide, oid = data.split(":", 1)
        if not _is_admin(chat):
            _answer(token, cb_id, "فقط ادمین")
            return
        try:
            if decide == "ok":
                approve_order(int(oid), actor={"Username": "telegram"}, source="telegram")
                _answer(token, cb_id, "تأیید شد")
            else:
                reject_order(int(oid), actor={"Username": "telegram"}, source="telegram")
                _answer(token, cb_id, "رد شد")
        except ValueError as e:
            _answer(token, cb_id, str(e))
        return
    if data.startswith(("payok:", "payno:")):
        decide, pid = data.split(":", 1)
        if not _is_admin(chat):
            _answer(token, cb_id, "فقط ادمین")
            return
        try:
            if decide == "payok":
                payments.decide(int(pid), True, actor={"Username": "telegram"}, source="telegram")
                _answer(token, cb_id, "پرداخت تأیید شد")
            else:
                payments.decide(int(pid), False, actor={"Username": "telegram"}, source="telegram")
                _answer(token, cb_id, "پرداخت رد شد")
        except ValueError as e:
            _answer(token, cb_id, str(e))
        return
    if data.startswith("payc2c:"):
        oid = int(data.split(":", 1)[1])
        order = get_db().query_one('SELECT * FROM "TgOrder" WHERE "TgOrderID" = :i', {"i": oid})
        if not order or str(order["ChatID"]) != str(chat):
            _answer(token, cb_id, "سفارش پیدا نشد")
            return
        _awaiting[chat] = f"c2c:{oid}"
        _answer(token, cb_id)
        _send(token, chat, T_PAY_C2C_INFO.format(card_text=payments.card_text() or
                                                 str(get_setting("telegram_pay_note") or "")))
        return
    if data.startswith("payoxa:"):
        oid = int(data.split(":", 1)[1])
        _answer(token, cb_id)
        _pay_oxa_for_order(token, chat, oid)
        return
    _answer(token, cb_id)


# ── the reminder pass ────────────────────────────────────────────────────────
def _reminder_pass(token: str) -> int:
    """One scan: linked accounts nearing (or past) expiry get one message each.

    Returns how many reminders went out. Each (chat, hub, user, threshold)
    fires exactly once, ever -- ``TgNotify`` remembers it.
    """
    raw = str(get_setting("telegram_reminder_days") or "7,3,1")
    try:
        thresholds = sorted({int(x) for x in raw.split(",") if x.strip().lstrip("-").isdigit()},
                            reverse=True)
    except ValueError:
        thresholds = [7, 3, 1]
    if not thresholds:
        return 0

    links = get_db().query_all('SELECT * FROM "TgLink"')
    if not links:
        return 0
    watched: dict[tuple[str, str], list[Wire]] = {}
    for link in links:
        key = (link["HubName"], str(link["UserName"]).casefold())
        watched.setdefault(key, []).append(link)

    now = datetime.now()
    sent = 0
    try:
        hubs = rpc("EnumHub", {}).get("HubList", []) or []
    except Exception:  # noqa: BLE001
        return 0
    for hw in hubs:
        hub = str(hw.get("HubName_str", ""))
        if not hub:
            continue
        try:
            users = rpc("EnumUser", {"HubName_str": hub}).get("UserList", []) or []
        except Exception:  # noqa: BLE001 -- one unreadable hub must not kill the pass
            continue
        for u in users:
            name = str(u.get("Name_str", ""))
            links_for = watched.get((hub, name.casefold()))
            if not links_for:
                continue
            expire = parse_expire(u.get("ExpireTime_dt"))
            if expire is None or expire.year <= 1970:
                continue
            days = (expire - now).total_seconds() / 86400.0
            kinds = [f"d{t}" for t in thresholds if days <= t and days >= 0]
            if days < 0 and 0 in thresholds:
                kinds.append("expired")
            for kind in kinds:
                for link in links_for:
                    if kind == "expired":
                        text = T_REMIND_EXPIRED.format(name=name, date=expire.date().isoformat())
                    else:
                        rel = "امروز" if round(days) == 0 else f"{round(days)} روز دیگر"
                        text = T_REMIND.format(name=name, rel=rel, date=expire.date().isoformat())
                    try:
                        # deliver first, remember after: a failed send retries
                        # on the next pass instead of being swallowed.
                        _send(token, link["ChatID"], text,
                              _inline([(T_BTN_RENEW, "buy")]))
                    except Exception as e:  # noqa: BLE001
                        logger.warning("telegram: reminder to %s failed: %s", link["ChatID"], e)
                        continue
                    get_db().execute(
                        'INSERT OR IGNORE INTO "TgNotify"("ChatID", "HubName", "UserName", '
                        '"Kind", "SentDate") VALUES (:c, :h, :u, :k, :now)',
                        {"c": link["ChatID"], "h": hub, "u": name, "k": kind, "now": utc_now()},
                    )
                    sent += 1
    return sent


# ── the loop ─────────────────────────────────────────────────────────────────
def _refresh_bot_username(token: str) -> None:
    try:
        me = _api(token, "getMe", {}, timeout=15)
        _status["bot_username"] = str(me.get("username") or "")
        _status["error"] = ""
    except Exception as e:  # noqa: BLE001
        _status["error"] = str(e)


def _cycle() -> None:
    """One poll iteration +, at most every 30 minutes, one reminder pass."""
    global _offset
    token = str(get_setting("telegram_bot_token") or "")
    if not token or not bool(get_setting("telegram_enabled")):
        _status["running"] = False
        time.sleep(5)
        return
    if not _status["running"]:
        _status["running"] = True
        _offset = 0
        _refresh_bot_username(token)
    try:
        updates = _api(token, "getUpdates",
                       {"offset": _offset, "timeout": 25,
                        "allowed_updates": ["message", "callback_query"]}) or []
    except Exception as e:  # noqa: BLE001
        _status["error"] = str(e)
        time.sleep(10)
        return
    _status["error"] = ""
    for upd in updates:
        _offset = max(_offset, int(upd.get("update_id", 0)) + 1)
        try:
            if "message" in upd and upd["message"].get("chat"):
                _handle_message(token, upd["message"])
            elif "callback_query" in upd:
                _handle_callback(token, upd["callback_query"])
        except Exception:  # noqa: BLE001 -- one bad update must not kill the loop
            logger.exception("telegram: update handling failed")
    # a quiet poll still earns a reminder check every ~30 minutes
    if _should_remind():
        try:
            _reminder_pass(token)
        except Exception:  # noqa: BLE001
            logger.exception("telegram: reminder pass failed")


#: remembers the last reminder scan so a busy poll doesn't scan too often
_last_reminder = 0.0


def _should_remind() -> bool:
    global _last_reminder
    if time.monotonic() - _last_reminder >= _REMINDER_EVERY:
        _last_reminder = time.monotonic()
        return True
    return False


def _loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            _cycle()
        except Exception:  # noqa: BLE001
            logger.exception("telegram: cycle failed")
            time.sleep(10)


#: the live generation: one (stop event, thread) pair; restart swaps it
_generation: list[tuple[threading.Event, threading.Thread]] = []


def start() -> None:
    """Start the poller if a token exists and none is running."""
    if not str(get_setting("telegram_bot_token") or ""):
        return
    if _generation and _generation[0][1].is_alive():
        return
    _status.update({"running": False, "error": ""})
    ev = threading.Event()  # one event per generation: restart never un-stops an old thread
    th = threading.Thread(target=_loop, args=(ev,), daemon=True, name="telegram-bot")
    _generation.clear()
    _generation.append((ev, th))
    th.start()


def stop() -> None:
    if _generation:
        _generation[0][0].set()


def restart() -> None:
    """Settings changed: retire the current poller generation and, if the
    token is present, spawn a fresh one. The retired thread may drain one
    pending long poll (up to ~25s) but then sees its own generation closed --
    its loop runs on the event captured at birth, not on the shared one."""
    old_ev, old_th = (_generation[0] if _generation else (None, None))
    _status.update({"running": False, "bot_username": "", "error": ""})
    _offset = 0
    if old_ev is not None:
        old_ev.set()
    if old_th is not None and old_th.is_alive():
        old_th.join(timeout=2)
    _generation.clear()
    start()


def run_reminders_now() -> int:
    """Force one reminder pass from the panel (or a test) -- returns how many
    messages went out. The hourly scan covers the routine; this covers
    'I just configured the bot and want the nagging to start now'."""
    token = str(get_setting("telegram_bot_token") or "")
    if not token:
        return 0
    return _reminder_pass(token)


def status() -> Wire:
    with _lock:
        return dict(_status)
