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


def system_prompt() -> str:
    anchors = []
    for layer in LAYERS:
        qs = "\n".join(f"    {i + 1}. {a}" for i, a in enumerate(layer["anchors"]))
        anchors.append(f"  Layer {layer['id']} — {layer['name']}:\n{qs}")
    anchor_block = "\n".join(anchors)
    return f"""# ROLE
You are Maya, a senior technical recruiter. You run a ~10-minute intake \
conversation with a hiring manager (HM) to capture a "Role Contract" — the real \
definition of the role behind the job title.

Your design principle is EXTRACTION, NOT INTERROGATION. There are 12 open \
questions ("anchors"), 2 per layer across 6 layers. A single rich answer often \
fills several parts of the contract at once — so let the HM talk and listen for \
everything, don't march through a checklist.

# PERSONA & TONE
- Name: Maya. Warm, sharp, senior-recruiter energy — a trusted hiring partner, \
never a form-filler.
- Every reply is 1–3 short sentences. Never lecture or monologue.
- React to what the HM actually said before moving on. Sound human, not scripted.

# RESPONSE STYLE RULES
- Ask exactly ONE question per turn. Never stack two questions.
- Open each turn with a one-line acknowledgment of the HM's previous answer, then \
ask the next question.
- Never say the words "layer", "field", "schema", "anchor", or "contract" to the HM.
- Never read the question list aloud and never number the questions.
- Never invent role details, commitments, or facts the HM did not give you.

# THE 12 ANCHORS (your question bank, in order — ask conversationally, do NOT recite)
{anchor_block}

# SCENARIO HANDLING (how to react inside a single turn)
- Vague adjective ("self-starter", "rockstar", "good culture fit"): ground it \
ONCE → "What did that look like the last time you saw it?"
- Conflict in the asks (e.g. a senior wishlist at a mid comp band): surface it \
gently, don't just record it → "That's a senior ask for this band — want to flag that?"
- HM answers several anchors at once: acknowledge it and skip ahead — never \
re-ask something already answered.
- HM asks you a question or drifts off-topic: answer in one line, then steer back \
to the current anchor.
- Thin answer on a critical point: you get ONE targeted follow-up per layer, then \
move on. Completion beats completeness.

# HARD RULES
- Stay in scope: you capture the role; you do NOT negotiate comp or promise outcomes.
- ONE anchor at a time. 1–3 sentences. Always.
- Each turn you will be given the exact next line to deliver (an acknowledgment + \
the next anchor, OR a single follow-up). Follow that instruction precisely and \
keep the conversation's momentum."""


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


def _offline_extract(user_text: str, current: dict) -> dict[str, Any]:
    """Heuristic extraction used only without an API key.

    Maps the answer to the primary field(s) of the anchor that was just asked.
    """
    asked = current.get("_asked")  # injected by caller for offline
    text = user_text.strip()
    if not asked or not text:
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

def _layer_missing_critical(layer_id: int, contract_fields: dict) -> list[str]:
    layer = LAYERS[layer_id]
    fields = contract_fields.get("fields", {})
    return [f for f in layer["fields"] if f in CRITICAL_FIELDS and not schema.is_filled(fields.get(f, {}))]


def _acknowledge_and_ask(transcript: list[dict], next_anchor: str) -> str:
    """One-line acknowledgment of the last answer + the next anchor question."""
    if not llm.has_api_key():
        return "Got it, thanks.\n\n" + next_anchor
    try:
        msgs = [{"role": "system", "content": system_prompt()}] + transcript[-6:] + [
            {
                "role": "system",
                "content": (
                    "Acknowledge the hiring manager's last answer in ONE short "
                    "sentence (optionally surface a conflict or ground a vague "
                    "adjective), then ask EXACTLY this next question verbatim on a "
                    f"new line:\n\n{next_anchor}"
                ),
            }
        ]
        out = llm.chat(msgs, temperature=0.5, max_tokens=180, _label="chat_acknowledge")
        return out if next_anchor.split()[0].lower() in out.lower() or next_anchor[:20] in out else f"{out}\n\n{next_anchor}"
    except Exception as e:
        log.warning("_acknowledge_and_ask: live call failed (%s) — using generic acknowledgment", e)
        return "Got it, thanks.\n\n" + next_anchor


def _recovery_followup(transcript: list[dict], missing: list[str]) -> str:
    labels = ", ".join(FIELD_LABELS[m] for m in missing)
    if not llm.has_api_key():
        return f"Before we move on — one thing I still need: {labels}. Can you say a bit more?"
    try:
        msgs = [{"role": "system", "content": system_prompt()}] + transcript[-6:] + [
            {
                "role": "system",
                "content": (
                    "Ask ONE short, targeted recovery follow-up to capture this "
                    f"still-missing critical information: {labels}. If the HM used a "
                    "vague adjective, ground it instead. One or two sentences."
                ),
            }
        ]
        return llm.chat(msgs, temperature=0.5, max_tokens=140, _label="chat_recovery_followup")
    except Exception as e:
        log.warning("_recovery_followup: live call failed (%s) — using generic follow-up", e)
        return f"Before we move on — one thing I still need: {labels}. Can you say a bit more?"


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
    contract.fields. Returns {assistant, done, touched}."""
    state = contract.chat_state or new_state()
    cfields = contract.fields or schema.blank_contract()

    state.setdefault("messages", []).append({"role": "user", "content": user_text})

    asked = state.get("asked")  # the anchor this answer responds to
    # Extraction (inject asked for offline heuristic).
    if asked:
        anchor_text = LAYERS[asked["layer"]]["anchors"][asked["anchor"]]
    else:
        anchor_text = ""
    cfields["_asked"] = asked  # transient hint for offline extractor
    extracted = extract_fields(anchor_text, user_text, cfields)
    cfields.pop("_asked", None)
    touched = merge_fields(cfields, extracted)

    # Decide the next assistant message.
    assistant, done = _next_message(state, cfields)

    state["messages"].append({"role": "assistant", "content": assistant})
    state["done"] = done
    contract.chat_state = state
    contract.fields = cfields
    return {"assistant": assistant, "done": done, "touched": touched}


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

    # Recovery follow-up: only at the last anchor of a layer, once per layer.
    if ai >= 1:
        missing = _layer_missing_critical(layer_id, cfields)
        if missing and layer_id not in state.get("recovery_used", []):
            state.setdefault("recovery_used", []).append(layer_id)
            state["asked"] = {"layer": layer_id, "anchor": ai}  # same anchor context
            return _recovery_followup(transcript, missing), False

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
