"""Craftboard MVP: FastAPI app, routers and page routes."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .routes import agent, auth, creator, public

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Craftboard", version="0.1.0", lifespan=lifespan,
              description="Back office for independent creatives: intake, queue, revisions, payments and pricing insight.")
app.include_router(auth.router)
app.include_router(creator.router)
app.include_router(agent.router)
app.include_router(public.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def revalidate_static(request, call_next):
    """No build step means no hashed filenames, so make browsers revalidate (cheap ETag check) after upgrades."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/app")


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(STATIC / "login.html")


@app.get("/app", include_in_schema=False)
def app_page():
    return FileResponse(STATIC / "app.html")


@app.get("/c/{handle}", include_in_schema=False)
def intake_page(handle: str):
    return FileResponse(STATIC / "intake.html")


@app.get("/p/{token}", include_in_schema=False)
def portal_page(token: str):
    return FileResponse(STATIC / "portal.html")


@app.get("/healthz", include_in_schema=False)
def health():
    return {"ok": True}
