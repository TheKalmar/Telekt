"""Temporal Activities and worker process for durable company execution."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.worker import Worker

from digital_company.orchestrator import CompanyOrchestrator
from digital_company.registry import CompanyRegistry
from digital_company.temporal_workflow import CompanyLoopWorkflow

TASK_QUEUE = "digital-company"


def registry() -> CompanyRegistry:
    root = Path(os.getenv("COMPANY_DATA_DIR", ".company")).expanduser().resolve()
    return CompanyRegistry(root)


@activity.defn(name="advance_company")
async def advance_company(company_id: str) -> dict:
    """Run one bounded, idempotency-protected orchestration cycle."""
    return await asyncio.to_thread(_advance_company_sync, company_id)


def _advance_company_sync(company_id: str) -> dict:
    """Keep blocking SQLite and model calls off Temporal's asyncio loop."""
    portfolio = registry()
    store = portfolio.store_for(company_id)
    if store.get_control()["state"] != "running":
        return {"status": store.get_control()["state"], "cycles": 0}
    return CompanyOrchestrator(
        store, portfolio.artifacts_for(company_id), company_id=company_id,
    ).run(max_cycles=1)


async def ensure_workflows(client: Client) -> None:
    """Reconcile newly-created companies into one durable workflow each."""
    while True:
        for company in registry().list():
            workflow_id = f"company-{company['id']}"
            handle = client.get_workflow_handle(workflow_id)
            try:
                description = await handle.describe()
                if description.status == WorkflowExecutionStatus.COMPLETED:
                    await client.start_workflow(CompanyLoopWorkflow.run, company["id"],
                                                id=workflow_id, task_queue=TASK_QUEUE)
            except Exception:
                try:
                    await client.start_workflow(CompanyLoopWorkflow.run, company["id"],
                                                id=workflow_id, task_queue=TASK_QUEUE)
                except Exception:
                    pass  # Another reconciler/worker may have won the start race.
        await asyncio.sleep(5)


async def run_worker() -> None:
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
                                  namespace=os.getenv("TEMPORAL_NAMESPACE", "default"))
    async with Worker(client, task_queue=TASK_QUEUE,
                      workflows=[CompanyLoopWorkflow], activities=[advance_company]):
        await ensure_workflows(client)


def main() -> None:
    load_dotenv(Path.cwd() / ".env.local")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
