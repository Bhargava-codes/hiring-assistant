from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from lib import agent, contractops, profiles, render, schema
from lib.db import get_db

router = APIRouter(prefix="/requisitions")


STATUS_STYLE = {
    models.REQ_DRAFT: "bg-gray-100 text-gray-600",
    models.REQ_INTAKE: "bg-amber-100 text-amber-700",
    models.REQ_CONTRACT_REVIEW: "bg-violet-100 text-violet-700",
    models.REQ_ACTIVE: "bg-green-100 text-green-700",
    models.REQ_OFFER: "bg-blue-100 text-blue-700",
    models.REQ_CLOSED: "bg-gray-200 text-gray-500",
}


def _tmpl(request: Request):
    return request.app.state.templates


def _get_req(db: Session, req_id: int) -> models.Requisition:
    req = db.get(models.Requisition, req_id)
    if not req:
        raise HTTPException(404, "Requisition not found")
    return req


# --- List + create -----------------------------------------------------------

@router.get("")
def list_reqs(request: Request, db: Session = Depends(get_db)):
    reqs = db.scalars(
        select(models.Requisition).order_by(models.Requisition.created_at.desc())
    ).all()
    return _tmpl(request).TemplateResponse(request, "requisitions.html",
        {"request": request, "nav": "requisitions", "page_title": "Requisitions",
         "reqs": reqs, "status_style": STATUS_STYLE},
    )


@router.get("/new")
def new_req_form(request: Request, db: Session = Depends(get_db)):
    managers = db.scalars(
        select(models.Employee).order_by(models.Employee.name)
    ).all()
    departments = ["Engineering", "Sales", "CS", "Marketing", "Product", "Finance", "HR", "Ops"]
    return _tmpl(request).TemplateResponse(request, "requisition_new.html",
        {"request": request, "nav": "requisitions", "page_title": "New requisition",
         "managers": managers, "departments": departments},
    )


@router.post("/new")
def create_req(
    request: Request,
    title: str = Form(...),
    department: str = Form(...),
    hiring_manager_id: int = Form(...),
    db: Session = Depends(get_db),
):
    req = models.Requisition(
        title=title.strip(),
        department=department,
        hiring_manager_id=hiring_manager_id,
        status=models.REQ_INTAKE,
    )
    db.add(req)
    db.flush()
    _start_contract(db, req)
    db.commit()
    return RedirectResponse(url=f"/requisitions/{req.id}/intake", status_code=303)


def _start_contract(db: Session, req: models.Requisition) -> models.Contract:
    state = agent.new_state()
    agent.start(state)
    contract = models.Contract(
        requisition_id=req.id,
        version=1,
        fields=schema.blank_contract(),
        chat_state=state,
    )
    db.add(contract)
    db.flush()
    return contract


# --- Detail dispatcher -------------------------------------------------------

