"""Concurrent-session ceilings: the online half of a sold subscription.

A plan sold through the Sales page can say how many sessions an account may
have online at once -- the number the customer actually bought, written on
the ``Sale`` row as ``MaxOnline`` (0 = no ceiling). SoftEther has no native
per-user session limit, so the panel holds this one itself: a tick, on the
same clock as the traffic quotas, that counts each watched account's live
sessions and deletes the *newest* ones beyond the allowance.

**Which sessions go.** The oldest survive. A customer's first connection is
the one doing their work; whatever squeezed in past the ceiling came later,
and cutting the newest is what makes the ceiling felt the moment it is
passed, without disturbing the session that was there first.

**Where the count comes from.** ``EnumSession`` per hub, grouped by the
session's ``Username_str`` -- the same source the session history is built
from, so the number the ledger shows and the number this acts on cannot
disagree by more than a tick.

**What it never touches.** Accounts sold with ``MaxOnline`` 0, accounts
whose latest sale carried no ceiling, and anything SoftEther reports for a
hub it cannot list. One unreadable hub is a gap in the pass, not a failure.
"""
from __future__ import annotations

import logging
from typing import Any

from ..audit import record
from ..db import get_db

logger = logging.getLogger(__name__)

Wire = dict[str, Any]


def watched_accounts() -> dict[str, dict[str, Any]]:
    """The accounts with an online ceiling, keyed ``hub/user`` case-folded.

    Only each account's *latest* sale counts: renewing with a new plan
    replaces the old ceiling, the way replacing any part of a plan does.
    """
    rows = get_db().query_all(
        'SELECT s."HubName" AS hub, s."UserName" AS name, s."MaxOnline" AS max_online '
        'FROM "Sale" s JOIN (SELECT "HubName", LOWER("UserName") AS uk, '
        'MAX("SaleID") AS mid FROM "Sale" '
        'WHERE "IsDeleted" = 0 AND "MaxOnline" > 0 '
        'GROUP BY "HubName", LOWER("UserName")) m '
        'ON s."HubName" = m."HubName" AND LOWER(s."UserName") = m.uk '
        'AND s."SaleID" = m.mid'
    )
    return {f"{r['hub']}/{str(r['name']).casefold()}": dict(r) for r in rows}


def _live_sessions(hub: str) -> list[dict[str, Any]]:
    """One hub's sessions, oldest first, as (name, user, started)."""
    from ..se import rpc

    entries = rpc("EnumSession", {"HubName_str": hub}).get("SessionList", []) or []
    out = []
    for s in entries:
        out.append({
            "name": str(s.get("Name_str", "")),
            "user": str(s.get("Username_str", "")).casefold(),
            "started": str(s.get("CreatedTime_dt", "")),
        })
    out.sort(key=lambda s: s["started"])
    return out


def tick() -> dict[str, Any]:
    """One enforcement pass on the quota clock.

    Returns a small summary for the sampler's log: how many accounts carry
    a ceiling, how many were over, and what was cut.
    """
    from ..se import connection, rpc

    try:
        limits = watched_accounts()
    except Exception as exc:  # noqa: BLE001 - before the database is ready, wait
        return {"watched": 0, "skipped": str(exc)}
    if not limits:
        return {"watched": 0}
    if not connection()["configured"]:
        return {"watched": len(limits), "skipped": "not configured"}

    by_hub: dict[str, dict[str, dict[str, Any]]] = {}
    for key, row in limits.items():
        by_hub.setdefault(row["hub"], {})[key] = row

    cut: list[dict[str, Any]] = []
    over = 0
    for hub, accounts in by_hub.items():
        try:
            sessions = _live_sessions(hub)
        except Exception as exc:  # noqa: BLE001 - an offline hub is a gap
            logger.debug("online tick could not list sessions on %s: %s", hub, exc)
            continue
        counted: dict[str, list[dict[str, Any]]] = {}
        for s in sessions:
            if s["user"]:
                counted.setdefault(s["user"], []).append(s)
        for key, row in accounts.items():
            user = str(row["name"]).casefold()
            max_online = int(row["max_online"] or 0)
            if max_online <= 0:
                continue
            live = counted.get(user, [])
            excess = len(live) - max_online
            if excess <= 0:
                continue
            over += 1
            # The newest sessions are the ones past the ceiling; the list is
            # oldest-first, so the excess sits at the end of it.
            for s in live[max_online:]:
                try:
                    rpc("DeleteSession", {"HubName_str": hub, "Name_str": s["name"]})
                    cut.append({"hub": hub, "user": str(row["name"]), "session": s["name"]})
                except Exception as exc:  # noqa: BLE001 - a session already gone is fine
                    logger.debug("could not cut %s on %s: %s", s["name"], hub, exc)
            if any(c["hub"] == hub and c["user"] == str(row["name"]) for c in cut):
                record(
                    None,
                    "online.excess_cut",
                    "vpn_user",
                    str(row["name"]),
                    f"hub {hub}: over the {max_online}-session ceiling, "
                    f"{excess} session(s) cut",
                )
    return {"watched": len(limits), "over": over, "cut": cut}
