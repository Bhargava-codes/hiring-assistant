"""The discovery agent (intake-instrument-v2).

Conversation flow is code-controlled so the per-layer progress in the live panel
is reliable; phrasing and field extraction go through the LLM. Everything has a
deterministic offline fallback (used when no OPENROUTER_API_KEY is set) so the
prototype is always demoable.

State lives in Contract.chat_state:
    {
      "messages": [{"role","content"}, ...],   # transcript (no system msg)
      "layer_index": int,   # 0..5 during layers, 6 = closing sweep, 7 = done
      "anchor_index": int,  # 0 or 1 within the current layer
      "recovery_used": [int],   # layer ids where the one recovery was spent
      "asked": {"layer": int, "anchor": int} | None,  # last anchor asked
      "done": bool,
    }
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from lib import llm, schema
from lib.schema import LAYERS, CRITICAL_FIELDS, FIELD_LABELS

log = logging.getLogger("contract_hrms.agent")


# --- Offline fallback: which fields each (layer, anchor) most maps to ---------
# Used only when there is no API key; the real path uses the extractor below.
_ANCHOR_PRIMARY: dict[tuple[int, int], list[str]] = {
    (0, 0): ["business_outcome"],
    (0, 1): ["alternatives_considered"],
    (1, 0): ["success_90d"],
    (1, 1): ["failure_mode"],
    (2, 0): ["ideal_profile", "must_haves"],
    (2, 1): ["trade_offs", "deal_breaker"],
    (3, 0): ["comp_band", "comp_logic", "comp_publishable"],
    (3, 1): ["relax_order"],
    (4, 0): ["interview_budget", "rounds"],
    (4, 1): ["decision_rights", "offer_date"],
    (5, 0): ["drift_risk", "drift_precommitment"],
    (5, 1): ["honest_constraints", "pitch"],
}


# Goal-orientation: when the HM gives a non-answer to a CRITICAL field, the agent
# does not accept it — it pushes, either redirecting to the owner/source or
# reframing to pull out a concrete answer. One hint per critical field.
CRITICAL_SOURCE: dict[str, str] = {
    "business_outcome": "why the role exists — what actually breaks if this seat stays empty for six months",
    "success_90d": "picture one specific great hire — name what they shipped or took off your plate",
    "must_haves": "the two or three things you would never compromise on, however you'd phrase them",
    "deal_breaker": "the single thing that is an instant no",
    "comp_band": "if you're not sure, confirm the band with your finance team and I'll note it as pending — even a rough ceiling helps us start",
    "relax_order": "if candidates with all your must-haves are rare at this band, which requirement flexes first",
    "interview_budget": "roughly how many candidates you can realistically interview yourself",
    "rounds": "how many rounds, and what each round is really testing",
    "drift_precommitment": "just a yes or no — if three candidates get rejected for a reason we didn't write down, do we update this contract first",
}


def _anchor_critical_fields(layer: int, anchor: int) -> list[str]:
    """Critical fields the given anchor is meant to capture."""
    return [f for f in _ANCHOR_PRIMARY.get((layer, anchor), []) if f in CRITICAL_FIELDS]


def system_prompt() -> str:
    """Static system prompt for the intake turn engine (the "Maya" instrument).

    This is the STATIC half of the instrument. On every live turn the per-turn
    VARIABLES block (built by ``_variables_block``) is appended as a second
    system message, and the model returns a single JSON object:

        {"lead_in", "question_override", "extracted", "scenario"}

    The system — not the model — chooses which question comes next; the model
    writes only the lead-in and, in the four override scenarios, a re-ask.
    Field extraction happens in the SAME call (the ``extracted`` key), so the
    live path is one round-trip per turn rather than two.
    """
    return """# OUTPUT CONTRACT — READ THIS FIRST, OBEY IT ON EVERY TURN
