"""Typed contracts exchanged between agents, policy, orchestration, and storage.

These Pydantic models are intentionally the boundary between probabilistic model
output and deterministic application code. Agents SDK validates structured model
responses against these schemas before the orchestrator acts on them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
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
    EXTERNAL_OUTREACH = "external_outreach"
    SPEND_MONEY = "spend_money"
    DEPLOY_PRODUCTION = "deploy_production"
    SIGN_CONTRACT = "sign_contract"
    STOP = "stop"


class TaskProposal(BaseModel):
    """One CEO-selected next action, including cost and required evidence."""
    action: ActionType
    title: str = Field(min_length=3, max_length=120)
    objective: str
    rationale: str
    expected_evidence: list[str] = Field(min_length=1)
    estimated_cost_eur: float = Field(default=0, ge=0)
    specialist: Literal["research", "platform", "operations", "product", "development", "qa", "growth", "ceo"]
    stakeholder_response: str | None = None
    stakeholder_message_ids_considered: list[str] = Field(default_factory=list)
    platform_candidate: str | None = Field(default=None, max_length=80)
    required_capabilities: list[str] = Field(default_factory=list)
    execution_mode: Literal["reasoning", "api", "browser", "manual", "outsourced", "build"] = "reasoning"
    handoff_url: str | None = Field(default=None, max_length=2000)
    handoff_instructions: list[str] = Field(default_factory=list)
    resume_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def platform_access_is_explicit(self):
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
        return self


class SpecialistResult(BaseModel):
    """Validated output returned by a specialist agent."""
    status: Literal["completed", "failed"]
    summary: str
    evidence: list[str]
    artifact_path: str | None = None
    artifact_content: str | None = None
    recommendation: str


class PolicyDecision(BaseModel):
    """Deterministic Governor verdict for a proposed action."""
    outcome: Literal["allow", "require_approval", "deny"]
    reason: str


class CompanySnapshot(BaseModel):
    """Compact canonical company context sent to the CEO and specialists."""
    goal: str
    initial_budget_eur: float
    spent_eur: float
    remaining_budget_eur: float
    completed_tasks: list[dict]
    pending_approvals: list[dict]
    recent_evidence: list[dict]
    stakeholder_messages: list[dict] = Field(default_factory=list)
    profile: dict = Field(default_factory=dict)
    capabilities: list[dict] = Field(default_factory=list)
    human_handoffs: list[dict] = Field(default_factory=list)
