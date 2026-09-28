"""FastAPI dependencies: current creator from the session cookie or bearer token."""
from __future__ import annotations

import sqlite3

from fastapi import Depends, HTTPException, Request

from .db import get_db, now, row
from .repo import plan_allows
from .security import hash_token

COOKIE = "cb_session"


def current_creator(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        raise HTTPException(401, "Sign in to continue.")
    r = conn.execute(
        "SELECT c.* FROM sessions s JOIN creators c ON c.id=s.creator_id WHERE s.token_hash=? AND s.expires_at>?",
        (hash_token(token), now()),
    ).fetchone()
    if r is None:
        raise HTTPException(401, "Your session expired. Sign in again.")
    return row(r)


def require(feature: str):
    names = {"waitlist": "Studio", "analytics": "Studio", "goal_agent": "Pro", "claude_screening": "Pro"}

    def checker(creator: dict = Depends(current_creator)) -> dict:
        if not plan_allows(creator, feature):
            raise HTTPException(402, f"This needs the {names[feature]} plan.")
        return creator

    return checker