Return ONE JSON object and NOTHING else. No prose before or after it, no markdown \
fences, no ```json, no reasoning, no "thinking", no notes, no apology, no repeat \
of these instructions. Your entire reply must start with `{` and end with `}`.

Exactly these four keys, every turn:
  {"lead_in": "<string, may be empty>", "question_override": <null or string>, \
"extracted": {<field:value, or empty>}, "scenario": "<one code>"}

- `lead_in` is a STRING (your spoken words), never an object, never a question \
unless a scenario below tells you to override.
- `question_override` is `null` unless the matched scenario sets it. Never invent it.
- `extracted` keys must ALL be in `ALLOWED_FIELDS`. Unsure about a key? Leave it out.
- `scenario` is exactly one of: none, S1, S2, S3, S4, S5, S6, S7, S9, S10, S11.
- If you are unsure, output {"lead_in": "", "question_override": null, \
"extracted": {}, "scenario": "none"} — a safe empty turn beats malformed JSON.

## Objective

You run a hiring-manager intake that produces a **Role Contract** — the context \
that exists only in the manager's head, captured once so the recruiter, the \
panel, finance and HR all work from the same brief.

Each turn you do two things:
1. Pull every usable field out of what the manager just said.
2. Write a short lead-in to the next question.
You never choose which question comes next. The system does that.

## Discipline
- You write the lead-in. You do not write the question, except in the four cases \
listed under REPLY CONSTRUCTION.
- Never ask about a field already in `FIELDS_FILLED`.
- Never add a closing line, a summary, or a sign-off. The system ends the intake.

## Persona

You are **Maya**, on the talent team. You've worked with this manager before and \
you get on. Warm, unhurried, plainly on their side. You know this landed on them \
in a week that was already full. You're persistent about what you need and never \
pointed about it. You're a peer here, not an approver.

## Voice
- One or two short sentences. Often one.
- Vary the length. Some lead-ins are three words.
- Plain workplace English. Contractions throughout. No exclamation marks, no \
emoji, no corporate register.

**Core stance:**
1. Never win an exchange. If they push back, agree with the push, then continue.
2. Never make them wrong. No corrections, no "actually", no consequence with them \
inside it.
3. Confusion is your fault. If a question landed badly, "Let me put that better" \
and rephrase.
4. Normalise gaps. "Most managers don't have that yet." Use it whenever they don't \
know something.
5. Say it and stop. Never explain why the sentence you just wrote mattered.
6. Never praise. No "great", "perfect", "that's helpful".

**Never write:** "It's not X, it's Y" · three options where two would do · stated \
empathy · a closing flourish.

## Role handling

`ROLE_TITLE` could be anything. The role changes your WORDING only. It never \
changes the question, the fields, or what you assume.
- Never assume anything the manager hasn't said — not seniority, team, tools, or \
reporting line.
- Never name a technology, market segment, competitor, certification, metric or \
salary figure yourself. If they name one, you may repeat it; you may not add a second.
- Never suggest what a good candidate looks like.
- Never say a role "usually" involves something.
- Unfamiliar title or term? Ask in plain language, once, and only if it's blocking \
the field.

## Reply construction

Your reply is assembled by the system as:

    lead_in + " " + (question_override ?? CURRENT_ASK)

- `lead_in` — your words. May be empty when the ask stands on its own.
- `question_override` — `null` normally. The system appends `CURRENT_ASK` verbatim.

Only these scenarios set an override:
- S1 — your re-ask, offering two concrete options
- S2 — your request for the number
- S4 — your request for their own expectation
- S6 — an easier version of the same question
- S9 — sets `question_override` to `""` (no question at all)

Everything else leaves it `null`. Do not rewrite `CURRENT_ASK` because you'd have \
phrased it differently.

## Scenario handling

Check the manager's last message against these IN ORDER, top to bottom. STOP at \
the FIRST one that matches — pick exactly ONE scenario code, never two, never a \
blend. If none match, the scenario is `none`. Setting a `question_override` is \
allowed ONLY for the scenario you matched (S1, S2, S4, S6, or S9); every other \
scenario leaves `question_override` as `null`.

**S1 — Non-answer.** "Not sure", "you tell me", or dodged. Say briefly why this \
one unblocks the req, then override with a re-ask offering two concrete options. \
Only fires when `IS_CRITICAL` is true. If false, accept and move on.

**S2 — Vague but countable.** A real answer containing a countable claim with no \
number — tickets, headcount, accounts, revenue, time, deals. Override with a \
request for the number. Only if `QUANT_BUDGET` is above zero. Countable things \
only, never judgement or preference.

**S3 — Over-answer.** They covered several fields at once. Extract all of them. \
One clause of acknowledgement. Don't read anything back.

**S4 — Deferred elsewhere.** "Finance owns that", "HR decides". Accept it, then \
override to ask their own expectation, saying plainly that nothing's being \
committed. Never re-ask afterwards. (Check S6 first.)

**S5 — Pasted a JD or document.** Extract silently, then ask for the part specific \
to this company and this stage.

**S6 — Doesn't know.** "No idea", "haven't thought about it". Normalise first, then \
override with an easier form — a relative anchor rather than an absolute one.

**S7 — Won't pre-commit.** "We'll see", "that won't happen". Say nothing's being \
committed and it's changeable any time, then let `CURRENT_ASK` stand.

**S9 — Wants to stop.** Agree immediately. Empty override. No persuading.

**S10 — Asks you to decide.** Never invent a number, company, stack, competitor or \
profile. Offer the STRUCTURE — closest internal peer, last hire at this level, \
budget ceiling — and let `CURRENT_ASK` stand.

**S11 — Off-topic or hostile.** One light redirect. Don't react to tone.

### The line that must not blur
Concede on tone. Hold on the field. If they push back three times on comp, you \
agree three times — and you still ask. Agreeing is not dropping the question.

## Facts you can state
Answer in one or two lines, then continue.
- Seen by: the recruiter, the interview panel, finance for comp, HR ops.
- Not scored, rated or reviewed by anyone. Candidates never see it.
- The band is published only if they opt in.
- Only confirmed fields are stored. Everything's amendable later.
- They decide the hire. This shapes who reaches them, nothing more.
- Roughly ten minutes.
Anything else — say you'll check with the talent team, then continue.

## Output

Return EXACTLY this JSON, and nothing but this JSON. No markdown fences, no \
```json, no text before `{` or after `}`, no reasoning.

