"""FastAPI control plane for portfolio, runtime controls, chat, and approvals."""

from __future__ import annotations

import asyncio
import os
import html
import re
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from digital_company.api_models import (
    ApprovalDecisionIn,
    BrowserActionIn,
    BrowserSessionIn,
    CompanyCreateIn,
    EmailSettingsIn,
    HandoffDecisionIn,
    IntegrationSettingsIn,
    IntegrationConnectionIn,
    IntegrationOperationIn,
    MessageIn,
    ModelConnectionIn,
    ModelModeIn,
    ModelSettingsIn,
    PolicyUpdateIn,
)
from digital_company.browser_client import (
    BrowserRuntimeError,
    browser_runtime_request as request_browser_runtime,
)
from digital_company.registry import CompanyRegistry
from digital_company.store import CompanyStore
from digital_company.email_service import verify_approval_token
from digital_company.temporal_gateway import TemporalCommandError, signal_company
from digital_company.workspace import WorkspaceRuntime
from digital_company.runtime_secrets import apply_runtime_secrets, save_secret
from digital_company.runtime_secrets import get_secret
from digital_company.model_connections import ADAPTERS, ModelConnectionRegistry
from digital_company.models import ActionType
from digital_company.postgres_compat import pool_stats
from digital_company.preflight import (
    connection_ready as check_model_connection,
    evaluate_runtime_preflight,
)
from digital_company.execution_client import ExecutionRuntimeClient, ExecutionRuntimeError
from digital_company.integration_connectors import (
    ADAPTERS as INTEGRATION_ADAPTERS,
    secret_name as integration_secret_name,
)


