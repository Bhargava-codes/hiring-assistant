"""Realism-only preview modules: Performance, Payroll, Leave, Settings.

Read-only. Each renders plausible seed-derived rows with a 'Module preview' banner.
No logic, no edit flows (per the build spec's non-goals).
"""
from __future__ import annotations

import random

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from lib.db import get_db

router = APIRouter()

_RATINGS = ["Exceeds", "Meets", "Meets", "Meets", "Developing"]
_LEAVE_TYPES = ["Earned", "Sick", "Casual"]


def _sample_employees(db: Session, n: int = 12):
    rows = db.scalars(select(models.Employee).order_by(models.Employee.id)).all()
    return rows[:n]


@router.get("/performance")
def performance(request: Request, db: Session = Depends(get_db)):
    templates = request.app.state.templates
    rnd = random.Random(7)
    rows = [
        {
            "emp": e,
            "cycle": "H1 FY26",
            "rating": rnd.choice(_RATINGS),
            "goal_pct": rnd.choice([70, 80, 85, 90, 95, 100]),
        }
        for e in _sample_employees(db, 14)
    ]
    return templates.TemplateResponse(request, "stub.html",
        {"request": request, "nav": "performance", "page_title": "Performance",
         "heading": "Performance", "kind": "performance", "rows": rows},
    )


@router.get("/payroll")
def payroll(request: Request, db: Session = Depends(get_db)):
    templates = request.app.state.templates
    rnd = random.Random(11)
    rows = [
        {
            "emp": e,
            "gross": f"₹{rnd.randint(60, 320)*1000:,}",
            "status": rnd.choice(["Processed", "Processed", "Pending"]),
            "cycle": "Jun 2026",
        }
        for e in _sample_employees(db, 14)
    ]
    return templates.TemplateResponse(request, "stub.html",
        {"request": request, "nav": "payroll", "page_title": "Payroll",
         "heading": "Payroll", "kind": "payroll", "rows": rows},
    )


@router.get("/leave")
def leave(request: Request, db: Session = Depends(get_db)):
    templates = request.app.state.templates
    rnd = random.Random(13)
    rows = [
        {
            "emp": e,
            "type": rnd.choice(_LEAVE_TYPES),
            "balance": rnd.randint(2, 18),
            "pending": rnd.choice([0, 0, 1, 2]),
        }
        for e in _sample_employees(db, 14)
    ]
    return templates.TemplateResponse(request, "stub.html",
        {"request": request, "nav": "leave", "page_title": "Leave",
         "heading": "Leave", "kind": "leave", "rows": rows},
    )


@router.get("/settings")
def settings(request: Request, db: Session = Depends(get_db)):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "stub.html",
        {"request": request, "nav": "settings", "page_title": "Settings",
         "heading": "Settings", "kind": "settings", "rows": []},
    )
