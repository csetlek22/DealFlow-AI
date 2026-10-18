from typing import Literal

from pydantic import BaseModel, Field


class CompanyCandidate(BaseModel):
    """A company prospect discovered via market mapping."""

    company_name: str
    website: str | None = None
    evidence: str = ""
    source_url: str = ""

    country: str | None = None
    industry: str | None = None
    commodity: str | None = None
    activity: str | None = None
    company_size: str | None = None
    priority: str | None = None
    confidence: str | None = None
    linkedin_url: str | None = None
    discovery_source: str | None = None


class CompanyCandidateList(BaseModel):
    """Candidate companies enumerated by the discovery LLM."""

    candidates: list[CompanyCandidate] = Field(
        default_factory=list
    )


class CompanyVerification(BaseModel):
    """Search-backed verification of a single enumerated candidate."""

    company_name: str
    is_relevant: bool = True
    website: str | None = None
    country: str | None = None
    industry: str | None = None
    activity: str | None = None
    company_size: str | None = None
    linkedin_url: str | None = None
    confidence: Literal[
        "confirmed",
        "likely",
        "potential",
        "unverified",
    ] = "unverified"
    evidence: str = ""


class CompanyVerificationList(BaseModel):
    """Batched verification results."""

    verifications: list[CompanyVerification] = Field(
        default_factory=list
    )


class CompanyResearch(BaseModel):
    """Structured evidence collected about a company."""

    company_name: str
    website: str | None = None

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
    provider: str | None = None
    evidence: str = ""

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
