from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from lib.db import get_db

router = APIRouter()


@router.get("/employees")
def employees(
    request: Request,
    q: str = Query(""),
    dept: str = Query(""),
    db: Session = Depends(get_db),
):
    templates = request.app.state.templates
    stmt = select(models.Employee).order_by(models.Employee.name)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            models.Employee.name.ilike(like) | models.Employee.employee_code.ilike(like)
        )
    if dept:
        stmt = stmt.where(models.Employee.department == dept)
    rows = db.scalars(stmt).all()

    departments = [
        r[0] for r in db.execute(
            select(models.Employee.department).distinct().order_by(models.Employee.department)
        ).all()
    ]

    return templates.TemplateResponse(request, "employees.html",
        {"request": request, "nav": "employees", "page_title": "Employees",
         "employees": rows, "departments": departments, "q": q, "dept": dept},
    )


@router.get("/employees/{emp_id}/drawer", response_class=HTMLResponse)
def employee_drawer(emp_id: int, request: Request, db: Session = Depends(get_db)):
    templates = request.app.state.templates
    emp = db.get(models.Employee, emp_id)
    reports = db.scalars(
        select(models.Employee).where(models.Employee.manager_id == emp_id)
    ).all()
    return templates.TemplateResponse(request, "_employee_drawer.html",
        {"request": request, "emp": emp, "reports": reports},
    )
