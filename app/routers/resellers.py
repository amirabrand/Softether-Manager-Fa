"""The reseller desk: dedicated sign-ins that buy accounts from a prepaid
wallet, at their own rate.

Two routers live here. ``self_router`` (``/reseller/...``) is what a
reseller sign-in reaches -- its wallet, its price list (list price times
its rate), buying single or in bulk, and its own purchase history. The
path gate in :mod:`app.deps` makes that the *only* corner of the API a
reseller can touch. ``admin_router`` (``/resellers/...``) is the
operator's side: create resellers, set each one's rate, top a wallet up
or claw credit back.

A purchase runs the very same sale core the till uses
(:func:`app.services.selling.perform_sale`), so accounts a reseller buys
are real the moment the wallet is debited -- no approval step, because
the credit was prepaid. Every batch is one wallet debit and one row per
account in the Sale ledger with the reseller as its creator, so the
operator's analytics and the expirations report already see them.
"""
from __future__ import annotations

import secrets
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..audit import record
from ..db import get_db, utc_now
from ..deps import CurrentUser
from ..se import rpc
from ..security import hash_password
from ..settings_store import get_setting
from ..services import wallet
from ..services.selling import perform_sale, suggest_password

self_router = APIRouter(tags=["reseller"])
admin_router = APIRouter(tags=["resellers"])

Wire = dict[str, Any]

_MONTHS = {"1month": 1, "2months": 2, "3months": 3, "6months": 6,
           "9months": 9, "1year": 12}

_NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789._-")


def _require_admin(user: dict) -> None:
    if user.get("Role") != "admin":
        raise HTTPException(status_code=403, detail="Only the operator does this.")


def _plans() -> dict[str, Wire]:
    raw = get_setting("sale_pricing") or {}
    out: dict[str, Wire] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[k] = {"price": float(v.get("price") or 0),
                      "volume_gb": float(v.get("volume_gb") or 0),
                      "max_online": int(v.get("max_online") or 0)}
        else:
            out[k] = {"price": float(v or 0), "volume_gb": 0.0, "max_online": 0}
    return out


def _rate_price(list_price: float, rate: float) -> int:
    return int(round(list_price * rate / 100.0))


def _user_exists(hub: str, name: str) -> bool:
    try:
        return bool(rpc("GetUser", {"HubName_str": hub, "Name_str": name}).get("Name_str"))
    except Exception:  # noqa: BLE001
        return False


def _default_hub() -> str:
    try:
        hubs = rpc("EnumHub", {}).get("HubList", []) or []
    except Exception:  # noqa: BLE001
        return ""
    return str(hubs[0].get("HubName_str", "")) if hubs else ""


# ── the reseller's own desk ──────────────────────────────────────────────────
@self_router.get("/reseller/overview")
def reseller_overview(user: dict = CurrentUser) -> Wire:
    rate = 100.0
    row = get_db().query_one('SELECT "Rate" FROM "PanelUser" WHERE "UserID" = :i',
                             {"i": user["UserID"]})
    if row:
        rate = float(row["Rate"] or 100)
    currency = str(get_setting("sale_currency") or "")
    prices = [
        {"group": g, "months": _MONTHS.get(g, 1),
         "list_price": p["price"], "price": _rate_price(p["price"], rate),
         "volume_gb": p["volume_gb"], "max_online": p["max_online"]}
        for g, p in _plans().items()
    ]
    return {
        "Username_str": user["Username"],
        "Rate_f64": rate,
        "Balance_f64": wallet.balance(user["UserID"]),
        "Currency_str": currency,
        "Prices": prices,
        "PendingNothing_u32": 0,
    }


class PurchaseIn(BaseModel):
    group: str = Field(min_length=3, max_length=64)
    count: int = Field(default=1, ge=1, le=50)
    prefix: str = Field(default="", max_length=32)