{"lead_in": "<your words, or empty string>", "question_override": null, \
"extracted": {}, "scenario": "none"}

- Every key in `extracted` must appear in `ALLOWED_FIELDS`. A key that isn't \
there is a failure — drop the value rather than invent a key.
- Only fields found in THIS message. Never restate `FIELDS_FILLED`.
- Never write a value the manager didn't say. No inference, no rounding, no \
defaults. A range stays a range.
- `must_haves` is a list of up to 3 objects {"text","verification"}; `rounds` is \
a list of objects {"round","tests"}; `interview_budget` is an integer; \
`comp_publishable` is one of yes / no / conditional.
- Nothing extractable -> `extracted` is `{}`.
- `scenario` is one of S1-S7, S9-S11, or `none`.

## Examples

S1 non-answer (business_outcome, IS_CRITICAL true), HM: "We just need another pair \
of hands really." ->
{"lead_in": "The recruiter screens against this one, so it's worth a minute.", \
"question_override": "Is it work that's queued up waiting, or something nobody \
currently owns?", "extracted": {}, "scenario": "S1"}

S2 quantify (business_outcome), HM: "Support volume's gone through the roof since \
the enterprise push and my CS lead is drowning." ->
{"lead_in": "Got it.", "question_override": "Roughly what's volume gone from and \
to?", "extracted": {"business_outcome": "support volume rose sharply after \
enterprise push; CS lead over capacity"}, "scenario": "S2"}

S3 several fields (success_90d), HM: "By then I want the payments roadmap owned end \
to end, not me approving every call. Someone who's shipped in fintech, ideally from \
a smaller company." ->
{"lead_in": "Right, roadmap owned and decisions off your desk.", \
"question_override": null, "extracted": {"success_90d": "owns payments roadmap end \
to end; HM no longer approving each decision", "ideal_profile": "shipped in fintech \
previously; from a smaller company"}, "scenario": "S3"}

S6 doesn't know (comp_band), HM: "Genuinely no idea." ->
{"lead_in": "Most managers don't have it at this point.", "question_override": \
"Roughly above or below the last hire at this level?", "extracted": {}, \
"scenario": "S6"}

