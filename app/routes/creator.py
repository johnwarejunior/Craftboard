from __future__ import annotations

import csv
import io
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..config import settings
from ..db import dumps, get_db, loads, now, row, rows
from ..deps import current_creator, require
from ..engines import analytics as analytics_engine
from ..engines.pricing import history_stats, Completed
from ..engines.screening import screen_brief
from ..models import AcceptIn, CommissionTypeIn, CommissionTypePatch, HoursIn, NoteIn
from ..repo import (ACTIVE_STATUSES, OPEN_STATUSES, active_hours, commission_view, completed_rows, effective_slots,
                    intake_state, active_count)
from ..security import portal_token
from ..services import claude, payments

router = APIRouter(prefix="/api", tags=["creator"])


def _cents(dollars: float) -> int:
    return int(round(dollars * 100))


def _type(conn: sqlite3.Connection, creator_id: int, type_id: int) -> dict:
    t = row(conn.execute("SELECT * FROM commission_types WHERE id=? AND creator_id=?", (type_id, creator_id)).fetchone())
    if t is None:
        raise HTTPException(404, "Commission type not found.")
    return t


def _commission(conn: sqlite3.Connection, creator_id: int, cid: int) -> dict:
    c = row(conn.execute("SELECT * FROM commissions WHERE id=? AND creator_id=?", (cid, creator_id)).fetchone())
    if c is None:
        raise HTTPException(404, "Commission not found.")
    return c


def _transition(conn, c: dict, allowed_from: tuple[str, ...], to: str, extra_sql: str = "", params: tuple = ()):
    if c["status"] not in allowed_from:
        raise HTTPException(409, f"Can't do that while the commission is {c['status'].replace('_', ' ')}.")
    conn.execute(f"UPDATE commissions SET status=?{extra_sql} WHERE id=?", (to, *params, c["id"]))


# ---------------------------------------------------------------- commission types

