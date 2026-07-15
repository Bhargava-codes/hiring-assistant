"""Hardcoded anonymized profiles for the revealed-preference exercise.

Six archetypes covering common hiring trade-offs. The HM force-ranks them; the
agent reconciles the ranking against the stated must-haves and flags divergences
as `stated vs revealed` conflicts in the contract.
"""
from __future__ import annotations

PROFILES = [
    {
        "id": "p1",
        "title": "Candidate A",
        "blurb": "Deep domain expert. 9 years in the exact problem space, ships slowly but rarely wrong. Not a culture-first pick.",
        "tags": ["deep domain", "senior", "reliable", "slow"],
    },
    {
        "id": "p2",
        "title": "Candidate B",
        "blurb": "Fast generalist. Learns anything in weeks, high energy, shallow on any one area. Strong communicator.",
        "tags": ["fast learner", "generalist", "communication", "junior-ish"],
    },
    {
        "id": "p3",
        "title": "Candidate C",
        "blurb": "Scrappy operator. Thrives in chaos, unglamorous work, no big-brand pedigree. Gets things over the line.",
        "tags": ["scrappy", "ownership", "gritty", "no pedigree"],
    },
    {
        "id": "p4",
        "title": "Candidate D",
        "blurb": "Polished and pedigreed. Top-brand background, excellent presentation, expects a premium and structure.",
        "tags": ["polished", "pedigree", "expensive", "process-oriented"],
    },
    {
        "id": "p5",
        "title": "Candidate E",
        "blurb": "Affordable and hungry. Below-band comp expectation, huge upside, needs mentoring and ramp time.",
        "tags": ["affordable", "high-potential", "raw", "needs ramp"],
    },
    {
        "id": "p6",
        "title": "Candidate F",
        "blurb": "Reliability-first engineer. Obsessive about on-call, postmortems, and edge cases. Less product intuition.",
        "tags": ["reliability", "rigorous", "on-call", "low product sense"],
    },
]

PROFILE_BY_ID = {p["id"]: p for p in PROFILES}