@self_router.post("/reseller/purchase")
def reseller_purchase(body: PurchaseIn, user: dict = CurrentUser) -> Wire:
    """Buy ``count`` accounts of one plan: wallet debit + real accounts now.

    Names come from the prefix (``ali`` -> ali, ali01, ali02...); without
    one the panel invents a base. Each account gets its own generated
    password, delivered here and remembered by the panel's credential store.
    """
    plan = _plans().get(body.group)
    if not plan:
        raise HTTPException(status_code=422, detail="Unknown plan group.")
    row = get_db().query_one('SELECT "Rate" FROM "PanelUser" WHERE "UserID" = :i',
                             {"i": user["UserID"]})
    rate = float(row["Rate"] or 100) if row else 100.0
    unit = _rate_price(plan["price"], rate)
    total = unit * body.count

    # Pre-flight: the wallet must cover the batch before anything is created.
    bal = wallet.balance(user["UserID"])
    if bal < total:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough credit: the batch costs {int(total):,} and the wallet holds {int(bal):,}.",
        )

    hub = _default_hub()
    if not hub:
        raise HTTPException(status_code=422, detail="No hub is available on the server.")

    base = body.prefix.strip().lower()
    base = "".join(c for c in base if c in _NAME_OK).strip("._-")[:40]
    if not base:
        # an invented base that can never collide with the operator's naming
        base = f"r{user['UserID']}x{secrets.token_hex(2)}"
    used: set[str] = set()
    names: list[str] = []
    for _ in range(body.count):
        name = base
        i = 0
        while name.casefold() in used or _user_exists(hub, name):
            i += 1
            name = f"{base}{i:02d}"
            if i > 999:
                raise HTTPException(status_code=422,
                                    detail="Could not find free usernames; try another prefix.")
        used.add(name.casefold())
        names.append(name)

    # Sell every account, then debit the wallet once for the whole batch.
    bought: list[Wire] = []
    try:
        for name in names:
            password = suggest_password()
            out = perform_sale(
                hub, name, months=_MONTHS.get(body.group, 1), group=body.group,
                realname=f"reseller {user['Username']}", password=password,
                volume_gb=plan["volume_gb"], max_online=plan["max_online"],
                price=unit, currency=str(get_setting("sale_currency") or ""),
                buyer=f"reseller {user['Username']}", extend_if_exists=False,
                actor={"Username": user["Username"]}, source="reseller",
            )
            bought.append({"username": name, "password": password,
                           "expire": out["expire"][:10], "price": unit})
    except Exception as e:  # noqa: BLE001 -- report honestly what got made
        raise HTTPException(status_code=500, detail=f"Sale failed mid-batch: {e}") from None

    balance_after = wallet.apply(
        user["UserID"], -total,
        note=f"خرید {len(bought)} اکانت ({body.group})",
        by=user["Username"], actor={"Username": user["Username"]},
        action="reseller.purchase",
    )
    return {"Purchases": bought, "Total_f64": total, "Balance_f64": balance_after,
            "Currency_str": str(get_setting("sale_currency") or "")}


@self_router.get("/reseller/purchases")
def reseller_purchases(limit: int = Query(default=100, ge=1, le=500),
                       user: dict = CurrentUser) -> Wire:
    rows = get_db().query_all(
        'SELECT * FROM "Sale" WHERE "IsDeleted" = 0 AND "CreatedBy" = :me '
        'ORDER BY "SaleID" DESC LIMIT :n',
        {"me": user["Username"], "n": limit},
    )
    total = get_db().query_one(
        'SELECT COUNT(*) AS n, COALESCE(SUM("Price"), 0) AS s FROM "Sale" '
        'WHERE "IsDeleted" = 0 AND "CreatedBy" = :me',
        {"me": user["Username"]},
    )
    return {"SaleList": [dict(r) for r in rows],
            "Count_u32": int(total["n"]) if total else 0,
            "Sum_f64": float(total["s"]) if total else 0.0}


@self_router.get("/reseller/wallet")
def reseller_wallet(limit: int = Query(default=50, ge=1, le=200),
                    user: dict = CurrentUser) -> Wire:
    return {"TxList": wallet.transactions(user["UserID"], limit),
            "Balance_f64": wallet.balance(user["UserID"])}


# ── the operator's side ──────────────────────────────────────────────────────
class ResellerIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    rate: float = Field(default=100, ge=1, le=100)


class ResellerPatch(BaseModel):
    rate: Optional[float] = Field(default=None, ge=1, le=100)
    is_deleted: Optional[bool] = None


