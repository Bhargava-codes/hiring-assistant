"""Goal-push scenarios for EVERY critical field across the 6 layers.

The budget example ("I don't know" -> confirm with finance) is one of nine.
This generates + verifies the same goal-oriented behaviour for each critical
field: drive the intake to that anchor, feed a realistic hiring-manager
non-answer, and assert the agent PUSHES (redirects to a source or reframes)
instead of moving on.

    python -m evals.goal_push          # offline (deterministic pushes)
    python -m evals.goal_push --live   # real model phrasing (rate-limit permitting)
"""
from __future__ import annotations

import argparse
import sys

from lib import agent, llm, schema

# A substantive filler answer per anchor, used to advance the flow to a target.
FILLERS = {
    (0, 0): "Support doubled after the enterprise push and the CS lead is underwater.",
    (0, 1): "Full-time is the way; a promotion isn't viable.",
    (1, 0): "They own the top 20 accounts and cut my escalations in half.",
    (1, 1): "The ones who fail never build champion relationships.",
    (2, 0): "Owned six-figure accounts; exec presence; SaaS fluency.",
    (2, 1): "I'd trade vertical knowledge; instant no if they never owned a number.",
    (3, 0): "25 to 35 lakh fixed, by book size, and yes we can publish it.",
    (3, 1): "SaaS fluency relaxes first; the other two never do.",
    (4, 0): "6 candidates across 2 rounds — ownership then exec presence.",
    (4, 1): "I decide, VP CS vetoes, CEO breaks ties, offer in 3 weeks.",
    (5, 0): "Might shift toward onboarding; and yes, we amend before a 4th rejection.",
    (5, 1): "It's firefighting with no playbook, but huge ownership from day one.",
}

# One scenario per critical field: (layer, anchor) -> the anchor it lives on,
# a realistic non-answer, and the redirect/reframe keyword the push should hit.
SCENARIOS = [
    ("Layer 0 · Business rationale", 0, 0, "business_outcome",
     "Honestly not sure, HR just told me to open it.", "breaks"),
    ("Layer 1 · Success definition", 1, 0, "success_90d",
     "Not sure, it's really too early to say.", "great hire"),
    ("Layer 2 · Candidate profile", 2, 0, "must_haves",
     "I don't know exactly — just someone good.", "compromise"),
    ("Layer 2 · Deal-breaker", 2, 1, "deal_breaker",
     "No idea really, I'm pretty open.", "instant no"),
    ("Layer 3 · Comp band (budget)", 3, 0, "comp_band",
     "I don't know the budget honestly.", "finance"),
    ("Layer 3 · Relaxation order", 3, 1, "relax_order",
     "Not sure, they all matter to me.", "flex"),
    ("Layer 4 · Interview budget/rounds", 4, 0, "interview_budget",
     "I don't know, however many it takes.", "interview"),
    ("Layer 5 · Drift pre-commitment", 5, 0, "drift_precommitment",
     "Not sure, we'll see how it goes.", "update this contract"),
]

_ORDER = [(l, a) for l in range(6) for a in range(2)]


class _Fake:
    def __init__(self):
        self.fields = schema.blank_contract()
        st = agent.new_state(); agent.start(st); self.chat_state = st


def _walk_to(layer: int, anchor: int) -> _Fake:
    """Feed valid fillers for every anchor before the target, landing on it."""
    c = _Fake()
    for la in _ORDER[: _ORDER.index((layer, anchor))]:
        agent.process_turn(c, FILLERS[la])
    return c


def run(live: bool) -> int:
    if not live:
        llm.has_api_key = lambda: False
    mode = "LIVE (" + llm.INTAKE_MODEL + ")" if live else "OFFLINE (deterministic push)"
    print(f"\nGoal-push scenarios — one per critical field · {mode}\n" + "=" * 70)

    passed = total = 0
    for label, layer, anchor, field, non_answer, keyword in SCENARIOS:
        c = _walk_to(layer, anchor)
        res = agent.process_turn(c, non_answer)
        reply = res["assistant"]
        pushed = field in c.chat_state.get("pushed", [])
        redirected = keyword.lower() in reply.lower()
        ok = pushed and redirected and not res["done"]
        total += 1; passed += ok
        print(f"\n[{label}]  ({'✓ push' if ok else '✗'})")
        print(f"  HM:   {non_answer}")
        print(f"  MAYA: {reply}")
        if not ok:
            print(f"  (push_fired={pushed} hit_redirect='{keyword}'={redirected})")

    print("\n" + "=" * 70 + f"\nTOTAL: {passed}/{total} critical fields push correctly on a non-answer")
    return 0 if passed == total else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.live))
