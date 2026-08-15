"""Temporal Activities and worker process for durable company execution."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agents import ModelBehaviorError
from dotenv import load_dotenv
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from digital_company.company_runtime import StakeholderBriefService, apply_orchestration_result
from digital_company.email_service import ApprovalMailer
from digital_company.errors import BudgetLimitError
from digital_company.orchestrator import CompanyOrchestrator
from digital_company.registry import CompanyRegistry
from digital_company.temporal_gateway import ensure_agent_workflow, ensure_workflow
from digital_company.temporal_workflow import AgentLoopWorkflow, CompanyLoopWorkflow, TASK_QUEUE
from digital_company.runtime_secrets import apply_runtime_secrets


def registry() -> CompanyRegistry:
    root = Path(os.getenv("COMPANY_DATA_DIR", ".company")).expanduser().resolve()
    return CompanyRegistry(root)


# Compatibility alias for callers that imported the old worker-private helper.
_apply_result_state = apply_orchestration_result


def is_non_retryable_activity_error(exc: Exception) -> bool:
    """Return whether replaying the same frozen input would only repeat failure.

    AgentEngine already performs its one bounded structured-output repair. A
    remaining ModelBehaviorError is therefore not a transport outage and must
    not make Temporal repeat the entire paid model activity.
    """
    return isinstance(exc, (
        KeyError, PermissionError, ValueError, ModelBehaviorError, BudgetLimitError,
    ))


@activity.defn(name="advance_company")
async def advance_company(input_value: str | dict) -> dict:
    """Run one bounded company cycle without blocking Temporal's event loop."""
    if isinstance(input_value, dict):
        company_id = input_value["company_id"]
        execution_key = input_value["execution_key"]
    else:
        company_id, execution_key = input_value, f"legacy:{input_value}"
    try:
        return await _to_thread_with_heartbeat(
            _advance_company_sync, company_id, execution_key,
        )
    except Exception as exc:
        attempt = activity.info().attempt
        if not is_non_retryable_activity_error(exc) and attempt < 3:
            raise
        return await asyncio.to_thread(
            _finalize_activity_failure, company_id, execution_key, exc,
            attempt,
        )


@activity.defn(name="advance_agent")
async def advance_agent(input_value: dict) -> dict:
    """Run one bounded cycle for exactly one independently configured agent."""
    company_id = input_value["company_id"]
    agent_id = input_value["agent_id"]
    execution_key = input_value["execution_key"]
    try:
        return await _to_thread_with_heartbeat(
            _advance_agent_sync, company_id, agent_id, execution_key,
        )
    except Exception as exc:
        attempt = activity.info().attempt
        if not is_non_retryable_activity_error(exc) and attempt < 3:
            raise
        return await asyncio.to_thread(
            _finalize_agent_activity_failure,
            company_id, agent_id, execution_key, exc, attempt,
        )


