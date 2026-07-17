# Contract HRMS

A prototype HRMS whose **only functional module is Requisition Management**, implementing
the **Role Contract** system from [`prd-role-contract-v1.1.md`](prd-role-contract-v1.1.md)
and [`intake-instrument-v2.md`](intake-instrument-v2.md). Every other module
(Dashboard, Employees, Performance, Payroll, Leave, Settings) exists for
look-and-feel realism only.

Built for design-partner demos, so it's kept simple and readable.

**Design:** the UI is themed after Deel's product-app look — warm cream canvas
(`#FFFBF4`), Deel blue (`#1032CF`), warm neutral greys, Inter body + a Space
Grotesk display face echoing Deel's Bagoss headings, soft rounded cards and pill
buttons. Tokens are sampled from Deel's own surfaces and centralized in the
Tailwind config + `<style>` block of [`app/templates/base.html`](app/templates/base.html)
(no Deel logo or trademarked assets are used).

> **Stack note:** the original brief specified Next.js. This machine has no
> Node.js toolchain, so — with the user's go-ahead — the same product is built
> in **Python** (FastAPI + Jinja2 + SQLAlchemy/SQLite), with **Tailwind via the
> Play CDN** (no build step) and the **OpenAI SDK pointed at OpenRouter**. The
> data model, flows, and LLM roles match the brief one-to-one; Prisma → SQLAlchemy
> and React → server-rendered templates + vanilla-JS `fetch` are the only swaps.

---

## Quick start

```bash
make setup          # create .venv and install requirements
cp .env.example .env # add your OPENROUTER_API_KEY (optional — see below)
make seed           # 100-person company + 4 requisitions, one per state
make dev            # http://127.0.0.1:8000
```

No Make? The same commands directly:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python -m scripts.seed
uvicorn app.main:app --reload --port 8000
```

### With vs. without an API key

The app is fully runnable **without** a key: intake extraction, the one-pager,
the four renderings, and drift-theme matching all fall back to deterministic
offline logic (the sidebar shows *"Offline mode"*). Add `OPENROUTER_API_KEY` to
`.env` to unlock the real magic — a single hiring-manager answer filling 4–6
contract fields at once via the extraction model.

Two models, both swappable in `.env`, centralized in [`lib/llm.py`](lib/llm.py):

| Env var | Default | Used for |
|---|---|---|
| `INTAKE_MODEL` | `anthropic/claude-sonnet-4.6` | discovery-agent turns, one-pager, renderings |
| `EXTRACT_MODEL` | `anthropic/claude-haiku-4.5` | per-turn structured field extraction (JSON mode) |

---

## The three flows

**1 · Intake** — `New requisition` → pick title / department / hiring manager →
a chat with the discovery agent. The conversation is the 6-layer / 12-anchor
instrument; after every HM reply an extraction call fills the **Role Contract
live in a right-side panel** with per-layer progress and `stated`/`revealed`/`null`
provenance tags. On completion the one-pager generates and status → `CONTRACT_REVIEW`.

**2 · Contract review & rendering** — one-pager + structured fields + provenance +
conflict flags. **Approve** generates all four renderings (candidate posting,
sourcing spec, screening rubric, panel scorecards) and flips status → `ACTIVE`.
The posting follows the PRD rules: comp band + pay logic, 90-day outcomes (not a
duty list), three must-haves only, an honest-constraints section.

**3 · Search enforcement** — on an `ACTIVE` requisition: move candidates through
stages; **every rejection must cite a contract criterion or an uncontracted reason**
(enforced in the API). Three rejections sharing an uncontracted theme (matched
semantically, not by string) raise a **drift alert** → *Amend* runs a short
agent turn, bumps the contract version, regenerates all four renderings, and
records a version-history note. A **slot burn-down** bar projects budget
exhaustion from the pass-rate so far, with the math shown.

---

## Data model

`Requisition` (DRAFT → INTAKE → CONTRACT_REVIEW → ACTIVE → OFFER → CLOSED) ·
`Contract` (versioned; immutable prior versions; provenance-tagged `fields` JSON +
`one_pager`) · `Rendering` (POSTING / SOURCING_SPEC / SCREENING_RUBRIC /
PANEL_SCORECARDS, regenerated per version) · `Candidate` (APPLIED → SCREENED →
INTERVIEW → OFFER | REJECTED, `slot_used`) · `Decision` (advance/reject, with a
`criterion_id` **or** `uncontracted_reason`).

## Seeded requisitions

- **Product Designer** — `DRAFT` (no contract yet)
- **Enterprise CSM** — `INTAKE` (partial contract, conversation mid-flight)
- **Senior Backend Engineer (Payments)** — `ACTIVE` (full v1 contract + 4 renderings +
  8 candidates incl. an active drift alert and a burn-down warning)
- **Lifecycle Marketing Manager** — `CLOSED` (accepted offer)

## Layout

```
app/
  main.py            FastAPI app, markdown filter, template globals
  models.py          SQLAlchemy models + status/stage/type enums
  routers/           dashboard · employees · stubs · requisitions
  templates/         Jinja2 (base shell + pages + partials)
lib/
  llm.py             single LLM entry point (OpenRouter); has_api_key gate
  schema.py          Role Contract schema: layers, anchors, critical fields
  agent.py           discovery agent: flow control, extraction, merge, one-pager
  render.py          one-pager, 4 renderings, drift semantic match (+ offline)
  contractops.py     criteria list, slot burn-down, drift detection
  db.py              engine / session / init
scripts/seed.py      Traqo Technologies (100 people) + 4 requisitions
```

## Non-goals

No auth (single "HR Admin" persona), no email/voice, no real ATS/job-board
integration, no candidate portal, no payments, no edit flows for the stub modules.
