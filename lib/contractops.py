"""Domain helpers for the requisition/contract lifecycle: criteria list,
slot burn-down, and drift detection."""
from __future__ import annotations

from typing import Any

from app import models
from lib import render, schema


def criteria_list(contract: "models.Contract | None") -> list[dict[str, str]]:
    """The contract must-haves + deal-breaker, as citable rejection criteria."""
    out: list[dict[str, str]] = []
    if not contract:
        return out
    fields = (contract.fields or {}).get("fields", {})
    mh = fields.get("must_haves", {}).get("value") or []
    for i, m in enumerate(mh):
        text = m.get("text") if isinstance(m, dict) else str(m)
        if text:
            out.append({"id": f"must_have_{i}", "label": text})
    db = fields.get("deal_breaker", {}).get("value")
    if db:
        out.append({"id": "deal_breaker", "label": f"Deal-breaker: {db}"})
    return out


def criterion_label(contract: "models.Contract | None", criterion_id: str | None) -> str:
    if not criterion_id:
        return ""
    for c in criteria_list(contract):
        if c["id"] == criterion_id:
            return c["label"]
    return criterion_id


def burndown(req: "models.Requisition") -> dict[str, Any]:
    """Slot burn-down + exhaustion projection.

    A slot is 'used' when a candidate consumes an HM interview (slot_used=True).
    Projection: from the pass-rate so far (candidates who advanced past interview
    vs. total interviewed), estimate whether the budget runs out before an offer.
    """
    contract = req.current_contract
    budget = 0
    if contract:
        budget = contract.fields.get("fields", {}).get("interview_budget", {}).get("value") or 0
        try:
            budget = int(budget)
        except (TypeError, ValueError):
            budget = 0

    interviewed = [c for c in req.candidates if c.slot_used]
    used = len(interviewed)
    remaining = max(budget - used, 0)

    # Outcomes among interviewed candidates.
    advanced = sum(1 for c in interviewed if c.stage in (models.C_OFFER,))
    rejected_after_interview = sum(
        1 for c in interviewed if c.stage == models.C_REJECTED
    )
    decided = advanced + rejected_after_interview
    has_offer = any(c.stage == models.C_OFFER for c in req.candidates)

    warning = None
    math = None
    if budget and used and not has_offer and decided >= 2:
        pass_rate = advanced / decided if decided else 0
        if pass_rate == 0:
            expected = "no offers projected"
            warning = (
                f"You've used {used}/{budget} interview slots with no offer. At this "
                f"pass-rate (0 of {decided} interviewed reached an offer), you'll run out "
                f"of slots before making a hire. Consider relaxing a must-have or "
                f"tightening the screen."
            )
            math = f"0 offers / {decided} interviewed → 0% pass-rate × {remaining} slots left = {expected}."
        elif remaining and (1 / pass_rate) > remaining:
            need = round(1 / pass_rate)
            warning = (
                f"At this pass-rate you'll need about {need} interviews per offer, but "
                f"only {remaining} slots remain. You'll likely run out before an offer."
            )
            math = f"1 / {pass_rate:.0%} pass-rate ≈ {need} interviews/offer > {remaining} slots left."

    pct = round(100 * used / budget) if budget else 0
    return {
        "budget": budget,
        "used": used,
        "remaining": remaining,
        "pct": min(pct, 100),
        "over": used > budget if budget else False,
        "warning": warning,
        "math": math,
        "has_offer": has_offer,
    }


def uncontracted_reasons(req: "models.Requisition") -> list[str]:
    """Uncontracted rejection reasons logged against the *current* contract
    version. Reasons from before the latest amendment don't count — amending
    folded that reason into the contract, so it's no longer 'uncontracted'."""
    cutoff = req.current_contract.created_at if req.current_contract else None
    reasons: list[str] = []
    for c in req.candidates:
        for d in c.decisions:
            if d.verdict != models.V_REJECT or d.criterion_id or not d.uncontracted_reason:
                continue
            if cutoff and d.created_at < cutoff:
                continue
            reasons.append(d.uncontracted_reason)
    return reasons


def drift_status(req: "models.Requisition") -> dict[str, Any]:
    """Active drift alert when >=3 rejections share an uncontracted theme and the
    alert hasn't been dismissed."""
    reasons = uncontracted_reasons(req)
    result = {"active": False, "theme": "", "count": len(reasons), "reasons": reasons}
    if req.drift_dismissed or len(reasons) < 3:
        return result
    theme = render.reasons_share_theme(reasons)
    if theme.get("match"):
        result["active"] = True
        result["theme"] = theme.get("theme", "")
    return result