class TopUpIn(BaseModel):
    amount: float = Field(ge=-10**15, le=10**15)
    note: str = Field(default="", max_length=256)


@admin_router.get("/resellers")
def list_resellers(user: dict = CurrentUser) -> Wire:
    _require_admin(user)
    rows = get_db().query_all(
        'SELECT "UserID", "Username", "Rate", "CreatedDate", "IsDeleted" FROM "PanelUser" '
        'WHERE "Role" = \'reseller\' ORDER BY "UserID" DESC'
    )
    out = []
    for r in rows:
        d = dict(r)
        d["Balance_f64"] = wallet.balance(d["UserID"])
        bought = get_db().query_one(
            'SELECT COUNT(*) AS n FROM "Sale" WHERE "IsDeleted" = 0 AND "CreatedBy" = :me',
            {"me": d["Username"]},
        )
        d["Purchases_u32"] = int(bought["n"]) if bought else 0
        out.append(d)
    return {"ResellerList": out, "Count_u32": len(out)}


@admin_router.post("/resellers")
def create_reseller(body: ResellerIn, user: dict = CurrentUser) -> Wire:
    _require_admin(user)
    import sqlite3

    name = body.username.strip()
    if not all(c.isalnum() or c in "._-" for c in name):
        raise HTTPException(status_code=422,
                            detail="The username may contain letters, digits, dot, dash, underscore.")
    db = get_db()
    try:
        uid = db.execute(
            'INSERT INTO "PanelUser"("Username", "PasswordHash", "Role", "Rate", '
            '"CreatedDate", "UpdatedDate") VALUES (:u, :h, \'reseller\', :r, :now, :now)',
            {"u": name, "h": hash_password(body.password), "r": body.rate, "now": utc_now()},
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409,
                            detail="An account with this username already exists.") from None
    record(user, "reseller.created", "panel_user", name, f"rate {body.rate:g}%")
    row = db.query_one('SELECT "UserID", "Username", "Rate" FROM "PanelUser" WHERE "UserID" = :i',
                       {"i": uid})
    return {"Reseller": dict(row) if row else {}}


@admin_router.put("/resellers/{uid}")
def update_reseller(uid: int, body: ResellerPatch, user: dict = CurrentUser) -> Wire:
    _require_admin(user)
    db = get_db()
    row = db.query_one('SELECT * FROM "PanelUser" WHERE "UserID" = :i AND "Role" = \'reseller\'',
                       {"i": uid})
    if row is None:
        raise HTTPException(status_code=404, detail="The reseller was not found.")
    patch = body.model_dump(exclude_none=True)
    if "is_deleted" in patch:
        db.execute('UPDATE "PanelUser" SET "IsDeleted" = :d, "UpdatedDate" = :now '
                   'WHERE "UserID" = :i',
                   {"d": 1 if patch["is_deleted"] else 0, "now": utc_now(), "i": uid})
        record(user, "reseller.updated", "panel_user", row["Username"],
               "disabled" if patch["is_deleted"] else "enabled")
    if "rate" in patch:
        db.execute('UPDATE "PanelUser" SET "Rate" = :r, "UpdatedDate" = :now '
                   'WHERE "UserID" = :i', {"r": patch["rate"], "now": utc_now(), "i": uid})
        record(user, "reseller.updated", "panel_user", row["Username"],
               f"rate {patch['rate']:g}%")
    return {"OK": True}


@admin_router.post("/resellers/{uid}/topup")
def topup_reseller(uid: int, body: TopUpIn, user: dict = CurrentUser) -> Wire:
    _require_admin(user)
    db = get_db()
    row = db.query_one('SELECT * FROM "PanelUser" WHERE "UserID" = :i AND "Role" = \'reseller\'',
                       {"i": uid})
    if row is None:
        raise HTTPException(status_code=404, detail="The reseller was not found.")
    if body.amount == 0:
        raise HTTPException(status_code=422, detail="The amount may not be zero.")
    new_balance = wallet.apply(
        uid, body.amount, note=body.note or ("شارژ کیف پول" if body.amount > 0 else "کسر اعتبار"),
        by=user["Username"], actor=user, action="reseller.topup",
    )
    return {"Balance_f64": new_balance}
