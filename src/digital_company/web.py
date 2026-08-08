"""FastAPI control plane for portfolio, runtime controls, chat, and approvals."""

from __future__ import annotations

import os
import html
import re
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from digital_company.registry import CompanyRegistry
from digital_company.store import CompanyStore
from digital_company.email_service import verify_approval_token
from digital_company.workspace import WorkspaceRuntime


ROOT = Path.cwd()
STATE_DIR = Path(os.getenv("COMPANY_DATA_DIR", str(ROOT / ".company"))).expanduser().resolve()
STATIC_DIR = Path(__file__).parent / "static"
load_dotenv(ROOT / ".env.local")

app = FastAPI(title="Digital Company Control Plane")
registry = CompanyRegistry(STATE_DIR)
_preflight_cache: dict[str, tuple[float, tuple, dict]] = {}


def get_store(company_id: str | None = None) -> CompanyStore:
    """Open state for an explicit company or the currently selected company."""
    return registry.store_for(company_id)


def runtime_preflight(store: CompanyStore, force: bool = False) -> dict:
    """Evaluate whether the selected company can safely start its configured loop."""
    settings = store.get_settings()
    mode = settings["model_mode"]
    cache_key = str(store.path)
    signature = (settings["updated_at"], bool(os.getenv("OPENAI_API_KEY")))
    cached = _preflight_cache.get(cache_key)
    if not force and cached and cached[1] == signature and time.monotonic() - cached[0] < 5:
        return cached[2]
    checks: list[dict] = []

    worker = registry.worker_status()
    checks.append({
        "id": "worker", "status": "pass" if worker["status"] == "online" else "block",
        "detail": "Background worker is online" if worker["status"] == "online"
                  else "Background worker is offline; restart the worker container",
    })

    installed: list[str] = []
    ollama_online = False
    if mode in {"local", "hybrid"}:
        model_inventory = local_models()
        installed = model_inventory["models"]
        ollama_online = model_inventory["status"] == "online"
        local_ready = ollama_online and settings["local_model"] in installed
        detail = (f"Local model {settings['local_model']} is installed" if local_ready else
                  "Ollama is offline; start Ollama and refresh model settings" if not ollama_online else
                  f"Local model {settings['local_model']} is not installed; select one of: "
                  + (", ".join(installed) or "none"))
        checks.append({"id": "local_model", "status": "pass" if local_ready else "block", "detail": detail})
    else:
        checks.append({"id": "local_model", "status": "skip", "detail": "Cloud mode does not require Ollama"})

    cloud_required = mode in {"cloud", "hybrid"}
    cloud_present = bool(os.getenv("OPENAI_API_KEY"))
    checks.append({
        "id": "openai", "status": "pass" if cloud_present else "block" if cloud_required else "warn",
        "detail": "OpenAI key is configured" if cloud_present else
                  "OPENAI_API_KEY is required for this routing mode" if cloud_required else
                  "OpenAI key is absent; local work can run but Computer Use is unavailable",
    })

    browser = browser_health()
    browser_ready = browser["status"] == "ok"
    checks.append({
        "id": "browser", "status": "pass" if browser_ready else "warn",
        "detail": "Isolated browser runtime is online" if browser_ready
                  else "Browser runtime is offline; reasoning work can run but Browser Missions cannot",
    })
    blockers = [check for check in checks if check["status"] == "block"]
    result = {"ready": not blockers, "mode": mode, "checks": checks,
              "blockers": [check["detail"] for check in blockers]}
    _preflight_cache[cache_key] = (time.monotonic(), signature, result)
    return result


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
    """Non-secret platform configuration; credentials stay in environment variables."""
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
            "artifacts": [],
            "capabilities": [],
            "operations": {"active_model_run": None, "model": {}, "tasks_by_status": {}, "estimated_spend_eur": 0, "recent_events": []},
            "approvals": [],
            "human_handoffs": [],
            "stakeholder_messages": [],
            "portfolio": {"active_company_id": None, "companies": []},
        }
    data = get_store(company_id).dashboard_data()
    data["portfolio"] = {"active_company_id": company_id, "companies": registry.list()}
    data["operations"]["worker"] = registry.worker_status()
    data["artifacts"] = WorkspaceRuntime(registry.artifacts_for(company_id)).inventory()
    data["preflight"] = runtime_preflight(get_store(company_id))
    return data


@app.get("/api/runtime/preflight")
def preflight():
    """Return actionable readiness checks without exposing secret values."""
    return runtime_preflight(get_store(), force=True)


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


@app.get("/api/artifacts")
def artifacts():
    """List immutable metadata for files in the active company's workspace."""
    try:
        return {"artifacts": WorkspaceRuntime(registry.artifacts_for()).inventory()}
    except RuntimeError:
        return {"artifacts": []}


