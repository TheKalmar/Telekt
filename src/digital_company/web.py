"""FastAPI control plane for portfolio, runtime controls, chat, and approvals."""

from __future__ import annotations

import os
import html
import re
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from digital_company.registry import CompanyRegistry
from digital_company.store import CompanyStore
from digital_company.email_service import verify_approval_token


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
    allow_cloud_fallback: bool = False


class ApprovalDecisionIn(BaseModel):
    """Dashboard approval decision with human context."""
    comment: str = Field(default="", max_length=4000)


class EmailSettingsIn(BaseModel):
    """Non-secret per-company approval notification settings."""
    enabled: bool = False
    approvers: list[str] = Field(default_factory=list, max_length=50)
    sender_name: str = Field(default="Digital Company", min_length=1, max_length=100)


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
            "settings": {"model_mode": "local", "local_model": "deepseek-company:8b", "allow_cloud_fallback": 0},
            "profile": None,
            "goal": "No company created yet",
            "initial_budget_eur": 0,
            "spent_eur": 0,
            "remaining_budget_eur": 0,
            "completed_tasks": [],
            "recent_tasks": [],
            "operations": {"active_model_run": None, "model": {}, "tasks_by_status": {}, "estimated_spend_eur": 0, "recent_events": []},
            "approvals": [],
            "stakeholder_messages": [],
            "portfolio": {"active_company_id": None, "companies": []},
        }
    data = get_store(company_id).dashboard_data()
    data["portfolio"] = {"active_company_id": company_id, "companies": registry.list()}
    data["operations"]["worker"] = registry.worker_status()
    return data


@app.get("/api/operations")
def operations():
    """Return operational telemetry for the selected company and worker."""
    try:
        data = get_store().operations_data()
    except RuntimeError:
        data = {
            "active_model_run": None,
            "model": {},
            "tasks_by_status": {},
            "estimated_spend_eur": 0,
            "recent_events": [],
        }
    data["worker"] = registry.worker_status()
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
        store.set_model_settings(payload.mode, payload.local_model, payload.allow_cloud_fallback)
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


@app.get("/api/settings/email")
def email_settings():
    return get_store().get_email_settings()


@app.post("/api/settings/email")
def save_email_settings(payload: EmailSettingsIn):
    store = get_store()
    emails = []
    for value in payload.approvers:
        email = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise HTTPException(400, f"Invalid approver email: {value}")
        if email not in emails:
            emails.append(email)
    if payload.enabled and not emails:
        raise HTTPException(400, "At least one approver email is required")
    return store.set_email_settings(payload.enabled, emails, payload.sender_name)


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
def approval(approval_id: str, decision: str, payload: ApprovalDecisionIn):
    """Resolve an exact frozen approval payload for the selected company."""
    store = get_store()
    try:
        if decision == "approve":
            store.approve(approval_id, payload.comment)
        elif decision == "reject":
            store.reject(approval_id, payload.comment)
        else:
            raise HTTPException(400, "Unknown decision")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": decision}


def approval_page(company_id: str, approval_id: str, email: str, expires: int, token: str, message: str = "") -> str:
    """Render a safe confirmation form; GET requests never change approval state."""
    if not verify_approval_token(company_id, approval_id, email, expires, token):
        raise HTTPException(403, "Invalid approval link")
    try:
        approval = next(a for a in registry.store_for(company_id).list_approvals() if a["id"] == approval_id)
    except (KeyError, StopIteration) as exc:
        raise HTTPException(404, "Approval not found") from exc
    proposal = __import__("json").loads(approval["payload_json"])
    disabled = approval["status"] != "pending"
    esc = html.escape
    buttons = "<p>This approval has already been resolved.</p>" if disabled else """
      <textarea name="comment" maxlength="4000" placeholder="Comment or decline reason"></textarea>
      <div class="buttons"><button name="decision" value="approve" class="approve">Approve</button>
      <button name="decision" value="reject" class="reject">Decline</button></div>"""
    return f"""<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>Approval review</title>
<style>body{{margin:0;background:#090d13;color:#eaf1f8;font:15px Arial,sans-serif}}main{{max-width:680px;margin:40px auto;padding:26px;background:#151c26;border:1px solid #2a384b;border-radius:16px}}h1{{font-size:26px}}.meta{{padding:16px;background:#0d1219;border-radius:10px;line-height:1.7;color:#cbd5e1}}textarea{{box-sizing:border-box;width:100%;min-height:120px;margin:20px 0;padding:12px;background:#0b1017;color:white;border:1px solid #34445a;border-radius:9px}}button{{padding:13px 22px;border:0;border-radius:9px;font-weight:bold;cursor:pointer}}.approve{{background:#4ee3a1}}.reject{{background:#ff6b7a;margin-left:8px}}.note{{color:#91a0b4}}.message{{color:#4ee3a1}}</style></head><body><main><div class="note">DIGITAL COMPANY · SECURE HUMAN DECISION</div><h1>{esc(proposal['title'])}</h1><p>{esc(proposal['objective'])}</p><div class="meta"><b>Action:</b> {esc(proposal['action'])}<br><b>Estimated cost:</b> €{proposal['estimated_cost_eur']:.2f}<br><b>Status:</b> {esc(approval['status'])}</div><p class="message">{esc(message)}</p><form method="post"><input type="hidden" name="email" value="{esc(email)}"><input type="hidden" name="expires" value="{expires}"><input type="hidden" name="token" value="{esc(token)}">{buttons}</form><p class="note">Decline requires a reason. Comments become canonical context for the AI company.</p></main></body></html>"""


@app.get("/approval/{company_id}/{approval_id}", response_class=HTMLResponse)
def email_approval_form(company_id: str, approval_id: str, email: str, expires: int, token: str):
    return approval_page(company_id, approval_id, email, expires, token)


@app.post("/approval/{company_id}/{approval_id}", response_class=HTMLResponse)
async def email_approval_decision(company_id: str, approval_id: str, request: Request):
    form = await request.form()
    email = str(form.get("email", ""))
    token = str(form.get("token", ""))
    expires = int(str(form.get("expires", "0")))
    decision = str(form.get("decision", ""))
    comment = str(form.get("comment", "")).strip()
    if not verify_approval_token(company_id, approval_id, email, expires, token):
        raise HTTPException(403, "Invalid approval link")
    store = registry.store_for(company_id)
    try:
        if decision == "approve":
            store.approve(approval_id, comment, email)
            message = "Approved. The exact frozen task has been queued for execution."
        elif decision == "reject":
            store.reject(approval_id, comment, email)
            message = "Declined. The CEO will reconsider using your reason."
        else:
            raise ValueError("Choose Approve or Decline")
    except (RuntimeError, ValueError) as exc:
        return HTMLResponse(approval_page(company_id, approval_id, email, expires, token, str(exc)), status_code=400)
    return approval_page(company_id, approval_id, email, expires, token, message)


def main() -> None:
    port = int(os.getenv("PORT", "8421"))
    host = os.getenv("HOST", "127.0.0.1")
    uvicorn.run("digital_company.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
