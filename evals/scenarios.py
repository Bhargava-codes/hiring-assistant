"""Behavioural evals: multi-turn scenario/goal completion + instruction adherence.

Complements evals/run_evals.py (which is single-turn, output-shape focused).
Here we drive the WHOLE intake with scripted hiring-manager personas and check:

  1. SCENARIO / GOAL  — did the full conversation reach the goal?
     (agent raises `done`; contract is renderable / at least reaches review)
     -> the code-controlled action trigger; the closest analog to "tool calls"
        in this architecture (there is no LLM function-calling — orchestration is
        Python). A renderable `done` is what fires one-pager + rendering generation.

  2. INSTRUCTION ADHERENCE — did every agent turn obey the rules?
     - deterministic (both modes): no internal jargon, bounded length, ends cleanly
     - LLM-judge rubric (live-only): tone, specific acknowledgment, in-scope,
       one question — scored by EXTRACT_MODEL and skipped offline.

Run:
    python -m evals.scenarios                 # offline (deterministic)
    python -m evals.scenarios --live          # + LLM-judge instruction rubric
    python -m evals.scenarios --csv evals/scenario_results.csv
"""
from __future__ import annotations

import argparse
import re
import sys
import time

from lib import agent, llm, schema


# --- a stand-in for the ORM Contract (process_turn only needs these two attrs)
class FakeContract:
    def __init__(self):
        self.fields = schema.blank_contract()
        state = agent.new_state()
        agent.start(state)
        self.chat_state = state


# --- HM personas -------------------------------------------------------------
# Each is a queue of answers fed one per turn; if the agent inserts a recovery
# follow-up the queue simply advances, and a filler is used if it runs dry.

PERSONAS = {
    "Prepared operator (cooperative, complete)": {
        "quality": "renderable",  # rich answers -> a good model fills every critical field
        "answers": [
            "Support doubled after our enterprise push and the CS lead is drowning; if this seat stays empty six months we lose two enterprise renewals.",
            "We weighed an internal promotion but no one has enterprise CS experience, so a full-time hire it is.",
            "Ninety days in they own the top 20 enterprise accounts and have cut escalations to me by half.",
            "The ones who fail are ticket-closers who never build relationships with the account champions.",
            "Ideal is a senior enterprise CSM. Three non-negotiables: owned six-figure accounts (I'd verify with a renewal story), executive presence (verify with a QBR roleplay), and SaaS fluency (verify by what they'd ask our engineers).",
            "I'd happily trade away knowledge of our specific vertical. Instant no if they've never personally owned a renewal number, just been support.",
            "Comp band is 25 to 35 lakh fixed, placement depends on the size of book they've carried, and yes we can publish it.",
            "If they're rare at this band, SaaS fluency relaxes first; ownership and exec presence never relax.",
            "I'll personally interview 6 across 2 rounds — round 1 tests account ownership, round 2 tests exec presence.",
            "I decide, VP CS can veto, CEO breaks ties, and I want the offer out within 3 weeks of first screen.",
            "Scope may shift toward onboarding if we sign two more logos. And yes, if I reject three for an unwritten reason we amend the contract first.",
            "Honestly it's a lot of firefighting right now with no playbook — but they'd get direct access to me and the CEO and real ownership from day one.",
        ],
    },
    "Busy skeptic (terse, pushes back)": {
        "quality": "gaps",  # non-answers -> a good model should leave critical gaps, not fabricate
        "answers": [
            "CS is underwater, we need help.",
            "Yeah full-time.",
            "They just handle the big accounts.",
            "Bad ones don't build relationships.",
            "Someone experienced. Why do you need all this?",
            "Trade whatever. No if they can't do enterprise.",
            "Market rate. Not publishing it.",
            "Not sure.",
            "A few rounds I guess.",
            "I decide. Soon.",
            "Things change. Sure, fine.",
            "It's hard work, that's it.",
        ],
    },
    "Rambler (verbose, off-topic)": {
        "quality": "renderable",  # real content, just verbose -> should still be captured
        "answers": [
            "Oh man, where do I start — so last quarter we closed three huge logos, which is amazing, but our poor CS lead Priya is completely buried, she's working weekends, and honestly the whole team morale is dipping because of it, so that's really what kicked this off.",
            "We thought about a lot of things honestly, contractors, moving someone from support, but full-time is the way, our COO agrees.",
            "Great question — success looks like Priya getting her weekends back, the top accounts feeling loved, fewer fires. You know how it is.",
            "We had a guy once, lovely person, but he just closed tickets and never picked up the phone to the champions, total mismatch.",
            "Ideal person — warm, sharp, owns their book. Non-negotiables: real enterprise ownership, exec presence, and they know SaaS. I'd test all three in interviews.",
            "I'd trade vertical knowledge. Dealbreaker is no real ownership experience.",
            "Comp's 25 to 35 lakh, based on their book size, and sure we can share it.",
            "If rare, drop the SaaS requirement first.",
            "Six people, two rounds, ownership then presence.",
            "Me, VP can veto, CEO tiebreak, offer in three weeks.",
            "Might shift to onboarding. Yes to amending, that's fair.",
            "It's firefighting and no playbook, but huge ownership and exec exposure.",
        ],
    },
}

