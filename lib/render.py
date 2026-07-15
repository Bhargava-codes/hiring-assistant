"""Generation from the Role Contract: one-pager, the four renderings, drift match.

Each generator receives the full contract JSON. The candidate posting follows the
PRD's rules: comp band + pay logic, 90-day outcomes (not a duty list), 3 must-haves
only, an honest constraints section. All four renderings regenerate on every
approved contract version.

Offline fallbacks produce deterministic markdown so the flow works without a key.
"""
from __future__ import annotations

import json
from typing import Any

from lib import llm, schema
from lib.schema import FIELD_LABELS


def _val(cfields: dict, name: str) -> Any:
    return cfields.get("fields", {}).get(name, {}).get("value")


def contract_summary_json(cfields: dict) -> str:
    """Compact JSON of filled fields for prompting the generators."""
    out = {}
    for name, entry in cfields.get("fields", {}).items():
        if schema.is_filled(entry):
            out[name] = entry["value"]
    return json.dumps(out, ensure_ascii=False, indent=2)


# --- One-pager ---------------------------------------------------------------

def generate_one_pager(cfields: dict, title: str, department: str) -> str:
    if llm.has_api_key():
        try:
            return _llm_one_pager(cfields, title, department)
        except Exception:
            pass
    return _offline_one_pager(cfields, title, department)


def _llm_one_pager(cfields: dict, title: str, department: str) -> str:
    msgs = [
        {
            "role": "system",
            "content": (
                "You write a one-page Role Contract summary in clean markdown. "
                "Sections in order: role in one line; Business outcome; 90-day "
                "success; Three non-negotiables (with how each is verified); "
                "Trade-offs; Deal-breaker; Comp band + placement logic + relaxation "
                "order; Process (interview budget, rounds->criteria, decision "
                "rights, offer date); Drift rule; Honest constraints. Be concise and "
                "faithful to the data — never invent facts. Leave a short '_not "
                "captured_' note for any empty field."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Role: {title} ({department}).\n\n"
                f"Contract data (JSON):\n{contract_summary_json(cfields)}"
            ),
        },
    ]
    return llm.chat(msgs, temperature=0.4, max_tokens=1400)


def _fmt(v: Any, empty: str = "_not captured_") -> str:
    if v in (None, "", [], {}):
        return empty
    if isinstance(v, list):
        return v  # caller handles
    return str(v)


def _offline_one_pager(cfields: dict, title: str, department: str) -> str:
    g = lambda n: _val(cfields, n)  # noqa: E731
    lines = [f"# Role Contract — {title}", f"*{department} · draft*", ""]
    lines += ["**Role in one line:** " + (str(g("business_outcome"))[:140] if g("business_outcome") else "_not captured_"), ""]
    lines += ["## Business outcome", _fmt(g("business_outcome")), ""]
    lines += ["## 90-day success", _fmt(g("success_90d")), ""]
    lines += ["## Three non-negotiables"]
    mh = g("must_haves")
    if isinstance(mh, list) and mh:
        for i, m in enumerate(mh, 1):
            lines.append(f"{i}. **{m.get('text','')}** — verify: {m.get('verification') or '_tbd_'}")
    else:
        lines.append("_not captured_")
    lines += ["", "## Trade-offs", _fmt(g("trade_offs")), ""]
    lines += ["## Deal-breaker", _fmt(g("deal_breaker")), ""]
    lines += ["## Comp", f"**Band:** {_fmt(g('comp_band'))}  ", f"**Placement logic:** {_fmt(g('comp_logic'))}  ", f"**Publishable:** {_fmt(g('comp_publishable'))}  ", f"**Relaxation order:** {_fmt(g('relax_order'))}", ""]
    lines += ["## Process", f"**Interview budget:** {_fmt(g('interview_budget'))} slots  "]
    rounds = g("rounds")
    if isinstance(rounds, list) and rounds:
        for r in rounds:
            lines.append(f"- {r.get('round','')}: tests {r.get('tests') or '_tbd_'}")
    lines += [f"**Decision rights:** {_fmt(g('decision_rights'))}  ", f"**Target offer date:** {_fmt(g('offer_date'))}", ""]
    lines += ["## Drift rule", _fmt(g("drift_precommitment")), f"_Likely drift:_ {_fmt(g('drift_risk'))}", ""]
    lines += ["## Honest constraints", _fmt(g("honest_constraints")), f"_Why us:_ {_fmt(g('pitch'))}", ""]
    return "\n".join(lines)


# --- Renderings --------------------------------------------------------------

