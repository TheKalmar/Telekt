"""Temporal Activities and worker process for durable company execution."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from digital_company.email_service import ApprovalMailer
from digital_company.orchestrator import CompanyOrchestrator
from digital_company.registry import CompanyRegistry
from digital_company.temporal_gateway import ensure_workflow
from digital_company.temporal_workflow import CompanyLoopWorkflow, TASK_QUEUE


def registry() -> CompanyRegistry:
    root = Path(os.getenv("COMPANY_DATA_DIR", ".company")).expanduser().resolve()
    return CompanyRegistry(root)


def _apply_result_state(store, result: dict) -> None:
    """Project orchestration exit conditions into canonical company control."""
    status = result.get("status", "unknown")
    if status == "waiting_for_approval":
        store.set_control("waiting_approval", "Human decision required")
    elif status == "waiting_for_human":
        store.set_control("waiting_human", result.get("reason", "Human action required"))
    elif status in {"stopped", "paused"}:
        store.set_control(status, result.get("reason"))
    elif status == "failed":
        store.set_control("error", "Approved task failed policy revalidation")


@activity.defn(name="advance_company")
async def advance_company(input_value: str | dict) -> dict:
    """Run one bounded company cycle without blocking Temporal's event loop."""
    if isinstance(input_value, dict):
        company_id = input_value["company_id"]
        execution_key = input_value["execution_key"]
    else:
        company_id, execution_key = input_value, f"legacy:{input_value}"
    try:
        return await asyncio.to_thread(_advance_company_sync, company_id, execution_key)
    except Exception as exc:
        if activity.info().attempt < 3:
            raise
        return await asyncio.to_thread(_finalize_activity_failure, company_id, execution_key, exc)


def _finalize_activity_failure(company_id: str, execution_key: str, exc: Exception) -> dict:
    portfolio = registry()
    store = portfolio.store_for(company_id)
    detail = f"{type(exc).__name__}: {exc}"
    result = store.fail_activity(execution_key, detail)
    store.set_control("error", detail)
    store.audit("temporal.activity_failed", {
        "activity": "advance_company", "execution_key": execution_key,
        "attempts": 3, "error": detail,
    })
    return result


def _advance_company_sync(company_id: str, execution_key: str | None = None) -> dict:
    portfolio = registry()
    store = portfolio.store_for(company_id)
    execution_key = execution_key or f"manual:{company_id}"
    cached = store.begin_activity(execution_key)
    if cached is not None:
        return cached
    if store.get_control()["state"] != "running":
        result = {"status": store.get_control()["state"], "cycles": 0}
        store.complete_activity(execution_key, result)
        return result
    result = CompanyOrchestrator(
        store, portfolio.artifacts_for(company_id), company_id=company_id,
        execution_key=execution_key,
    ).run(max_cycles=1)
    _apply_result_state(store, result)
    store.complete_activity(execution_key, result)
    return result


@activity.defn(name="send_company_brief")
async def send_company_brief(company_id: str) -> dict:
    """Send at most one stakeholder brief per configured contact interval."""
    return await asyncio.to_thread(_send_company_brief_sync, company_id)


def _send_company_brief_sync(company_id: str) -> dict:
    portfolio = registry()
    store = portfolio.store_for(company_id)
    contact_hours = max(1, int(os.getenv("STAKEHOLDER_CONTACT_INTERVAL_HOURS", "24")))
    settings = store.get_email_settings()
    approvals = store.pending_approval_details()
    if not settings["enabled"] or not (approvals or store.snapshot().completed_tasks):
        return {"status": "not_needed"}
    if not store.stakeholder_notification_allowed(contact_hours):
        return {"status": "rate_limited"}
    try:
        snapshot = store.snapshot()
        sent = ApprovalMailer().send_daily_brief(company_id, {
            "control": store.get_control()["state"],
            "spent": snapshot.spent_eur,
            "remaining": snapshot.remaining_budget_eur,
            "results": [task["title"] for task in snapshot.completed_tasks[-8:]],
        }, approvals, settings)
        if sent:
            store.audit("stakeholder.notification_sent", {
                "channel": "daily_ceo_brief", "recipients": sent,
            })
        return {"status": "sent" if sent else "not_configured", "recipients": len(sent)}
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        store.audit("stakeholder.notification_failed", {
            "channel": "daily_ceo_brief", "error": detail,
        })
        return {"status": "failed", "error": detail}


async def reconcile_workflows(client: Client) -> None:
    """One-time startup recovery; new commands create workflows on demand."""
    for company in registry().list():
        handle = await ensure_workflow(client, company["id"])
        if company["runtime"] == "running":
            await handle.signal("start", "worker_startup_recovery")


async def heartbeat_loop() -> None:
    """Expose Temporal worker liveness through the existing dashboard projection."""
    portfolio = registry()
    while True:
        await asyncio.to_thread(portfolio.heartbeat_worker, "temporal-worker")
        await asyncio.sleep(5)


async def run_worker() -> None:
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CompanyLoopWorkflow],
        activities=[advance_company, send_company_brief],
    ):
        await reconcile_workflows(client)
        await heartbeat_loop()


def main() -> None:
    load_dotenv(Path.cwd() / ".env.local")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
