"""Typed contracts exchanged between agents, policy, orchestration, and storage.

These Pydantic models are intentionally the boundary between probabilistic model
output and deterministic application code. Agents SDK validates structured model
responses against these schemas before the orchestrator acts on them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    """Closed set of actions the CEO may propose in the current POC."""
    RESEARCH_MARKET = "research_market"
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
    specialist: Literal["research", "product", "development", "qa", "growth", "ceo"]
    stakeholder_response: str | None = None
    stakeholder_message_ids_considered: list[str] = Field(default_factory=list)


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
