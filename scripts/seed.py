"""Seed Contract HRMS with a fictional 100-person Indian SaaS company
("Traqo Technologies", Bangalore) and four requisitions — one in each lifecycle
state — so every screen has something real to show.

Run:  python -m scripts.seed
"""
from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import render, schema  # noqa: E402
from lib.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app import models  # noqa: E402

RND = random.Random(42)

FIRST = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna",
    "Ishaan", "Rohan", "Kabir", "Ananya", "Diya", "Aadhya", "Saanvi", "Pari",
    "Ira", "Myra", "Aarohi", "Anika", "Navya", "Priya", "Riya", "Sneha", "Kavya",
    "Meera", "Nisha", "Pooja", "Divya", "Shreya", "Rahul", "Amit", "Nikhil",
    "Karthik", "Varun", "Siddharth", "Manish", "Deepak", "Suresh", "Rajesh",
    "Vikram", "Ashwin", "Harsha", "Tejas", "Gaurav", "Naveen", "Prakash",
    "Lakshmi", "Sunita", "Neha", "Ritu", "Swati", "Aishwarya", "Bhavana",
]
LAST = [
    "Sharma", "Verma", "Iyer", "Nair", "Reddy", "Rao", "Menon", "Gupta", "Bose",
    "Chatterjee", "Mukherjee", "Patel", "Shah", "Desai", "Kulkarni", "Joshi",
    "Deshpande", "Pillai", "Krishnan", "Subramanian", "Bhat", "Kamath", "Shetty",
    "Hegde", "Gowda", "Naidu", "Chowdhury", "Das", "Ghosh", "Banerjee", "Singh",
    "Malhotra", "Kapoor", "Chopra", "Agarwal", "Bansal", "Mehta", "Jain",
]
LOCATIONS = ["Bangalore", "Bangalore", "Bangalore", "Pune", "Remote"]
CTC_BANDS = ["B1", "B2", "B3", "B4", "B5", "B6"]

# department -> (count, [designation ladder by seniority])
DEPARTMENTS = {
    "Engineering": (40, ["VP Engineering", "Engineering Manager", "Staff Engineer",
                         "Senior Engineer", "Software Engineer", "Associate Engineer"]),
    "Sales": (20, ["VP Sales", "Sales Manager", "Account Executive", "SDR"]),
    "CS": (12, ["Head of CS", "CS Manager", "Customer Success Manager", "CS Associate"]),
    "Marketing": (8, ["Head of Marketing", "Marketing Manager", "Content Lead", "Marketing Associate"]),
    "Product": (8, ["Head of Product", "Group PM", "Product Manager", "Associate PM"]),
    "Finance": (5, ["Head of Finance", "Finance Manager", "Accountant"]),
    "HR": (4, ["Head of HR", "HR Manager", "HR Executive"]),
    # Ops is 2 generated + the founder (below) = 3, keeping the company at
    # exactly 100 people with the spec's per-department headcounts.
    "Ops": (2, ["Head of Ops", "Ops Manager", "Ops Executive"]),
}


def _reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()


