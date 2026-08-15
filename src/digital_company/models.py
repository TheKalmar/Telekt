"""Typed contracts exchanged between agents, policy, orchestration, and storage.

These Pydantic models are intentionally the boundary between probabilistic model
output and deterministic application code. Agents SDK validates structured model
responses against these schemas before the orchestrator acts on them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator


class ActionType(StrEnum):
    """Closed set of actions the CEO may propose in the current POC."""
    RESEARCH_MARKET = "research_market"
    EVALUATE_PLATFORM = "evaluate_platform"
    REQUEST_PLATFORM_ACCESS = "request_platform_access"
    SOURCE_PRODUCTS = "source_products"
    PUBLISH_CATALOG = "publish_catalog"
    DISCOVER_TOOL = "discover_tool"
    SOURCE_TALENT = "source_talent"
    BROWSER_OPERATE = "browser_operate"
    REQUEST_HUMAN_HANDOFF = "request_human_handoff"
    NEGOTIATE_VENDOR = "negotiate_vendor"
    HIRE_VENDOR = "hire_vendor"
    DEFINE_PRODUCT = "define_product"
    BUILD_MVP = "build_mvp"
    QA_MVP = "qa_mvp"
    PREPARE_OUTREACH = "prepare_outreach"
    RESEARCH_CONTENT = "research_content"
    CREATE_CONTENT_DRAFT = "create_content_draft"
    SAVE_CONTENT_DRAFT = "save_content_draft"
    PUBLISH_CONTENT = "publish_content"
    EXTERNAL_OUTREACH = "external_outreach"
    SPEND_MONEY = "spend_money"
    DEPLOY_PRODUCTION = "deploy_production"
    SIGN_CONTRACT = "sign_contract"
    STOP = "stop"


class TaskProposalDraft(BaseModel):
    """Grammar-safe CEO output before deterministic cross-field validation."""
    action: ActionType
    # Keep grammar bounds deliberately small. Ollama 0.32 rejects very large
    # repetitions (the former 2,000-character URL), while unbounded strings let
    # reasoning models fill the entire output budget with prose.
    title: str = Field(min_length=3, max_length=120)
    objective: str = Field(max_length=200)
    rationale: str = Field(max_length=200)
    expected_evidence: list[Annotated[str, Field(max_length=120)]] = Field(min_length=1, max_length=3)
    estimated_cost_eur: float = Field(default=0, ge=0)
    specialist: Literal["research", "platform", "operations", "product", "development", "qa", "growth", "ceo"]
    stakeholder_response: str | None = Field(default=None, max_length=240)
    stakeholder_message_ids_considered: list[str] = Field(default_factory=list, max_length=20)
    skill_ids: list[str] = Field(default_factory=list)
    # Carries one content topic across research, drafting, review, and publish.
    # It is runtime-owned for the first research task and model-selected from
    # canonical ``work_queue`` state for every subsequent content action.
    work_item_id: str | None = Field(default=None, max_length=100)
    platform_candidate: str | None = Field(default=None, max_length=80)
    required_capabilities: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=5)
    execution_mode: Literal["reasoning", "api", "browser", "manual", "outsourced", "build"] = "reasoning"
    handoff_url: str | None = Field(default=None, max_length=500)
    handoff_allowed_domains: list[Annotated[str, Field(max_length=120)]] = Field(
        default_factory=list, max_length=10
    )
    handoff_instructions: list[Annotated[str, Field(max_length=160)]] = Field(default_factory=list, max_length=5)
    resume_evidence: list[Annotated[str, Field(max_length=160)]] = Field(default_factory=list, max_length=5)


class TaskProposal(TaskProposalDraft):
    """One validated CEO-selected action, including conditional requirements."""

    @model_validator(mode="after")
    def platform_access_is_explicit(self):
        if not 3 <= len(self.title) <= 120:
            raise ValueError("Task title must contain between 3 and 120 characters")
        if len(self.skill_ids) > 5:
            raise ValueError("At most five skills may be assigned to one task")
        if self.platform_candidate and len(self.platform_candidate) > 80:
            raise ValueError("Platform candidate must not exceed 80 characters")
        if self.handoff_url and len(self.handoff_url) > 500:
            raise ValueError("Handoff URL must not exceed 500 characters")
        if any(
            not value or "://" in value or "/" in value or "@" in value
            for value in self.handoff_allowed_domains
        ):
            raise ValueError("Handoff allowed domains must contain hostnames only")
        if self.action == ActionType.REQUEST_PLATFORM_ACCESS:
            if not self.platform_candidate or not self.required_capabilities:
                raise ValueError(
                    "Platform access requests require platform_candidate and required_capabilities"
                )
        if self.action == ActionType.REQUEST_HUMAN_HANDOFF:
            if self.execution_mode != "manual" or not self.handoff_instructions or not self.resume_evidence:
                raise ValueError(
                    "Human handoffs require manual execution_mode, instructions, and resume evidence"
                )
            if self.handoff_url and urlparse(self.handoff_url).scheme not in {"http", "https"}:
                raise ValueError("Handoff URL must use http or https")
        if self.action == ActionType.BROWSER_OPERATE:
            if self.execution_mode != "browser" or not self.handoff_url:
                raise ValueError("Browser operations require browser execution_mode and a starting URL")
            if urlparse(self.handoff_url).scheme != "https":
                raise ValueError("Browser operation URL must use https")
        if self.action in {ActionType.SAVE_CONTENT_DRAFT, ActionType.PUBLISH_CONTENT}:
            if self.execution_mode != "browser" or not self.handoff_url:
                raise ValueError("WordPress content operations require browser execution_mode and a starting URL")
            if urlparse(self.handoff_url).scheme != "https":
                raise ValueError("WordPress content operation URL must use https")
        return self


class FeaturedImageSpec(BaseModel):
    """Reviewable instructions for a content-specific featured image."""

    prompt: str = Field(min_length=20, max_length=2000)
    alt_text: str = Field(min_length=3, max_length=250)
    filename: str = Field(min_length=3, max_length=120)


class ContentPackage(BaseModel):
    """Structured publication payload produced before any CMS side effect."""

    title: str = Field(min_length=5, max_length=200)
    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    focus_keyword: str = Field(min_length=2, max_length=160)
    secondary_keywords: list[Annotated[str, Field(max_length=160)]] = Field(
        default_factory=list, max_length=12,
    )
    seo_title: str = Field(min_length=5, max_length=100)
    meta_description: str = Field(min_length=40, max_length=320)
    excerpt: str = Field(min_length=20, max_length=500)
    categories: list[Annotated[str, Field(max_length=100)]] = Field(min_length=1, max_length=5)
    tags: list[Annotated[str, Field(max_length=100)]] = Field(min_length=2, max_length=15)
    html_content: str = Field(min_length=200, max_length=200_000)
    internal_links: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=15)
    cta: str = Field(min_length=10, max_length=1000)
    jurisdiction_notes: str = Field(default="", max_length=4000)
    source_urls: list[Annotated[str, Field(max_length=1000)]] = Field(min_length=2, max_length=30)
    featured_image: FeaturedImageSpec | None = None

    @model_validator(mode="after")
    def direct_sources_only(self):
        if any(urlparse(value).scheme not in {"http", "https"} for value in self.source_urls):
            raise ValueError("Content sources must be direct HTTP(S) URLs")
        return self


class SpecialistResultDraft(BaseModel):
    """Strict structured output produced by a specialist model.

    Runtime-owned metadata is deliberately excluded. Arbitrary dictionaries are
    not valid strict JSON Schema output and must never be trusted as model-owned
    execution state.
    """
    status: Literal["completed", "failed"]
    summary: str
    evidence: list[str]
    sources: list[str] = Field(default_factory=list)
    artifact_path: str | None = None
    artifact_content: str | None = None
    content_package: ContentPackage | None = None
    recommendation: str


class SpecialistResult(SpecialistResultDraft):
    """Validated result enriched by deterministic QA or integration runtimes."""
    quality_report: dict | None = None
    publication_state: dict | None = None


class PolicyDecision(BaseModel):
    """Deterministic Governor verdict for a proposed action."""
    outcome: Literal["allow", "require_approval", "deny"]
    reason: str
    approval_quorum: int = Field(default=1, ge=1, le=20)
    approval_ttl_hours: int = Field(default=72, ge=1, le=720)


class PolicyDocument(BaseModel):
    """Versioned, per-company authorization rules interpreted by the Governor."""

    deny_actions: list[ActionType] = Field(default_factory=lambda: [ActionType.SIGN_CONTRACT])
    approval_actions: list[ActionType] = Field(default_factory=lambda: [
        ActionType.EXTERNAL_OUTREACH,
        ActionType.SPEND_MONEY,
        ActionType.DEPLOY_PRODUCTION,
        ActionType.REQUEST_PLATFORM_ACCESS,
        ActionType.PUBLISH_CATALOG,
        ActionType.PUBLISH_CONTENT,
        ActionType.BROWSER_OPERATE,
        ActionType.NEGOTIATE_VENDOR,
        ActionType.HIRE_VENDOR,
    ])
    autonomous_spend_limit_eur: float = Field(default=0, ge=0)
    approval_quorum: int = Field(default=1, ge=1, le=20)
    approval_ttl_hours: int = Field(default=72, ge=1, le=720)


class CompanySnapshot(BaseModel):
    """Compact canonical company context sent to the CEO and specialists."""
    goal: str
    initial_budget_eur: float
    spent_eur: float
    remaining_budget_eur: float
    completed_tasks: list[dict]
    pending_approvals: list[dict]
    recent_evidence: list[dict]
    recent_failures: list[dict] = Field(default_factory=list)
    stakeholder_messages: list[dict] = Field(default_factory=list)
    profile: dict = Field(default_factory=dict)
    capabilities: list[dict] = Field(default_factory=list)
    human_handoffs: list[dict] = Field(default_factory=list)
    skills: list[dict] = Field(default_factory=list)
    work_queue: list[dict] = Field(default_factory=list)