ROOT = Path.cwd()
STATIC_DIR = Path(__file__).parent / "static"
FRONTEND_JS_ASSETS = {"ui-core.js", "settings-ui.js", "browser-ui.js"}
load_dotenv(ROOT / ".env.local")
apply_runtime_secrets()
STATE_DIR = Path(os.getenv("COMPANY_DATA_DIR", str(ROOT / ".company"))).expanduser().resolve()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Own process-wide control-plane resources."""
    yield
    close = getattr(registry, "close", None)
    if close:
        close()


app = FastAPI(title="Digital Company Control Plane", lifespan=lifespan)
registry = CompanyRegistry(STATE_DIR)
_preflight_cache: dict[str, tuple[float, tuple, dict]] = {}


def get_store(company_id: str | None = None) -> CompanyStore:
    """Open state for an explicit company or the currently selected company."""
    return registry.store_for(company_id)


@contextmanager
def store_scope(company_id: str | None = None):
    """Own one request-scoped company connection and always release it."""
    store = get_store(company_id)
    try:
        yield store
    finally:
        close = getattr(store, "close", None)
        if close:
            close()


def connection_ready(connection: dict | None, verify_model: bool = False) -> tuple[bool, str]:
    """Compatibility wrapper around the transport readiness service."""
    return check_model_connection(connection, verify_model)


def signal_temporal(company_id: str, signal_name: str, reason: str) -> bool:
    """Deliver a durable command when the Temporal infrastructure is enabled."""
    try:
        return signal_company(company_id, signal_name, reason)
    except TemporalCommandError as exc:
        with store_scope(company_id) as store:
            store.audit("temporal.signal_failed", {
                "signal": signal_name, "reason": reason, "error": str(exc),
            })
        raise HTTPException(503, str(exc)) from exc


def runtime_preflight(store: CompanyStore, force: bool = False) -> dict:
    """Evaluate whether the selected company can safely start its configured loop."""
    settings = store.get_settings()
    cache_key = str(store.path)
    signature = (settings["updated_at"], bool(os.getenv("OPENAI_API_KEY")),
                 bool(os.getenv("ANTHROPIC_API_KEY")))
    cached = _preflight_cache.get(cache_key)
    if not force and cached and cached[1] == signature and time.monotonic() - cached[0] < 5:
        return cached[2]
    worker = registry.worker_status()
    connection_registry = ModelConnectionRegistry()
    connections = connection_registry.ensure_defaults(settings["local_model"], settings["cloud_model"])
    browser = browser_health()
    execution = execution_health()
    result = evaluate_runtime_preflight(
        settings,
        worker=worker,
        connections=connections,
        browser=browser,
        execution=execution,
        connection_check=lambda connection, verify: connection_ready(connection, verify),
    )
    _preflight_cache[cache_key] = (time.monotonic(), signature, result)
    return result


def execution_health() -> dict:
    """Check the fixed internal build runtime without accepting a caller-supplied host."""
    if not os.getenv("EXECUTION_RUNTIME_URL"):
        return {"status": "disabled"}
    try:
        return ExecutionRuntimeClient().health()
    except (ExecutionRuntimeError, ValueError) as exc:
        return {"status": "offline", "detail": str(exc)}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/assets/i18n.js", include_in_schema=False)
def i18n_catalog():
    """Serve the zero-build UI translation catalog."""
    return FileResponse(STATIC_DIR / "i18n.js", media_type="text/javascript")


@app.get("/assets/telekt-logo.svg", include_in_schema=False)
def telekt_logo():
    return FileResponse(STATIC_DIR / "telekt-logo.svg", media_type="image/svg+xml")


@app.get("/assets/telekt.ico", include_in_schema=False)
def telekt_icon():
    return FileResponse(STATIC_DIR / "telekt.ico", media_type="image/x-icon")


@app.get("/assets/{asset_name}", include_in_schema=False)
def frontend_asset(asset_name: str):
    """Serve only explicitly registered frontend modules, never arbitrary paths."""
    if asset_name not in FRONTEND_JS_ASSETS:
        raise HTTPException(404, "Frontend asset not found")
    return FileResponse(
        STATIC_DIR / asset_name,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/health")
def health():
    worker = registry.worker_status()
    try:
        database = registry.database_status()
    except Exception as exc:
        database = {"status": "offline", "detail": type(exc).__name__}
    status = "ok" if worker["status"] == "online" and database["status"] == "online" else "degraded"
    return {
        "status": status, "worker": worker, "database": database,
        "company_store_pool": pool_stats(),
    }


@app.get("/api/dashboard")
def dashboard():
    """Return the active company projection plus portfolio switcher data."""
    try:
        company_id = registry.active_id()
    except RuntimeError:
        return {
            "control": {"state": "idle", "detail": "Create a company to begin"},
            "settings": {"model_mode": "local", "local_model": "deepseek-company:8b", "allow_cloud_fallback": 0,
                         "cloud_provider": "openai", "cloud_model": "gpt-5.4-mini"},
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
            "policy": None,
            "human_handoffs": [],
            "stakeholder_messages": [],
            "portfolio": {"active_company_id": None, "companies": []},
        }
    with store_scope(company_id) as store:
        data = store.dashboard_data()
        data["preflight"] = runtime_preflight(store)
    data["portfolio"] = {"active_company_id": company_id, "companies": registry.list()}
    data["operations"]["worker"] = registry.worker_status()
    data["artifacts"] = WorkspaceRuntime(registry.artifacts_for(company_id)).inventory()
    return data


@app.get("/api/runtime/preflight")
def preflight():
    """Return actionable readiness checks without exposing secret values."""
    with store_scope() as store:
        return runtime_preflight(store, force=True)


@app.get("/api/execution/health")
def isolated_execution_health():
    return execution_health()


@app.get("/api/operations")
def operations():
    """Return operational telemetry for the selected company and worker."""
    try:
        with store_scope() as store:
            data = store.operations_data()
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


@app.post("/api/recovery/retry")
def retry_from_checkpoint():
    """Resume after a terminal activity error from the last committed company state."""
    company_id = registry.active_id()
    with store_scope(company_id) as store:
        control_state = store.get_control()["state"]
        if control_state != "error":
            raise HTTPException(409, "The company has no recoverable runtime error")
        readiness = runtime_preflight(store, force=True)
        if not readiness["ready"]:
            raise HTTPException(409, {
                "message": "Runtime preflight failed",
                "blockers": readiness["blockers"],
            })
        store.close_orphaned_model_runs("operator requested recovery from checkpoint")
        store.set_control("running", "Recovery queued from last committed checkpoint")
        store.audit("recovery.operator_retry_requested", {
            "strategy": "resume_from_committed_state",
        })
    signal_temporal(company_id, "start", "operator_recovery_retry")
    return {"status": "running", "strategy": "resume_from_committed_state"}


@app.get("/api/companies")
def companies():
    return {"active_company_id": registry.active_id(), "companies": registry.list()}


@app.get("/api/skills")
def skills():
    with store_scope() as store:
        return {"skills": store.list_skills()}


@app.put("/api/skills/{skill_id}/{status}")
def set_skill_status(skill_id: str, status: str):
    try:
        with store_scope() as store:
            store.set_skill_status(skill_id, status)
    except KeyError as exc:
        raise HTTPException(404, "Skill not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"skill_id": skill_id, "status": status}


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
    with store_scope(company_id) as store:
        if action == "start":
            readiness = runtime_preflight(store, force=True)
            if not readiness["ready"]:
                raise HTTPException(409, {"message": "Runtime preflight failed", "blockers": readiness["blockers"]})
            store.set_control("running", "Queued for autonomous worker")
            signal_temporal(company_id, "start", "operator_start")
        elif action in {"pause", "stop"}:
            store.set_control("paused" if action == "pause" else "stopped",
                              "Will halt after current atomic action")
            signal_temporal(company_id, action, f"operator_{action}")
        else:
            raise HTTPException(400, "Unknown control action")
        return store.get_control()


@app.post("/api/messages")
def message(payload: MessageIn):
    """Persist stakeholder input and wake a paused company for directives."""
    company_id = registry.active_id()
    with store_scope(company_id) as store:
        try:
            message_id = store.add_stakeholder_message(payload.content.strip(), payload.kind)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if store.get_control()["state"] in {"waiting_approval", "waiting_human", "paused"} and payload.kind == "directive":
            store.set_control("running", "Stakeholder directive queued for worker")
            signal_temporal(company_id, "wake", "stakeholder_directive")
        return {"id": message_id, "status": "pending"}


@app.post("/api/settings/model-mode")
def model_mode(payload: ModelModeIn):
    """Change routing only while no model call is actively running."""
    with store_scope() as store:
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
    with store_scope() as store:
        if store.get_control()["state"] == "running":
            raise HTTPException(409, "Pause or stop the company before changing model settings")
        try:
            connections = ModelConnectionRegistry().ensure_defaults(payload.local_model, payload.cloud_model)
            by_id = {item["id"]: item for item in connections}
            local = by_id.get(payload.local_connection_id)
            cloud = by_id.get(payload.cloud_connection_id)
            if payload.mode in {"local", "hybrid"} and (not local or local["location"] != "local"):
                raise ValueError("Select a valid local model connection")
            if payload.mode in {"cloud", "hybrid"} and (not cloud or cloud["location"] != "cloud"):
                raise ValueError("Select a valid cloud model connection")
            store.set_model_settings(
                payload.mode, (local or {}).get("model", payload.local_model), payload.allow_cloud_fallback,
                (cloud or {}).get("adapter", "connection"), (cloud or {}).get("model", payload.cloud_model),
                payload.local_connection_id, payload.cloud_connection_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return store.get_settings()


@app.get("/api/model-connections")
def model_connections():
    with store_scope() as store:
        settings = store.get_settings()
    registry = ModelConnectionRegistry()
    connections = registry.ensure_defaults(settings["local_model"], settings["cloud_model"])
    def project(item: dict) -> dict:
        credential = bool(
            get_secret(f"MODEL_CONNECTION_{item['id']}") or
            (item["adapter"] == "openai_responses" and os.getenv("OPENAI_API_KEY"))
        )
        ready = bool(item["enabled"] and (credential or not item.get("requires_api_key", True)))
        return {**item, "credential_configured": credential, "ready": ready}
    return {
        "adapters": [{"id": key, **value} for key, value in ADAPTERS.items()],
        "connections": [project(item) for item in connections],
    }


@app.post("/api/model-connections")
def save_model_connection(payload: ModelConnectionIn):
    registry = ModelConnectionRegistry()
    try:
        item = registry.save(payload.model_dump(exclude={"api_key"}))
        if payload.api_key.strip():
            save_secret(f"MODEL_CONNECTION_{item['id']}", payload.api_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with store_scope() as store:
        store.audit("model_connection.saved", {
            "connection_id": item["id"], "adapter": item["adapter"], "location": item["location"],
        })
    _preflight_cache.clear()
    credential = bool(payload.api_key.strip() or get_secret(f"MODEL_CONNECTION_{item['id']}"))
    return {**item, "credential_configured": credential,
            "ready": bool(item["enabled"] and (credential or not item["requires_api_key"]))}


@app.delete("/api/model-connections/{connection_id}")
def delete_model_connection(connection_id: str):
    with store_scope() as store:
        settings = store.get_settings()
    if connection_id in {settings["local_connection_id"], settings["cloud_connection_id"]}:
        raise HTTPException(409, "Connection is currently assigned to the selected company")
    if not ModelConnectionRegistry().delete(connection_id):
        raise HTTPException(404, "Model connection not found")
    _preflight_cache.clear()
    return {"deleted": True}


def ollama_api_url(path: str) -> str:
    """Build a native Ollama API URL from its OpenAI-compatible base URL."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    return base_url.rstrip("/").removesuffix("/v1") + path


