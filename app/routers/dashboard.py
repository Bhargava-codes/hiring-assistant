from __future__ import annotations

from collections import Counter
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from lib.db import get_db

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    templates = request.app.state.templates

    headcount = db.scalar(select(func.count(models.Employee.id))) or 0
    open_reqs = db.scalar(
        select(func.count(models.Requisition.id)).where(
            models.Requisition.status.in_(
                [models.REQ_INTAKE, models.REQ_CONTRACT_REVIEW, models.REQ_ACTIVE, models.REQ_OFFER]
            )
        )
    ) or 0

    this_month = date.today().replace(day=1)
    joiners = db.scalar(
        select(func.count(models.Employee.id)).where(models.Employee.join_date >= this_month)
    ) or 0

    # Static, plausible attrition figure for the realism layer.
    attrition_pct = 11.4

    dept_counts = Counter(
        r[0] for r in db.execute(select(models.Employee.department)).all()
    )
    dept_order = ["Engineering", "Sales", "CS", "Marketing", "Product", "Finance", "HR", "Ops"]
    max_ct = max(dept_counts.values()) if dept_counts else 1
    dept_bars = [
        {"name": d, "count": dept_counts.get(d, 0), "pct": round(100 * dept_counts.get(d, 0) / max_ct)}
        for d in dept_order
        if dept_counts.get(d)
    ]

    cards = [
        {"label": "Headcount", "value": headcount, "hint": "active employees"},
        {"label": "Open requisitions", "value": open_reqs, "hint": "in intake / review / active"},
        {"label": "Joiners this month", "value": joiners, "hint": this_month.strftime("%B %Y")},
        {"label": "Attrition (TTM)", "value": f"{attrition_pct}%", "hint": "trailing twelve months"},
    ]

    return templates.TemplateResponse(request, "dashboard.html",
        {"request": request, "nav": "dashboard", "page_title": "Dashboard",
         "cards": cards, "dept_bars": dept_bars},
    )
