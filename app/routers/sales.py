"""Subscription sales: the operator's till and the expiry calendar.

Four things live here:

* ``POST /hubs/{hub}/sales`` -- sell one subscription: create (or extend)
  the VPN user on SoftEther with the duration group's expiry, write a
  row in the panel's own ``Sale`` ledger with the price that was charged,
  and apply the plan's limits: a traffic volume becomes a quota (with the
  meter zeroed at the moment of sale, and any old block lifted -- the
  customer paid), a concurrent-session ceiling is remembered for the
  online-limit tick.
* ``GET /sales`` -- the recent ledger, newest first, with each sold
  account's live usage beside it: sessions online against the ceiling,
  bytes moved against the volume, and whether enforcement has bitten.
* ``GET /expirations`` -- every user on the server whose subscription ends
  within a window (or already ended), sorted by the date it ends, so the
  operator can renew before the customer calls.

The plans themselves (price, volume, online count per duration group) are a
panel setting (``sale_pricing`` / ``sale_currency``) edited from the Sales
page; the backend only validates their shape and stores what the page sends.
"""
from __future__ import annotations

import calendar
import secrets
import string
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from ..audit import record
from ..db import get_db, utc_now
from ..deps import CurrentUser
from ..se import rpc
from ..services import quota

router = APIRouter(tags=["sales"])

Wire = dict[str, Any]

#: SoftEther stores "no expiry" as the epoch sentinel; it is not a date.
NEVER = datetime(1970, 1, 1)

GB = 1024 ** 3


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
    # The plan's limits, as the Sales page prefilled them: how much traffic
    # the subscription may move (GB, 0 = unlimited) and how many sessions
    # may be online at once (0 = unlimited). Both are snapshotted onto the
    # ledger row, so a later plan change never rewrites history.
    volume_gb: float = Field(default=0, ge=0, le=1_000_000)
    max_online: int = Field(default=0, ge=0, le=1_000)
    buyer: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=512)
    extend_if_exists: bool = Field(default=False)
    # An optional discount coupon: the code is validated here (exists,
    # active, within its window and use count), the taken amount is
    # computed from the list price and frozen onto the ledger row.
    coupon_code: str = Field(default="", max_length=64)


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

    # --- the plan's limits -------------------------------------------------
    volume_bytes = int(round(body.volume_gb * GB))
    if volume_bytes > 0:
        # The ceiling rides on the quota machinery: the tick blocks the user
        # (and cuts their sessions) the moment the counter passes it. The
        # meter starts at zero *here* -- SoftEther's counters never reset,
        # so the baseline moves up to the current reading -- and an old
        # block lifts: the customer just paid, and a paying customer is
        # entitled to a working account.
        quota.save("user", hub, name, volume_bytes, "total", True)
        quota.reset_transfer("user", hub, name)
    else:
        # An unlimited plan lifts any ceiling a previous sale put up -- the
        # baseline record survives, the ceiling and its block do not.
        quota.delete("user", hub, name)

    db = get_db()

    # --- the coupon, if the sale carries one -------------------------------
    # A code only bites a real price; every failure is a 422 the till page
    # shows inline. The discount is computed from the *list* price the
    # operator charged, rounded to a whole unit, never above it.
    discount = 0.0
    coupon_code = body.coupon_code.strip().upper()
    if coupon_code and body.price > 0:
        cpn = db.query_one('SELECT * FROM "Coupon" WHERE "Code" = :c', {"c": coupon_code})
        if cpn is None:
            raise HTTPException(status_code=422, detail="The coupon code was not found.")
        if not cpn["IsActive"]:
            raise HTTPException(status_code=422, detail="This coupon is deactivated.")
        if cpn["ExpiresDate"] and str(cpn["ExpiresDate"]) < date.today().isoformat():
            raise HTTPException(status_code=422, detail="This coupon has expired.")
        if cpn["MaxUses"] > 0 and cpn["UsedCount"] >= cpn["MaxUses"]:
            raise HTTPException(status_code=422, detail="This coupon has run out of uses.")
        discount = min(round(body.price * float(cpn["PercentOff"]) / 100.0), body.price)
        db.execute(
            'UPDATE "Coupon" SET "UsedCount" = "UsedCount" + 1 WHERE "Code" = :c',
            {"c": coupon_code},
        )

    sale_id = db.execute(
        'INSERT INTO "Sale"("HubName", "UserName", "GroupName", "Months", "Price", '
        '"Currency", "VolumeBytes", "MaxOnline", "Discount", "CouponCode", '
        '"BuyerName", "Note", "CreatedDate", "CreatedBy") '
        "VALUES (:hub, :user, :grp, :months, :price, :cur, :vol, :mon, :disc, :cpn, "
        ":buyer, :note, :now, :by)",
        {
            "hub": hub, "user": name, "grp": body.group, "months": body.months,
            "price": body.price, "cur": body.currency, "vol": volume_bytes,
            "mon": body.max_online, "disc": discount, "cpn": coupon_code,
            "buyer": body.buyer,
            "note": body.note, "now": utc_now(), "by": str(user.get("Username", "")),
        },
    )
    record(user, "sale.completed", "vpn_user", name,
           f"hub {hub} {body.group} {body.months}m price {body.price} {body.currency} "
           f"volume {body.volume_gb:g}GB online {body.max_online}"
           + (f" coupon {coupon_code} -{discount:g}" if discount else ""))
    row = db.query_one('SELECT * FROM "Sale" WHERE "SaleID" = :id', {"id": sale_id})
    return {
        "sale": dict(row) if row else {},
        "user": {"HubName_str": hub, "Name_str": name, "ExpireTime_dt": expire.isoformat()},
    }


