"""Subscription sales: the operator's till and the expiry calendar.

Three things live here:

* ``POST /hubs/{hub}/sales`` -- sell one subscription: create (or extend)
  the VPN user on SoftEther with the duration group's expiry, and write a
  row in the panel's own ``Sale`` ledger with the price that was charged.
* ``GET /sales`` -- the recent ledger, newest first.
* ``GET /expirations`` -- every user on the server whose subscription ends
  within a window (or already ended), sorted by the date it ends, so the
  operator can renew before the customer calls.

The pricing itself is a panel setting (``sale_pricing`` / ``sale_currency``)
edited from the Sales page; the backend only validates its shape and stores
what the page sends.
"""
from __future__ import annotations

import calendar
import secrets
import string
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from ..audit import record
from ..db import get_db, utc_now
from ..deps import CurrentUser
from ..se import rpc

router = APIRouter(tags=["sales"])

Wire = dict[str, Any]

#: SoftEther stores "no expiry" as the epoch sentinel; it is not a date.
NEVER = datetime(1970, 1, 1)


def _add_months(base: datetime, months: int) -> datetime:
    """Whole calendar months, landing on the same day of the month."""
    m = base.month - 1 + months
    y = base.year + m // 12
    mo = m % 12 + 1
    d = min(base.day, calendar.monthrange(y, mo)[1])
    return base.replace(year=y, month=mo, day=d)


def _parse_expire(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def suggest_password(length: int = 12) -> str:
    """A readable password: no look-alikes, digits and letters balanced."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class SaleIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=64)
    realname: str = Field(default="", max_length=128)
    group: str = Field(default="", max_length=64)
    months: int = Field(ge=1, le=120)
    price: float = Field(default=0, ge=0)
    currency: str = Field(default="", max_length=24)
    buyer: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=512)
    extend_if_exists: bool = Field(default=False)


@router.post("/hubs/{hub}/sales")
def sell_subscription(hub: str, body: SaleIn, user: dict = CurrentUser) -> Wire:
    """Sell one subscription: the VPN user, its expiry, and the ledger row."""
    name = body.username.strip()
    if not name:
        raise HTTPException(status_code=422, detail="The username may not be empty.")

    now = datetime.now()
    expire = _add_months(now, body.months)

    # An existing account is either extended (keeping paid days) or refused;
    # silently overwriting one would destroy a customer's credential.
    existing: Wire = {}
    try:
        existing = rpc("GetUser", {"HubName_str": hub, "Name_str": name})
    except Exception:  # noqa: BLE001 -- not found is the normal path
        existing = {}
    if existing.get("Name_str"):
        if not body.extend_if_exists:
            raise HTTPException(
                status_code=409,
                detail=f"A user named {name} already exists on hub {hub}. "
                "Enable 'extend' to add months to that account instead.",
            )
        current = _parse_expire(existing.get("ExpireTime_dt"))
        base = now
        if current is not None and current.year > 1970 and current > now:
            base = current
        expire = _add_months(base, body.months)

    wire: Wire = {
        "HubName_str": hub,
        "Name_str": name,
        "Realname_utf": body.realname,
        "Note_utf": body.note,
        "AuthType_u32": 1,
        "ExpireTime_dt": expire.isoformat(),
    }
    if body.group:
        wire["GroupName_str"] = body.group
    if body.password:
        wire["Auth_Password_str"] = body.password

    if existing.get("Name_str"):
        # Round-trip so a sale of an existing account changes only the expiry.
        round_trip = {
            k: v for k, v in existing.items()
            if not k.startswith(("Recv.", "Send.")) and k != "Auth_Password_str"
        }
        round_trip.update({k: v for k, v in wire.items() if k != "HubName_str"})
        rpc("SetUser", {**round_trip, "HubName_str": hub, "Name_str": name})
    else:
        rpc("CreateUser", wire)

    db = get_db()
    sale_id = db.execute(
        'INSERT INTO "Sale"("HubName", "UserName", "GroupName", "Months", "Price", '
        '"Currency", "BuyerName", "Note", "CreatedDate", "CreatedBy") '
        "VALUES (:hub, :user, :grp, :months, :price, :cur, :buyer, :note, :now, :by)",
        {
            "hub": hub, "user": name, "grp": body.group, "months": body.months,
            "price": body.price, "cur": body.currency, "buyer": body.buyer,
            "note": body.note, "now": utc_now(), "by": str(user.get("Username", "")),
        },
    )
    record(user, "sale.completed", "vpn_user", name,
           f"hub {hub} {body.group} {body.months}m price {body.price} {body.currency}")
    row = db.query_one('SELECT * FROM "Sale" WHERE "SaleID" = :id', {"id": sale_id})
    return {
        "sale": dict(row) if row else {},
        "user": {"HubName_str": hub, "Name_str": name, "ExpireTime_dt": expire.isoformat()},
    }


@router.get("/sales")
def list_sales(limit: int = Query(default=100, ge=1, le=500),
               user: dict = CurrentUser) -> Wire:
    rows = get_db().query_all(
        'SELECT * FROM "Sale" WHERE "IsDeleted" = 0 ORDER BY "SaleID" DESC LIMIT :n',
        {"n": limit},
    )
    total = get_db().query_one(
        'SELECT COUNT(*) AS n, COALESCE(SUM("Price"), 0) AS sum FROM "Sale" WHERE "IsDeleted" = 0'
    )
    return {"SaleList": [dict(r) for r in rows], "Count_u32": total["n"] if total else 0,
            "PriceSum_f64": float(total["sum"]) if total else 0.0}


class ExpirationRow(BaseModel):
    hub: str
    name: str
    realname: str = ""
    group: str = ""
    expire: str
    days_left: float
    status: str  # expired | critical | soon | later


@router.get("/expirations")
def expirations(days: int = Query(default=30, ge=0, le=3650),
                user: dict = CurrentUser) -> Wire:
    """Every dated subscription ending within ``days``, soonest first.

    Expired accounts are always included -- they are the reason the report
    exists -- with a negative ``days_left``.
    """
    now = datetime.now()
    horizon = now.timestamp() + days * 86400
    out: list[dict[str, Any]] = []

    hubs = rpc("EnumHub", {}).get("HubList", []) or []
    for hub_wire in hubs:
        hub = str(hub_wire.get("HubName_str", ""))
        if not hub:
            continue
        try:
            users = rpc("EnumUser", {"HubName_str": hub}).get("UserList", []) or []
        except Exception:  # noqa: BLE001 -- one unreadable hub must not kill the report
            continue
        for u in users:
            expire = _parse_expire(u.get("ExpireTime_dt"))
            if expire is None or expire.year <= 1970:
                continue  # never expires: nothing to renew
            ts = expire.timestamp()
            if ts > horizon:
                continue
            left = (ts - now.timestamp()) / 86400.0
            status = (
                "expired" if left < 0
                else "critical" if left <= 7
                else "soon" if left <= 14
                else "later"
            )
            out.append({
                "hub": hub,
                "name": str(u.get("Name_str", "")),
                "realname": str(u.get("Realname_utf", "")),
                "group": str(u.get("GroupName_str", "")),
                "expire": expire.isoformat(),
                "days_left": round(left, 2),
                "status": status,
            })

    out.sort(key=lambda r: r["expire"])
    expired = sum(1 for r in out if r["status"] == "expired")
    critical = sum(1 for r in out if r["status"] == "critical")
    return {
        "Expirations": out,
        "Count_u32": len(out),
        "Expired_u32": expired,
        "Critical_u32": critical,
    }
