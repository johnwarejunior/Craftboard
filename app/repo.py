"""Shared queries and business rules that touch the database."""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import loads, now, row, rows
from .engines.pricing import Completed, PriceChange

ACTIVE_STATUSES = ("awaiting_deposit", "queued", "in_progress", "in_review", "approved")
OPEN_STATUSES = ACTIVE_STATUSES + ("delivered",)

# What each plan unlocks (mirrors the pitch deck's pricing slide).
PLANS: dict[str, dict[str, Any]] = {
    "free":   {"max_active": 3,    "waitlist": False, "analytics": False, "goal_agent": False, "claude_screening": False},
    "studio": {"max_active": None, "waitlist": True,  "analytics": True,  "goal_agent": False, "claude_screening": False},
    "pro":    {"max_active": None, "waitlist": True,  "analytics": True,  "goal_agent": True,  "claude_screening": True},
}


def plan_allows(creator: dict[str, Any], feature: str) -> bool:
    return bool(PLANS[creator["plan"]][feature])


def effective_slots(creator: dict[str, Any]) -> int:
    cap = PLANS[creator["plan"]]["max_active"]
    return min(creator["slots"], cap) if cap else creator["slots"]


def active_count(conn: sqlite3.Connection, creator_id: int) -> int:
    q = f"SELECT COUNT(*) FROM commissions WHERE creator_id=? AND status IN ({','.join('?' * len(ACTIVE_STATUSES))})"
    return conn.execute(q, (creator_id, *ACTIVE_STATUSES)).fetchone()[0]


def active_hours(conn: sqlite3.Connection, creator_id: int) -> float:
    q = f"""SELECT COALESCE(SUM(t.est_hours), 0) FROM commissions c LEFT JOIN commission_types t ON t.id=c.type_id
            WHERE c.creator_id=? AND c.status IN ({','.join('?' * len(ACTIVE_STATUSES))})"""
    return float(conn.execute(q, (creator_id, *ACTIVE_STATUSES)).fetchone()[0])


