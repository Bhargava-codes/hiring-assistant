"""Stage-by-stage evals for the Contract HRMS LLM pipeline.

Real hiring-manager utterances as fixtures, with assertions per stage. Two kinds
of check:
  - structural  : must hold in BOTH offline and live mode (plumbing / format /
                  rule adherence the deterministic fallback also satisfies)
  - live-only   : semantic quality only a real model can do (e.g. splitting one
                  answer into distinct fields, matching a theme across different
                  wording). SKIPPED (not failed) in offline mode.

Run:
    python -m evals.run_evals            # offline (deterministic, free)
    python -m evals.run_evals --live     # against the configured OpenRouter model

Offline mode forces llm.has_api_key() -> False so every stage takes its
deterministic fallback path; useful as a always-green plumbing check and to see
exactly which behaviours actually need a live model.
"""
from __future__ import annotations

import argparse
import sys
import time

from lib import agent, llm, profiles, render, schema
from lib.schema import LAYERS


# --- check builders ----------------------------------------------------------
# A check is (description, predicate, live_only).

def C(desc, fn):
    """Structural check — must pass in both modes."""
    return (desc, fn, False)


def CL(desc, fn):
    """Live-only check — semantic; skipped in offline mode."""
    return (desc, fn, True)


# --- fixtures ----------------------------------------------------------------

def make_contract(values: dict) -> dict:
    c = schema.blank_contract()
    for k, v in values.items():
        c["fields"][k] = {"value": v, "provenance": schema.STATED}
    return c


# A fully-captured contract (the Enterprise CSM role) for the generation stages.
GOLDEN = make_contract({
    "business_outcome": "Support doubled after the enterprise push; the CS lead is underwater and we risk two renewals if unfilled.",
    "alternatives_considered": "Weighed an internal promotion; no one has enterprise CS experience.",
    "success_90d": "Owns the top 20 enterprise accounts and has cut escalations to me by half.",
    "failure_mode": "A ticket-closer who never builds relationships with the account champions.",
    "ideal_profile": "An enterprise CSM who has carried a book of six-figure accounts.",
    "must_haves": [
        {"text": "Owned enterprise accounts at six figures+", "verification": "Walk through a renewal they saved"},
        {"text": "Executive presence (runs a QBR with a VP solo)", "verification": "Roleplay QBR in the interview"},
        {"text": "SaaS platform fluency", "verification": "Ask what they'd ask our engineers in week one"},
    ],
    "trade_offs": "Happy to trade deep knowledge of our specific vertical.",
    "deal_breaker": "Never personally owned a renewal or expansion number.",
    "comp_band": "₹25–35L fixed",
    "comp_logic": "Placement by size of book previously carried.",
    "comp_publishable": "yes",
    "relax_order": "Relax SaaS fluency first; ownership and exec presence never relax.",
    "interview_budget": 6,
    "rounds": [
        {"round": "Round 1", "tests": "Account ownership + renewal story"},
        {"round": "Round 2", "tests": "Executive presence (QBR roleplay)"},
    ],
    "decision_rights": "HM decides; VP CS can veto; CEO breaks ties.",
    "offer_date": "Within 3 weeks of first screen.",
    "drift_risk": "Scope may shift toward onboarding if two more logos sign.",
    "drift_precommitment": "Agreed — amend the contract before rejecting a 4th on an unwritten reason.",
    "honest_constraints": "Lots of firefighting right now, no playbook yet.",
    "pitch": "Direct access to the CEO and real ownership of the enterprise book from day one.",
})

# A partial contract (missing honest_constraints + pitch) for the "gaps" check.
PARTIAL = make_contract({
    "business_outcome": "Backend team is drowning in payment reconciliation bugs.",
    "success_90d": "Owns the payouts service end to end.",
    "must_haves": [{"text": "Production payments experience", "verification": "Debug a reconciliation bug"}],
    "deal_breaker": "No money-movement experience.",
    "comp_band": "₹38–52L",
    "relax_order": "Relax Postgres depth first.",
    "interview_budget": 8,
    "rounds": [{"round": "Round 1", "tests": "Payments experience"}],
    "drift_precommitment": "Agreed.",
})


def _extract(layer: int, aidx: int, answer: str) -> dict:
    cur = schema.blank_contract()
    cur["_asked"] = {"layer": layer, "anchor": aidx}  # needed by the offline heuristic
    return agent.extract_fields(LAYERS[layer]["anchors"][aidx], answer, cur)


