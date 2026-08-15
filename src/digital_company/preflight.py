"""Runtime readiness checks independent from FastAPI route handling."""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable

from digital_company.runtime_secrets import get_secret


def connection_ready(
    connection: dict | None,
    verify_model: bool = False,
    *,
    secret_reader: Callable[[str], str | None] = get_secret,
) -> tuple[bool, str]:
    """Validate a transport profile without making a billable generation call."""
    if not connection or not connection.get("enabled"):
        return False, "Model connection is missing or disabled"
    secret = secret_reader(f"MODEL_CONNECTION_{connection['id']}")
    if connection.get("requires_api_key", True) and not (
        secret
        or (connection["adapter"] == "openai_responses" and os.getenv("OPENAI_API_KEY"))
    ):
        return False, f"Credential is missing for {connection['name']}"
    if verify_model and connection["adapter"] == "openai_compatible":
        request = urllib.request.Request(connection["base_url"].rstrip("/") + "/models")
        if secret:
            request.add_header("Authorization", f"Bearer {secret}")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read())
            models = [item.get("id") for item in payload.get("data", [])]
            if connection["model"] not in models:
                return False, f"{connection['model']} was not reported by {connection['name']}"
        except Exception as exc:
            return False, f"{connection['name']} is unreachable ({type(exc).__name__})"
    return True, f"{connection['name']} · {connection['model']} is ready"


def evaluate_runtime_preflight(
    settings: dict,
    *,
    worker: dict,
    connections: list[dict],
    browser: dict,
    connection_check: Callable[[dict | None, bool], tuple[bool, str]],
    execution: dict | None = None,
    browser_required: bool = True,
) -> dict:
    """Build readiness checks from explicit runtime observations."""
    mode = settings["model_mode"]
    checks: list[dict] = [{
        "id": "worker",
        "status": "pass" if worker["status"] == "online" else "block",
        "detail": (
            "Background worker is online" if worker["status"] == "online"
            else "Background worker is offline; restart the worker container"
        ),
    }]
    by_id = {item["id"]: item for item in connections}

    if mode in {"local", "hybrid"}:
        ready, detail = connection_check(
            by_id.get(settings["local_connection_id"]), True,
        )
        checks.append({
            "id": "local_connection", "status": "pass" if ready else "block",
            "detail": detail,
        })
    else:
        checks.append({
            "id": "local_connection", "status": "skip",
            "detail": "Remote-only routing does not require a local connection",
        })

    cloud_required = mode in {"cloud", "hybrid"}
    cloud = by_id.get(settings.get("cloud_connection_id", "cloud-default"))
    provider = cloud["name"] if cloud else "Cloud connection"
    cloud_ready, cloud_detail = connection_check(cloud, False)
    checks.append({
        "id": "cloud_connection",
        "status": "pass" if cloud_ready else "block" if cloud_required else "warn",
        "detail": (
            cloud_detail if cloud_ready or cloud_required
            else f"{provider} is not ready; local work can still run"
        ),
    })

    if browser_required:
        browser_ready = browser["status"] == "ok"
        checks.append({
            "id": "browser", "status": "pass" if browser_ready else "warn",
            "detail": (
                "Isolated browser runtime is online" if browser_ready
                else "Browser runtime is offline; reasoning work can run but Browser Missions cannot"
            ),
        })
    execution = execution or {"status": "disabled"}
    execution_ready = execution.get("status") == "ok"
    execution_configured = execution.get("status") != "disabled"
    checks.append({
        "id": "execution_runtime",
        "status": "pass" if execution_ready else "block" if execution_configured else "warn",
        "detail": (
            "Isolated build and Git runtime is online" if execution_ready
            else "Execution runtime is configured but unavailable; code tasks cannot run safely"
            if execution_configured
            else "Execution runtime is not configured; reasoning can run but code is artifact-only"
        ),
    })
    blockers = [check for check in checks if check["status"] == "block"]
    return {
        "ready": not blockers,
        "mode": mode,
        "checks": checks,
        "blockers": [check["detail"] for check in blockers],
    }
