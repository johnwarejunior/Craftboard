"""Goal-setting agent: set a target, get a plan, check in weekly."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..db import dumps, get_db, loads, now, row
from ..deps import require
from ..engines.pricing import build_plan, checkin
from ..models import GoalIn
from ..repo import plan_inputs
from ..services import claude

router = APIRouter(prefix="/api/goals", tags=["goal agent"])


def _goal_view(conn: sqlite3.Connection, g: dict) -> dict:
    plan = loads(g["plan_json"], {})
    earned, delivered = conn.execute(
        "SELECT COALESCE(SUM(price_cents),0), COUNT(*) FROM commissions WHERE creator_id=? AND status='completed' AND completed_at>=?",
        (g["creator_id"], g["created_at"])).fetchone()
    return {
        "id": g["id"],
        "created_at": g["created_at"],
        "plan": plan,
        "narrative": g["narrative"],
        "claude_available": claude.available(),
        "checkin": checkin(plan, g["created_at"], int(earned), int(delivered), now()),
    }


@router.post("", status_code=201)
def create_goal(body: GoalIn, creator: dict = Depends(require("goal_agent")), conn: sqlite3.Connection = Depends(get_db)):
    hours = body.hours_per_week or creator["hours_per_week"]
    try:
        plan = build_plan(goal_cents=int(round(body.amount * 100)), weeks=body.weeks, hours_per_week=hours, now=now(),
                          **plan_inputs(conn, creator))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    narrative = claude.narrate_plan(plan) if body.narrate else None
    conn.execute("UPDATE goals SET active=0 WHERE creator_id=?", (creator["id"],))
    cur = conn.execute(
        "INSERT INTO goals (creator_id, amount_cents, weeks, hours_per_week, plan_json, narrative, created_at) VALUES (?,?,?,?,?,?,?)",
        (creator["id"], plan["goal_cents"], body.weeks, hours, dumps(plan), narrative, now()))
    return _goal_view(conn, row(conn.execute("SELECT * FROM goals WHERE id=?", (cur.lastrowid,)).fetchone()))


@router.get("/active")
def active_goal(creator: dict = Depends(require("goal_agent")), conn: sqlite3.Connection = Depends(get_db)):
    g = row(conn.execute("SELECT * FROM goals WHERE creator_id=? AND active=1 ORDER BY id DESC LIMIT 1",
                         (creator["id"],)).fetchone())
    return {"goal": _goal_view(conn, g) if g else None, "claude_available": claude.available()}


@router.post("/{goal_id}/narrate")
def narrate(goal_id: int, creator: dict = Depends(require("goal_agent")), conn: sqlite3.Connection = Depends(get_db)):
    g = row(conn.execute("SELECT * FROM goals WHERE id=? AND creator_id=?", (goal_id, creator["id"])).fetchone())
    if g is None:
        raise HTTPException(404, "Goal not found.")
    if not claude.available():
        raise HTTPException(503, "Set ANTHROPIC_API_KEY to have Claude explain your plan.")
    text = claude.narrate_plan(loads(g["plan_json"], {}))
    if not text:
        raise HTTPException(502, "Claude didn't answer. Try again.")
    conn.execute("UPDATE goals SET narrative=? WHERE id=?", (text, goal_id))
    return {"narrative": text}


@router.delete("/{goal_id}")
def end_goal(goal_id: int, creator: dict = Depends(require("goal_agent")), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("UPDATE goals SET active=0 WHERE id=? AND creator_id=?", (goal_id, creator["id"]))
    return {"ok": True}
