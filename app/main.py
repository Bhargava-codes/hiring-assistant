"""Contract HRMS — FastAPI application entrypoint.

Requisitions is the only functional module; the rest of the HRMS is a realism
layer (read-only stubs). Run with:  make dev   (or)   uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from pathlib import Path

import markdown as md
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from lib import llm
from lib.db import init_db
from app.routers import dashboard, employees, requisitions, stubs

# INFO so lib/llm.py's per-call token+cost logging and the agent/render
# fallback warnings both show up in the terminal running uvicorn.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Contract HRMS")

templates = Jinja2Templates(directory="app/templates")


def _md(text: str | None) -> str:
    if not text:
        return ""
    return md.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])


templates.env.filters["markdown"] = _md

# Shared globals available in every template.
templates.env.globals.update(
    org_name="Traqo Technologies",
    current_user="HR Admin",
    llm_online=llm.has_api_key,
    intake_model=llm.INTAKE_MODEL,
    extract_model=llm.EXTRACT_MODEL,
)

# Resolve relative to this file (not the CWD) and ensure it exists — the folder
# is empty (assets are CDN-served), and git doesn't track empty dirs, so it may
# be absent on a fresh checkout/deploy.
_static_dir = Path(__file__).resolve().parent / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Make templates available to routers via app state.
app.state.templates = templates

app.include_router(dashboard.router)
app.include_router(employees.router)
app.include_router(requisitions.router)
app.include_router(stubs.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Seed demo data when the database is empty (e.g. first boot on a host with
    # an ephemeral filesystem, like Render) so the deployed app isn't blank.
    try:
        from lib.db import SessionLocal
        from app import models

        db = SessionLocal()
        try:
            if db.query(models.Employee).count() == 0:
                from scripts.seed import main as seed_main
                seed_main()
        finally:
            db.close()
    except Exception as e:  # never let seeding crash startup
        logging.getLogger("contract_hrms").warning("startup seed skipped: %s", e)


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/debug/llm", include_in_schema=False)
def debug_llm() -> dict:
    """Read-only diagnostic: is the key present in THIS process's environment,
    and roughly what shape is it. Never returns the key itself — only whether
    it's set and its length, which is enough to catch "not set" vs "set with a
    stray quote/space/newline" without leaking anything sensitive."""
    import os

    raw = os.getenv("OPENROUTER_API_KEY")
    return {
        "has_api_key": llm.has_api_key(),
        "key_present_in_env": raw is not None,
        "key_length": len(raw) if raw else 0,
        "key_looks_wrapped_in_quotes": bool(raw) and (raw[:1] in "\"'" or raw[-1:] in "\"'"),
        "intake_model": llm.INTAKE_MODEL,
        "extract_model": llm.EXTRACT_MODEL,
    }