@router.get("/{req_id}")
def req_detail(req_id: int, request: Request, tab: str = "", db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    contract = req.current_contract

    if req.status == models.REQ_INTAKE:
        return RedirectResponse(url=f"/requisitions/{req_id}/intake", status_code=303)

    ctx = {
        "request": request, "nav": "requisitions", "page_title": req.title,
        "req": req, "contract": contract, "status_style": STATUS_STYLE,
        "layers": schema.LAYERS, "field_labels": schema.FIELD_LABELS,
        "critical": schema.CRITICAL_FIELDS,
    }

    if req.status == models.REQ_DRAFT:
        return _tmpl(request).TemplateResponse(request, "requisition_draft.html", ctx)

    if req.status == models.REQ_CONTRACT_REVIEW:
        ctx["can_render"] = schema.can_render(contract.fields) if contract else False
        ctx["missing"] = schema.missing_critical(contract.fields) if contract else []
        return _tmpl(request).TemplateResponse(request, "contract_review.html", ctx)

    # ACTIVE / OFFER / CLOSED -> full workspace with tabs.
    ctx["tab"] = tab or "candidates"
    ctx["criteria"] = contractops.criteria_list(contract)
    ctx["burndown"] = contractops.burndown(req)
    ctx["drift"] = contractops.drift_status(req)
    ctx["renderings"] = {r.type: r for r in contract.renderings} if contract else {}
    ctx["rendering_types"] = models.RENDERING_TYPES
    ctx["rendering_labels"] = models.RENDERING_LABELS
    ctx["all_contracts"] = req.contracts
    ctx["candidate_stages"] = models.CANDIDATE_STAGES
    return _tmpl(request).TemplateResponse(request, "requisition_workspace.html", ctx)


@router.post("/{req_id}/start-intake")
def start_intake(req_id: int, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    if req.status == models.REQ_DRAFT:
        req.status = models.REQ_INTAKE
        if not req.current_contract:
            _start_contract(db, req)
        db.commit()
    return RedirectResponse(url=f"/requisitions/{req_id}/intake", status_code=303)


# --- Intake chat -------------------------------------------------------------

@router.get("/{req_id}/intake")
def intake_page(req_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    contract = req.current_contract
    if not contract:
        contract = _start_contract(db, req)
        req.status = models.REQ_INTAKE
        db.commit()
    return _tmpl(request).TemplateResponse(request, "intake.html",
        {"request": request, "nav": "requisitions", "page_title": f"Intake · {req.title}",
         "req": req, "contract": contract, "layers": schema.LAYERS,
         "field_labels": schema.FIELD_LABELS, "critical": schema.CRITICAL_FIELDS},
    )


@router.get("/{req_id}/intake/transcript")
def intake_transcript(req_id: int, request: Request, db: Session = Depends(get_db)):
    """Read-only view of the intake conversation that produced the current
    contract version — useful for review/demo once intake has moved on."""
    req = _get_req(db, req_id)
    contract = req.current_contract
    messages = (contract.chat_state or {}).get("messages", []) if contract else []
    return _tmpl(request).TemplateResponse(request, "intake_transcript.html",
        {"request": request, "nav": "requisitions", "page_title": f"Intake transcript · {req.title}",
         "req": req, "contract": contract, "messages": messages},
    )


def _contract_state_payload(contract: models.Contract) -> dict:
    cfields = contract.fields or schema.blank_contract()
    fields_out = {}
    for name, entry in cfields.get("fields", {}).items():
        fields_out[name] = {
            "label": schema.FIELD_LABELS.get(name, name),
            "value": entry.get("value"),
            "provenance": entry.get("provenance", schema.NULL),
            "filled": schema.is_filled(entry),
            "critical": name in schema.CRITICAL_FIELDS,
            "layer": schema.field_layer(name),
        }
    return {
        "fields": fields_out,
        "progress": schema.layer_progress(cfields),
        "missing_critical": schema.missing_critical(cfields),
        "can_render": schema.can_render(cfields),
        "conflicts": cfields.get("conflicts", []),
    }


@router.get("/{req_id}/intake/state")
def intake_state(req_id: int, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    contract = req.current_contract
    state = contract.chat_state or {}
    return JSONResponse(
        {
            "messages": state.get("messages", []),
            "done": state.get("done", False),
            "status": req.status,
            "contract": _contract_state_payload(contract),
        }
    )


@router.post("/{req_id}/intake/message")
def intake_message(req_id: int, payload: dict, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    contract = req.current_contract
    if not contract:
        raise HTTPException(400, "No draft contract")
    if (contract.chat_state or {}).get("done"):
        return JSONResponse({"error": "Intake already complete", "done": True}, status_code=400)

    user_text = (payload.get("message") or "").strip()
    if not user_text:
        raise HTTPException(400, "Empty message")

    result = agent.process_turn(contract, user_text)

    # Persist JSON mutations (SQLAlchemy needs the attribute reassigned).
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(contract, "fields")
    flag_modified(contract, "chat_state")

    if result["done"]:
        # Generate the one-pager and advance to review.
        contract.one_pager = render.generate_one_pager(
            contract.fields, req.title, req.department
        )
        req.status = models.REQ_CONTRACT_REVIEW

    db.commit()

    return JSONResponse(
        {
            "assistant": result["assistant"],
            "touched": result["touched"],
            "done": result["done"],
            "status": req.status,
            "contract": _contract_state_payload(contract),
            "redirect": f"/requisitions/{req.id}" if result["done"] else None,
        }
    )


# --- Contract approval + renderings -----------------------------------------

@router.post("/{req_id}/contract/approve")
def approve_contract(req_id: int, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    contract = req.current_contract
    if not contract:
        raise HTTPException(400, "No contract to approve")
    if not schema.can_render(contract.fields):
        missing = ", ".join(schema.missing_critical(contract.fields))
        raise HTTPException(400, f"Critical fields missing: {missing}")

    _regenerate_renderings(db, contract, req)
    contract.approved_at = datetime.utcnow()
    req.status = models.REQ_ACTIVE
    db.commit()
    return RedirectResponse(url=f"/requisitions/{req_id}?tab=renderings", status_code=303)


def _regenerate_renderings(db: Session, contract: models.Contract, req: models.Requisition):
    # Old versions are immutable; on the current version we (re)build all four.
    for old in list(contract.renderings):
        db.delete(old)
    db.flush()
    for rtype in models.RENDERING_TYPES:
        content = render.generate_rendering(rtype, contract.fields, req.title, req.department)
        db.add(models.Rendering(contract_id=contract.id, type=rtype, content=content))


# --- Candidate stage moves + rejection citation ------------------------------

_STAGE_ORDER = [models.C_APPLIED, models.C_SCREENED, models.C_INTERVIEW, models.C_OFFER]


@router.post("/{req_id}/candidates/{cid}/advance")
def advance_candidate(req_id: int, cid: int, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    cand = db.get(models.Candidate, cid)
    if not cand or cand.requisition_id != req.id:
        raise HTTPException(404, "Candidate not found")
    if cand.stage in _STAGE_ORDER and cand.stage != models.C_OFFER:
        idx = _STAGE_ORDER.index(cand.stage)
        cand.stage = _STAGE_ORDER[idx + 1]
        # Entering INTERVIEW consumes an HM slot.
        if cand.stage == models.C_INTERVIEW:
            cand.slot_used = True
        db.add(models.Decision(candidate_id=cand.id, verdict=models.V_ADVANCE))
    db.commit()
    return RedirectResponse(url=f"/requisitions/{req_id}?tab=candidates", status_code=303)


@router.post("/{req_id}/candidates/{cid}/reject")
def reject_candidate(
    req_id: int,
    cid: int,
    criterion_id: str = Form(""),
    uncontracted_reason: str = Form(""),
    db: Session = Depends(get_db),
):
    req = _get_req(db, req_id)
    cand = db.get(models.Candidate, cid)
    if not cand or cand.requisition_id != req.id:
        raise HTTPException(404, "Candidate not found")

    criterion_id = criterion_id.strip() or None
    reason = uncontracted_reason.strip() or None
    # Enforce: a rejection must cite a criterion OR give an uncontracted reason.
    if not criterion_id and not reason:
        raise HTTPException(400, "A rejection must cite a criterion or an uncontracted reason.")

    cand.stage = models.C_REJECTED
    db.add(
        models.Decision(
            candidate_id=cand.id,
            verdict=models.V_REJECT,
            criterion_id=criterion_id,
            uncontracted_reason=reason,
        )
    )
    db.commit()
    return RedirectResponse(url=f"/requisitions/{req_id}?tab=candidates", status_code=303)


# --- Drift: dismiss + amend --------------------------------------------------

@router.post("/{req_id}/drift/dismiss")
def dismiss_drift(req_id: int, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    req.drift_dismissed = True
    db.commit()
    return RedirectResponse(url=f"/requisitions/{req_id}?tab=candidates", status_code=303)


@router.post("/{req_id}/amend")
def amend_contract(req_id: int, amendment: str = Form(...), db: Session = Depends(get_db)):
    """Short amendment turn: fold the new requirement into a new contract version,
    regenerate all four renderings, keep the old version immutable."""
    req = _get_req(db, req_id)
    current = req.current_contract
    if not current:
        raise HTTPException(400, "No contract to amend")

    amendment = amendment.strip()
    if not amendment:
        raise HTTPException(400, "Empty amendment")

    # New immutable version: deep-copy fields, then merge the amendment.
    import copy
    new_fields = copy.deepcopy(current.fields)
    extracted = agent.extract_fields("Amendment to the role contract.", amendment, new_fields)
    agent.merge_fields(new_fields, extracted)
    # Record the amendment note as a conflict-resolution trail.
    new_fields.setdefault("conflicts", []).append(
        {"type": "amendment", "field": "", "note": amendment[:280]}
    )

    new_contract = models.Contract(
        requisition_id=req.id,
        version=current.version + 1,
        fields=new_fields,
        chat_state={"messages": [], "done": True},
        one_pager=render.generate_one_pager(new_fields, req.title, req.department),
        approved_at=datetime.utcnow(),
    )
    db.add(new_contract)
    db.flush()
    _regenerate_renderings(db, new_contract, req)

    req.drift_dismissed = False  # fresh contract clears the alert
    db.commit()
    return RedirectResponse(url=f"/requisitions/{req_id}?tab=contract", status_code=303)


# --- Revealed-preference ranking exercise -----------------------------------

@router.get("/{req_id}/ranking")
def ranking_page(req_id: int, request: Request, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    return _tmpl(request).TemplateResponse(request, "ranking.html",
        {"request": request, "nav": "requisitions", "page_title": f"Ranking · {req.title}",
         "req": req, "profiles": profiles.PROFILES},
    )


@router.post("/{req_id}/ranking")
def submit_ranking(req_id: int, order: str = Form(...), db: Session = Depends(get_db)):
    """order is a comma-separated list of profile ids, top choice first."""
    req = _get_req(db, req_id)
    contract = req.current_contract
    if not contract:
        raise HTTPException(400, "No contract")
    ranked = [profiles.PROFILE_BY_ID[pid] for pid in order.split(",") if pid in profiles.PROFILE_BY_ID]
    conflicts = render.reconcile_ranking(contract.fields, ranked)

    cfields = contract.fields
    # Replace any prior stated_vs_revealed flags, keep other conflict types.
    existing = [c for c in cfields.get("conflicts", []) if c.get("type") != "stated_vs_revealed"]
    cfields["conflicts"] = existing + conflicts
    contract.fields = cfields
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(contract, "fields")
    db.commit()

    dest = "contract" if req.status in (models.REQ_ACTIVE, models.REQ_OFFER, models.REQ_CLOSED) else ""
    suffix = f"?tab={dest}" if dest else ""
    return RedirectResponse(url=f"/requisitions/{req_id}{suffix}", status_code=303)


# --- Renderings copy endpoint (raw markdown) --------------------------------

@router.get("/{req_id}/renderings/{rtype}", response_class=HTMLResponse)
def rendering_raw(req_id: int, rtype: str, db: Session = Depends(get_db)):
    req = _get_req(db, req_id)
    contract = req.current_contract
    if contract:
        for r in contract.renderings:
            if r.type == rtype:
                return HTMLResponse(r.content, media_type="text/plain")
    raise HTTPException(404, "Rendering not found")


@router.post("/{req_id}/renderings/{rtype}/edit")
def edit_rendering(
    req_id: int, rtype: str, content: str = Form(...), db: Session = Depends(get_db)
):
    """Save a hand-edit to a generated rendering. Marks it 'edited' so the UI can
    distinguish it from the model's original output; a later amend/regenerate on
    this requisition still replaces it (a new contract version means fresh docs)."""
    req = _get_req(db, req_id)
    contract = req.current_contract
    if not contract:
        raise HTTPException(400, "No contract")
    for r in contract.renderings:
        if r.type == rtype:
            r.content = content
            r.edited = True
            r.edited_at = datetime.utcnow()
            db.commit()
            return RedirectResponse(url=f"/requisitions/{req_id}?tab=renderings", status_code=303)
    raise HTTPException(404, "Rendering not found")
