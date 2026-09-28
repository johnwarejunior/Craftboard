"""Client-facing endpoints: the intake form (/c/<handle>) and the commission portal (/p/<token>).

No login: the intake form is public, and the portal is reached through an
unguessable token sent to the client.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..config import settings
from ..db import dumps, get_db, now, row, rows
from ..engines.screening import scope_check, screen_brief
from ..models import ChangeIn, IntakeIn, PayIn, ScreenIn
from ..repo import commission_view, get_or_create_client, intake_state
from ..services import payments

router = APIRouter(prefix="/api", tags=["public"])


def _creator_by_handle(conn: sqlite3.Connection, handle: str) -> dict:
    c = row(conn.execute("SELECT * FROM creators WHERE handle=?", (handle,)).fetchone())
    if c is None:
        raise HTTPException(404, "No creator with that link.")
    return c


def _active_type(conn: sqlite3.Connection, creator_id: int, type_id: int) -> dict:
    t = row(conn.execute("SELECT * FROM commission_types WHERE id=? AND creator_id=? AND active=1",
                         (type_id, creator_id)).fetchone())
    if t is None:
        raise HTTPException(404, "That commission type isn't available.")
    return t


@router.get("/public/{handle}")
def public_profile(handle: str, conn: sqlite3.Connection = Depends(get_db)):
    c = _creator_by_handle(conn, handle)
    types = rows(conn.execute(
        "SELECT id, name, description, base_price_cents, characters_included, extra_character_pct FROM commission_types "
        "WHERE creator_id=? AND active=1 ORDER BY sort_order, id", (c["id"],)).fetchall())
    return {"name": c["name"], "handle": c["handle"], "message": c["intake_message"], "types": types,
            "revisions_included": c["revisions_included"], "deposit_pct": c["deposit_pct"],
            "intake": intake_state(conn, c)}


@router.post("/public/{handle}/screen")
def live_screen(handle: str, body: ScreenIn, conn: sqlite3.Connection = Depends(get_db)):
    c = _creator_by_handle(conn, handle)
    return screen_brief(body.brief.model_dump(), _active_type(conn, c["id"], body.type_id))


@router.post("/public/{handle}/requests", status_code=201)
def submit_request(handle: str, body: IntakeIn, conn: sqlite3.Connection = Depends(get_db)):
    c = _creator_by_handle(conn, handle)
    t = _active_type(conn, c["id"], body.type_id)
    state = intake_state(conn, c)
    client_id = get_or_create_client(conn, c["id"], body.client_handle, body.email)
    if not state["open"]:
        if not state["waitlist_enabled"]:
            raise HTTPException(409, f"{c['name']} isn't taking requests right now. Check back soon.")
        existing = conn.execute("SELECT id FROM waitlist WHERE creator_id=? AND client_id=? AND status='waiting'",
                                (c["id"], client_id)).fetchone()
        if existing:
            entry_id = existing["id"]
        else:
            entry_id = conn.execute("INSERT INTO waitlist (creator_id, client_id, type_id, note, created_at) VALUES (?,?,?,?,?)",
                                    (c["id"], client_id, t["id"], body.brief.description[:500], now())).lastrowid
        position = conn.execute(
            "SELECT COUNT(*) FROM waitlist WHERE creator_id=? AND status='waiting' AND "
            "(created_at, id) <= (SELECT created_at, id FROM waitlist WHERE id=?)", (c["id"], entry_id)).fetchone()[0]
        return {"status": "waitlisted", "position": position}
    brief = body.brief.model_dump()
    screening = screen_brief(brief, t)
    conn.execute("INSERT INTO requests (creator_id, client_id, type_id, brief_json, screening_json, created_at) VALUES (?,?,?,?,?,?)",
                 (c["id"], client_id, t["id"], dumps(brief), dumps(screening), now()))
    return {"status": "received"}


# ---------------------------------------------------------------- portal

def _by_token(conn: sqlite3.Connection, token: str) -> dict:
    c = row(conn.execute("SELECT * FROM commissions WHERE portal_token=?", (token,)).fetchone())
    if c is None or c["status"] == "cancelled":
        raise HTTPException(404, "This commission link isn't valid.")
    return c


def _portal(conn: sqlite3.Connection, c: dict) -> dict:
    creator = conn.execute("SELECT name, handle FROM creators WHERE id=?", (c["creator_id"],)).fetchone()
    v = commission_view(conn, c, for_client=True)
    v["creator"] = {"name": creator["name"], "handle": creator["handle"]}
    v["open_quote"] = next((r for r in v["revision_requests"] if r["status"] == "quoted"), None)
    v["files_unlocked"] = c["status"] == "completed"
    v["payment_mode"] = "stripe" if payments.stripe_enabled() else "simulated" if settings.allow_simulated_payments else "none"
    return v


@router.get("/portal/{token}")
def portal(token: str, conn: sqlite3.Connection = Depends(get_db)):
    return _portal(conn, _by_token(conn, token))


@router.post("/portal/{token}/pay")
def pay(token: str, body: PayIn, conn: sqlite3.Connection = Depends(get_db)):
    c = _by_token(conn, token)
    creator = row(conn.execute("SELECT * FROM creators WHERE id=?", (c["creator_id"],)).fetchone())
    try:
        result = payments.start_payment(conn, c, creator, body.kind)
    except payments.PaymentError as exc:
        raise HTTPException(409, str(exc))
    result["commission"] = _portal(conn, _by_token(conn, token))
    return result


@router.post("/portal/{token}/approve")
def approve(token: str, conn: sqlite3.Connection = Depends(get_db)):
    c = _by_token(conn, token)
    if c["status"] != "in_review":
        raise HTTPException(409, "There's nothing to approve yet.")
    conn.execute("UPDATE revision_requests SET status='declined', resolved_at=? WHERE commission_id=? AND status='quoted'",
                 (now(), c["id"]))
    conn.execute("UPDATE commissions SET status='approved' WHERE id=?", (c["id"],))
    return _portal(conn, _by_token(conn, token))


@router.post("/portal/{token}/changes")
def request_changes(token: str, body: ChangeIn, conn: sqlite3.Connection = Depends(get_db)):
    c = _by_token(conn, token)
    if c["status"] != "in_review":
        raise HTTPException(409, "You can ask for changes when the artist shares a WIP.")
    if conn.execute("SELECT 1 FROM revision_requests WHERE commission_id=? AND status='quoted'", (c["id"],)).fetchone():
        raise HTTPException(409, "Accept or decline the open quote first.")
    upgrades = conn.execute("SELECT COALESCE(SUM(quote_cents),0) FROM revision_requests WHERE commission_id=? AND status='accepted'",
                            (c["id"],)).fetchone()[0]
    result = scope_check(body.text, c["price_cents"] - upgrades, c["revisions_included"], c["revisions_used"])
    ts = now()
    if result["kind"] == "revision":
        conn.execute("INSERT INTO revision_requests (commission_id, text, kind, status, created_at, resolved_at) "
                     "VALUES (?,?, 'revision', 'applied', ?, ?)", (c["id"], body.text, ts, ts))
        conn.execute("UPDATE commissions SET revisions_used = revisions_used + 1, status='in_progress' WHERE id=?", (c["id"],))
    else:
        conn.execute("INSERT INTO revision_requests (commission_id, text, kind, reasons_json, quote_cents, status, created_at) "
                     "VALUES (?,?,?,?,?, 'quoted', ?)",
                     (c["id"], body.text, result["kind"], dumps(result["reasons"]), result["quote_cents"], ts))
    return {"result": result, "commission": _portal(conn, _by_token(conn, token))}


def _quote(conn: sqlite3.Connection, c: dict, rid: int) -> dict:
    q = row(conn.execute("SELECT * FROM revision_requests WHERE id=? AND commission_id=? AND status='quoted'",
                         (rid, c["id"])).fetchone())
    if q is None:
        raise HTTPException(404, "That quote isn't open.")
    return q


@router.post("/portal/{token}/quotes/{rid}/accept")
def accept_quote(token: str, rid: int, conn: sqlite3.Connection = Depends(get_db)):
    c = _by_token(conn, token)
    q = _quote(conn, c, rid)
    conn.execute("UPDATE revision_requests SET status='accepted', resolved_at=? WHERE id=?", (now(), rid))
    conn.execute("UPDATE commissions SET price_cents = price_cents + ?, status='in_progress' WHERE id=?", (q["quote_cents"], c["id"]))
    return _portal(conn, _by_token(conn, token))


@router.post("/portal/{token}/quotes/{rid}/decline")
def decline_quote(token: str, rid: int, conn: sqlite3.Connection = Depends(get_db)):
    c = _by_token(conn, token)
    _quote(conn, c, rid)
    conn.execute("UPDATE revision_requests SET status='declined', resolved_at=? WHERE id=?", (now(), rid))
    return _portal(conn, _by_token(conn, token))


@router.get("/portal/{token}/files/{file_id}")
def download(token: str, file_id: int, conn: sqlite3.Connection = Depends(get_db)):
    c = _by_token(conn, token)
    if c["status"] != "completed":
        raise HTTPException(402, "Files unlock once the balance is paid.")
    f = conn.execute("SELECT * FROM deliverables WHERE id=? AND commission_id=?", (file_id, c["id"])).fetchone()
    if f is None:
        raise HTTPException(404, "File not found.")
    path = settings.upload_dir / str(c["id"]) / f["stored_name"]
    if not path.exists():
        raise HTTPException(410, "This file is no longer available.")
    return FileResponse(path, filename=f["filename"])


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    try:
        kind = payments.handle_webhook(conn, await request.body(), request.headers.get("stripe-signature"))
    except payments.PaymentError as exc:
        raise HTTPException(400, str(exc))
    return {"received": kind}