@app.get("/api/artifacts/{artifact_path:path}")
def download_artifact(artifact_path: str):
    """Download a confined artifact; generated HTML is never executed in the control plane."""
    try:
        workspace = WorkspaceRuntime(registry.artifacts_for())
    except RuntimeError as exc:
        raise HTTPException(404, "No active company") from exc
    try:
        target = workspace.resolve(artifact_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(404, "Artifact not found")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


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
        readiness = runtime_preflight(store, force=True)
        if not readiness["ready"]:
            raise HTTPException(409, {"message": "Runtime preflight failed", "blockers": readiness["blockers"]})
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
    if store.get_control()["state"] in {"waiting_approval", "waiting_human", "paused"} and payload.kind == "directive":
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


def browser_runtime_request(method: str, path: str, payload: dict | None = None,
                            timeout: float = 40) -> tuple[bytes, str]:
    """Call only the configured internal browser service; user input never selects the host."""
    import json
    import urllib.error
    import urllib.request
    base = os.getenv("BROWSER_RUNTIME_URL", "http://127.0.0.1:8430").rstrip("/")
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        base + path, data=body, method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(exc.code, detail) from exc
    except Exception as exc:
        raise HTTPException(503, f"Browser runtime unavailable: {type(exc).__name__}") from exc


@app.get("/api/browser/health")
def browser_health():
    try:
        body, _ = browser_runtime_request("GET", "/health", timeout=2)
        return __import__("json").loads(body)
    except HTTPException:
        return {"status": "offline", "browser": None}


@app.put("/api/browser/session")
def open_browser_session(payload: BrowserSessionIn):
    company_id = registry.active_id()
    body, _ = browser_runtime_request(
        "PUT", f"/sessions/{company_id}", payload.model_dump(mode="json")
    )
    get_store(company_id).audit("browser.session_opened", {
        "url": payload.url, "allowed_domains": payload.allowed_domains, "actor": "human",
    })
    return __import__("json").loads(body)


@app.get("/api/browser/session")
def browser_session():
    body, _ = browser_runtime_request("GET", f"/sessions/{registry.active_id()}")
    return __import__("json").loads(body)


@app.get("/api/browser/screenshot")
def browser_screenshot():
    body, content_type = browser_runtime_request(
        "GET", f"/sessions/{registry.active_id()}/screenshot"
    )
    return Response(body, media_type=content_type, headers={"Cache-Control": "no-store"})


@app.post("/api/browser/actions")
def browser_action(payload: BrowserActionIn):
    company_id = registry.active_id()
    body, _ = browser_runtime_request(
        "POST", f"/sessions/{company_id}/actions", payload.model_dump(mode="json")
    )
    get_store(company_id).audit("browser.human_action", {
        "kind": payload.kind, "url": payload.url,
        "coordinates": [payload.x, payload.y] if payload.kind == "click" else None,
    })
    return __import__("json").loads(body)


@app.delete("/api/browser/session")
def close_browser_session():
    company_id = registry.active_id()
    body, _ = browser_runtime_request("DELETE", f"/sessions/{company_id}")
    get_store(company_id).audit("browser.session_closed", {"actor": "human"})
    return __import__("json").loads(body)


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


@app.get("/api/settings/integrations")
def integration_settings():
    return {"integrations": get_store().list_integrations()}


@app.post("/api/settings/integrations")
def save_integration_settings(payload: IntegrationSettingsIn):
    """Configure capability metadata without accepting or returning secret values."""
    provider = payload.provider.lower()
    domain = payload.store_domain.strip().lower().removeprefix("https://").rstrip("/")
    if provider == "shopify":
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
            raise HTTPException(400, "Shopify store domain must look like store-name.myshopify.com")
        required = ["SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"]
        capabilities = payload.capabilities or ["read_products", "write_products"]
    else:
        required = payload.required_secrets
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", name) for name in required):
            raise HTTPException(400, "Secret references must be uppercase environment variable names")
        capabilities = payload.capabilities
    return get_store().upsert_integration(
        provider, "configured",
        {"store_domain": domain, "capabilities": capabilities},
        required,
    )


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


@app.post("/api/handoffs/{handoff_id}/{decision}")
def handoff(handoff_id: str, decision: str, payload: HandoffDecisionIn):
    """Complete or cancel a human takeover and wake the autonomous loop."""
    if decision not in {"complete", "cancel"}:
        raise HTTPException(400, "Unknown handoff decision")
    try:
        get_store().resolve_handoff(handoff_id, payload.outcome, decision == "complete")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": "completed" if decision == "complete" else "cancelled"}


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