_RENDERING_BRIEFS = {
    "POSTING": (
        "Write the CANDIDATE-FACING JOB POSTING in markdown. Rules (from the PRD): "
        "lead with the mission and the 90-day outcomes (NOT a duty list); include the "
        "comp band AND the logic for where someone lands in it; list AT MOST THREE "
        "must-haves; include an honest 'What's hard about this role' constraints "
        "section. Warm, specific, no corporate filler."
    ),
    "SOURCING_SPEC": (
        "Write the internal SOURCING SPEC in markdown for the recruiter: target "
        "profiles / titles / companies to search, boolean-style keywords, must-haves "
        "vs. nice-to-haves, the relaxation order to widen the funnel, and explicit "
        "anti-patterns (the failure mode / deal-breaker)."
    ),
    "SCREENING_RUBRIC": (
        "Write the SCREENING RUBRIC in markdown: for each of the three must-haves, a "
        "pass/fail signal and a probing question; a scoring table; and a note tying "
        "screen depth to the interview budget (be selective when budget is tight)."
    ),
    "PANEL_SCORECARDS": (
        "Write PANEL SCORECARDS in markdown: one scorecard per interview round mapped "
        "to the round->criteria plan, each with the criterion tested, 2–3 evaluation "
        "questions, and a 1–4 rating scale with anchors. Include decision rights and "
        "the target offer date at the end."
    ),
}


def generate_rendering(rtype: str, cfields: dict, title: str, department: str) -> str:
    if llm.has_api_key():
        try:
            return _llm_rendering(rtype, cfields, title, department)
        except Exception:
            pass
    return _offline_rendering(rtype, cfields, title, department)


def _llm_rendering(rtype: str, cfields: dict, title: str, department: str) -> str:
    msgs = [
        {"role": "system", "content": _RENDERING_BRIEFS[rtype]},
        {
            "role": "user",
            "content": (
                f"Role: {title} ({department}).\n\n"
                f"Full Role Contract (JSON):\n{contract_summary_json(cfields)}\n\n"
                "Generate the document now. Do not invent facts not in the contract; "
                "mark genuine gaps as _to be confirmed_."
            ),
        },
    ]
    return llm.chat(msgs, temperature=0.5, max_tokens=1600)


def _offline_rendering(rtype: str, cfields: dict, title: str, department: str) -> str:
    g = lambda n: _val(cfields, n)  # noqa: E731
    mh = g("must_haves") or []
    mh_lines = "\n".join(
        f"{i}. **{m.get('text','')}**" + (f" — verify: {m.get('verification')}" if m.get("verification") else "")
        for i, m in enumerate(mh, 1)
    ) or "_to be confirmed_"
    if rtype == "POSTING":
        return (
            f"# {title}\n\n**{department} · Traqo Technologies · Bangalore**\n\n"
            f"## Why this role exists\n{_fmt(g('business_outcome'))}\n\n"
            f"## What you'll have shipped in 90 days\n{_fmt(g('success_90d'))}\n\n"
            f"## What we need (top 3)\n{mh_lines}\n\n"
            f"## Compensation\n**Band:** {_fmt(g('comp_band'))}. Placement: {_fmt(g('comp_logic'))}\n\n"
            f"## What's genuinely hard about this role\n{_fmt(g('honest_constraints'))}\n\n"
            f"_Why us over a bigger brand:_ {_fmt(g('pitch'))}\n"
        )
    if rtype == "SOURCING_SPEC":
        return (
            f"# Sourcing spec — {title}\n\n## Target profile\n{_fmt(g('ideal_profile'))}\n\n"
            f"## Must-haves\n{mh_lines}\n\n## Trade-offs (nice-to-have)\n{_fmt(g('trade_offs'))}\n\n"
            f"## Widen the funnel in this order\n{_fmt(g('relax_order'))}\n\n"
            f"## Anti-patterns\n- Deal-breaker: {_fmt(g('deal_breaker'))}\n- Failure mode: {_fmt(g('failure_mode'))}\n"
        )
    if rtype == "SCREENING_RUBRIC":
        rows = "\n".join(f"| {m.get('text','')} | {m.get('verification') or '_tbd_'} | ⬜ pass / ⬜ fail |" for m in mh) or "| _tbd_ | | |"
        return (
            f"# Screening rubric — {title}\n\n| Must-have | Signal / probe | Verdict |\n|---|---|---|\n{rows}\n\n"
            f"**Interview budget:** {_fmt(g('interview_budget'))} slots — screen selectively; only advance clear passes.\n"
        )
    # PANEL_SCORECARDS
    rounds = g("rounds") or []
    cards = "\n\n".join(
        f"### {r.get('round','Round')}\n- **Tests:** {r.get('tests') or '_tbd_'}\n- Questions: _to be confirmed_\n- Rating: 1 (no) – 4 (strong yes)"
        for r in rounds
    ) or "### Round 1\n- Tests: _to be confirmed_\n- Rating: 1–4"
    return (
        f"# Panel scorecards — {title}\n\n{cards}\n\n"
        f"**Decision rights:** {_fmt(g('decision_rights'))}\n**Target offer date:** {_fmt(g('offer_date'))}\n"
    )


# --- Drift semantic matching -------------------------------------------------

