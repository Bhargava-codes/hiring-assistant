"""The Role Contract schema — the extraction target for the intake agent.

Per intake-instrument-v2: the conversation is 12 open anchors (2 per layer);
the schema runs in the background as an extraction target. Each field is tagged
with a provenance of ``stated`` (from the chat), ``revealed`` (from the async
ranking exercise), or ``null`` (not yet captured).

This module is the single source of truth for: the six layers, their anchor
questions, the field ids that live in each layer, which fields are critical
(the contract cannot render without them), and the blank-contract factory.
"""
from __future__ import annotations

from typing import Any

# Provenance tags
STATED = "stated"
REVEALED = "revealed"
NULL = "null"


# Each layer: id, human name, the two open anchor questions, and the field ids
# the agent tries to fill from the free answers to those anchors.
LAYERS: list[dict[str, Any]] = [
    {
        "id": 0,
        "name": "Business rationale",
        "anchors": [
            "What changed in the business that created this role — and what breaks if the seat stays empty for six months?",
            "Is a full-time hire the only way to close this gap, or did you weigh contract / promotion / restructuring?",
        ],
        "fields": ["business_outcome", "alternatives_considered"],
    },
    {
        "id": 1,
        "name": "Success definition",
        "anchors": [
            "It's 90 days after joining: what has this person shipped or taken off your plate that makes you say \"great hire\"?",
            "Think of someone who failed in a role like this — here or elsewhere. What did they get wrong?",
        ],
        "fields": ["success_90d", "failure_mode"],
    },
    {
        "id": 2,
        "name": "Candidate profile",
        "anchors": [
            "Describe your ideal candidate freely — then give me the three things that are truly non-negotiable and how you'd verify each in an interview.",
            "What would you happily trade away if those three are strong — and what's the one thing that ends the conversation instantly?",
        ],
        "fields": ["ideal_profile", "must_haves", "trade_offs", "deal_breaker"],
    },
    {
        "id": 3,
        "name": "Market reality",
        "anchors": [
            "What's the comp band, what decides where someone lands in it, and are you open to publishing it?",
            "If candidates with all three non-negotiables at this band turn out to be rare, which one relaxes first?",
        ],
        "fields": ["comp_band", "comp_logic", "comp_publishable", "relax_order"],
    },
    {
        "id": 4,
        "name": "Process contract",
        "anchors": [
            "How many candidates will you personally interview for this, across how many rounds — and which non-negotiable does each round test?",
            "Who can veto, who breaks a tie, and by when do you want the offer out?",
        ],
        "fields": ["interview_budget", "rounds", "decision_rights", "offer_date"],
    },
    {
        "id": 5,
        "name": "Drift rules + candidate-facing honesty",
        "anchors": [
            "What's most likely to change about this role mid-search? And do you agree that if you reject three candidates for a reason we haven't written down, we amend this contract first?",
            "What's genuinely hard or unglamorous about this role — and why would a strong candidate pick you over a bigger brand?",
        ],
        "fields": ["drift_risk", "drift_precommitment", "honest_constraints", "pitch"],
    },
]

# Human-readable labels for every field (used by the live panel and one-pager).
FIELD_LABELS: dict[str, str] = {
    "business_outcome": "Business outcome (what breaks if unfilled)",
    "alternatives_considered": "Alternatives considered",
    "success_90d": "90-day success",
    "failure_mode": "Failure mode",
    "ideal_profile": "Ideal profile",
    "must_haves": "Non-negotiables (3) + verification",
    "trade_offs": "Trade-offs",
    "deal_breaker": "Deal-breaker",
    "comp_band": "Comp band",
    "comp_logic": "Comp placement logic",
    "comp_publishable": "Open to publishing comp?",
    "relax_order": "Relaxation order",
    "interview_budget": "Interview budget (slots)",
    "rounds": "Rounds → criteria map",
    "decision_rights": "Decision rights (veto / tie-break)",
    "offer_date": "Target offer date",
    "drift_risk": "Likely mid-search drift",
    "drift_precommitment": "Drift pre-commitment (amend after 3)",
    "honest_constraints": "Honest constraints",
    "pitch": "Why pick you over a bigger brand",
}

# Contract cannot render without these (intake-instrument-v2 "Critical fields").
CRITICAL_FIELDS: list[str] = [
    "business_outcome",
    "success_90d",
    "must_haves",
    "deal_breaker",
    "comp_band",
    "relax_order",
    "interview_budget",
    "rounds",
    "drift_precommitment",
]

# Fields whose value is a list of structured objects rather than a string.
STRUCTURED_LIST_FIELDS = {"must_haves", "rounds"}

ALL_FIELDS: list[str] = [f for layer in LAYERS for f in layer["fields"]]


def blank_contract() -> dict[str, Any]:
    """A fresh contract-fields dict: every field null, no provenance."""
    fields: dict[str, Any] = {}
    for name in ALL_FIELDS:
        fields[name] = {"value": None, "provenance": NULL}
    return {
        "fields": fields,
        "conflicts": [],  # list of {type, field, note} e.g. stated vs revealed
    }


def field_layer(field: str) -> int:
    for layer in LAYERS:
        if field in layer["fields"]:
            return layer["id"]
    return -1


def is_filled(field_entry: dict[str, Any]) -> bool:
    """A field counts as filled when it has a non-empty value."""
    if not field_entry:
        return False
    value = field_entry.get("value")
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def layer_progress(contract_fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-layer fill progress for the live panel."""
    fields = contract_fields.get("fields", {})
    out = []
    for layer in LAYERS:
        total = len(layer["fields"])
        filled = sum(1 for f in layer["fields"] if is_filled(fields.get(f, {})))
        out.append(
            {
                "id": layer["id"],
                "name": layer["name"],
                "filled": filled,
                "total": total,
                "complete": filled == total,
            }
        )
    return out


def missing_critical(contract_fields: dict[str, Any]) -> list[str]:
    fields = contract_fields.get("fields", {})
    return [f for f in CRITICAL_FIELDS if not is_filled(fields.get(f, {}))]


def can_render(contract_fields: dict[str, Any]) -> bool:
    return len(missing_critical(contract_fields)) == 0