class _StubContract:
    """Minimal stand-in for the ORM Contract — process_turn only touches these."""
    def __init__(self):
        self.fields = schema.blank_contract()
        st = agent.new_state(); agent.start(st)
        self.chat_state = st


def _one_turn(answer: str) -> str:
    """One full intake turn through the real engine (live or offline), returning
    Maya's reply. Exercises whichever path the API key selects."""
    return agent.process_turn(_StubContract(), answer)["assistant"]


def _distinct(o: dict, *keys) -> bool:
    vals = [str(o.get(k)) for k in keys if k in o]
    return len(vals) == len(set(vals)) and len(vals) == len(keys)


# --- the suite ---------------------------------------------------------------

def build_suite() -> list[dict]:
    return [
        # ===== STAGE 2 — Field extraction ====================================
        {"stage": "2 · Extraction", "name": "Comp — three things at once",
         "run": lambda: _extract(3, 0,
            "Comp band is 25 to 35 lakh fixed, placement depends on the size of book "
            "they've carried before, and yes we're fine publishing it."),
         "checks": [
            C("captures comp_band", lambda o: "comp_band" in o),
            C("captures comp_logic", lambda o: "comp_logic" in o),
            C("captures comp_publishable", lambda o: "comp_publishable" in o),
            CL("the three comp values are DISTINCT (no whole-answer duplication)",
               lambda o: _distinct(o, "comp_band", "comp_logic", "comp_publishable")),
            CL("comp_publishable is a short yes/no", lambda o: len(str(o.get("comp_publishable", ""))) <= 15),
         ]},
        {"stage": "2 · Extraction", "name": "The vague non-answer",
         "run": lambda: _extract(1, 0, "Honestly not sure yet, we'll figure it out as we go."),
         "checks": [
            CL("does NOT hallucinate a 90-day success from a non-answer",
               lambda o: not o.get("success_90d")),
         ]},
        {"stage": "2 · Extraction", "name": "Three must-haves with verification",
         "run": lambda: _extract(2, 0,
            "Ideal is a senior CSM. Three non-negotiables: owned six-figure accounts "
            "(I'd verify with a renewal story), exec presence (verify with a QBR roleplay), "
            "and SaaS fluency (verify by what they'd ask our engineers)."),
         "checks": [
            C("captures must_haves as a list", lambda o: isinstance(o.get("must_haves"), list)),
            CL("captures exactly three must-haves", lambda o: len(o.get("must_haves") or []) == 3),
            CL("each must-have carries a verification", lambda o: sum(
                1 for m in (o.get("must_haves") or []) if isinstance(m, dict) and m.get("verification")) >= 2),
         ]},
        {"stage": "2 · Extraction", "name": "Budget + rounds combined",
         "run": lambda: _extract(4, 0,
            "I'll personally interview 6 across 2 rounds — round 1 tests ownership, "
            "round 2 tests exec presence."),
         "checks": [
            C("interview_budget is an integer", lambda o: isinstance(o.get("interview_budget"), int)),
            C("interview_budget == 6", lambda o: o.get("interview_budget") == 6),
            CL("rounds captured as 2 distinct rounds", lambda o: len(o.get("rounds") or []) >= 2),
         ]},
        {"stage": "2 · Extraction", "name": "Budget written as a word",
         "run": lambda: _extract(4, 0, "I'll personally meet about six candidates for this."),
         "checks": [
            CL("parses 'six' -> 6", lambda o: o.get("interview_budget") == 6),
         ]},
        {"stage": "2 · Extraction", "name": "Trade-off and deal-breaker together",
         "run": lambda: _extract(2, 1,
            "I'd happily trade away knowledge of our specific vertical. Instant no if "
            "they've never personally owned a number, just been a support resource."),
         "checks": [
            C("captures trade_offs", lambda o: "trade_offs" in o),
            C("captures deal_breaker", lambda o: "deal_breaker" in o),
            CL("trade-off and deal-breaker are DISTINCT values",
               lambda o: _distinct(o, "trade_offs", "deal_breaker")),
         ]},

        # ===== STAGE 1 — Conversational turn =================================
        {"stage": "1 · Discovery agent", "name": "Acknowledge + ask next anchor",
         "run": lambda: _one_turn(
            "Support doubled after our enterprise push and the CS lead is drowning."),
         "checks": [
            C("asks the intended next question", lambda o: LAYERS[0]["anchors"][1][:30].lower() in o.lower()),
            C("never leaks internal jargon (layer/field/schema)",
              lambda o: not any(w in o.lower() for w in ["layer", "schema", "field", "anchor"])),
         ]},

        # ===== STAGE 3 — One-pager ==========================================
        {"stage": "3 · One-pager", "name": "Full contract -> all sections",
         "run": lambda: render.generate_one_pager(GOLDEN, "Enterprise CSM", "CS"),
         "checks": [
            C("has Business outcome", lambda o: "business outcome" in o.lower()),
            C("has 90-day success", lambda o: "90-day" in o.lower() or "90 day" in o.lower()),
            C("has non-negotiables", lambda o: "non-negotiable" in o.lower()),
            C("has deal-breaker", lambda o: "deal-breaker" in o.lower() or "deal breaker" in o.lower()),
            C("has comp", lambda o: "comp" in o.lower()),
            C("has drift rule", lambda o: "drift" in o.lower()),
            C("has honest constraints", lambda o: "constraint" in o.lower() or "hard" in o.lower()),
         ]},
        {"stage": "3 · One-pager", "name": "Partial contract flags the gaps",
         "run": lambda: render.generate_one_pager(PARTIAL, "Backend Engineer", "Engineering"),
         "checks": [
            C("marks missing fields rather than fabricating",
              lambda o: "not captured" in o.lower() or "_tbd_" in o.lower() or "to be confirmed" in o.lower()),
         ]},

        # ===== STAGE 4 — Renderings =========================================
        {"stage": "4 · Rendering · Posting", "name": "Candidate posting rules",
         "run": lambda: render.generate_rendering("POSTING", GOLDEN, "Enterprise CSM", "CS"),
         "checks": [
            C("includes the comp band", lambda o: any(t in o.lower() for t in ["lakh", "₹", "25"])),
            C("has an honest 'what's hard' section",
              lambda o: any(t in o.lower() for t in ["hard", "honest", "constraint", "unglamorous"])),
         ]},
        {"stage": "4 · Rendering · Sourcing", "name": "Sourcing spec anti-patterns",
         "run": lambda: render.generate_rendering("SOURCING_SPEC", GOLDEN, "Enterprise CSM", "CS"),
         "checks": [
            C("names anti-patterns / deal-breaker to screen out",
              lambda o: any(t in o.lower() for t in ["anti", "deal-breaker", "deal breaker", "avoid", "failure"])),
         ]},
        {"stage": "4 · Rendering · Rubric", "name": "Screening rubric structure",
         "run": lambda: render.generate_rendering("SCREENING_RUBRIC", GOLDEN, "Enterprise CSM", "CS"),
         "checks": [
            C("contains a markdown table", lambda o: o.count("|") >= 3),
            C("references the interview budget", lambda o: "budget" in o.lower() or "slot" in o.lower() or "6" in o),
         ]},
        {"stage": "4 · Rendering · Scorecards", "name": "Panel scorecards structure",
         "run": lambda: render.generate_rendering("PANEL_SCORECARDS", GOLDEN, "Enterprise CSM", "CS"),
         "checks": [
            C("names interview rounds", lambda o: "round" in o.lower()),
            C("has a 1-4 rating scale", lambda o: "1" in o and "4" in o),
         ]},

        # ===== STAGE 5 — Drift theme match ==================================
        {"stage": "5 · Drift theme", "name": "Same theme — literal wording",
         "run": lambda: render.reasons_share_theme(
            ["Poor communication on the call", "Communication was weak", "Bad written communication"]),
         "checks": [
            C("detects the shared theme", lambda o: o.get("match") is True),
         ]},
        {"stage": "5 · Drift theme", "name": "Same theme — different wording",
         "run": lambda: render.reasons_share_theme(
            ["Couldn't articulate their thinking", "Struggled to explain tradeoffs", "Vague and hard to follow"]),
         "checks": [
            CL("detects the theme despite no shared keyword", lambda o: o.get("match") is True),
         ]},
        {"stage": "5 · Drift theme", "name": "Genuinely unrelated reasons",
         "run": lambda: render.reasons_share_theme(
            ["Comp expectations too high", "Wanted fully remote", "90-day notice period"]),
         "checks": [
            C("does NOT invent a false theme", lambda o: o.get("match") is False),
         ]},
        {"stage": "5 · Drift theme", "name": "Below the 3-rejection threshold",
         "run": lambda: render.reasons_share_theme(["Too junior", "Not senior enough"]),
         "checks": [
            C("no alert under 3 reasons", lambda o: o.get("match") is False),
         ]},

        # ===== STAGE 6 — Ranking reconciliation =============================
        {"stage": "6 · Ranking", "name": "Ranking contradicts a stated must-have",
         "run": lambda: render.reconcile_ranking(
            make_contract({"must_haves": [{"text": "Deep domain expertise", "verification": ""}],
                           "deal_breaker": "No pedigree"}),
            [profiles.PROFILE_BY_ID["p2"], profiles.PROFILE_BY_ID["p3"], profiles.PROFILE_BY_ID["p5"]]),
         "checks": [
            C("flags at least one stated-vs-revealed conflict", lambda o: len(o) >= 1),
            C("conflict notes are non-empty", lambda o: all(c.get("note") for c in o)),
         ]},
        {"stage": "6 · Ranking", "name": "Ranking agrees with the stated must-have",
         "run": lambda: render.reconcile_ranking(
            make_contract({"must_haves": [{"text": "Reliability and on-call rigor", "verification": ""}],
                           "deal_breaker": "No pedigree"}),
            [profiles.PROFILE_BY_ID["p6"], profiles.PROFILE_BY_ID["p1"], profiles.PROFILE_BY_ID["p4"]]),
         "checks": [
            CL("aligned ranking raises NO false stated-vs-revealed conflict", lambda o: len(o) == 0),
         ]},
    ]