FORBIDDEN = re.compile(r"\b(layer|schema|anchor|contract field|field\d)\b", re.I)


def run_intake(answers: list[str], max_turns: int = 22):
    c = FakeContract()
    replies: list[str] = []
    fed = 0
    for _ in range(max_turns):
        ans = answers[fed] if fed < len(answers) else "Yes, that's everything."
        fed += 1
        res = agent.process_turn(c, ans)
        replies.append(res["assistant"])
        if res["done"]:
            break
    return c, replies


# --- LLM-judge for instruction adherence (live-only) -------------------------

_JUDGE_SYSTEM = """# ROLE
You grade ONE reply from a recruiter intake agent (persona: "Maya", a warm senior
recruiter) against a rubric. Output ONLY JSON.

# RUBRIC (each true/false)
- warm_professional: friendly, human, senior-recruiter tone (not robotic/curt).
- acknowledges_specifically: references the hiring manager's actual last answer,
  not a generic "got it".
- one_question: asks a single clear question (a compound anchor counts as one).
- in_scope: does NOT negotiate comp, promise outcomes, or invent role facts.
- no_jargon: never says "layer", "field", "schema", or "anchor".

# OUTPUT
{"scores": {"warm_professional": bool, "acknowledges_specifically": bool,
"one_question": bool, "in_scope": bool, "no_jargon": bool},
"verdict": "pass" | "fail", "reason": "<one short sentence>"}
verdict = "pass" only if at least 4 of 5 are true AND in_scope + no_jargon are true."""


def judge_turn(last_answer: str, reply: str) -> dict:
    data = llm.extract_json(
        [{"role": "system", "content": _JUDGE_SYSTEM},
         {"role": "user", "content": f"Hiring manager just said:\n{last_answer}\n\nAgent replied:\n{reply}\n\nGrade it."}],
        max_tokens=250, _label="instruction_judge",
    )
    return data


# --- checks ------------------------------------------------------------------

def C(desc, fn):
    return (desc, fn, False)


def CL(desc, fn):
    return (desc, fn, True)


def build_cases():
    cases = []
    for name, spec in PERSONAS.items():
        answers, quality = spec["answers"], spec["quality"]

        def make_run(a=answers):
            return lambda: run_intake(a)

        checks = [
            # --- SCENARIO / control flow (structural, both modes) ---
            # This is the code-controlled "action trigger" — the analog of a tool
            # call here: `done` firing correctly is what launches one-pager +
            # rendering generation downstream.
            C("SCENARIO: reaches the end of intake (agent raises done)", lambda r: r["done"]),
            C("finishes within a sane number of turns (<=20)", lambda r: r["turns"] <= 20),
            C("no already-answered anchor is re-asked", lambda r: not r["reasked"]),
            # --- INSTRUCTION adherence: deterministic (both modes) ---
            C("no internal jargon leaked in any turn", lambda r: not r["jargon_turns"]),
            C("every turn is reasonably bounded (<800 chars)", lambda r: r["max_len"] < 800),
        ]
        # --- GOAL QUALITY: live-only (offline fills every field, so meaningless there) ---
        if quality == "renderable":
            checks.append(CL("GOAL-QUALITY: rich input yields a renderable contract",
                             lambda r: r["can_render"]))
        else:  # "gaps"
            checks.append(CL("GOAL-QUALITY: non-answers leave critical gaps (no fabrication)",
                             lambda r: len(r["missing_critical"]) >= 1))
        # --- INSTRUCTION adherence: LLM-judge rubric (live-only) ---
        checks.append(CL("INSTRUCTION: a mid-intake turn passes the LLM-judge rubric",
                         lambda r: r.get("judge_pass") is True))
        cases.append({"name": name, "run": make_run(), "quality": quality, "checks": checks})
    return cases


