"""ORM models for Contract HRMS.

Requisitions is the only functional module; the rest of the HRMS is realism.
Status/stage/type enums are kept as plain strings with named constants so the
SQLite rows stay readable and the values match the spec exactly.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lib.db import Base


# ---- Requisition lifecycle -------------------------------------------------
REQ_DRAFT = "DRAFT"
REQ_INTAKE = "INTAKE"
REQ_CONTRACT_REVIEW = "CONTRACT_REVIEW"
REQ_ACTIVE = "ACTIVE"
REQ_OFFER = "OFFER"
REQ_CLOSED = "CLOSED"
REQ_STATUSES = [
    REQ_DRAFT,
    REQ_INTAKE,
    REQ_CONTRACT_REVIEW,
    REQ_ACTIVE,
    REQ_OFFER,
    REQ_CLOSED,
]

# ---- Rendering types -------------------------------------------------------
R_POSTING = "POSTING"
R_SOURCING_SPEC = "SOURCING_SPEC"
R_SCREENING_RUBRIC = "SCREENING_RUBRIC"
R_PANEL_SCORECARDS = "PANEL_SCORECARDS"
RENDERING_TYPES = [R_POSTING, R_SOURCING_SPEC, R_SCREENING_RUBRIC, R_PANEL_SCORECARDS]
RENDERING_LABELS = {
    R_POSTING: "Candidate posting",
    R_SOURCING_SPEC: "Sourcing spec",
    R_SCREENING_RUBRIC: "Screening rubric",
    R_PANEL_SCORECARDS: "Panel scorecards",
}

# ---- Candidate stages ------------------------------------------------------
C_APPLIED = "APPLIED"
C_SCREENED = "SCREENED"
C_INTERVIEW = "INTERVIEW"
C_OFFER = "OFFER"
C_REJECTED = "REJECTED"
CANDIDATE_STAGES = [C_APPLIED, C_SCREENED, C_INTERVIEW, C_OFFER, C_REJECTED]

# ---- Decision verdicts -----------------------------------------------------
V_ADVANCE = "advance"
V_REJECT = "reject"


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    employee_code: Mapped[str] = mapped_column(String(20), unique=True)
    department: Mapped[str] = mapped_column(String(40))
    designation: Mapped[str] = mapped_column(String(80))
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    location: Mapped[str] = mapped_column(String(40))
    ctc_band: Mapped[str] = mapped_column(String(20))
    join_date: Mapped[date] = mapped_column(Date)

    manager: Mapped["Employee | None"] = relationship(remote_side=[id])


class Requisition(Base):
    __tablename__ = "requisitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    department: Mapped[str] = mapped_column(String(40))
    hiring_manager_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String(24), default=REQ_DRAFT)
    # True once the HM dismisses an active drift alert; reset on amendment.
    drift_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    hiring_manager: Mapped["Employee"] = relationship()
    contracts: Mapped[list["Contract"]] = relationship(
        back_populates="requisition", cascade="all, delete-orphan", order_by="Contract.version"
    )
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="requisition", cascade="all, delete-orphan"
    )

    @property
    def current_contract(self) -> "Contract | None":
        return self.contracts[-1] if self.contracts else None


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    requisition_id: Mapped[int] = mapped_column(ForeignKey("requisitions.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Full schema from schema.py: {"fields": {...}, "conflicts": [...]}.
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    # Intake conversation state while the draft is being built:
    # {"messages": [...], "layer_index": int, "recovery_used": [int], "done": bool}
    chat_state: Mapped[dict] = mapped_column(JSON, default=dict)
    one_pager: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    requisition: Mapped["Requisition"] = relationship(back_populates="contracts")
    renderings: Mapped[list["Rendering"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan"
    )


class Rendering(Base):
    __tablename__ = "renderings"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"))
    type: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    contract: Mapped["Contract"] = relationship(back_populates="renderings")


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    requisition_id: Mapped[int] = mapped_column(ForeignKey("requisitions.id"))
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(40))
    stage: Mapped[str] = mapped_column(String(20), default=C_APPLIED)
    slot_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    requisition: Mapped["Requisition"] = relationship(back_populates="candidates")
    decisions: Mapped[list["Decision"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="Decision.created_at"
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    verdict: Mapped[str] = mapped_column(String(12))  # advance | reject
    # References a contract must-have/deal-breaker id, or NULL for uncontracted.
    criterion_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    uncontracted_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    candidate: Mapped["Candidate"] = relationship(back_populates="decisions")