@router.get("/types")
def list_types(creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    return rows(conn.execute("SELECT * FROM commission_types WHERE creator_id=? ORDER BY active DESC, sort_order, id",
                             (creator["id"],)).fetchall())


@router.post("/types", status_code=201)
def create_type(body: CommissionTypeIn, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    cur = conn.execute(
        """INSERT INTO commission_types (creator_id, name, description, base_price_cents, est_hours, characters_included,
           extra_character_pct, sort_order, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (creator["id"], body.name.strip(), body.description, _cents(body.base_price), body.est_hours,
         body.characters_included, body.extra_character_pct,
         conn.execute("SELECT COUNT(*) FROM commission_types WHERE creator_id=?", (creator["id"],)).fetchone()[0], now()),
    )
    return _type(conn, creator["id"], int(cur.lastrowid))


@router.patch("/types/{type_id}")
def update_type(type_id: int, body: CommissionTypePatch, creator: dict = Depends(current_creator),
                conn: sqlite3.Connection = Depends(get_db)):
    t = _type(conn, creator["id"], type_id)
    changes = body.model_dump(exclude_none=True)
    if "base_price" in changes:
        new_price = _cents(changes.pop("base_price"))
        if t["base_price_cents"] > 0 and new_price != t["base_price_cents"]:
            pct = (new_price - t["base_price_cents"]) / t["base_price_cents"] * 100
            conn.execute("INSERT INTO price_changes (creator_id, type_id, pct, changed_at) VALUES (?,?,?,?)",
                         (creator["id"], type_id, round(pct, 2), now()))
        changes["base_price_cents"] = new_price
    if "active" in changes:
        changes["active"] = int(changes["active"])
    if changes:
        cols = ", ".join(f"{k}=?" for k in changes)
        conn.execute(f"UPDATE commission_types SET {cols} WHERE id=?", (*changes.values(), type_id))
    return _type(conn, creator["id"], type_id)


# ---------------------------------------------------------------- dashboard

@router.get("/dashboard")
def dashboard(creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    q = f"SELECT * FROM commissions WHERE creator_id=? AND status IN ({','.join('?' * len(OPEN_STATUSES))}) ORDER BY accepted_at"
    queue = [commission_view(conn, row(c)) for c in conn.execute(q, (creator["id"], *OPEN_STATUSES)).fetchall()]
    done = completed_rows(conn, creator["id"])
    stats = history_stats([Completed(d["price_cents"], d["hours_logged"] or 0, d["accepted_at"], d["completed_at"])
                           for d in done], now())
    pending = conn.execute("SELECT COUNT(*) FROM requests WHERE creator_id=? AND status IN ('pending','info_requested')",
                           (creator["id"],)).fetchone()[0]
    types = conn.execute("SELECT COUNT(*) FROM commission_types WHERE creator_id=? AND active=1", (creator["id"],)).fetchone()[0]
    return {
        "intake": intake_state(conn, creator),
        "queue": queue,
        "pending_requests": pending,
        "has_types": types > 0,
        "last_30_revenue_cents": stats["revenue_buckets_cents"][5],
        "effective_hourly_cents": stats["effective_hourly_cents"],
        "intake_url": f"{settings.base_url}/c/{creator['handle']}",
        "recent": [{"client": d["client_handle"], "type": d["type_name"], "price_cents": d["price_cents"],
                    "completed_at": d["completed_at"]} for d in done[-5:][::-1]],
    }


# ---------------------------------------------------------------- requests

@router.get("/requests")
def list_requests(creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    rs = conn.execute(
        """SELECT r.*, cl.handle AS client, cl.email AS client_email, t.name AS type_name, t.base_price_cents
           FROM requests r JOIN clients cl ON cl.id=r.client_id JOIN commission_types t ON t.id=r.type_id
           WHERE r.creator_id=? AND r.status IN ('pending','info_requested') ORDER BY r.created_at""",
        (creator["id"],)).fetchall()
    out = []
    for r in rs:
        d = dict(r)
        d["brief"] = loads(d.pop("brief_json"), {})
        d["screening"] = loads(d.pop("screening_json"), {})
        d["claude"] = loads(d.pop("claude_json"), None)
        out.append(d)
    return {"requests": out, "intake": intake_state(conn, creator), "claude_available": claude.available()}


def _request(conn, creator_id: int, rid: int) -> dict:
    r = row(conn.execute("SELECT * FROM requests WHERE id=? AND creator_id=?", (rid, creator_id)).fetchone())
    if r is None:
        raise HTTPException(404, "Request not found.")
    if r["status"] not in ("pending", "info_requested"):
        raise HTTPException(409, "This request was already handled.")
    return r


@router.post("/requests/{rid}/accept")
def accept_request(rid: int, body: AcceptIn | None = None, creator: dict = Depends(current_creator),
                   conn: sqlite3.Connection = Depends(get_db)):
    r = _request(conn, creator["id"], rid)
    if active_count(conn, creator["id"]) >= effective_slots(creator):
        raise HTTPException(409, "All slots are full. Deliver a commission or add a slot first.")
    t = _type(conn, creator["id"], r["type_id"])
    brief = loads(r["brief_json"], {})
    if body and body.price is not None:
        price = _cents(body.price)
    else:
        extra = max(0, int(brief.get("characters", 1)) - t["characters_included"])
        price = t["base_price_cents"] + round(t["base_price_cents"] * t["extra_character_pct"] / 100 * extra)
    deposit = round(price * creator["deposit_pct"] / 100)
    ts = now()
    status = "awaiting_deposit" if deposit > 0 else "queued"
    client = conn.execute("SELECT handle FROM clients WHERE id=?", (r["client_id"],)).fetchone()
    cur = conn.execute(
        """INSERT INTO commissions (creator_id, client_id, type_id, request_id, title, brief_json, brief_locked_at, price_cents,
           deposit_cents, revisions_included, status, portal_token, accepted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (creator["id"], r["client_id"], t["id"], rid, f"{t['name']} for @{client['handle']}", r["brief_json"], ts, price,
         deposit, creator["revisions_included"], status, portal_token(), ts),
    )
    conn.execute("UPDATE requests SET status='accepted' WHERE id=?", (rid,))
    conn.execute("UPDATE waitlist SET status='removed' WHERE creator_id=? AND client_id=? AND status!='removed'",
                 (creator["id"], r["client_id"]))
    c = commission_view(conn, row(conn.execute("SELECT * FROM commissions WHERE id=?", (cur.lastrowid,)).fetchone()))
    c["portal_url"] = f"{settings.base_url}/p/{c['portal_token']}"
    return c


@router.post("/requests/{rid}/decline")
def decline_request(rid: int, body: NoteIn, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    _request(conn, creator["id"], rid)
    conn.execute("UPDATE requests SET status='declined', creator_note=? WHERE id=?", (body.note, rid))
    return {"ok": True}


@router.post("/requests/{rid}/ask")
def ask_for_details(rid: int, body: NoteIn, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    r = _request(conn, creator["id"], rid)
    screening = loads(r["screening_json"], {})
    asks = [f.get("question") or f["message"] for f in screening.get("flags", []) if f["kind"] == "vague"]
    note = body.note or "Thanks for your request! Before I can accept it, could you send me " + (
        "; ".join(asks) if asks else "a little more detail") + "?"
    conn.execute("UPDATE requests SET status='info_requested', creator_note=? WHERE id=?", (note, rid))
    return {"ok": True, "message": note}


@router.post("/requests/{rid}/claude-review")
def claude_review(rid: int, creator: dict = Depends(require("claude_screening")), conn: sqlite3.Connection = Depends(get_db)):
    r = _request(conn, creator["id"], rid)
    if not claude.available():
        raise HTTPException(503, "Set ANTHROPIC_API_KEY to use Claude.")
    t = _type(conn, creator["id"], r["type_id"])
    result = claude.review_brief(loads(r["brief_json"], {}), t["name"], t["base_price_cents"], t["est_hours"])
    if result is None:
        raise HTTPException(502, "Claude didn't return a review. Try again.")
    conn.execute("UPDATE requests SET claude_json=? WHERE id=?", (dumps(result), rid))
    return result


@router.post("/requests/{rid}/rescreen")
def rescreen(rid: int, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    r = _request(conn, creator["id"], rid)
    s = screen_brief(loads(r["brief_json"], {}), _type(conn, creator["id"], r["type_id"]))
    conn.execute("UPDATE requests SET screening_json=? WHERE id=?", (dumps(s), rid))
    return s


# ---------------------------------------------------------------- waitlist

@router.get("/waitlist")
def get_waitlist(creator: dict = Depends(require("waitlist")), conn: sqlite3.Connection = Depends(get_db)):
    return rows(conn.execute(
        """SELECT w.id, w.note, w.status, w.created_at, w.invited_at, cl.handle AS client, cl.email, t.name AS type_name
           FROM waitlist w JOIN clients cl ON cl.id=w.client_id LEFT JOIN commission_types t ON t.id=w.type_id
           WHERE w.creator_id=? AND w.status!='removed' ORDER BY w.created_at""", (creator["id"],)).fetchall())


@router.post("/waitlist/{wid}/invite")
def invite(wid: int, creator: dict = Depends(require("waitlist")), conn: sqlite3.Connection = Depends(get_db)):
    w = conn.execute("SELECT w.*, cl.handle, cl.email FROM waitlist w JOIN clients cl ON cl.id=w.client_id "
                     "WHERE w.id=? AND w.creator_id=?", (wid, creator["id"])).fetchone()
    if w is None:
        raise HTTPException(404, "Waitlist entry not found.")
    conn.execute("UPDATE waitlist SET status='invited', invited_at=? WHERE id=?", (now(), wid))
    link = f"{settings.base_url}/c/{creator['handle']}"
    return {"ok": True, "client": w["handle"], "email": w["email"],
            "message": f"Hi @{w['handle']}! A slot just opened. Send your request here: {link}"}


@router.post("/waitlist/{wid}/remove")
def remove_waitlist(wid: int, creator: dict = Depends(require("waitlist")), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("UPDATE waitlist SET status='removed' WHERE id=? AND creator_id=?", (wid, creator["id"]))
    return {"ok": True}


# ---------------------------------------------------------------- commissions

@router.get("/commissions/{cid}")
def get_commission(cid: int, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    c = commission_view(conn, _commission(conn, creator["id"], cid))
    c["portal_url"] = f"{settings.base_url}/p/{c['portal_token']}"
    return c


@router.post("/commissions/{cid}/start")
def start(cid: int, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    c = _commission(conn, creator["id"], cid)
    _transition(conn, c, ("queued",), "in_progress", ", started_at=?", (now(),))
    return commission_view(conn, _commission(conn, creator["id"], cid))


@router.post("/commissions/{cid}/submit-wip")
def submit_wip(cid: int, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    c = _commission(conn, creator["id"], cid)
    _transition(conn, c, ("in_progress",), "in_review")
    return commission_view(conn, _commission(conn, creator["id"], cid))


@router.post("/commissions/{cid}/hours")
def log_hours(cid: int, body: HoursIn, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    _commission(conn, creator["id"], cid)
    conn.execute("UPDATE commissions SET hours_logged = hours_logged + ? WHERE id=?", (body.hours, cid))
    return commission_view(conn, _commission(conn, creator["id"], cid))


@router.post("/commissions/{cid}/deliver")
async def deliver(cid: int, files: list[UploadFile] = File(default=[]), hours: float | None = Form(default=None),
                  creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    c = _commission(conn, creator["id"], cid)
    if c["status"] != "approved":
        raise HTTPException(409, "The client needs to approve the WIP before you deliver.")
    if not files:
        raise HTTPException(400, "Attach at least one file to deliver.")
    folder = settings.upload_dir / str(cid)
    folder.mkdir(parents=True, exist_ok=True)
    limit = settings.max_upload_mb * 1024 * 1024
    uploads = []
    for f in files:
        data = await f.read()
        if len(data) > limit:
            raise HTTPException(413, f"{f.filename} is over {settings.max_upload_mb} MB.")
        uploads.append((f, data))
    for f, data in uploads:
        safe = Path(f.filename or "file").name[:120] or "file"
        stored = secrets.token_hex(8) + "_" + safe
        (folder / stored).write_bytes(data)
        conn.execute("INSERT INTO deliverables (commission_id, filename, stored_name, size_bytes, uploaded_at) VALUES (?,?,?,?,?)",
                     (cid, safe, stored, len(data), now()))
    if hours:
        conn.execute("UPDATE commissions SET hours_logged = hours_logged + ? WHERE id=?", (hours, cid))
    conn.execute("UPDATE commissions SET status='delivered', delivered_at=? WHERE id=?", (now(), cid))
    c = _commission(conn, creator["id"], cid)
    if c["price_cents"] - c["deposit_cents"] <= 0:
        conn.execute("UPDATE commissions SET status='completed', completed_at=? WHERE id=?", (now(), cid))
    return commission_view(conn, _commission(conn, creator["id"], cid))


@router.post("/commissions/{cid}/cancel")
def cancel(cid: int, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    c = _commission(conn, creator["id"], cid)
    _transition(conn, c, OPEN_STATUSES, "cancelled")
    return {"ok": True}


@router.post("/commissions/{cid}/mark-paid")
def mark_paid_offline(cid: int, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    """For clients who paid outside Craftboard (bank transfer, in person)."""
    c = _commission(conn, creator["id"], cid)
    kind = {"awaiting_deposit": "deposit", "delivered": "balance"}.get(c["status"])
    if not kind:
        raise HTTPException(409, "No payment is due right now.")
    cur = conn.execute("INSERT INTO payments (commission_id, kind, amount_cents, provider, status, created_at) "
                       "VALUES (?,?,?, 'import', 'pending', ?)", (cid, kind, payments.amount_due(c, kind), now()))
    payments.mark_paid(conn, int(cur.lastrowid), "offline")
    return commission_view(conn, _commission(conn, creator["id"], cid))


@router.get("/history")
def history(creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    return completed_rows(conn, creator["id"])[::-1]


@router.get("/analytics")
def analytics(creator: dict = Depends(require("analytics")), conn: sqlite3.Connection = Depends(get_db)):
    return analytics_engine.compute(completed_rows(conn, creator["id"]), active_hours(conn, creator["id"]),
                                    creator["hours_per_week"], now())


# ---------------------------------------------------------------- import & payouts

def _parse_date(value: str) -> int:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date: {value!r} (use YYYY-MM-DD)")


@router.post("/import/csv")
async def import_csv(file: UploadFile = File(...), creator: dict = Depends(current_creator),
                     conn: sqlite3.Connection = Depends(get_db)):
    """Import past commissions from a spreadsheet export.

    Columns (header row required): client, type, price, completed_at
    Optional: hours, accepted_at, revisions
    """
    from ..repo import get_or_create_client

    text = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    missing = {"client", "type", "price", "completed_at"} - headers
    if missing:
        raise HTTPException(400, "Missing columns: " + ", ".join(sorted(missing)))
    imported, errors = 0, []
    type_ids: dict[str, int] = {r["name"].lower(): r["id"] for r in
                                conn.execute("SELECT id, name FROM commission_types WHERE creator_id=?", (creator["id"],))}
    for i, raw in enumerate(reader, start=2):
        rec = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        try:
            price = _cents(float(rec["price"].replace("$", "").replace(",", "")))
            completed = _parse_date(rec["completed_at"])
            accepted = _parse_date(rec["accepted_at"]) if rec.get("accepted_at") else completed - 7 * 86400
            hours = float(rec["hours"]) if rec.get("hours") else 0.0
            revisions = int(rec["revisions"]) if rec.get("revisions") else 0
            if price < 0 or hours < 0 or revisions < 0:
                raise ValueError("price, hours and revisions can't be negative")
            if accepted > completed:
                raise ValueError("accepted_at is after completed_at")
            tname = rec["type"] or "Other"
            if tname.lower() not in type_ids:
                cur = conn.execute("INSERT INTO commission_types (creator_id, name, base_price_cents, est_hours, active, created_at) "
                                   "VALUES (?,?,?,?,0,?)", (creator["id"], tname, price, hours or 3, now()))
                type_ids[tname.lower()] = int(cur.lastrowid)
            client_handle = rec["client"].lstrip("@") or "unknown"
            client_id = get_or_create_client(conn, creator["id"], client_handle)
            cur = conn.execute(
                """INSERT INTO commissions (creator_id, client_id, type_id, title, brief_json, brief_locked_at, price_cents,
                   deposit_cents, revisions_included, revisions_used, hours_logged, status, portal_token, source,
                   accepted_at, completed_at, delivered_at) VALUES (?,?,?,?, '{}', ?,?,?,?,?,?, 'completed', ?, 'import', ?,?,?)""",
                (creator["id"], client_id, type_ids[tname.lower()], f"{tname} for @{client_handle}", accepted, price, 0,
                 creator["revisions_included"], revisions, hours, portal_token(), accepted, completed, completed))
            conn.execute("INSERT INTO payments (commission_id, kind, amount_cents, provider, status, created_at, paid_at) "
                         "VALUES (?, 'balance', ?, 'import', 'paid', ?, ?)", (cur.lastrowid, price, completed, completed))
            imported += 1
        except (ValueError, KeyError) as exc:
            errors.append(f"Row {i}: {exc}")
    return {"imported": imported, "errors": errors[:20]}


@router.post("/stripe/connect")
def stripe_connect(creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return {"url": payments.connect_onboarding_link(conn, creator)}
    except payments.PaymentError as exc:
        raise HTTPException(400, str(exc))