def _usage_status() -> dict[str, Wire]:
    """Live usage for every account the ledger has sold, keyed by
    ``hub/username`` (case-folded, as SoftEther matches).

    Three sources, one pass: the latest sale per account carries the
    ceilings it was sold with, the quota rows carry the bytes moved and
    whether enforcement has bitten, and one ``EnumSession`` per hub counts
    who is online right now. Cached quota readings are used so the ledger
    costs one request regardless of size; the tick keeps them fresh.
    """
    rows = get_db().query_all(
        'SELECT * FROM "Sale" WHERE "IsDeleted" = 0 ORDER BY "SaleID" DESC'
    )
    latest: dict[str, Wire] = {}
    for r in rows:
        key = f"{r['HubName']}/{str(r['UserName']).casefold()}"
        if key not in latest:
            latest[key] = dict(r)

    online: dict[str, int] = {}
    try:
        for hub_wire in rpc("EnumHub", {}).get("HubList", []) or []:
            hub = str(hub_wire.get("HubName_str", ""))
            if not hub:
                continue
            for s in rpc("EnumSession", {"HubName_str": hub}).get("SessionList", []) or []:
                name = str(s.get("Username_str", "")).casefold()
                if name:
                    online[f"{hub}/{name}"] = online.get(f"{hub}/{name}", 0) + 1
    except Exception:  # noqa: BLE001 -- usage is decoration, never a 500
        pass

    out: dict[str, Wire] = {}
    for key, sale in latest.items():
        hub, name = sale["HubName"], sale["UserName"]
        volume_bytes = int(sale.get("VolumeBytes") or 0)
        max_online = int(sale.get("MaxOnline") or 0)
        used = 0
        limit_bytes = volume_bytes
        blocked = False
        q = quota.get("user", hub, name)
        if q:
            used = int(q.get("used_bytes") or 0)
            if q.get("has_limit"):
                limit_bytes = int(q.get("limit_bytes") or 0)
            blocked = bool(q.get("blocked"))
        now_online = online.get(key, 0)
        out[key] = {
            "hub": hub,
            "name": name,
            "online_now": now_online,
            "max_online": max_online,
            "used_bytes": used,
            "volume_bytes": volume_bytes,
            "limit_bytes": limit_bytes,
            "blocked": blocked,
            "over_volume": bool(volume_bytes > 0 and used >= volume_bytes),
            "over_online": bool(max_online > 0 and now_online > max_online),
        }
    return out