def reasons_share_theme(reasons: list[str]) -> dict[str, Any]:
    """Given >=3 uncontracted rejection reasons, decide if they share a theme
    (semantic, not string equality). Returns {"match": bool, "theme": str}."""
    reasons = [r for r in reasons if r and r.strip()]
    if len(reasons) < 3:
        return {"match": False, "theme": ""}
    if llm.has_api_key():
        try:
            data = llm.extract_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "You judge whether hiring rejection reasons share a single "
                            "underlying theme (semantically, ignoring wording). Return "
                            'JSON: {"match": true|false, "theme": "<short phrase>"}. '
                            "match=true only if at least 3 of them point at the same "
                            "missing quality or requirement."
                        ),
                    },
                    {"role": "user", "content": "Reasons:\n- " + "\n- ".join(reasons)},
                ]
            )
            return {"match": bool(data.get("match")), "theme": str(data.get("theme", "")).strip()}
        except Exception:
            pass
    return _offline_theme(reasons)


def reconcile_ranking(cfields: dict, ranked: list[dict]) -> list[dict[str, Any]]:
    """Compare a force-ranking of anonymized profiles against the stated
    must-haves. Returns a list of stated-vs-revealed conflict flags.

    ``ranked`` is the ordered list of profile dicts (top choice first).
    """
    must_haves = [m.get("text", "") for m in (_val(cfields, "must_haves") or []) if isinstance(m, dict)]
    deal_breaker = _val(cfields, "deal_breaker") or ""
    if llm.has_api_key():
        try:
            data = llm.extract_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "A hiring manager force-ranked anonymized candidate profiles. "
                            "Compare what the ranking REVEALS they actually prioritize "
                            "against what they STATED as must-haves. Return JSON: "
                            '{"conflicts": [{"note": "<one sentence>"}]}. Only include a '
                            "conflict when the ranking meaningfully diverges from the "
                            "stated must-haves or deal-breaker (e.g. they rank a profile "
                            "weak on a stated must-have at the top, or reveal a priority "
                            "not written down). Empty list if they align."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Stated must-haves: " + "; ".join(must_haves) + "\n"
                            "Deal-breaker: " + deal_breaker + "\n\n"
                            "Ranking (top first):\n"
                            + "\n".join(
                                f"{i+1}. {p['title']} — {p['blurb']} [tags: {', '.join(p['tags'])}]"
                                for i, p in enumerate(ranked)
                            )
                        ),
                    },
                ]
            )
            out = []
            for c in data.get("conflicts", [])[:4]:
                note = (c.get("note") if isinstance(c, dict) else str(c)) or ""
                if note.strip():
                    out.append({"type": "stated_vs_revealed", "field": "must_haves", "note": note.strip()})
            return out
        except Exception:
            pass
    return _offline_reconcile(must_haves, ranked)


def _offline_reconcile(must_haves: list[str], ranked: list[dict]) -> list[dict[str, Any]]:
    """Heuristic reconciliation: does the top pick reflect the stated must-haves?"""
    import re

    if len(ranked) < 2:
        return []
    top_tags = set()
    for p in ranked[:2]:
        top_tags.update(t.lower() for t in p["tags"])
        top_tags.update(re.findall(r"[a-z]{4,}", p["blurb"].lower()))
    conflicts: list[dict[str, Any]] = []
    # A stated must-have with no echo in the top-2 profiles is a divergence.
    for mh in must_haves:
        words = set(re.findall(r"[a-z]{4,}", mh.lower()))
        if words and not (words & top_tags):
            conflicts.append({
                "type": "stated_vs_revealed", "field": "must_haves",
                "note": f'You ranked profiles weak on your stated must-have "{mh[:60]}" at the top — worth confirming it is truly non-negotiable.',
            })
    # Surface a strong revealed priority from the top pick.
    if ranked:
        top = ranked[0]
        conflicts.append({
            "type": "stated_vs_revealed", "field": "must_haves",
            "note": f'Your top pick ({top["title"]}) signals a revealed preference for: {", ".join(top["tags"][:2])}.',
        })
    return conflicts[:3]


def _offline_theme(reasons: list[str]) -> dict[str, Any]:
    """Keyword-overlap fallback: a shared non-trivial word across >=3 reasons."""
    import re
    from collections import Counter

    stop = {"the", "a", "an", "was", "were", "not", "no", "too", "and", "of", "to",
            "in", "for", "with", "is", "are", "had", "has", "them", "they", "this",
            "that", "enough", "very", "on", "at", "it", "candidate", "he", "she"}
    counts: Counter = Counter()
    per_reason_words = []
    for r in reasons:
        words = {w for w in re.findall(r"[a-z]{4,}", r.lower()) if w not in stop}
        per_reason_words.append(words)
        counts.update(words)
    for word, c in counts.most_common():
        if c >= 3:
            return {"match": True, "theme": word}
    return {"match": False, "theme": ""}
