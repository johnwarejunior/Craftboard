from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..config import settings
from ..db import get_db, now, row
from ..deps import COOKIE, current_creator
from ..models import Login, SettingsPatch, Signup
from ..repo import creator_public
from ..security import hash_password, hash_token, new_token, verify_password

router = APIRouter(prefix="/api", tags=["auth"])
RESERVED = {"app", "api", "static", "login", "admin", "p", "c", "craftboard", "help", "support"}


def _start_session(conn: sqlite3.Connection, response: Response, creator_id: int) -> str:
    token = new_token()
    conn.execute("INSERT INTO sessions (token_hash, creator_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (hash_token(token), creator_id, now(), now() + settings.session_days * 86400))
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=settings.secure_cookies,
                        max_age=settings.session_days * 86400, path="/")
    return token


@router.post("/auth/signup")
def signup(body: Signup, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    handle = body.handle.lower()
    if handle in RESERVED:
        raise HTTPException(409, "That handle is reserved. Pick another.")
    if conn.execute("SELECT 1 FROM creators WHERE email=?", (body.email,)).fetchone():
        raise HTTPException(409, "An account with that email already exists.")
    if conn.execute("SELECT 1 FROM creators WHERE handle=?", (handle,)).fetchone():
        raise HTTPException(409, "That handle is taken. Pick another.")
    plan = settings.default_plan if settings.default_plan in {"free", "studio", "pro"} else "free"
    cur = conn.execute(
        "INSERT INTO creators (email, password_hash, name, handle, plan, slots, created_at) VALUES (?,?,?,?,?,?,?)",
        (body.email, hash_password(body.password), body.name.strip(), handle, plan, 3 if plan == "free" else 6, now()),
    )
    token = _start_session(conn, response, int(cur.lastrowid))
    creator = row(conn.execute("SELECT * FROM creators WHERE id=?", (cur.lastrowid,)).fetchone())
    return {"creator": creator_public(creator), "token": token}


@router.post("/auth/login")
def login(body: Login, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    r = conn.execute("SELECT * FROM creators WHERE email=?", (body.email.strip(),)).fetchone()
    if r is None or not verify_password(body.password, r["password_hash"]):
        raise HTTPException(401, "Email or password is incorrect.")
    token = _start_session(conn, response, r["id"])
    return {"creator": creator_public(row(r)), "token": token}


@router.post("/auth/logout")
def logout(request: Request, response: Response, creator: dict = Depends(current_creator),
           conn: sqlite3.Connection = Depends(get_db)):
    token = request.cookies.get(COOKIE) or request.headers.get("authorization", "")[7:].strip()
    conn.execute("DELETE FROM sessions WHERE token_hash=? OR (creator_id=? AND expires_at<?)",
                 (hash_token(token), creator["id"], now()))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(creator: dict = Depends(current_creator)):
    return creator_public(creator)


@router.patch("/me")
def update_me(body: SettingsPatch, creator: dict = Depends(current_creator), conn: sqlite3.Connection = Depends(get_db)):
    changes = body.model_dump(exclude_none=True)
    if changes:
        cols = ", ".join(f"{k}=?" for k in changes)
        conn.execute(f"UPDATE creators SET {cols} WHERE id=?", (*changes.values(), creator["id"]))
    return creator_public(row(conn.execute("SELECT * FROM creators WHERE id=?", (creator["id"],)).fetchone()))
