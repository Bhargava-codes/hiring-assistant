# Contract HRMS — Evals Review Sheet

Machine-generated data: [`results.csv`](results.csv) (39 checks). Regenerate with:

```bash
make evals                                  # offline, deterministic, free
python -m evals.run_evals --csv evals/results.csv   # writes the sheet
make evals-live                             # against the configured model (needs credit/quota)
```

Every stage has a deterministic **offline fallback**, so each check is one of two kinds:

- **structural** — format / rules / plumbing the offline path also satisfies → must pass in **both** modes.
- **live-only** — semantic judgement only a real model can do → **skipped** in offline mode, and it is exactly these that **fail on the offline (or a weak) model**.

---

## Summary — where it breaks

| Stage | Structural | Live-only | Offline result | Reads as |
|---|---|---|---|---|
| 1 · Discovery agent | 2 | 0 | 2/2 ✓ | solid — code-controlled turn-taking |
| **2 · Extraction** | **5** | **8** | 5/5 ✓ (8 skipped) | **fragile — 8 of the 9 breaking checks live here** |
| 3 · One-pager | 8 | 0 | 8/8 ✓ | solid — all sections + gap-marking |
| 4 · Renderings (×4) | 7 | 0 | 7/7 ✓ | solid — structure/rules hold |
| 5 · Drift theme | 3 | 1 | 3/3 ✓ (1 skipped) | mostly solid — 1 semantic check breaks |
| 6 · Ranking | 2 | 0 | 2/2 ✓ | solid |

**Headline:** the pipeline's quality risk is concentrated in **Stage 2 (Extraction)**. Everything else is structurally sound regardless of model. Extraction is where a real model actually earns its keep — and where the offline heuristic (and, by extension, a weak model) visibly fails.

---

## The 9 checks that break (live-only) — and why they matter

Each is written from the hiring manager's chair: what real behaviour it protects, and what "broken" looks like.

### Stage 2 · Extraction (8)

| Case (real HM utterance) | Check | Broken output looks like | Why it matters |
|---|---|---|---|
| "25–35 lakh, depends on book size, yes publish it" | 3 comp values are **distinct** | the whole sentence copied into `comp_band`, `comp_logic` **and** `comp_publishable` | HM said three different things; a good model splits them, a weak one duplicates |
| (same) | `comp_publishable` is a short yes/no | the full sentence instead of "yes" | downstream posting logic keys off a clean yes/no |
| "Not sure yet, we'll figure it out" | does **not** hallucinate a 90-day success | `success_90d = "Not sure yet…"` | a non-answer must stay empty, not become a fake requirement |
| "…owned six-figure accounts (verify with a renewal story), exec presence (verify with a QBR), SaaS fluency…" | captures **exactly three** must-haves | one blob in `ideal_profile`, `must_haves` empty | the three non-negotiables are the spine of the contract |
| (same) | each must-have carries its **verification** | text captured, verification dropped | "how you'd check it" is half the value of a must-have |
| "6 across 2 rounds — round 1 ownership, round 2 presence" | **2 distinct rounds** captured | one round with the whole sentence dumped in `tests` | the rounds→criteria map drives the panel scorecards |
| "…meet about **six** candidates" | parses **"six" → 6** | budget missing or defaulted | interview-budget burn-down needs a real integer |
| "I'd trade domain knowledge. Instant no if they've never owned a number." | trade-off & deal-breaker are **distinct** | same sentence in both fields | these are opposite concepts; conflating them corrupts sourcing |

### Stage 5 · Drift theme (1)

| Case | Check | Broken output | Why it matters |
|---|---|---|---|
| 3 rejections: "couldn't articulate", "struggled to explain", "vague and hard to follow" | detects the theme **despite no shared keyword** | `match: false` (offline keyword-overlap finds nothing) | real drift is worded differently each time; semantic matching is the whole point of the alert |

