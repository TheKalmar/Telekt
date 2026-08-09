"""Validated HTTP request contracts for the control-plane API."""

from pydantic import BaseModel, Field


class MessageIn(BaseModel):
    """Stakeholder chat payload."""

    content: str = Field(min_length=1, max_length=4000)
    kind: str = "directive"


class ModelModeIn(BaseModel):
    """Per-company model router selection."""

    mode: str


class ModelSettingsIn(BaseModel):
    """Per-company routing mode and connection selection."""

    mode: str
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
    location: str
    base_url: str = Field(default="", max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=500)
    enabled: bool = True
    requires_api_key: bool = True


class ApprovalDecisionIn(BaseModel):
    """Dashboard approval decision with human context."""

    comment: str = Field(default="", max_length=4000)


class HandoffDecisionIn(BaseModel):
    """Stakeholder evidence returned after a manual browser/account step."""

    outcome: str = Field(min_length=1, max_length=4000)


class BrowserSessionIn(BaseModel):
    url: str = Field(max_length=2000)
    allowed_domains: list[str] = Field(min_length=1, max_length=30)


class BrowserActionIn(BaseModel):
    kind: str
    x: float | None = None
    y: float | None = None
    text: str | None = Field(default=None, max_length=4000)
    key: str | None = Field(default=None, max_length=40)
    url: str | None = Field(default=None, max_length=2000)


class EmailSettingsIn(BaseModel):
    """Non-secret per-company approval notification settings."""

    enabled: bool = False
    approvers: list[str] = Field(default_factory=list, max_length=50)
    sender_name: str = Field(default="Digital Company", min_length=1, max_length=100)


class IntegrationSettingsIn(BaseModel):
    """Non-secret platform configuration; credentials stay outside company state."""

    provider: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    store_domain: str = Field(default="", max_length=255)
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    required_secrets: list[str] = Field(default_factory=list, max_length=20)


class CompanyCreateIn(BaseModel):
    """Validated company creation form with safe, editable defaults."""

    name: str = Field(min_length=2, max_length=100)
    company_type: str = Field(default="SaaS", max_length=60)
    concept: str = Field(min_length=3, max_length=1000)
    description: str = Field(default="", max_length=2000)
    goal: str = Field(
        default="Validate the concept, build an MVP, and find a path to profitability",
        max_length=2000,
    )
    budget: float = Field(default=1000, gt=0)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    target_market: str = Field(default="Small and medium businesses", max_length=500)
    customer_type: str = Field(default="B2B", max_length=30)
    time_horizon_days: int = Field(default=30, ge=1, le=3650)
    risk_tolerance: str = "medium"
    autonomy_level: str = "balanced"
    constraints: list[str] = Field(default_factory=lambda: [
        "Approval before external communication",
        "Approval before spending money",
        "Approval before production deployment",
        "Never sign contracts",
    ])
    success_criteria: list[str] = Field(default_factory=lambda: [
        "Evidence-backed problem selection",
        "Functional MVP",
        "Clear human decision point",
    ])
