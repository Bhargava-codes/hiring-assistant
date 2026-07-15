"""Contract HRMS — FastAPI application entrypoint.

Requisitions is the only functional module; the rest of the HRMS is a realism
layer (read-only stubs). Run with:  make dev   (or)   uvicorn app.main:app --reload
"""
from __future__ import annotations

import markdown as md
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from lib import llm
from lib.db import init_db
from app.routers import dashboard, employees, requisitions, stubs

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

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Make templates available to routers via app state.
app.state.templates = templates

app.include_router(dashboard.router)
app.include_router(employees.router)
app.include_router(requisitions.router)
app.include_router(stubs.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")
