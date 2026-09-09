"""Validated HTTP request contracts for the control-plane API."""

from typing import Literal

from pydantic import BaseModel, Field

from digital_company.models import PolicyDocument


class MessageIn(BaseModel):
    """Stakeholder chat payload."""

    content: str = Field(min_length=1, max_length=4000)
    kind: Literal["directive", "question", "memory"] = "directive"


class AgentPlaybookIn(BaseModel):
    """Durable owner-approved operating rules for one agent."""

    rules: list[str] = Field(default_factory=list, max_length=100)
    reference_examples: list[str] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=12_000)


class ModelModeIn(BaseModel):
    """Per-company model router selection."""

    mode: Literal["local", "hybrid", "cloud"]


class ModelSettingsIn(BaseModel):
    """Per-company routing mode and connection selection."""

    mode: Literal["local", "hybrid", "cloud"]
    local_model: str = Field(min_length=1, max_length=200)
    allow_cloud_fallback: bool = False
    cloud_provider: str = "openai"
    cloud_model: str = Field(default="gpt-5.4-mini", min_length=1, max_length=200)
    local_connection_id: str = "local-default"
    cloud_connection_id: str = "cloud-default"


class ModelConnectionIn(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    adapter: str
    location: Literal["local", "cloud"]
    base_url: str = Field(default="", max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=500)
    enabled: bool = True
    requires_api_key: bool = True


class ApprovalDecisionIn(BaseModel):
    """Dashboard approval decision with human context."""

    comment: str = Field(default="", max_length=4000)


class PolicyUpdateIn(PolicyDocument):
    """A complete replacement document persisted as a new immutable version."""


class HandoffDecisionIn(BaseModel):
    """Stakeholder evidence returned after a manual browser/account step."""

    outcome: str = Field(min_length=1, max_length=4000)


class BrowserSessionIn(BaseModel):
    url: str = Field(max_length=2000)
    allowed_domains: list[str] = Field(min_length=1, max_length=30)


class BrowserActionIn(BaseModel):
    kind: Literal[
        "click",
        "type",
        "key",
        "scroll",
        "move",
        "drag",
        "wait",
        "screenshot",
        "navigate",
    ]
    x: float | None = Field(default=None, ge=0, le=1440)
    y: float | None = Field(default=None, ge=0, le=1000)
    text: str | None = Field(default=None, max_length=4000)
    key: str | None = Field(default=None, max_length=40)
    url: str | None = Field(default=None, max_length=2000)
    scroll_x: float | None = Field(default=None, ge=-2000, le=2000)
    scroll_y: float | None = Field(default=None, ge=-2000, le=2000)
    path: list[dict[str, float]] | None = Field(default=None, max_length=100)
    keys: list[str] = Field(default_factory=list, max_length=8)


class EmailSettingsIn(BaseModel):
    """Non-secret per-company approval notification settings."""

    enabled: bool = False
    approvers: list[str] = Field(default_factory=list, max_length=50)
    sender_name: str = Field(default="Digital Company", min_length=1, max_length=100)
    smtp_connection_id: str | None = Field(default=None, max_length=100)
    from_address: str = Field(default="", max_length=254)
    public_base_url: str = Field(default="", max_length=2000)


class EmailTestIn(BaseModel):
    """Explicit recipient for a user-triggered SMTP transport test."""

    recipient: str = Field(min_length=3, max_length=254)


class IntegrationSettingsIn(BaseModel):
    """Non-secret platform configuration; credentials stay outside company state."""

    provider: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    store_domain: str = Field(default="", max_length=255)
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    required_secrets: list[str] = Field(default_factory=list, max_length=20)


class IntegrationConnectionIn(BaseModel):
    """Provider-neutral API/OAuth/webhook transport profile."""

    id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    adapter: str
    provider: str = Field(min_length=1, max_length=100)
    location: Literal["local", "cloud"] = "cloud"
    base_url: str = Field(min_length=1, max_length=2000)
    capabilities: list[str] = Field(min_length=1, max_length=50)
    config: dict = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class IntegrationOperationIn(BaseModel):
    """Frozen connector operation prepared before governed dispatch."""

    execution_key: str = Field(min_length=8, max_length=200)
    connection_id: str = Field(min_length=1, max_length=100)
    capability: str = Field(min_length=2, max_length=80)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=1000)
    request: dict = Field(default_factory=dict)


class CompanyCreateIn(BaseModel):
    """Organization facts only; digital employees are created separately."""

    name: str = Field(min_length=2, max_length=100)
    company_type: str = Field(default="Company", max_length=60)
    industry: str = Field(default="", max_length=200)
    website_url: str = Field(default="", max_length=2000)
    jurisdiction: str = Field(default="", max_length=500)
    concept: str = Field(min_length=3, max_length=12_000)
    description: str = Field(default="", max_length=20_000)
    goal: str = Field(min_length=3, max_length=12_000)
    budget: float | None = Field(default=None, gt=0)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")


class CompanyBriefIn(BaseModel):
    """Organization facts shared by all agents; agent capabilities live elsewhere."""

    name: str = Field(min_length=2, max_length=100)
    company_type: str = Field(default="Company", max_length=60)
    industry: str = Field(default="", max_length=200)
    website_url: str = Field(default="", max_length=2000)
    jurisdiction: str = Field(default="", max_length=500)
    concept: str = Field(min_length=3, max_length=12_000)
    goal: str = Field(min_length=3, max_length=12_000)
    description: str = Field(default="", max_length=20_000)
    reset_pending_work: bool = True


class AgentConfigIn(BaseModel):
    """One independently runnable digital employee."""

    name: str = Field(min_length=2, max_length=100)
    agent_type: str = Field(default="custom", pattern=r"^[a-z][a-z0-9_]{1,40}$")
    role: str = Field(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9 _-]+$")
    purpose: str = Field(min_length=3, max_length=4000)
    instructions: str = Field(default="", max_length=20_000)
    model_connection_id: str = Field(min_length=1, max_length=100)
    autonomy_mode: str = Field(default="governed", pattern="^(supervised|governed|autonomous)$")
    token_limit: int | None = Field(default=None, ge=1, le=1_000_000_000)
    spend_limit_eur: float | None = Field(default=None, ge=0, le=1_000_000_000)
    schedule: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)


class AgentPluginGrantIn(BaseModel):
    """Typed plugin configuration and least-privilege grant for one agent."""

    connection_id: str | None = Field(default=None, max_length=100)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    config: dict = Field(default_factory=dict)