def run(live: bool, delay: float = 0.0, csv_path: str | None = None) -> int:
    if not live:
        llm.has_api_key = lambda: False  # force every stage onto its offline path
    mode = "LIVE (" + llm.INTAKE_MODEL + ")" if live else "OFFLINE (deterministic fallback)"

    print(f"\nContract HRMS — stage evals · mode: {mode}"
          + (f" · pacing {delay}s/call" if live and delay else "") + "\n" + "=" * 64)
    total_pass = total_run = total_skip = 0
    stage_totals: dict[str, list[int]] = {}
    records: list[dict] = []  # one row per check, for the CSV sheet

    for i, case in enumerate(build_suite()):
        stage = case["stage"]
        # Free models are capped per-minute (Gemma free = 16/min); pace live runs.
        if live and delay and i:
            time.sleep(delay)
        try:
            out = case["run"]()
            err = None
        except Exception as e:  # a live 429/402 etc. — report, don't crash the suite
            out, err = None, e

        print(f"\n[{stage}] {case['name']}")
        if err is not None:
            print(f"  ✗ run failed: {type(err).__name__}: {str(err)[:90]}")
            stage_totals.setdefault(stage, [0, 0])
            for desc, fn, live_only in case["checks"]:
                records.append({"stage": stage, "case": case["name"], "check": desc,
                                "type": "live-only" if live_only else "structural",
                                "result": "RUN-ERROR", "detail": f"{type(err).__name__}: {str(err)[:80]}"})
            continue

        for desc, fn, live_only in case["checks"]:
            ctype = "live-only" if live_only else "structural"
            if live_only and not live:
                print(f"  ~ SKIP (live-only): {desc}")
                total_skip += 1
                records.append({"stage": stage, "case": case["name"], "check": desc,
                                "type": ctype, "result": "SKIP", "detail": "needs a live model"})
                continue
            try:
                ok = bool(fn(out))
            except Exception as e:
                ok = False
                desc = f"{desc}  [check error: {e}]"
            total_run += 1
            st = stage_totals.setdefault(stage, [0, 0])
            st[1] += 1
            records.append({"stage": stage, "case": case["name"], "check": desc,
                            "type": ctype, "result": "PASS" if ok else "FAIL",
                            "detail": "" if ok else str(out)[:160]})
            if ok:
                total_pass += 1
                st[0] += 1
                print(f"  ✓ {desc}")
            else:
                print(f"  ✗ {desc}\n      got: {str(out)[:120]}")

    print("\n" + "=" * 64 + "\nPer-stage:")
    for stage, (p, t) in stage_totals.items():
        print(f"  {stage:28s} {p}/{t}")
    print(f"\nTOTAL: {total_pass}/{total_run} passed"
          + (f"  ({total_skip} live-only checks skipped in offline mode)" if not live else ""))

    if csv_path:
        import csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["stage", "case", "check", "type", "result", "detail"])
            w.writeheader()
            w.writerows(records)
        print(f"\nWrote {len(records)} rows to {csv_path}")

    return 0 if total_pass == total_run else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="run against the configured OpenRouter model")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="seconds between calls in live mode (default 4.0, ~15/min to stay under the free 16/min cap)")
    ap.add_argument("--csv", metavar="PATH", help="write a per-check results sheet to this CSV path")
    args = ap.parse_args()
    sys.exit(run(args.live, args.delay, args.csv))