def _unique_names(n: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    while len(out) < n:
        name = f"{RND.choice(FIRST)} {RND.choice(LAST)}"
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def seed_employees(db) -> dict[str, list[models.Employee]]:
    """Create 100 employees with a sensible manager hierarchy. Returns
    {department: [employees...]} for requisition wiring."""
    names = iter(_unique_names(100))
    code = 1000
    by_dept: dict[str, list[models.Employee]] = {}
    # CEO first.
    ceo = models.Employee(
        name="Rohan Malhotra", employee_code="TT1000", department="Ops",
        designation="Founder & CEO", manager_id=None, location="Bangalore",
        ctc_band="B6", join_date=date(2019, 3, 1),
    )
    db.add(ceo)
    db.flush()
    code = 1001

    for dept, (count, ladder) in DEPARTMENTS.items():
        emps: list[models.Employee] = []
        for i in range(count):
            # First person in dept is the head (reports to CEO); shape the rest.
            if i == 0:
                designation = ladder[0]
                level = len(ladder)
            else:
                # Weight toward junior designations.
                pick = min(int(RND.triangular(1, len(ladder) - 1, len(ladder) - 1)), len(ladder) - 1)
                designation = ladder[pick]
                level = len(ladder) - pick
            emp = models.Employee(
                name=next(names),
                employee_code=f"TT{code}",
                department=dept,
                designation=designation,
                manager_id=None,  # set below
                location=RND.choice(LOCATIONS),
                ctc_band=CTC_BANDS[min(level - 1, 5)],
                join_date=date(2020, 1, 1) + timedelta(days=RND.randint(0, 2000)),
            )
            code += 1
            db.add(emp)
            emps.append(emp)
        db.flush()
        # Wire managers: head -> CEO; managers -> head; ICs -> a manager.
        head = emps[0]
        head.manager_id = ceo.id
        managers = [e for e in emps[1:] if "Manager" in e.designation or "Lead" in e.designation or "Staff" in e.designation or "VP" in e.designation]
        for e in emps[1:]:
            if e in managers:
                e.manager_id = head.id
            else:
                e.manager_id = (RND.choice(managers).id if managers else head.id)
        by_dept[dept] = emps
    db.flush()
    return by_dept


def _make_fields(values: dict) -> dict:
    """Build a contract-fields dict with the given values tagged 'stated'."""
    c = schema.blank_contract()
    for name, val in values.items():
        if name in c["fields"] and val not in (None, "", [], {}):
            c["fields"][name] = {"value": val, "provenance": schema.STATED}
    return c


# --- A fully specified contract for the ACTIVE requisition -------------------
BACKEND_VALUES = {
    "business_outcome": (
        "Our payments team is drowning — reconciliation and payout bugs are eating a "
        "third of the roadmap. If this seat stays empty six months, we miss the "
        "enterprise billing launch and keep bleeding senior time on firefighting."
    ),
    "alternatives_considered": "Weighed a contractor and an internal promotion; neither gives the ownership this needs.",
    "success_90d": (
        "Owns the payouts service end-to-end, has shipped idempotent reconciliation, "
        "and taken the on-call payment escalations off my plate."
    ),
    "failure_mode": "Someone who writes clever code but can't reason about money-movement edge cases or partial failures.",
    "ideal_profile": "A backend engineer who has run a payments or ledger system in production at scale.",
    "must_haves": [
        {"text": "Production payments/ledger experience", "verification": "Walk through a reconciliation bug they debugged; probe idempotency."},
        {"text": "Strong Python + Postgres", "verification": "Live schema-design exercise for a payouts table."},
        {"text": "Owns reliability (on-call maturity)", "verification": "Ask for a postmortem they wrote and what changed after."},
    ],
    "trade_offs": "Happy to trade away domain knowledge of our vertical and formal CS degree if the three are strong.",
    "deal_breaker": "No production experience with money movement — instant no.",
    "comp_band": "₹38–52L fixed + 0.05–0.1% ESOP",
    "comp_logic": "Depth of payments experience and reliability ownership decide placement in the band.",
    "comp_publishable": "yes",
    "relax_order": "Relax 'Postgres depth' first, then 'on-call maturity'; never relax payments experience.",
    "interview_budget": 8,
    "rounds": [
        {"round": "Round 1 — Screen", "tests": "Production payments/ledger experience"},
        {"round": "Round 2 — Systems", "tests": "Python + Postgres schema design"},
        {"round": "Round 3 — Reliability", "tests": "On-call maturity / postmortem review"},
    ],
    "decision_rights": "I decide; VP Eng can veto; CTO breaks ties.",
    "offer_date": "Within 4 weeks of first screen.",
    "drift_risk": "Scope may tilt toward fraud/risk if the billing launch slips.",
    "drift_precommitment": "Agreed — if I reject 3 candidates for a reason we haven't written down, we amend this contract first.",
    "honest_constraints": "It's a lot of legacy reconciliation code and on-call. Not greenfield.",
    "pitch": "You own money movement for a fast-growing SaaS — real scope, direct line to the CTO, no bureaucracy.",
}

CLOSED_VALUES = {
    "business_outcome": "Marketing had no owner for lifecycle; activation was flat for two quarters.",
    "success_90d": "Owns lifecycle email + in-app nudges; has moved week-1 activation by a measurable amount.",
    "failure_mode": "A 'campaign calendar' marketer who never touches the funnel data.",
    "ideal_profile": "A lifecycle/growth marketer comfortable with SQL and experimentation.",
    "must_haves": [
        {"text": "Owned lifecycle/activation before", "verification": "Show a funnel they moved and how."},
        {"text": "Comfortable with data (SQL/analytics)", "verification": "Interpret a retention chart live."},
        {"text": "Writes well", "verification": "Review a real onboarding email they wrote."},
    ],
    "deal_breaker": "Can't read a funnel — no.",
    "comp_band": "₹18–26L fixed",
    "comp_logic": "Ownership of measurable activation wins decides band placement.",
    "relax_order": "Relax copywriting polish first; never relax data literacy.",
    "interview_budget": 6,
    "rounds": [
        {"round": "Round 1 — Screen", "tests": "Owned lifecycle/activation"},
        {"round": "Round 2 — Craft", "tests": "Data literacy + writing"},
    ],
    "decision_rights": "Head of Marketing decides; CEO breaks ties.",
    "offer_date": "Closed — offer accepted.",
    "drift_precommitment": "Agreed.",
    "honest_constraints": "Small team, you'll do your own analytics; no big brand budget.",
    "pitch": "Own activation for the whole product from day one.",
}

INTAKE_PARTIAL = {
    "business_outcome": "Support volume doubled after the enterprise push; the CS lead is underwater.",
    "alternatives_considered": "Considered promoting internally but no one has enterprise CS experience.",
    "success_90d": "Owns the top 20 enterprise accounts and has cut escalations to me by half.",
    "failure_mode": "A ticket-closer who never builds relationships with the account champions.",
    "ideal_profile": "An enterprise CSM who has carried a book of six-figure accounts.",
}


def seed_requisitions(db, by_dept: dict[str, list[models.Employee]]) -> None:
    eng_head = by_dept["Engineering"][0]
    cs_head = by_dept["CS"][0]
    mkt_head = by_dept["Marketing"][0]
    product_head = by_dept["Product"][0]

    # 1) DRAFT — no contract yet.
    draft = models.Requisition(
        title="Product Designer", department="Product",
        hiring_manager_id=product_head.id, status=models.REQ_DRAFT,
        created_at=datetime.utcnow() - timedelta(days=1),
    )
    db.add(draft)

    # 2) INTAKE — partial contract, conversation mid-flight.
    intake = models.Requisition(
        title="Enterprise Customer Success Manager", department="CS",
        hiring_manager_id=cs_head.id, status=models.REQ_INTAKE,
        created_at=datetime.utcnow() - timedelta(days=2),
    )
    db.add(intake)
    db.flush()
    intake_c = models.Contract(
        requisition_id=intake.id, version=1,
        fields=_make_fields(INTAKE_PARTIAL),
        chat_state={
            "messages": [
                {"role": "assistant", "content": "Hi — I'm the discovery agent. Let's start.\n\n" + schema.LAYERS[0]["anchors"][0]},
                {"role": "user", "content": INTAKE_PARTIAL["business_outcome"]},
                {"role": "assistant", "content": "Got it. " + schema.LAYERS[0]["anchors"][1]},
                {"role": "user", "content": INTAKE_PARTIAL["alternatives_considered"]},
                {"role": "assistant", "content": "Makes sense. " + schema.LAYERS[1]["anchors"][0]},
                {"role": "user", "content": INTAKE_PARTIAL["success_90d"]},
                {"role": "assistant", "content": "And " + schema.LAYERS[1]["anchors"][1]},
                {"role": "user", "content": INTAKE_PARTIAL["failure_mode"]},
                {"role": "assistant", "content": schema.LAYERS[2]["anchors"][0]},
            ],
            "layer_index": 2, "anchor_index": 0, "recovery_used": [],
            "asked": {"layer": 2, "anchor": 0}, "done": False,
        },
    )
    db.add(intake_c)

    # 3) ACTIVE — full approved contract + 4 renderings + 8 candidates + drift.
    active = models.Requisition(
        title="Senior Backend Engineer (Payments)", department="Engineering",
        hiring_manager_id=eng_head.id, status=models.REQ_ACTIVE,
        created_at=datetime.utcnow() - timedelta(days=9),
    )
    db.add(active)
    db.flush()
    active_fields = _make_fields(BACKEND_VALUES)
    active_c = models.Contract(
        requisition_id=active.id, version=1, fields=active_fields,
        chat_state={"messages": [], "done": True},
        one_pager=render.generate_one_pager(active_fields, active.title, active.department),
        approved_at=datetime.utcnow() - timedelta(days=7),
    )
    db.add(active_c)
    db.flush()
    for rtype in models.RENDERING_TYPES:
        db.add(models.Rendering(
            contract_id=active_c.id, type=rtype,
            content=render.generate_rendering(rtype, active_fields, active.title, active.department),
        ))

    # Candidates: 8. Three uncontracted rejections share a "communication" theme
    # so the drift alert is live on load; two interviews in progress.
    cand_specs = [
        ("Kavya Reddy", "LinkedIn", models.C_INTERVIEW, True, None),
        ("Arjun Nair", "Referral", models.C_INTERVIEW, True, None),
        ("Sneha Iyer", "Inbound", models.C_SCREENED, False, None),
        ("Vikram Rao", "Agency", models.C_APPLIED, False, None),
        ("Deepak Shah", "LinkedIn", models.C_REJECTED, False, ("criterion", "must_have_0")),
        ("Priya Menon", "Agency", models.C_REJECTED, True, ("uncontracted", "Communication was not clear enough on the call.")),
        ("Rahul Gupta", "Inbound", models.C_REJECTED, True, ("uncontracted", "Weak communication in the interview; couldn't explain tradeoffs.")),
        ("Nisha Bose", "Referral", models.C_REJECTED, True, ("uncontracted", "Poor written communication in the take-home.")),
    ]
    for name, source, stage, slot, decision in cand_specs:
        cand = models.Candidate(
            requisition_id=active.id, name=name, source=source,
            stage=stage, slot_used=slot,
        )
        db.add(cand)
        db.flush()
        if stage in (models.C_SCREENED, models.C_INTERVIEW):
            db.add(models.Decision(candidate_id=cand.id, verdict=models.V_ADVANCE))
        if decision:
            kind, val = decision
            if kind == "criterion":
                db.add(models.Decision(candidate_id=cand.id, verdict=models.V_REJECT, criterion_id=val))
            else:
                db.add(models.Decision(candidate_id=cand.id, verdict=models.V_REJECT, uncontracted_reason=val))

    # 4) CLOSED — full contract, renderings, an accepted offer.
    closed = models.Requisition(
        title="Lifecycle Marketing Manager", department="Marketing",
        hiring_manager_id=mkt_head.id, status=models.REQ_CLOSED,
        created_at=datetime.utcnow() - timedelta(days=40),
    )
    db.add(closed)
    db.flush()
    closed_fields = _make_fields(CLOSED_VALUES)
    closed_c = models.Contract(
        requisition_id=closed.id, version=1, fields=closed_fields,
        chat_state={"messages": [], "done": True},
        one_pager=render.generate_one_pager(closed_fields, closed.title, closed.department),
        approved_at=datetime.utcnow() - timedelta(days=35),
    )
    db.add(closed_c)
    db.flush()
    for rtype in models.RENDERING_TYPES:
        db.add(models.Rendering(
            contract_id=closed_c.id, type=rtype,
            content=render.generate_rendering(rtype, closed_fields, closed.title, closed.department),
        ))
    for name, stage, slot in [
        ("Aishwarya Kamath", models.C_OFFER, True),
        ("Tejas Joshi", models.C_REJECTED, True),
        ("Meera Pillai", models.C_REJECTED, False),
    ]:
        cand = models.Candidate(requisition_id=closed.id, name=name, source="LinkedIn", stage=stage, slot_used=slot)
        db.add(cand)
        db.flush()
        if stage == models.C_OFFER:
            db.add(models.Decision(candidate_id=cand.id, verdict=models.V_ADVANCE))
        else:
            db.add(models.Decision(candidate_id=cand.id, verdict=models.V_REJECT, criterion_id="must_have_1"))

    db.commit()


def main() -> None:
    _reset_db()
    db = SessionLocal()
    try:
        by_dept = seed_employees(db)
        db.commit()
        seed_requisitions(db, by_dept)
        emp_count = db.query(models.Employee).count()
        req_count = db.query(models.Requisition).count()
        print(f"Seeded {emp_count} employees and {req_count} requisitions.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
