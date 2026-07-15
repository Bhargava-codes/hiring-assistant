# Intake Instrument v2 — 10-Minute Conversational Intake

Companion spec to PRD v1.1. Medium: chat. Target: ~10 minutes, 12 open-ended anchor questions (2 per layer).

## System design: extraction, not interrogation

v1 treated the schema as a question list — 26 prescriptive prompts. v2 inverts it: the schema is an extraction target running in the background, and the conversation is 12 open anchors. HMs answer open questions richly; a single free answer typically fills 4–6 fields at once. The loop per layer:

```
anchor question → free answer → agent extracts to schema →
gap check against critical fields → at most ONE recovery follow-up → next layer
```

### Agent rules

1. One anchor at a time; let the HM talk.
2. Adjectives get grounded once: "self-starter" → "what did that look like the last time you saw it?"
3. Conflicts get surfaced, not transcribed (comp vs. asks, stated vs. revealed).
4. Max one recovery follow-up per layer. Missing non-critical fields are left null and flagged in the contract for the recruiter — completion beats completeness.
5. Everything extracted is tagged `stated`; the async ranking exercise (below) supplies `revealed`.

## The 12 anchors

**Layer 0 — Business rationale**
1. What changed in the business that created this role — and what breaks if the seat stays empty for six months?
2. Is a full-time hire the only way to close this gap, or did you weigh contract / promotion / restructuring?

**Layer 1 — Success definition**
3. It's 90 days after joining: what has this person shipped or taken off your plate that makes you say "great hire"?
4. Think of someone who failed in a role like this — here or elsewhere. What did they get wrong?

**Layer 2 — Candidate profile**
5. Describe your ideal candidate freely — then give me the three things that are truly non-negotiable and how you'd verify each in an interview.
6. What would you happily trade away if those three are strong — and what's the one thing that ends the conversation instantly?

**Layer 3 — Market reality**
7. What's the comp band, what decides where someone lands in it, and are you open to publishing it?
8. If candidates with all three non-negotiables at this band turn out to be rare, which one relaxes first?

**Layer 4 — Process contract**
9. How many candidates will you personally interview for this, across how many rounds — and which non-negotiable does each round test?
10. Who can veto, who breaks a tie, and by when do you want the offer out?

**Layer 5 — Drift rules + candidate-facing honesty**
11. What's most likely to change about this role mid-search? And do you agree that if you reject three candidates for a reason we haven't written down, we amend this contract first?
12. What's genuinely hard or unglamorous about this role — and why would a strong candidate pick you over a bigger brand?

## Critical fields (contract cannot render without these)

`business_outcome · success_90d · must_haves[3] + verification · deal_breaker · comp_band · relax_order · interview_budget · rounds→criteria map · drift_precommitment`

If any is still null after its layer's recovery follow-up, the agent closes with a single targeted ask at the end rather than derailing the flow mid-conversation.

## Async step (outside the 10 minutes)

Revealed-preference exercise: after the chat, the HM receives 5–6 anonymized profiles to force-rank at their convenience (~5 min, async). The agent reconciles the ranking against the stated must-haves; divergences are flagged in the contract as `stated vs revealed` conflicts for the sign-off conversation.

## Output

Role Contract v1: structured record (provenance-tagged) + one-page summary — role in one line, business outcome, 90-day success, 3 non-negotiables / trade-offs / deal-breaker, comp band + logic, relaxation order, process (budget, rounds, decision rights, offer date), drift rule, honest constraints. HM and recruiter approve before anything renders.