> In the paced `--live` attempt, these are precisely the rows that came back **FAIL** — because the free tier 429'd and the suite scored the *offline fallback* output. That's the suite working correctly: it fails when the output is weak. What it can't yet do is grade real model output (blocked on API quota).

---

## What passes everywhere (structural, 30/30)

- **Stage 1** — asks the intended next anchor; never leaks "layer/field/schema" jargon.
- **Stage 3** — one-pager carries all 10 sections; a partial contract is marked `_not captured_` rather than fabricated.
- **Stage 4** — posting has comp band + an honest "what's hard" section; sourcing names anti-patterns; rubric has a table + budget; scorecards have rounds + a 1–4 scale.
- **Stage 5** — literal shared themes detected; unrelated reasons don't trigger a false theme; under-3 reasons don't alert.
- **Stage 6** — a ranking that contradicts a stated must-have is flagged with a non-empty note.

---

## How to read the CSV

`results.csv` columns: `stage, case, check, type, result, detail`.
- `type` = `structural` | `live-only`
- `result` (offline run) = `PASS` | `SKIP` (live-only, needs a model) | `FAIL`
- Run `make evals-live` to replace the `SKIP`s with real `PASS`/`FAIL` once a model is reachable.

---

# Behavioural evals (`evals/scenarios.py`, sheet: `scenario_results.csv`)

The suite above is single-turn / output-shape. This second suite drives the
**whole intake** with scripted HM personas and evaluates the three dimensions a
conversational agent actually needs. `make evals-scenarios` (offline) /
`make evals-scenarios-live` (adds the LLM-judge).

### The three personas

| Persona | Voice | Expected goal quality |
|---|---|---|
| **Prepared operator** | cooperative, rich answers to all 12 anchors | renderable contract (all critical fields) |
| **Busy skeptic** | terse, deflecting ("Market rate. Not publishing."), pushes back | reaches done but should **leave critical gaps** — a good model must **not fabricate** values for non-answers |
| **Rambler** | verbose, tangential, but real content | renderable despite the noise |

### Mapping to your three dimensions

| Your dimension | Check(s) | Mode |
|---|---|---|
| **Instruction handling** | no jargon leaked, every turn bounded (<800 chars) | structural (both) |
| | LLM-judge rubric: warm tone · acknowledges specifically · one question · in-scope · no jargon (pass = ≥4/5 incl. in-scope + no-jargon) | **live-only** |
| **Scenario handling (goal y/n)** | reaches `done`; ≤20 turns; no anchor re-asked | structural (both) |
| | goal-QUALITY: rich→renderable, non-answers→gaps (no fabrication) | **live-only** |
| **Tool calls** | **N/A — no LLM function-calling.** The analog is the code-controlled action trigger: `done` firing at the right moment is what launches one-pager + rendering generation. Covered by the `done`/renderable checks. | structural |

### Offline result: 15/15 structural pass · 6 live-only skipped

**Honest caveat baked into the design:** offline extraction fills every field with
the raw answer regardless of substance, so `can_render` is meaningless offline
(all three personas show it True). That's why goal-QUALITY is **live-only** — only
a real model leaves gaps for the skeptic's non-answers. Offline proves the
**control flow** (completes, terminates, no re-ask, no jargon); the **behaviour
quality** (tone, faithfulness, gap-handling) is unverified until `-live` runs.

### The LLM-judge rubric (instruction adherence)

Grades one mid-intake turn on 5 booleans — `warm_professional`,
`acknowledges_specifically`, `one_question`, `in_scope`, `no_jargon` — pass only
if ≥4/5 **and** `in_scope` + `no_jargon` both hold. Judge model = `EXTRACT_MODEL`.

### Still NOT covered (honest backlog)

- Safety / PII / refusal (e.g. "never store OTP/PAN") — no cases yet.
- Prompt-injection / adversarial HM input.
- Consistency/variance (same persona run N times).
- The enforcement loop end-to-end (drift → amend → renderings regenerate).