async def _to_thread_with_heartbeat(function, *args):
    """Heartbeat while a blocking company cycle runs in its worker thread."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=20)
        if done:
            break
        activity.heartbeat({"phase": function.__name__})
    return await task


def _finalize_activity_failure(
    company_id: str, execution_key: str, exc: Exception, attempts: int = 3,
) -> dict:
    portfolio = registry()
    try:
        with portfolio.store_for(company_id) as store:
            detail = f"{type(exc).__name__}: {exc}"
            store.close_orphaned_model_runs("temporal activity failed after retries")
            result = store.fail_activity(execution_key, detail)
            store.set_control("error", detail)
            store.audit("temporal.activity_failed", {
                "activity": "advance_company", "execution_key": execution_key,
                "attempts": attempts, "error": detail,
            })
            return result
    finally:
        portfolio.close()


def _advance_company_sync(company_id: str, execution_key: str | None = None) -> dict:
    apply_runtime_secrets()
    portfolio = registry()
    try:
        with portfolio.store_for(company_id) as store:
            execution_key = execution_key or f"manual:{company_id}"
            cached = store.begin_activity(execution_key)
            if cached is not None:
                return cached
            control = store.get_control()
            if control["state"] != "running":
                result = {"status": control["state"], "cycles": 0}
                store.complete_activity(execution_key, result)
                return result
            result = CompanyOrchestrator(
                store, portfolio.artifacts_for(company_id), company_id=company_id,
                execution_key=execution_key,
            ).run(max_cycles=1)
            apply_orchestration_result(store, result)
            store.complete_activity(execution_key, result)
            return result
    finally:
        portfolio.close()


def _advance_agent_sync(company_id: str, agent_id: str, execution_key: str) -> dict:
    """Own idempotency and lifecycle projection around one agent cycle."""
    apply_runtime_secrets()
    portfolio = registry()
    try:
        with portfolio.store_for(company_id) as store:
            cached = store.begin_activity(execution_key, agent_id=agent_id)
            if cached is not None:
                return cached
            agent = store.get_agent(agent_id)
            if agent["status"] == "sleeping":
                # A Temporal timer, not a browser request, owns this transition.
                store.set_agent_status(agent_id, "running")
                agent = store.get_agent(agent_id)
            if agent["status"] != "running":
                result = {"status": agent["status"], "cycles": 0, "agent_id": agent_id}
                store.complete_activity(execution_key, result)
                return result
            run_id = store.begin_agent_run(agent_id, execution_key)
            result = CompanyOrchestrator(
                store,
                portfolio.artifacts_for(company_id),
                company_id=company_id,
                agent_id=agent_id,
                execution_key=execution_key,
            ).run(max_cycles=1)
            status = result.get("status", "unknown")
            if status == "waiting_for_approval":
                store.set_agent_status(agent_id, "waiting_approval")
                run_status = "waiting"
            elif status == "waiting_for_human":
                store.set_agent_status(agent_id, "waiting_human")
                run_status = "waiting"
            elif status in {"stopped", "paused"}:
                store.set_agent_status(agent_id, status)
                run_status = status
            elif status == "sleeping":
                # The orchestrator already stored next_wake_at atomically with
                # its stopping decision. Keep the run successful and idle.
                run_status = "completed"
            elif status in {"failed", "error"}:
                store.set_agent_status(agent_id, "error")
                run_status = "failed"
            else:
                run_status = "completed"
            store.complete_agent_run(run_id, run_status, result.get("error"))
            result["agent_id"] = agent_id
            store.complete_activity(execution_key, result)
            # Content-review delivery follows committed state. SMTP failures are
            # audited and never roll back finished agent work.
            contact_hours = max(1, int(os.getenv("STAKEHOLDER_CONTACT_INTERVAL_HOURS", "24")))
            result["stakeholder_notification"] = StakeholderBriefService(
                ApprovalMailer(), contact_interval_hours=contact_hours,
            ).send_if_due(company_id, store)
            return result
    finally:
        portfolio.close()


def _finalize_agent_activity_failure(
    company_id: str,
    agent_id: str,
    execution_key: str,
    exc: Exception,
    attempts: int = 3,
) -> dict:
    portfolio = registry()
    try:
        with portfolio.store_for(company_id) as store:
            detail = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, BudgetLimitError):
                result = {
                    "status": "stopped", "cycles": 0, "reason": "agent_budget_limit",
                    "detail": str(exc), "agent_id": agent_id,
                }
                store.complete_activity(execution_key, result)
                store.complete_agent_run_by_execution(execution_key, "stopped", detail)
                store.set_agent_status(agent_id, "stopped")
                store.audit("budget.agent_limit_reached", {
                    "agent_id": agent_id, "execution_key": execution_key,
                    "attempts": attempts, "detail": str(exc),
                })
                return result
            result = store.fail_activity(execution_key, detail)
            store.fail_agent_run_by_execution(execution_key, detail)
            store.set_agent_status(agent_id, "error")
            store.audit("temporal.agent_activity_failed", {
                "activity": "advance_agent", "agent_id": agent_id,
                "execution_key": execution_key, "attempts": attempts, "error": detail,
            })
            return {**result, "agent_id": agent_id}
    finally:
        portfolio.close()


@activity.defn(name="send_company_brief")
async def send_company_brief(company_id: str) -> dict:
    """Send at most one stakeholder brief per configured contact interval."""
    return await asyncio.to_thread(_send_company_brief_sync, company_id)


def _send_company_brief_sync(company_id: str) -> dict:
    portfolio = registry()
    contact_hours = max(1, int(os.getenv("STAKEHOLDER_CONTACT_INTERVAL_HOURS", "24")))
    try:
        with portfolio.store_for(company_id) as store:
            return StakeholderBriefService(
                ApprovalMailer(), contact_interval_hours=contact_hours,
            ).send_if_due(company_id, store)
    finally:
        portfolio.close()


async def reconcile_workflows(client: Client) -> None:
    """One-time startup recovery; new commands create workflows on demand."""
    portfolio = registry()
    try:
        companies = portfolio.list()
    finally:
        portfolio.close()
    for company in companies:
        handle = await ensure_workflow(client, company["id"])
        if company["runtime"] == "running":
            await handle.signal("start", "worker_startup_recovery")
        company_registry = registry()
        try:
            with company_registry.store_for(company["id"]) as store:
                agents = store.list_agents()
        finally:
            company_registry.close()
        for agent in agents:
            agent_handle = await ensure_agent_workflow(client, company["id"], agent["id"])
            if agent["status"] == "running":
                await agent_handle.signal("start", "worker_startup_recovery")


async def heartbeat_loop() -> None:
    """Expose Temporal worker liveness through the existing dashboard projection."""
    portfolio = registry()
    try:
        while True:
            await asyncio.to_thread(portfolio.heartbeat_worker, "temporal-worker")
            await asyncio.sleep(5)
    finally:
        portfolio.close()


async def run_worker() -> None:
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CompanyLoopWorkflow, AgentLoopWorkflow],
        activities=[advance_company, advance_agent, send_company_brief],
    ):
        await reconcile_workflows(client)
        await heartbeat_loop()


def main() -> None:
    load_dotenv(Path.cwd() / ".env.local")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
