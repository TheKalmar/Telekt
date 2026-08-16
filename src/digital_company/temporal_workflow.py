"""Signal-driven Temporal workflow for one long-running digital company."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


TASK_QUEUE = "digital-company-v4"
HISTORY_EVENT_LIMIT = 1000
RUN_CYCLE_LIMIT = 250
WAITING_STATUSES = {
    "waiting_for_approval", "waiting_for_human", "paused", "stopped",
    "error", "failed",
}


def waits_for_external_signal(status: str) -> bool:
    """Return whether another company cycle needs an explicit external wake-up."""
    return status in WAITING_STATUSES


def agent_execution_key(
    company_id: str,
    agent_id: str,
    sequence: int,
    namespace: str | None = None,
) -> str:
    """Build an idempotency key that cannot collide across loop generations.

    Legacy V2 histories did not carry a namespace. Keeping that exact format
    when ``namespace`` is absent preserves deterministic replay, while all new
    V3 workflow identities explicitly use the ``v3`` activity namespace.
    """
    prefix = f"{company_id}:agent:{agent_id}"
    if namespace:
        return f"{prefix}:{namespace}:execution:{sequence}"
    return f"{prefix}:execution:{sequence}"


@workflow.defn(name="CompanyLoopWorkflowV4")
class CompanyLoopWorkflow:
    """Own durable scheduling while Activities own every side effect.

    The workflow never polls company state. Web/API mutations persist intent and
    then signal this workflow. An hourly durable timer only checks whether a
    daily stakeholder brief is due; it does not advance the company.
    """

    def __init__(self) -> None:
        self._running = False
        self._paused = False
        self._shutdown = False
        self._cycles = 0
        self._wake_count = 0
        self._last_status = "idle"
        self._last_wake_reason = "workflow_created"
        self._execution_sequence = 0
        self._run_cycles = 0

    @workflow.signal
    async def start(self, reason: str = "operator_start") -> None:
        self._running = True
        self._paused = False
        self._wake_count += 1
        self._last_wake_reason = reason

    @workflow.signal
    async def pause(self, reason: str = "operator_pause") -> None:
        self._running = False
        self._paused = True
        self._last_status = "paused"
        self._last_wake_reason = reason

    @workflow.signal
    async def resume(self, reason: str = "operator_resume") -> None:
        await self.start(reason)

    @workflow.signal
    async def wake(self, reason: str = "external_event") -> None:
        await self.start(reason)

    @workflow.signal
    async def stop(self, reason: str = "operator_stop") -> None:
        # Stop is restartable. The workflow remains alive and consumes no model
        # calls until a later start signal arrives.
        self._running = False
        self._paused = False
        self._last_status = "stopped"
        self._last_wake_reason = reason

    @workflow.signal
    async def shutdown(self, reason: str = "company_deleted") -> None:
        self._shutdown = True
        self._running = False
        self._last_wake_reason = reason

    @workflow.query
    def state(self) -> dict:
        return {
            "running": self._running,
            "paused": self._paused,
            "shutdown": self._shutdown,
            "cycles": self._cycles,
            "wake_count": self._wake_count,
            "last_status": self._last_status,
            "last_wake_reason": self._last_wake_reason,
        }

    @workflow.run
    async def run(self, input_value: str | dict) -> dict:
        if isinstance(input_value, dict):
            company_id = input_value["company_id"]
            self._running = bool(input_value.get("running", False))
            self._execution_sequence = int(input_value.get("execution_sequence", 0))
            self._cycles = int(input_value.get("cycles", 0))
            self._wake_count = int(input_value.get("wake_count", 0))
            self._last_status = str(input_value.get("last_status", "idle"))
            self._last_wake_reason = str(input_value.get("last_wake_reason", "continued_as_new"))
        else:
            company_id = input_value

        while not self._shutdown:
            if not self._running or self._paused:
                try:
                    await workflow.wait_condition(
                        lambda: (self._running and not self._paused) or self._shutdown,
                        timeout=timedelta(hours=1),
                    )
                except asyncio.TimeoutError:
                    await workflow.execute_activity(
                        "send_company_brief",
                        company_id,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                    self._continue_if_history_is_large(company_id)
                    continue
                if self._shutdown:
                    break

            result = await workflow.execute_activity(
                "advance_company",
                {"company_id": company_id,
                 "execution_key": f"{company_id}:execution:{self._execution_sequence + 1}"},
                start_to_close_timeout=timedelta(minutes=10),
                schedule_to_start_timeout=timedelta(minutes=2),
                schedule_to_close_timeout=timedelta(minutes=35),
                heartbeat_timeout=timedelta(seconds=45),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(minutes=1),
                ),
            )
            self._cycles += 1
            self._run_cycles += 1
            self._execution_sequence += 1
            self._last_status = str(result.get("status", "unknown"))
            if waits_for_external_signal(self._last_status):
                self._running = False
            else:
                await workflow.sleep(1)

            self._continue_if_history_is_large(company_id)

        return {"status": "shutdown", "cycles": self._cycles}

    def _continue_if_history_is_large(self, company_id: str) -> None:
        """Bound history for both busy and months-long idle companies."""
        info = workflow.info()
        if (
            self._run_cycles < RUN_CYCLE_LIMIT
            and info.get_current_history_length() < HISTORY_EVENT_LIMIT
            and not info.is_continue_as_new_suggested()
        ):
            return
        workflow.continue_as_new({
            "company_id": company_id,
            "running": self._running and not self._paused,
            "execution_sequence": self._execution_sequence,
            "cycles": self._cycles,
            "wake_count": self._wake_count,
            "last_status": self._last_status,
            "last_wake_reason": self._last_wake_reason,
        })


@workflow.defn(name="AgentLoopWorkflowV2")
class AgentLoopWorkflow:
    """One durable, independently controlled loop for one configured agent.

    Company workflows remain for backward compatibility. New agent instances
    never share their running flag, sequence, approval wait, or retry identity
    with another agent in the same organization.
    """

    def __init__(self) -> None:
        self._running = False
        self._paused = False
        self._shutdown = False
        self._cycles = 0
        self._sequence = 0
        self._run_cycles = 0
        self._last_status = "idle"
        self._last_wake_reason = "workflow_created"
        self._next_wake_at = None
        self._execution_namespace = None

    @workflow.signal
    async def start(self, reason: str = "operator_start") -> None:
        self._running = True
        self._paused = False
        self._last_wake_reason = reason
        self._next_wake_at = None

    @workflow.signal
    async def pause(self, reason: str = "operator_pause") -> None:
        self._running = False
        self._paused = True
        self._last_status = "paused"
        self._last_wake_reason = reason
        self._next_wake_at = None

    @workflow.signal
    async def resume(self, reason: str = "operator_resume") -> None:
        await self.start(reason)

    @workflow.signal
    async def wake(self, reason: str = "external_event") -> None:
        await self.start(reason)

    @workflow.signal
    async def stop(self, reason: str = "operator_stop") -> None:
        self._running = False
        self._paused = False
        self._last_status = "stopped"
        self._last_wake_reason = reason
        self._next_wake_at = None

    @workflow.signal
    async def shutdown(self, reason: str = "agent_deleted") -> None:
        self._shutdown = True
        self._running = False
        self._last_wake_reason = reason

    @workflow.query
    def state(self) -> dict:
        return {
            "running": self._running,
            "paused": self._paused,
            "shutdown": self._shutdown,
            "cycles": self._cycles,
            "last_status": self._last_status,
            "last_wake_reason": self._last_wake_reason,
            "next_wake_at": self._next_wake_at,
        }

    @workflow.run
    async def run(self, input_value: dict) -> dict:
        company_id = input_value["company_id"]
        agent_id = input_value["agent_id"]
        self._running = bool(input_value.get("running", False))
        self._sequence = int(input_value.get("execution_sequence", 0))
        self._cycles = int(input_value.get("cycles", 0))
        self._last_status = str(input_value.get("last_status", "idle"))
        self._last_wake_reason = str(input_value.get("last_wake_reason", "workflow_created"))
        self._next_wake_at = input_value.get("next_wake_at")
        self._execution_namespace = input_value.get("execution_namespace")

        while not self._shutdown:
            if not self._running or self._paused:
                await workflow.wait_condition(
                    lambda: (self._running and not self._paused) or self._shutdown,
                )
                if self._shutdown:
                    break

            result = await workflow.execute_activity(
                "advance_agent",
                {
                    "company_id": company_id,
                    "agent_id": agent_id,
                    "execution_key": agent_execution_key(
                        company_id,
                        agent_id,
                        self._sequence + 1,
                        self._execution_namespace,
                    ),
                },
                start_to_close_timeout=timedelta(minutes=10),
                schedule_to_start_timeout=timedelta(minutes=2),
                schedule_to_close_timeout=timedelta(minutes=35),
                heartbeat_timeout=timedelta(seconds=45),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(minutes=1),
                ),
            )
            self._cycles += 1
            self._run_cycles += 1
            self._sequence += 1
            self._last_status = str(result.get("status", "unknown"))
            if self._last_status == "sleeping":
                # STOP from a scheduled content planner means "no more useful
                # work this shift". Keep the workflow alive and wake it after
                # the durable timer, unless an operator signal supersedes it.
                self._running = False
                self._next_wake_at = result.get("next_wake_at")
                delay = max(60, int(result.get("wake_after_seconds", 86400)))
                try:
                    await workflow.wait_condition(
                        lambda: self._running or self._shutdown,
                        timeout=timedelta(seconds=delay),
                    )
                except asyncio.TimeoutError:
                    if self._last_status == "sleeping" and not self._paused and not self._shutdown:
                        self._running = True
                        self._last_wake_reason = "scheduled_wake"
                        self._next_wake_at = None
            elif waits_for_external_signal(self._last_status):
                self._running = False
            else:
                await workflow.sleep(1)

            info = workflow.info()
            if (
                self._run_cycles >= RUN_CYCLE_LIMIT
                or info.get_current_history_length() >= HISTORY_EVENT_LIMIT
                or info.is_continue_as_new_suggested()
            ):
                next_input = {
                    "company_id": company_id,
                    "agent_id": agent_id,
                    "running": self._running and not self._paused,
                    "execution_sequence": self._sequence,
                    "cycles": self._cycles,
                    "last_status": self._last_status,
                    "last_wake_reason": self._last_wake_reason,
                    "next_wake_at": self._next_wake_at,
                }
                # Preserve the exact V2 Continue-As-New payload for legacy
                # histories; only V3 identities carry the namespace field.
                if self._execution_namespace:
                    next_input["execution_namespace"] = self._execution_namespace
                workflow.continue_as_new(next_input)

        return {"status": "shutdown", "cycles": self._cycles}