def waitlist_count(conn: sqlite3.Connection, creator_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM waitlist WHERE creator_id=? AND status='waiting'", (creator_id,)).fetchone()[0]


def intake_state(conn: sqlite3.Connection, creator: dict[str, Any]) -> dict[str, Any]:
    slots = effective_slots(creator)
    active = active_count(conn, creator["id"])
    if creator["intake_mode"] == "closed":
        is_open = False
    elif creator["intake_mode"] == "open":
        is_open = True
    else:
        is_open = active < slots
    return {
        "slots": slots,
        "filled": active,
        "open": is_open,
        "waitlist": waitlist_count(conn, creator["id"]),
        "waitlist_enabled": plan_allows(creator, "waitlist"),
    }


def get_or_create_client(conn: sqlite3.Connection, creator_id: int, handle: str, email: str | None = None) -> int:
    handle = handle.strip().lstrip("@")
    r = conn.execute("SELECT id, email FROM clients WHERE creator_id=? AND handle=?", (creator_id, handle)).fetchone()
    if r:
        if email and not r["email"]:
            conn.execute("UPDATE clients SET email=? WHERE id=?", (email, r["id"]))
        return int(r["id"])
    cur = conn.execute("INSERT INTO clients (creator_id, handle, email, created_at) VALUES (?,?,?,?)",
                       (creator_id, handle, email, now()))
    return int(cur.lastrowid)


def commission_view(conn: sqlite3.Connection, c: dict[str, Any], *, for_client: bool = False) -> dict[str, Any]:
    client = conn.execute("SELECT handle, email FROM clients WHERE id=?", (c["client_id"],)).fetchone()
    ctype = row(conn.execute("SELECT * FROM commission_types WHERE id=?", (c["type_id"],)).fetchone()) if c["type_id"] else None
    revs = rows(conn.execute("SELECT * FROM revision_requests WHERE commission_id=? ORDER BY id", (c["id"],)).fetchall())
    for r in revs:
        r["reasons"] = loads(r.pop("reasons_json"), [])
    pays = rows(conn.execute("SELECT kind, amount_cents, provider, status, paid_at FROM payments WHERE commission_id=? ORDER BY id",
                             (c["id"],)).fetchall())
    files = rows(conn.execute("SELECT id, filename, size_bytes, uploaded_at FROM deliverables WHERE commission_id=? ORDER BY id",
                              (c["id"],)).fetchall())
    upgrades = sum(r["quote_cents"] for r in revs if r["status"] == "accepted")
    view = {
        "id": c["id"],
        "title": c["title"],
        "status": c["status"],
        "client": client["handle"] if client else None,
        "type": ctype["name"] if ctype else None,
        "brief": loads(c["brief_json"], {}),
        "brief_locked_at": c["brief_locked_at"],
        "price_cents": c["price_cents"],
        "base_price_cents": c["price_cents"] - upgrades,
        "deposit_cents": c["deposit_cents"],
        "balance_cents": c["price_cents"] - c["deposit_cents"],
        "upgrades_cents": upgrades,
        "revisions_included": c["revisions_included"],
        "revisions_used": c["revisions_used"],
        "revision_requests": revs,
        "payments": pays,
        "files": files,
        "accepted_at": c["accepted_at"],
        "started_at": c["started_at"],
        "delivered_at": c["delivered_at"],
        "completed_at": c["completed_at"],
    }
    if not for_client:
        view.update({"portal_token": c["portal_token"], "hours_logged": c["hours_logged"],
                     "client_email": client["email"] if client else None, "source": c["source"]})
    return view


def completed_rows(conn: sqlite3.Connection, creator_id: int) -> list[dict[str, Any]]:
    return rows(conn.execute(
        """SELECT cl.handle AS client_handle, t.name AS type_name, c.price_cents, c.hours_logged, c.revisions_used,
                  EXISTS(SELECT 1 FROM revision_requests r WHERE r.commission_id=c.id AND r.status='accepted'
                         AND r.kind='out_of_scope') AS had_upgrade,
                  c.accepted_at, c.completed_at
           FROM commissions c JOIN clients cl ON cl.id=c.client_id LEFT JOIN commission_types t ON t.id=c.type_id
           WHERE c.creator_id=? AND c.status='completed' ORDER BY c.completed_at""",
        (creator_id,)).fetchall())


def plan_inputs(conn: sqlite3.Connection, creator: dict[str, Any]) -> dict[str, Any]:
    done = completed_rows(conn, creator["id"])
    history = [Completed(d["price_cents"], d["hours_logged"] or 0, d["accepted_at"], d["completed_at"]) for d in done]
    changes = [PriceChange(r["pct"], r["changed_at"]) for r in
               conn.execute("SELECT pct, changed_at FROM price_changes WHERE creator_id=? ORDER BY changed_at",
                            (creator["id"],)).fetchall()]
    cat = conn.execute("SELECT AVG(base_price_cents), AVG(est_hours) FROM commission_types WHERE creator_id=? AND active=1",
                       (creator["id"],)).fetchone()
    return {
        "history": history,
        "price_changes": changes,
        "waitlist": waitlist_count(conn, creator["id"]),
        "slots": effective_slots(creator),
        "active": active_count(conn, creator["id"]),
        "active_hours": active_hours(conn, creator["id"]),
        "catalog_avg_price_cents": int(cat[0] or 0),
        "catalog_avg_hours": float(cat[1] or 3.0),
    }


def creator_public(creator: dict[str, Any]) -> dict[str, Any]:
    keys = ["id", "email", "name", "handle", "plan", "slots", "intake_mode", "revisions_included", "deposit_pct",
            "hours_per_week", "intake_message", "stripe_account_id", "created_at"]
    out = {k: creator[k] for k in keys}
    out["features"] = PLANS[creator["plan"]]
    return out
