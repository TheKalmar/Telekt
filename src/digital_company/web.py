"""FastAPI control plane for portfolio, runtime controls, chat, and approvals."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from digital_company.registry import CompanyRegistry
from digital_company.store import CompanyStore


ROOT = Path.cwd()
STATE_DIR = Path(os.getenv("COMPANY_DATA_DIR", str(ROOT / ".company"))).expanduser().resolve()
STATIC_DIR = Path(__file__).parent / "static"
load_dotenv(ROOT / ".env.local")

app = FastAPI(title="Digital Company Control Plane")
registry = CompanyRegistry(STATE_DIR)


def get_store(company_id: str | None = None) -> CompanyStore:
    """Open state for an explicit company or the currently selected company."""
    return registry.store_for(company_id)


class MessageIn(BaseModel):
    """Stakeholder chat payload."""
    content: str = Field(min_length=1, max_length=4000)
    kind: str = "directive"


class ModelModeIn(BaseModel):
    """Per-company model router selection."""
    mode: str


class ModelSettingsIn(BaseModel):
    """Per-company routing mode and local Ollama model selection."""
    mode: str
    local_model: str = Field(min_length=1, max_length=200)


class CompanyCreateIn(BaseModel):
    """Validated company creation form with safe, editable defaults."""
    name: str = Field(min_length=2, max_length=100)
    company_type: str = Field(default="SaaS", max_length=60)
    concept: str = Field(min_length=3, max_length=1000)
    description: str = Field(default="", max_length=2000)
    goal: str = Field(default="Validate the concept, build an MVP, and find a path to profitability", max_length=2000)
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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    worker = registry.worker_status()
    return {"status": "ok" if worker["status"] == "online" else "degraded", "worker": worker}


@app.get("/api/dashboard")
def dashboard():
    """Return the active company projection plus portfolio switcher data."""
    try:
        company_id = registry.active_id()
    except RuntimeError:
        return {
            "control": {"state": "idle", "detail": "Create a company to begin"},
            "settings": {"model_mode": "local"},
            "profile": None,
            "goal": "No company created yet",
            "initial_budget_eur": 0,
            "spent_eur": 0,
            "remaining_budget_eur": 0,
            "completed_tasks": [],
            "approvals": [],
            "stakeholder_messages": [],
            "portfolio": {"active_company_id": None, "companies": []},
        }
    data = get_store(company_id).dashboard_data()
    data["portfolio"] = {"active_company_id": company_id, "companies": registry.list()}
    return data


@app.get("/api/companies")
def companies():
    return {"active_company_id": registry.active_id(), "companies": registry.list()}


@app.post("/api/companies")
def create_company(payload: CompanyCreateIn):
    profile = payload.model_dump()
    profile["currency"] = profile["currency"].upper()
    return registry.create(profile)


@app.post("/api/companies/{company_id}/select")
def select_company(company_id: str):
    try:
        return registry.select(company_id)
    except KeyError as exc:
        raise HTTPException(404, "Company not found") from exc


@app.post("/api/control/{action}")
def control(action: str):
    """Start, pause, or stop the selected company's cooperative loop."""
    company_id = registry.active_id()
    store = get_store(company_id)
    if action == "start":
        store.set_control("running", "Queued for autonomous worker")
    elif action in {"pause", "stop"}:
        store.set_control("paused" if action == "pause" else "stopped",
                          "Will halt after current atomic action")
    else:
        raise HTTPException(400, "Unknown control action")
    return store.get_control()


@app.post("/api/messages")
def message(payload: MessageIn):
    """Persist stakeholder input and wake a paused company for directives."""
    company_id = registry.active_id()
    store = get_store(company_id)
    try:
        message_id = store.add_stakeholder_message(payload.content.strip(), payload.kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if store.get_control()["state"] in {"waiting_approval", "paused"} and payload.kind == "directive":
        store.set_control("running", "Stakeholder directive queued for worker")
    return {"id": message_id, "status": "pending"}


@app.post("/api/settings/model-mode")
def model_mode(payload: ModelModeIn):
    """Change routing only while no model call is actively running."""
    store = get_store()
    if store.get_control()["state"] == "running":
        raise HTTPException(409, "Pause or stop the company before switching model mode")
    try:
        store.set_model_mode(payload.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return store.get_settings()


@app.post("/api/settings/models")
def model_settings(payload: ModelSettingsIn):
    """Update model routing for the active company while its loop is stopped."""
    store = get_store()
    if store.get_control()["state"] == "running":
        raise HTTPException(409, "Pause or stop the company before changing model settings")
    try:
        store.set_model_settings(payload.mode, payload.local_model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return store.get_settings()


def ollama_api_url(path: str) -> str:
    """Build a native Ollama API URL from its OpenAI-compatible base URL."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    return base_url.rstrip("/").removesuffix("/v1") + path


@app.get("/api/local-model/models")
def local_models():
    """List models already installed on the configured Ollama server."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(ollama_api_url("/api/tags"), timeout=3) as response:
            payload = json.load(response)
        return {
            "status": "online",
            "models": [model["name"] for model in payload.get("models", []) if model.get("name")],
        }
    except Exception:
        return {"status": "offline", "models": []}


@app.get("/api/local-model/health")
def local_model_health():
    """Check Ollama API reachability; this does not run an inference."""
    import urllib.request
    try:
        with urllib.request.urlopen(ollama_api_url("/api/tags"), timeout=2) as response:
            return {"status": "online", "ollama": response.status == 200}
    except Exception:
        return {"status": "offline", "ollama": False}


@app.post("/api/approvals/{approval_id}/{decision}")
def approval(approval_id: str, decision: str):
    """Resolve an exact frozen approval payload for the selected company."""
    store = get_store()
    try:
        if decision == "approve":
            store.approve(approval_id)
        elif decision == "reject":
            store.reject(approval_id)
        else:
            raise HTTPException(400, "Unknown decision")
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": decision}


def main() -> None:
    port = int(os.getenv("PORT", "8421"))
    host = os.getenv("HOST", "127.0.0.1")
    uvicorn.run("digital_company.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
