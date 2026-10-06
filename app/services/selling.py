"""The sale itself: create or extend the VPN user, apply the plan's limits,
write the ledger row.

Shared by two sellers: the panel's till (``POST /hubs/{hub}/sales``, the
Sales page) and the Telegram bot's admin-approved orders. Both must land on
the same state -- same expiry arithmetic, same quota/online ceilings, same
ledger shape -- or the books and the server drift apart. The coupon is
validated by the caller (it needs HTTP status codes); the taken amount and
code arrive here already computed.
"""
from __future__ import annotations

import calendar
import secrets
from datetime import datetime
from typing import Any, Optional

from ..audit import record
from ..db import get_db, utc_now
from ..se import rpc
from . import quota

Wire = dict[str, Any]

#: SoftEther stores "no expiry" as the epoch sentinel; it is not a date.
NEVER = datetime(1970, 1, 1)

GB = 1024 ** 3


def add_months(base: datetime, months: int) -> datetime:
    """Whole calendar months, landing on the same day of the month."""
    m = base.month - 1 + months
    y = base.year + m // 12
    mo = m % 12 + 1
    d = min(base.day, calendar.monthrange(y, mo)[1])
    return base.replace(year=y, month=mo, day=d)


def parse_expire(raw: Any) -> Optional[datetime]:
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


def perform_sale(
    hub: str,
    name: str,
    *,
    months: int,
    group: str = "",
    realname: str = "",
    note: str = "",
    password: str = "",
    volume_gb: float = 0,
    max_online: int = 0,
    price: float = 0,
    currency: str = "",
    buyer: str = "",
    extend_if_exists: bool = True,
    discount: float = 0,
    coupon_code: str = "",
    actor: Optional[dict] = None,
    source: str = "panel",
) -> Wire:
    """One subscription sold: the VPN user, its expiry, the limits, the ledger.

    An existing account is either extended from its current expiry (keeping
    paid days) or refused -- silently overwriting one would destroy a
    customer's credential. Raises ``ValueError`` on that conflict so each
    caller phrases the refusal its own way.
    """
    now = datetime.now()
    expire = add_months(now, months)

    existing: Wire = {}
    try:
        existing = rpc("GetUser", {"HubName_str": hub, "Name_str": name})
    except Exception:  # noqa: BLE001 -- not found is the normal path
        existing = {}
    created = not existing.get("Name_str")
    if not created:
        if not extend_if_exists:
            raise ValueError(
                f"A user named {name} already exists on hub {hub}. "
                "Enable 'extend' to add months to that account instead."
            )
        current = parse_expire(existing.get("ExpireTime_dt"))
        base = now
        if current is not None and current.year > 1970 and current > now:
            base = current
        expire = add_months(base, months)

    wire: Wire = {
        "HubName_str": hub,
        "Name_str": name,
        "Realname_utf": realname,
        "Note_utf": note,
        "AuthType_u32": 1,
        "ExpireTime_dt": expire.isoformat(),
    }
    if group:
        wire["GroupName_str"] = group
    if password:
        wire["Auth_Password_str"] = password

    if created:
        rpc("CreateUser", wire)
    else:
        # Round-trip so a sale of an existing account changes only the expiry.
        round_trip = {
            k: v for k, v in existing.items()
            if not k.startswith(("Recv.", "Send.")) and k != "Auth_Password_str"
        }
        round_trip.update({k: v for k, v in wire.items() if k != "HubName_str"})
        rpc("SetUser", {**round_trip, "HubName_str": hub, "Name_str": name})

    # --- the plan's limits --------------------------------------------------
    volume_bytes = int(round(volume_gb * GB))
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
    sale_id = db.execute(
        'INSERT INTO "Sale"("HubName", "UserName", "GroupName", "Months", "Price", '
        '"Currency", "VolumeBytes", "MaxOnline", "Discount", "CouponCode", '
        '"BuyerName", "Note", "CreatedDate", "CreatedBy") '
        "VALUES (:hub, :user, :grp, :months, :price, :cur, :vol, :mon, :disc, :cpn, "
        ":buyer, :note, :now, :by)",
        {
            "hub": hub, "user": name, "grp": group, "months": months,
            "price": price, "cur": currency, "vol": volume_bytes,
            "mon": max_online, "disc": discount, "cpn": coupon_code,
            "buyer": buyer,
            "note": note, "now": utc_now(),
            "by": str((actor or {}).get("Username", "") or source),
        },
    )
    record(actor, "sale.completed", "vpn_user", name,
           f"[{source}] hub {hub} {group} {months}m price {price} {currency} "
           f"volume {volume_gb:g}GB online {max_online}"
           + (f" coupon {coupon_code} -{discount:g}" if discount else ""))
    row = db.query_one('SELECT * FROM "Sale" WHERE "SaleID" = :id', {"id": sale_id})
    return {
        "sale": dict(row) if row else {},
        "expire": expire.isoformat(),
        "created": created,
        "password": password,
    }