def analyse(c: FakeContract, replies: list[str], live: bool) -> dict:
    fields = c.fields
    jargon_turns = [i for i, m in enumerate(replies) if FORBIDDEN.search(m)]
    # detect a re-asked anchor: same anchor first-question appearing twice as a
    # *fresh ask* (recovery follow-ups reword, so exact-substring repeats = a bug)
    anchor_hits = {}
    reasked = False
    for layer in schema.LAYERS:
        for a in layer["anchors"]:
            key = a[:40]
            n = sum(1 for m in replies if key in m)
            anchor_hits[key] = n
            if n > 1:
                reasked = True
    out = {
        "done": c.chat_state.get("done", False),
        "turns": len(replies),
        "can_render": schema.can_render(fields),
        "missing_critical": schema.missing_critical(fields),
        "jargon_turns": jargon_turns,
        "max_len": max((len(m) for m in replies), default=0),
        "reasked": reasked,
        "replies": replies,
    }
    if live:
        # judge the 3rd agent turn (a mid-intake acknowledgment) if we have one
        try:
            idx = min(2, len(replies) - 1)
            verdict = judge_turn("(mid-intake answer)", replies[idx])
            out["judge_pass"] = verdict.get("verdict") == "pass"
            out["judge_reason"] = verdict.get("reason", "")
        except Exception as e:
            out["judge_pass"] = None
            out["judge_reason"] = f"judge unavailable: {str(e)[:60]}"
    return out


def run(live: bool, csv_path: str | None = None) -> int:
    if not live:
        llm.has_api_key = lambda: False
    mode = "LIVE (" + llm.INTAKE_MODEL + ")" if live else "OFFLINE (deterministic)"
    print(f"\nContract HRMS — scenario + instruction evals · mode: {mode}\n" + "=" * 66)

    total_pass = total_run = total_skip = 0
    records = []
    for case in build_cases():
        c, replies = case["run"]()
        r = analyse(c, replies, live)
        print(f"\n[{case['name']}]  turns={r['turns']} done={r['done']} "
              f"can_render={r['can_render']} missing={len(r['missing_critical'])}")
        for desc, fn, live_only in case["checks"]:
            ctype = "instruction-judge" if live_only else "scenario/structural"
            if live_only and not live:
                print(f"  ~ SKIP (live-only): {desc}")
                total_skip += 1
                records.append({"persona": case["name"], "check": desc, "type": ctype,
                                "result": "SKIP", "detail": "needs live model"})
                continue
            try:
                ok = bool(fn(r))
            except Exception as e:
                ok = False
                desc += f"  [err: {e}]"
            total_run += 1
            total_pass += ok
            records.append({"persona": case["name"], "check": desc, "type": ctype,
                            "result": "PASS" if ok else "FAIL",
                            "detail": "" if ok else f"missing={r['missing_critical']} jargon={r['jargon_turns']} reason={r.get('judge_reason','')}"})
            print(("  ✓ " if ok else "  ✗ ") + desc)
            if not ok:
                print(f"      detail: done={r['done']} can_render={r['can_render']} "
                      f"missing={r['missing_critical']} judge={r.get('judge_reason','')}")

    print("\n" + "=" * 66 + f"\nTOTAL: {total_pass}/{total_run} passed"
          + (f"  ({total_skip} live-only skipped)" if not live else ""))
    if csv_path:
        import csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["persona", "check", "type", "result", "detail"])
            w.writeheader(); w.writerows(records)
        print(f"Wrote {len(records)} rows to {csv_path}")
    return 0 if total_pass == total_run else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--csv", metavar="PATH")
    args = ap.parse_args()
    sys.exit(run(args.live, args.csv))