S4 concede-tone-hold-field (comp_band, second pushback), HM: "I've told you finance \
owns comp. Why do you keep asking?" ->
{"lead_in": "Fair, and I'll route it to them.", "question_override": "Just your \
rough read on where it lands — goes down as your view, not an approval.", \
"extracted": {}, "scenario": "S4"}"""


# --- Per-turn VARIABLES block (live path) ------------------------------------

DEFAULT_QUANT_BUDGET = 3   # total number-chasing pushes across the whole intake
MAX_REASK = 1              # one push per anchor, then accept and move on


def _variables_block(
    role_title: str,
    company: str,
    current_ask: str,
    is_critical: bool,
    fields_map: dict,
    reask_count: int,
    quant_budget: int,
    last_hm: str,
) -> str:
    filled = [name for name, entry in fields_map.items() if schema.is_filled(entry)]
    return (
        "<<< VARIABLES >>>\n"
        f"ROLE_TITLE:      {role_title}\n"
        f"COMPANY_NAME:    {company}\n\n"
        f"CURRENT_ASK:     {current_ask}\n"
        f"IS_CRITICAL:     {'true' if is_critical else 'false'}\n\n"
        f"ALLOWED_FIELDS:  {', '.join(schema.ALL_FIELDS)}\n"
        f"FIELDS_FILLED:   {json.dumps(filled)}\n"
        f"REASK_COUNT:     {reask_count} of {MAX_REASK}\n"
        f"QUANT_BUDGET:    {quant_budget} remaining\n\n"
        f"LAST_HM_MESSAGE: {last_hm}\n\n"
        "Return only the JSON object now."
    )


def _role_context(contract) -> tuple[str, str]:
    """Best-effort (role_title, company) for the VARIABLES block.

    Works with the ORM Contract (via its requisition) and with the eval stubs
    (which expose neither); everything degrades to a neutral placeholder so the
    prompt never invents a title.
    """
    role, company = "this role", "the company"
    req = getattr(contract, "requisition", None)
    if req is not None and getattr(req, "title", None):
        role = req.title
    elif getattr(contract, "role_title", None):
        role = contract.role_title
    if company_override := os.getenv("COMPANY_NAME"):
        company = company_override
    return role, company


def new_state() -> dict[str, Any]:
    return {
        "messages": [],
        "layer_index": 0,
        "anchor_index": 0,
        "recovery_used": [],
        "asked": None,
        "done": False,
    }


def opening_message() -> str:
    first = LAYERS[0]["anchors"][0]
    return (
        "Hi, I'm Maya from the talent team. I'll ask you a handful of open "
        "questions to build the Role Contract for this hire — answer as fully as "
        "you like and I'll capture the rest. Let's start.\n\n" + first
    )


def start(state: dict[str, Any]) -> dict[str, Any]:
    """Prime a fresh conversation with the opening anchor."""
    msg = opening_message()
    state["messages"].append({"role": "assistant", "content": msg})
    state["asked"] = {"layer": 0, "anchor": 0}
    return state


# --- Extraction --------------------------------------------------------------

def _extraction_messages(anchor_text: str, user_text: str, current: dict) -> list[dict]:
    field_docs = []
    for name in schema.ALL_FIELDS:
        note = FIELD_LABELS[name]
        if name == "must_haves":
            note += ' — a list of up to 3 objects: {"text","verification"}'
        elif name == "rounds":
            note += ' — a list of objects: {"round","tests"}'
        elif name == "interview_budget":
            note += " — an integer (number of candidates the HM will interview)"
        elif name == "comp_publishable":
            note += " — a short string: yes / no / conditional"
        field_docs.append(f'  "{name}": {note}')
    schema_block = "\n".join(field_docs)
    filled = {k: v.get("value") for k, v in current.get("fields", {}).items() if schema.is_filled(v)}
    system = f"""# ROLE
You extract structured Role Contract fields from a hiring manager's (HM) answer \
during an intake call. You output ONLY a JSON object — no prose, no code fences.

# EXTRACTION RULES
1. Include a key ONLY if the answer gives real, specific information for that \
field. Omit every field the answer does not actually address.
2. Extract the SPECIFIC value for each key. NEVER copy the whole answer into \
several keys — split the answer into its distinct pieces, one value per key.
3. Do not invent or infer beyond what was said. Use the HM's own substance.
4. If the answer refines a field already captured, return the fuller version; \
otherwise leave already-captured fields out.
5. NON-ANSWERS ("I don't know", "not sure", "we'll figure it out", "market \
rate", deflections): these carry NO real information. Do NOT extract them as if \
they were the value — omit the key entirely, even though the anchor was about \
that field. A non-answer is not a substitute for a real answer.

# FIELDS (key: meaning)
{schema_block}

# EXAMPLES (note: each key gets its OWN distinct value — never the whole sentence)
Answer: "Comp is 25 to 35 lakh fixed, depends on the size of book they've carried, and yes we can publish it."
JSON: {{"comp_band": "₹25–35L fixed", "comp_logic": "Placement by size of book previously carried", "comp_publishable": "yes"}}

Answer: "I'll interview 6 people across 2 rounds — round 1 tests ownership, round 2 tests exec presence."
JSON: {{"interview_budget": 6, "rounds": [{{"round": "Round 1", "tests": "Account ownership"}}, {{"round": "Round 2", "tests": "Executive presence"}}]}}

Answer: "Honestly I just need someone reliable."
JSON: {{}}   (too vague — nothing specific to extract)

# ALREADY CAPTURED (do not repeat unless the new answer clearly refines them)
{json.dumps(filled, ensure_ascii=False)[:1500]}

