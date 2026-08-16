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

from digital_company.temporal_workflow import AgentLoopWorkflow, CompanyLoopWorkflow, TASK_QUEUE


class TemporalCommandError(RuntimeError):
    """Raised when configured durable execution cannot accept a command."""


def enabled() -> bool:
    return bool(os.getenv("TEMPORAL_ADDRESS", "").strip())


def workflow_id(company_id: str) -> str:
    # V4 adds activity heartbeats/timeouts and history-size rollover. A fresh
    # identity avoids replaying V3 history with structurally different commands.
    return f"company-loop-v4-{company_id}"


def agent_workflow_id(company_id: str, agent_id: str) -> str:
    # V3 isolates activity idempotency keys from earlier agent-loop generations.
    # V2 could replay completed V1 activity rows and appear running while doing
    # no work because both generations started their sequence at one.
    return f"agent-loop-v3-{company_id}-{agent_id}"


def legacy_agent_workflow_id(company_id: str, agent_id: str) -> str:
    """Return the superseded V2 identity retained only for safe shutdown."""
    return f"agent-loop-v2-{company_id}-{agent_id}"


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


async def ensure_agent_workflow(client: Client, company_id: str, agent_id: str):
    """Start exactly one stable Temporal workflow for this agent instance."""
    identity = agent_workflow_id(company_id, agent_id)
    try:
        await client.start_workflow(
            AgentLoopWorkflow.run,
            {
                "company_id": company_id,
                "agent_id": agent_id,
                "execution_namespace": "v3",
            },
            id=identity,
            task_queue=TASK_QUEUE,
        )
    except WorkflowAlreadyStartedError:
        pass
    handle = client.get_workflow_handle(identity)
    # A sleeping V2 loop could otherwise wake later and race the V3 owner. This
    # migration signal is best-effort because many installations never created
    # a V2 workflow for this agent.
    try:
        legacy = client.get_workflow_handle(legacy_agent_workflow_id(company_id, agent_id))
        await legacy.signal("shutdown", "superseded_by_agent_loop_v3")
    except Exception:
        pass
    return handle


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


async def _signal_agent(
    company_id: str, agent_id: str, signal_name: str, reason: str,
) -> None:
    client = await Client.connect(
        os.environ["TEMPORAL_ADDRESS"],
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    handle = await ensure_agent_workflow(client, company_id, agent_id)
    await handle.signal(signal_name, reason)


def signal_agent(
    company_id: str, agent_id: str, signal_name: str, reason: str,
) -> bool:
    """Durably control one agent without changing any sibling agent."""
    if not enabled():
        return False
    try:
        asyncio.run(_signal_agent(company_id, agent_id, signal_name, reason))
    except Exception as exc:
        raise TemporalCommandError(
            f"Temporal agent command {signal_name!r} failed: {type(exc).__name__}"
        ) from exc
    return True