@router.get("/sales")
def list_sales(limit: int = Query(default=100, ge=1, le=500),
               user: dict = CurrentUser) -> Wire:
    rows = get_db().query_all(
        'SELECT * FROM "Sale" WHERE "IsDeleted" = 0 ORDER BY "SaleID" DESC LIMIT :n',
        {"n": limit},
    )
    total = get_db().query_one(
        'SELECT COUNT(*) AS n, COALESCE(SUM("Price" - "Discount"), 0) AS sum '
        'FROM "Sale" WHERE "IsDeleted" = 0'
    )
    return {"SaleList": [dict(r) for r in rows], "Count_u32": total["n"] if total else 0,
            "PriceSum_f64": float(total["sum"]) if total else 0.0,
            "Status": _usage_status()}


class CouponIn(BaseModel):
    code: str = Field(min_length=3, max_length=64)
    percent_off: float = Field(ge=1, le=100)
    max_uses: int = Field(default=0, ge=0, le=1_000_000)  # 0 = unlimited
    expires: str = Field(default="", max_length=10)       # YYYY-MM-DD or "" = never
    is_active: bool = Field(default=True)


class CouponPatch(BaseModel):
    percent_off: Optional[float] = Field(default=None, ge=1, le=100)
    max_uses: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    expires: Optional[str] = Field(default=None, max_length=10)
    is_active: Optional[bool] = None


@router.get("/coupons")
def list_coupons(user: dict = CurrentUser) -> Wire:
    rows = get_db().query_all('SELECT * FROM "Coupon" ORDER BY "CouponID" DESC')
    return {"CouponList": [dict(r) for r in rows]}


@router.post("/coupons")
def create_coupon(body: CouponIn, user: dict = CurrentUser) -> Wire:
    code = body.code.strip().upper()
    if len(code) < 3:
        raise HTTPException(status_code=422, detail="The coupon code needs at least 3 characters.")
    if body.expires:
        try:
            date.fromisoformat(body.expires)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="The coupon expiry must be a YYYY-MM-DD date."
            ) from None
    db = get_db()
    if db.query_one('SELECT 1 FROM "Coupon" WHERE "Code" = :c', {"c": code}):
        raise HTTPException(status_code=409, detail="A coupon with this code already exists.")
    db.execute(
        'INSERT INTO "Coupon"("Code", "PercentOff", "MaxUses", "ExpiresDate", '
        '"IsActive", "CreatedDate", "CreatedBy") '
        "VALUES (:c, :p, :m, :e, :a, :now, :by)",
        {"c": code, "p": body.percent_off, "m": body.max_uses, "e": body.expires,
         "a": 1 if body.is_active else 0, "now": utc_now(),
         "by": str(user.get("Username", ""))},
    )
    record(user, "coupon.created", "coupon", code,
           f"{body.percent_off:g}% max {body.max_uses or 'unlimited'} "
           f"until {body.expires or '-'}")
    row = db.query_one('SELECT * FROM "Coupon" WHERE "Code" = :c', {"c": code})
    return {"Coupon": dict(row) if row else {}}