# OUTPUT
Return only the JSON object of newly-extracted fields."""
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Anchor question asked:\n{anchor_text}\n\n"
                f"Hiring manager's answer:\n{user_text}\n\n"
                "Return the JSON object now."
            ),
        },
    ]


def extract_fields(anchor_text: str, user_text: str, current: dict) -> dict[str, Any]:
    """Return {field: value} extracted from the latest answer."""
    if llm.has_api_key():
        try:
            data = llm.extract_json(_extraction_messages(anchor_text, user_text, current), _label="extract_fields")
            cleaned = _clean_extraction(data)
            if cleaned:
                return cleaned
            log.warning(
                "extract_fields: live call returned no usable fields (empty/"
                "truncated JSON) — falling back to offline heuristic for this turn"
            )
        except Exception as e:
            log.warning("extract_fields: live call failed (%s) — falling back to offline heuristic", e)
    return _offline_extract(user_text, current)


def _clean_extraction(data: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in (data or {}).items():
        if key not in schema.ALL_FIELDS or val in (None, "", [], {}):
            continue
        if key == "must_haves" and isinstance(val, list):
            cleaned = []
            for item in val[:3]:
                if isinstance(item, dict):
                    cleaned.append(
                        {"text": str(item.get("text", "")).strip(),
                         "verification": str(item.get("verification", "")).strip()}
                    )
                elif isinstance(item, str):
                    cleaned.append({"text": item.strip(), "verification": ""})
            cleaned = [c for c in cleaned if c["text"]]
            if cleaned:
                out[key] = cleaned
        elif key == "rounds" and isinstance(val, list):
            cleaned = []
            for item in val:
                if isinstance(item, dict):
                    cleaned.append(
                        {"round": str(item.get("round", "")).strip(),
                         "tests": str(item.get("tests", "")).strip()}
                    )
                elif isinstance(item, str):
                    cleaned.append({"round": item.strip(), "tests": ""})
            cleaned = [c for c in cleaned if c["round"]]
            if cleaned:
                out[key] = cleaned
        elif key == "interview_budget":
            n = _to_int(val)
            if n is not None:
                out[key] = n
        else:
            out[key] = str(val).strip()
    return out


def _to_int(val: Any) -> int | None:
    if isinstance(val, int):
        return val
    try:
        import re
        m = re.search(r"\d+", str(val))
        return int(m.group()) if m else None
    except (ValueError, TypeError):
        return None


# Explicit non-answers — a real model leaves the field empty for these, so the
# offline heuristic must too (otherwise the goal-push can never fire).
_NON_ANSWER = re.compile(
    r"\b(i (don'?t|do not) know|dunno|no idea|not sure|unsure|can'?t say|"
    r"not decided|haven'?t decided|to be decided|tbd|no clue|you (decide|tell me)|"
    r"whatever|market rate|not really sure)\b", re.I)


def _is_non_answer(text: str) -> bool:
    t = text.strip()
    return bool(_NON_ANSWER.search(t)) and len(t.split()) <= 10


def _offline_extract(user_text: str, current: dict) -> dict[str, Any]:
    """Heuristic extraction used only without an API key.

    Maps the answer to the primary field(s) of the anchor that was just asked —
    unless the answer is an explicit non-answer, in which case nothing is
    extracted (mirroring what a good model does, and letting the goal-push fire).
    """
    asked = current.get("_asked")  # injected by caller for offline
    text = user_text.strip()
    if not asked or not text or _is_non_answer(text):
        return {}
    primaries = _ANCHOR_PRIMARY.get((asked["layer"], asked["anchor"]), [])
    out: dict[str, Any] = {}
    # Without an LLM we can't cleanly separate fields, so we map the raw answer
    # onto every field the anchor targets. Crude, but it lets a keyless demo reach
    # a renderable contract; a real EXTRACT_MODEL call does this properly.
    for field in primaries:
        if field == "must_haves":
            parts = [p.strip(" .;-") for p in text.replace("\n", ";").split(";") if p.strip()][:3]
            out[field] = [{"text": p, "verification": ""} for p in parts] or None
        elif field == "rounds":
            out[field] = [{"round": "Round 1", "tests": text[:200]}]
        elif field == "interview_budget":
            n = _to_int(text)
            out[field] = n if n is not None else 6
        else:
            out[field] = text
    return {k: v for k, v in out.items() if v}


def merge_fields(contract_fields: dict, extracted: dict[str, Any], provenance: str = schema.STATED) -> list[str]:
    """Merge extracted values into the contract. Returns the list of fields touched."""
    fields = contract_fields.setdefault("fields", {})
    touched = []
    for name, value in extracted.items():
        entry = fields.setdefault(name, {"value": None, "provenance": schema.NULL})
        # Don't clobber a filled field with something weaker; refine when fuller.
        if schema.is_filled(entry) and not _is_fuller(name, value, entry["value"]):
            continue
        entry["value"] = value
        entry["provenance"] = provenance
        touched.append(name)
    return touched


def _is_fuller(name: str, new_val: Any, old_val: Any) -> bool:
    if name in schema.STRUCTURED_LIST_FIELDS:
        return isinstance(new_val, list) and len(new_val) >= len(old_val or [])
    return len(str(new_val)) > len(str(old_val or "")) + 8


# --- Conversation progression ------------------------------------------------


def _acknowledge_and_ask(transcript: list[dict], next_anchor: str) -> str:
    """One-line acknowledgment of the last answer + the next anchor question."""
    if not llm.has_api_key():
        return "Got it, thanks.\n\n" + next_anchor
    try:
        msgs = [{"role": "system", "content": system_prompt()}] + transcript[-6:] + [
            {
                "role": "system",
                "content": (
                    "Run your TURN PROCEDURE now on the hiring manager's last "
                    "answer above. If none of rules 1-5 fire, rule 6 applies: "
                    "react in one line to what they said, then ask EXACTLY this "
                    f"next question verbatim:\n\n{next_anchor}"
                ),
            }
        ]
        out = llm.chat(msgs, temperature=0.5, max_tokens=180, _label="chat_acknowledge")
        # Safety net: only force the anchor in if the model asked NO question at
        # all (a real miss). If a ladder rule fired instead (a different, valid
        # single question), trust it — appending the anchor on top would create
        # a second question in the same turn, which the prompt forbids.
        return out if "?" in out else f"{out}\n\n{next_anchor}"
    except Exception as e:
        log.warning("_acknowledge_and_ask: live call failed (%s) — using generic acknowledgment", e)
        return "Got it, thanks.\n\n" + next_anchor


def _goal_push(transcript: list[dict], fields: list[str]) -> str:
    """Goal-oriented push on a critical field the HM did not really answer.

    Does NOT accept the non-answer: states why it matters and either redirects
    the HM to where they can get it (e.g. comp -> finance) or reframes to pull
    out a concrete answer. Never invents the value.
    """
    hints = "; ".join(f"{FIELD_LABELS[f]} — {CRITICAL_SOURCE.get(f, 'give me a specific answer')}" for f in fields)
    if not llm.has_api_key():
        return ("I don't want to leave this one open, it's important for the role: "
                f"{hints}?")
    try:
        msgs = [{"role": "system", "content": system_prompt()}] + transcript[-6:] + [
            {
                "role": "system",
                "content": (
                    "The hiring manager did NOT give a usable answer for a CRITICAL "
                    "item. Do not accept the non-answer and do not move on. Push, "
                    "warmly but firmly: briefly say why it matters, then EITHER "
                    "redirect them to where they can get it OR reframe the question "
                    "to pull out a concrete answer. Never invent the answer yourself. "
                    f"One or two sentences. Item and how to handle it: {hints}"
                ),
            }
        ]
        return llm.chat(msgs, temperature=0.5, max_tokens=150, _label="chat_goal_push")
    except Exception as e:
        log.warning("_goal_push: live call failed (%s) — using deterministic push", e)
        return ("I don't want to leave this one open, it's important for the role: "
                f"{hints}?")


def _closing_sweep(contract_fields: dict) -> str | None:
    missing = schema.missing_critical(contract_fields)
    if not missing:
        return None
    labels = "; ".join(FIELD_LABELS[m] for m in missing)
    return (
        "Almost done. A couple of critical items are still open — can you give me a "
        f"quick line on each: {labels}?"
    )


def process_turn(contract, user_text: str) -> dict[str, Any]:
    """Advance the conversation by one HM turn. Mutates contract.chat_state and
    contract.fields. Returns {assistant, done, touched}.

    Two engines share the code-controlled question flow (the layer/anchor
    pointer, the one-push-per-critical-field budget, the closing sweep):

      - LIVE (API key present): one JSON call per turn on the Maya instrument
        (``system_prompt``) that both extracts fields and writes the lead-in /
        re-ask. See ``_live_turn``.
      - OFFLINE (no key): the deterministic fallback — heuristic extraction plus
        canned acknowledgements/pushes — so the prototype stays demoable and the
        evals have an always-green plumbing path. See ``_offline_turn``.
    """
    state = contract.chat_state or new_state()
    cfields = contract.fields or schema.blank_contract()

    state.setdefault("messages", []).append({"role": "user", "content": user_text})

    if llm.has_api_key():
        result = _live_turn(state, cfields, contract, user_text)
    else:
        result = _offline_turn(state, cfields, user_text)

    state["messages"].append({"role": "assistant", "content": result["assistant"]})
    state["done"] = result["done"]
    contract.chat_state = state
    contract.fields = cfields
    return result


def _offline_turn(state: dict, cfields: dict, user_text: str) -> dict[str, Any]:
    """Deterministic, keyless turn: heuristic extraction + code-driven reply."""
    asked = state.get("asked")  # the anchor this answer responds to
    anchor_text = LAYERS[asked["layer"]]["anchors"][asked["anchor"]] if asked else ""
    cfields["_asked"] = asked  # transient hint for offline extractor
    extracted = extract_fields(anchor_text, user_text, cfields)
    cfields.pop("_asked", None)
    touched = merge_fields(cfields, extracted)
    assistant, done = _next_message(state, cfields)
    return {"assistant": assistant, "done": done, "touched": touched}


# --- Live turn engine (Maya instrument) --------------------------------------


def _join(lead_in: str, question: str) -> str:
    """Assemble the reply the way the instrument specifies: lead_in + ask."""
    return f"{lead_in} {question}".strip() if lead_in else question.strip()


def _anchor_all_filled(layer: int, anchor: int, fields_map: dict) -> bool:
    """True when an over-answer already filled every field this anchor targets,
    so re-asking it would ask about a `FIELDS_FILLED` item."""
    primaries = _ANCHOR_PRIMARY.get((layer, anchor), [])
    return bool(primaries) and all(
        schema.is_filled(fields_map.get(f, {})) for f in primaries
    )


def _step(li: int, ai: int) -> dict[str, Any] | None:
    """One pointer step forward, or None once the 12 anchors are exhausted."""
    if ai == 0:
        return {"kind": "anchor", "text": LAYERS[li]["anchors"][1],
                "li": li, "ai": 1, "asked": {"layer": LAYERS[li]["id"], "anchor": 1}}
    if li + 1 < len(LAYERS):
        return {"kind": "anchor", "text": LAYERS[li + 1]["anchors"][0],
                "li": li + 1, "ai": 0, "asked": {"layer": li + 1, "anchor": 0}}
    return None


def _peek_advance(li: int, ai: int, fields_map: dict) -> dict[str, Any]:
    """The next question the flow would ask, without mutating state.

    Mirrors the pointer advance in ``_next_message`` but skips any anchor whose
    fields an over-answer already filled (so the live path never re-asks a
    `FIELDS_FILLED` item). Returns the target pointer plus a 'kind': a concrete
    'anchor', or 'closing' once the anchors are exhausted.
    """
    step = _step(li, ai)
    while step is not None and _anchor_all_filled(
        step["asked"]["layer"], step["asked"]["anchor"], fields_map
    ):
        step = _step(step["li"], step["ai"])
    if step is None:
        return {"kind": "closing", "text": "", "li": 6, "ai": 0, "asked": None}
    return step


def _apply_advance(state: dict, peek: dict) -> None:
    state["layer_index"] = peek["li"]
    state["anchor_index"] = peek["ai"]
    state["asked"] = peek["asked"]


def _call_turn(
    transcript: list[dict],
    role_title: str,
    company: str,
    current_ask: str,
    is_critical: bool,
    fields_map: dict,
    reask_count: int,
    quant_budget: int,
    user_text: str,
) -> dict[str, Any]:
    """One JSON round-trip on the Maya instrument. Returns the parsed object
    (lead_in / question_override / extracted / scenario), or {} on failure."""
    messages = (
        [{"role": "system", "content": system_prompt()}]
        + transcript[-6:]
        + [{"role": "system", "content": _variables_block(
            role_title, company, current_ask, is_critical,
            fields_map, reask_count, quant_budget, user_text)}]
    )
    return llm.extract_json(
        messages, model=llm.INTAKE_MODEL, temperature=0.4,
        max_tokens=500, _label="chat_turn",
    )


def _turn_reply(data: dict) -> tuple[str, str | None, str, dict]:
    """Pull the four instrument outputs out of a parsed turn object, defensively."""
    lead_in = str(data.get("lead_in") or "").strip()
    override = data.get("question_override")
    override = override.strip() if isinstance(override, str) and override.strip() else None
    scenario = str(data.get("scenario") or "none")
    extracted = _clean_extraction(data.get("extracted") or {})
    return lead_in, override, scenario, extracted


def _live_turn(state: dict, cfields: dict, contract, user_text: str) -> dict[str, Any]:
    role_title, company = _role_context(contract)
    transcript = state.get("messages", [])
    fields_map = cfields.setdefault("fields", {})
    li = state.get("layer_index", 0)
    quant_budget = state.get("quant_budget", DEFAULT_QUANT_BUDGET)

    # --- Closing-sweep / finish phase: this answer responds to the sweep. -----
    if li >= 6:
        data = _call_turn(transcript, role_title, company,
                          _finish_message(cfields), False, fields_map,
                          0, quant_budget, user_text)
        lead_in, _override, _scenario, extracted = _turn_reply(data)
        touched = merge_fields(cfields, extracted)
        return {"assistant": _join(lead_in, _finish_message(cfields)),
                "done": True, "touched": touched}

    # --- Normal phase: pick the advance target, then run the turn. ------------
    asked = state.get("asked")
    peek = _peek_advance(li, state.get("anchor_index", 0), fields_map)
    is_end = peek["kind"] == "closing"
    current_ask = peek["text"]
    if is_end:
        current_ask = _closing_sweep(cfields) or _finish_message(cfields)

    # IS_CRITICAL: does the anchor they just answered still owe a critical field?
    asked_crit = _anchor_critical_fields(asked["layer"], asked["anchor"]) if asked else []
    is_critical = any(not schema.is_filled(fields_map.get(f, {})) for f in asked_crit)

    pushed = state.setdefault("pushed", [])
    reasks = state.setdefault("reasks", {})
    rkey = f'{asked["layer"]}:{asked["anchor"]}' if asked else ""
    reask_count = reasks.get(rkey, 0)

    data = _call_turn(transcript, role_title, company, current_ask, is_critical,
                      fields_map, reask_count, quant_budget, user_text)
    lead_in, override, scenario, extracted = _turn_reply(data)
    touched = merge_fields(cfields, extracted)
    unfilled_after = [f for f in asked_crit if not schema.is_filled(fields_map.get(f, {}))]

    # S9: the manager wants to stop. Agree, end, no further questions.
    if scenario == "S9":
        return {"assistant": lead_in or "No problem — we can leave it there.",
                "done": True, "touched": touched}

    # Stay on the current item for one re-ask when the model overrode the ask
    # in a push scenario, we still have budget, and (for S1) it's critical.
    stay = (
        override is not None
        and scenario in ("S1", "S2", "S4", "S6")
        and reask_count < MAX_REASK
        and (scenario != "S2" or quant_budget > 0)
        and (scenario != "S1" or is_critical)
    )
    if stay:
        reasks[rkey] = reask_count + 1
        if scenario == "S2":
            state["quant_budget"] = max(0, quant_budget - 1)
        for f in unfilled_after:  # goal-push parity: one push per critical field
            if f not in pushed:
                pushed.append(f)
        return {"assistant": _join(lead_in, override), "done": False, "touched": touched}

    # Otherwise advance. Recompute the target now that this turn's extraction is
    # merged, so a field the HM just over-answered isn't asked on the way out.
    peek = _peek_advance(li, state.get("anchor_index", 0), fields_map)
    _apply_advance(state, peek)
    if peek["kind"] == "closing":
        sweep = _closing_sweep(cfields)
        if sweep and not state.get("_sweep_asked"):
            state["_sweep_asked"] = True
            return {"assistant": _join(lead_in, sweep), "done": False, "touched": touched}
        return {"assistant": _join(lead_in, _finish_message(cfields)),
                "done": True, "touched": touched}
    return {"assistant": _join(lead_in, peek["text"]), "done": False, "touched": touched}


def _next_message(state: dict, cfields: dict) -> tuple[str, bool]:
    li = state.get("layer_index", 0)
    ai = state.get("anchor_index", 0)
    transcript = state.get("messages", [])

    # Closing sweep phase.
    if li >= 6:
        sweep = _closing_sweep(cfields)
        if sweep and not state.get("_sweep_asked"):
            state["_sweep_asked"] = True
            return sweep, False
        return _finish_message(cfields), True

    layer_id = LAYERS[li]["id"]

    # GOAL-PUSH: if the anchor the HM just answered was meant to capture a
    # critical field that is STILL empty, and we haven't pushed on it yet, push
    # now instead of advancing — don't let a critical non-answer slide.
    asked = state.get("asked")
    if asked:
        fields_map = cfields.get("fields", {})
        pushed = state.setdefault("pushed", [])
        to_push = [f for f in _anchor_critical_fields(asked["layer"], asked["anchor"])
                   if not schema.is_filled(fields_map.get(f, {})) and f not in pushed]
        if to_push:
            pushed.extend(to_push)  # one push per field, then move on
            # keep `asked` unchanged so the HM's next reply maps to this anchor
            return _goal_push(transcript, to_push), False

    # Advance the pointer.
    if ai == 0:
        state["anchor_index"] = 1
        next_anchor = LAYERS[li]["anchors"][1]
        state["asked"] = {"layer": layer_id, "anchor": 1}
        return _acknowledge_and_ask(transcript, next_anchor), False

    # ai == 1 -> move to next layer.
    if li + 1 < len(LAYERS):
        state["layer_index"] = li + 1
        state["anchor_index"] = 0
        next_anchor = LAYERS[li + 1]["anchors"][0]
        state["asked"] = {"layer": li + 1, "anchor": 0}
        return _acknowledge_and_ask(transcript, next_anchor), False

    # Finished all layers -> closing sweep.
    state["layer_index"] = 6
    sweep = _closing_sweep(cfields)
    if sweep:
        state["_sweep_asked"] = True
        return sweep, False
    return _finish_message(cfields), True


def _finish_message(cfields: dict) -> str:
    if schema.can_render(cfields):
        return (
            "That's everything I need — thank you. I've drafted the Role Contract "
            "from our conversation. Head to the contract page to review the one-pager, "
            "check the provenance tags, and approve it to generate the postings."
        )
    missing = ", ".join(FIELD_LABELS[m] for m in schema.missing_critical(cfields))
    return (
        "Thanks — I've drafted the Role Contract. A few critical items are still open "
        f"({missing}); the recruiter can fill those on the contract page before approval."
    )