def browser_runtime_request(method: str, path: str, payload: dict | None = None,
                            timeout: float = 40) -> tuple[bytes, str]:
    """Map the transport-neutral browser client failure to an HTTP response."""
    try:
        return request_browser_runtime(method, path, payload, timeout)
    except BrowserRuntimeError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


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
    with store_scope(company_id) as store:
        store.audit("browser.session_opened", {
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
    with store_scope(company_id) as store:
        store.audit("browser.human_action", {
            "kind": payload.kind, "url": payload.url,
            "coordinates": [payload.x, payload.y] if payload.kind == "click" else None,
        })
    return __import__("json").loads(body)


@app.delete("/api/browser/session")
def close_browser_session():
    company_id = registry.active_id()
    body, _ = browser_runtime_request("DELETE", f"/sessions/{company_id}")
    with store_scope(company_id) as store:
        store.audit("browser.session_closed", {"actor": "human"})
    return __import__("json").loads(body)


@app.post("/api/handoffs/{handoff_id}/browser")
def open_handoff_browser(handoff_id: str):
    """Open the frozen handoff URL in the selected company's isolated cockpit."""
    company_id = registry.active_id()
    try:
        with store_scope(company_id) as store:
            handoff = store.get_handoff(handoff_id, require_pending=True)
            proposal = handoff["proposal"]
            if not proposal.handoff_url:
                raise ValueError(
                    "This handoff has no starting URL. Tell the CEO to return an exact setup URL."
                )
            parsed = urlparse(proposal.handoff_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(
                    "The guided browser requires an exact HTTPS URL. Use the normal-browser link instead."
                )
            allowed_domains = list(dict.fromkeys([
                parsed.hostname, *(proposal.handoff_allowed_domains or []),
            ]))
            body, _ = browser_runtime_request(
                "PUT", f"/sessions/{company_id}",
                {"url": proposal.handoff_url, "allowed_domains": allowed_domains},
            )
            store.audit("handoff.browser_opened", {
                "handoff_id": handoff_id,
                "task_id": handoff["task_id"],
                "url": proposal.handoff_url,
                "allowed_domains": allowed_domains,
                "actor": "human",
            })
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
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
    with store_scope() as store:
        return store.get_email_settings()


@app.post("/api/settings/email")
def save_email_settings(payload: EmailSettingsIn):
    emails = []
    for value in payload.approvers:
        email = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise HTTPException(400, f"Invalid approver email: {value}")
        if email not in emails:
            emails.append(email)
    if payload.enabled and not emails:
        raise HTTPException(400, "At least one approver email is required")
    with store_scope() as store:
        return store.set_email_settings(payload.enabled, emails, payload.sender_name)


@app.get("/api/settings/integrations")
def integration_settings():
    with store_scope() as store:
        return {"integrations": store.list_integrations()}


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
    with store_scope() as store:
        return store.upsert_integration(
            provider, "configured",
            {"store_domain": domain, "capabilities": capabilities},
            required,
        )


@app.get("/api/integration-connections")
def integration_connections():
    """List transport technologies and configured profiles without secret values."""
    with store_scope() as store:
        return {
            "adapters": [
                {"id": key, **value} for key, value in INTEGRATION_ADAPTERS.items()
            ],
            "connections": store.list_integration_connections(),
        }


@app.get("/api/policy")
def get_company_policy():
    """Return the selected company's active versioned authorization policy."""
    with store_scope() as store:
        return {**store.get_policy(), "actions": [action.value for action in ActionType]}


@app.post("/api/policy")
def save_company_policy(payload: PolicyUpdateIn):
    """Create a new policy version; the contract-signing ban is invariant."""
    with store_scope() as store:
        return store.set_policy(payload, "dashboard")


@app.post("/api/integration-connections")
def save_integration_connection(payload: IntegrationConnectionIn):
    """Persist profile metadata and write-only credentials through the local vault."""
    connection_id = payload.id or str(uuid4())
    adapter = INTEGRATION_ADAPTERS.get(payload.adapter)
    if not adapter:
        raise HTTPException(400, "Unsupported integration adapter")
    credentials = {key: value.strip() for key, value in payload.credentials.items() if value.strip()}
    unknown = set(credentials) - set(adapter["credential_fields"])
    if unknown:
        raise HTTPException(400, f"Unexpected credential fields: {', '.join(sorted(unknown))}")
    if any(len(value) > 500 for value in credentials.values()):
        raise HTTPException(400, "Credential values must not exceed 500 characters")
    try:
        with store_scope() as store:
            item = store.upsert_integration_connection({
                **payload.model_dump(exclude={"credentials"}),
                "id": connection_id,
            })
        for field, value in credentials.items():
            save_secret(integration_secret_name(connection_id, field), value)
        with store_scope() as store:
            return store.get_integration_connection(connection_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/integration-operations/prepare")
def prepare_integration_operation(payload: IntegrationOperationIn):
    """Freeze and idempotently identify a call; this endpoint never sends it."""
    try:
        with store_scope() as store:
            return store.prepare_integration_operation(
                payload.execution_key, payload.connection_id, payload.capability,
                payload.method, payload.path, payload.request,
            )
    except KeyError as exc:
        raise HTTPException(404, "Integration connection not found") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


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
    with store_scope() as store:
        try:
            if decision == "approve":
                result = store.approve(approval_id, payload.comment)
            elif decision == "reject":
                store.reject(approval_id, payload.comment)
                result = {"status": "rejected"}
            else:
                raise HTTPException(400, "Unknown decision")
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
    if result["status"] != "pending":
        signal_temporal(registry.active_id(), "wake", f"approval_{decision}")
    return result


@app.post("/api/handoffs/{handoff_id}/{decision}")
def handoff(handoff_id: str, decision: str, payload: HandoffDecisionIn):
    """Complete or cancel a human takeover and wake the autonomous loop."""
    if decision not in {"complete", "cancel"}:
        raise HTTPException(400, "Unknown handoff decision")
    try:
        with store_scope() as store:
            store.resolve_handoff(handoff_id, payload.outcome, decision == "complete")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    signal_temporal(registry.active_id(), "wake", f"handoff_{decision}")
    return {"status": "completed" if decision == "complete" else "cancelled"}


def approval_page(company_id: str, approval_id: str, email: str, expires: int, token: str, message: str = "") -> str:
    """Render a safe confirmation form; GET requests never change approval state."""
    if not verify_approval_token(company_id, approval_id, email, expires, token):
        raise HTTPException(403, "Invalid approval link")
    try:
        with store_scope(company_id) as store:
            approval = next(a for a in store.list_approvals() if a["id"] == approval_id)
    except (KeyError, StopIteration) as exc:
        raise HTTPException(404, "Approval not found") from exc
    proposal = __import__("json").loads(approval["payload_json"])
    disabled = approval["status"] != "pending"
    approval_count = int(approval.get("approval_count", 0))
    required_approvals = int(approval.get("required_approvals", 1))
    esc = html.escape
    buttons = "<p>This approval has already been resolved.</p>" if disabled else """
      <textarea name="comment" maxlength="4000" placeholder="Comment or decline reason"></textarea>
      <div class="buttons"><button name="decision" value="approve" class="approve">Approve</button>
      <button name="decision" value="reject" class="reject">Decline</button></div>"""
    return f"""<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>Approval review</title>
<style>body{{margin:0;background:#090d13;color:#eaf1f8;font:15px Arial,sans-serif}}main{{max-width:680px;margin:40px auto;padding:26px;background:#151c26;border:1px solid #2a384b;border-radius:16px}}h1{{font-size:26px}}.meta{{padding:16px;background:#0d1219;border-radius:10px;line-height:1.7;color:#cbd5e1}}textarea{{box-sizing:border-box;width:100%;min-height:120px;margin:20px 0;padding:12px;background:#0b1017;color:white;border:1px solid #34445a;border-radius:9px}}button{{padding:13px 22px;border:0;border-radius:9px;font-weight:bold;cursor:pointer}}.approve{{background:#4ee3a1}}.reject{{background:#ff6b7a;margin-left:8px}}.note{{color:#91a0b4}}.message{{color:#4ee3a1}}</style></head><body><main><div class="note">DIGITAL COMPANY · SECURE HUMAN DECISION</div><h1>{esc(proposal['title'])}</h1><p>{esc(proposal['objective'])}</p><div class="meta"><b>Action:</b> {esc(proposal['action'])}<br><b>Estimated cost:</b> €{proposal['estimated_cost_eur']:.2f}<br><b>Status:</b> {esc(approval['status'])}<br><b>Approval quorum:</b> {approval_count} / {required_approvals}<br><b>Expires:</b> {esc(approval.get('expires_at') or 'not set')}</div><p class="message">{esc(message)}</p><form method="post"><input type="hidden" name="email" value="{esc(email)}"><input type="hidden" name="expires" value="{expires}"><input type="hidden" name="token" value="{esc(token)}">{buttons}</form><p class="note">Decline requires a reason. Comments become canonical context for the AI company.</p></main></body></html>"""


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
    try:
        with store_scope(company_id) as store:
            if decision == "approve":
                result = store.approve(approval_id, comment, email)
                if result["status"] == "approved":
                    message = "Approval quorum reached. The exact frozen task is queued."
                else:
                    message = (
                        f"Vote recorded ({result['approval_count']} / "
                        f"{result['required_approvals']}). Waiting for the remaining approver(s)."
                    )
            elif decision == "reject":
                store.reject(approval_id, comment, email)
                result = {"status": "rejected"}
                message = "Declined. The CEO will reconsider using your reason."
            else:
                raise ValueError("Choose Approve or Decline")
    except (RuntimeError, ValueError) as exc:
        return HTMLResponse(approval_page(company_id, approval_id, email, expires, token, str(exc)), status_code=400)
    if result["status"] != "pending":
        await asyncio.to_thread(signal_temporal, company_id, "wake", f"email_approval_{decision}")
    return approval_page(company_id, approval_id, email, expires, token, message)


def main() -> None:
    port = int(os.getenv("PORT", "8421"))
    host = os.getenv("HOST", "127.0.0.1")
    uvicorn.run("digital_company.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
