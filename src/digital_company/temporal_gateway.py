"""Synchronous web-to-Temporal command gateway.

The FastAPI handlers persist canonical intent first, then use this module to
durably wake the matching company workflow. The gateway is disabled when
``TEMPORAL_ADDRESS`` is absent, preserving the lightweight SQLite development
stack while the infrastructure overlay uses Temporal exclusively.
"""
from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from digital_company.temporal_workflow import CompanyLoopWorkflow, TASK_QUEUE


class TemporalCommandError(RuntimeError):
    """Raised when configured durable execution cannot accept a command."""


def enabled() -> bool:
    return bool(os.getenv("TEMPORAL_ADDRESS", "").strip())


def workflow_id(company_id: str) -> str:
    # V2 intentionally starts a fresh history. Replaying the former polling
    # workflow with signal-driven code would be nondeterministic.
    return f"company-loop-v2-{company_id}"


async def ensure_workflow(client: Client, company_id: str):
    """Start the company workflow once and return its stable handle."""
    try:
        await client.start_workflow(
            CompanyLoopWorkflow.run,
            company_id,
            id=workflow_id(company_id),
            task_queue=TASK_QUEUE,
        )
    except WorkflowAlreadyStartedError:
        pass
    return client.get_workflow_handle(workflow_id(company_id))


async def _signal(company_id: str, signal_name: str, reason: str) -> None:
    client = await Client.connect(
        os.environ["TEMPORAL_ADDRESS"],
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    handle = await ensure_workflow(client, company_id)
    await handle.signal(signal_name, reason)


def signal_company(company_id: str, signal_name: str, reason: str) -> bool:
    """Ensure and signal one workflow from a synchronous API handler.

    Returns ``False`` only when Temporal is intentionally not configured. When
    it is configured, failure is explicit so the API never claims a durable
    command was accepted when it was not.
    """
    if not enabled():
        return False
    try:
        asyncio.run(_signal(company_id, signal_name, reason))
    except Exception as exc:
        raise TemporalCommandError(
            f"Temporal command {signal_name!r} failed: {type(exc).__name__}"
        ) from exc
    return True