@router.put("/coupons/{code}")
def update_coupon(code: str, body: CouponPatch, user: dict = CurrentUser) -> Wire:
    code = code.strip().upper()
    db = get_db()
    row = db.query_one('SELECT * FROM "Coupon" WHERE "Code" = :c', {"c": code})
    if row is None:
        raise HTTPException(status_code=404, detail="The coupon code was not found.")
    patch = body.model_dump(exclude_none=True)
    if "expires" in patch and patch["expires"]:
        try:
            date.fromisoformat(patch["expires"])
        except ValueError:
            raise HTTPException(
                status_code=422, detail="The coupon expiry must be a YYYY-MM-DD date."
            ) from None
    if patch:
        sets = ", ".join(f'"{k}" = :{k}' for k in patch)
        params = {**patch, "code": code}
        if "is_active" in patch:
            # snake_case column names do not bind in SQLite; rename by hand.
            sets = sets.replace('"is_active" = :is_active', '"IsActive" = :is_active')
        if "percent_off" in patch:
            sets = sets.replace('"percent_off" = :percent_off', '"PercentOff" = :percent_off')
        if "max_uses" in patch:
            sets = sets.replace('"max_uses" = :max_uses', '"MaxUses" = :max_uses')
        if "expires" in patch:
            sets = sets.replace('"expires" = :expires', '"ExpiresDate" = :expires')
        db.execute(f'UPDATE "Coupon" SET {sets} WHERE "Code" = :code', params)
        record(user, "coupon.updated", "coupon", code, str(patch))
    row = db.query_one('SELECT * FROM "Coupon" WHERE "Code" = :c', {"c": code})
    return {"Coupon": dict(row) if row else {}}


@router.delete("/coupons/{code}")
def delete_coupon(code: str, user: dict = CurrentUser) -> Wire:
    code = code.strip().upper()
    db = get_db()
    if not db.query_one('SELECT 1 FROM "Coupon" WHERE "Code" = :c', {"c": code}):
        raise HTTPException(status_code=404, detail="The coupon code was not found.")
    db.execute('DELETE FROM "Coupon" WHERE "Code" = :c', {"c": code})
    record(user, "coupon.deleted", "coupon", code, "")
    return {"OK": True}


@router.get("/sales/stats")
def sales_stats(days: int = Query(default=30, ge=7, le=365),
                user: dict = CurrentUser) -> Wire:
    """The till at a glance: totals, the daily curve, and the popular plans.

    Every figure is net of discounts -- what was actually charged -- and the
    daily curve is zero-filled, so a quiet day reads as a quiet day instead
    of vanishing from the chart.
    """
    db = get_db()
    total = db.query_one(
        'SELECT COUNT(*) AS n, COALESCE(SUM("Price"), 0) AS gross, '
        'COALESCE(SUM("Discount"), 0) AS disc '
        'FROM "Sale" WHERE "IsDeleted" = 0'
    )
    cur = db.query_one(
        'SELECT "Currency" AS c FROM "Sale" WHERE "IsDeleted" = 0 AND "Currency" != \'\' '
        'GROUP BY "Currency" ORDER BY COUNT(*) DESC LIMIT 1'
    )
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = db.query_all(
        'SELECT substr("CreatedDate", 1, 10) AS d, COUNT(*) AS n, '
        'COALESCE(SUM("Price" - "Discount"), 0) AS net FROM "Sale" '
        'WHERE "IsDeleted" = 0 AND substr("CreatedDate", 1, 10) >= :since '
        'GROUP BY d ORDER BY d',
        {"since": since},
    )
    by_day = {str(r["d"]): r for r in rows}
    d0 = date.today() - timedelta(days=days - 1)
    day_list = []
    for i in range(days):
        d = (d0 + timedelta(days=i)).isoformat()
        r = by_day.get(d)
        day_list.append({"date": d, "count": int(r["n"]) if r else 0,
                         "net": float(r["net"]) if r else 0.0})
    plans = db.query_all(
        'SELECT "GroupName" AS g, COUNT(*) AS n, '
        'COALESCE(SUM("Price" - "Discount"), 0) AS net '
        'FROM "Sale" WHERE "IsDeleted" = 0 '
        'GROUP BY "GroupName" ORDER BY n DESC LIMIT 8'
    )
    return {
        "Count_u32": int(total["n"]) if total else 0,
        "Gross_f64": float(total["gross"]) if total else 0.0,
        "Discount_f64": float(total["disc"]) if total else 0.0,
        "Net_f64": float(total["gross"]) - float(total["disc"]) if total else 0.0,
        "Currency": str(cur["c"]) if cur else "",
        "Days_u32": days,
        "ByDay": day_list,
        "ByPlan": [{"group": str(r["g"]) or "—", "count": int(r["n"]),
                    "net": float(r["net"])} for r in plans],
    }


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
