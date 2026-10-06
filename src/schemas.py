from typing import Literal

from pydantic import BaseModel, Field


class LeadList(BaseModel):
    """Companies identified as potential prospects."""

    companies: list[str] = Field(
        default_factory=list
    )


class CompanyResearch(BaseModel):
    """Structured evidence collected about a company."""

    company_name: str

    industry: str | None = None
    business_model: str | None = None
    geography: str | None = None
    company_size: str | None = None

    company_summary: str

    recent_events: list[str] = Field(
        default_factory=list
    )

    hiring_signals: list[str] = Field(
        default_factory=list
    )

    leadership_signals: list[str] = Field(
        default_factory=list
    )

    operational_signals: list[str] = Field(
        default_factory=list
    )

    buying_signals: list[str] = Field(
        default_factory=list
    )

    key_evidence: list[str] = Field(
        default_factory=list
    )

    evidence_quality: Literal[
        "high",
        "medium",
        "low",
    ] = "low"


class LeadAnalysis(BaseModel):
    """ICP qualification and business-signal analysis."""

    company_name: str

    fit_score: int = Field(
        ge=0,
        le=10,
    )

    icp_score: int = Field(
        ge=0,
        le=3,
    )

    business_need_score: int = Field(
        ge=0,
        le=3,
    )

    recency_score: int = Field(
        ge=0,
        le=2,
    )

    signal_strength_score: int = Field(
        ge=0,
        le=2,
    )

    signal: str
    rationale: str
    signal_category: str

    evidence: list[str] = Field(
        default_factory=list
    )

    confidence: Literal[
        "high",
        "medium",
        "low",
    ] = "low"


class ContactResearch(BaseModel):
    """Verified decision-maker information from public sources."""

    contact_name: str | None = None
    title: str | None = None
    contact_email: str | None = None
    email_source: str | None = None

    rationale: str = ""

    confidence: Literal[
        "high",
        "medium",
        "low",
    ] = "low"


class EmailDraft(BaseModel):
    """Personalized outbound email."""

    subject: str
    body: str
