"""Signal-driven Temporal workflow for one long-running digital company."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


TASK_QUEUE = "digital-company-v3"
WAITING_STATUSES = {
    "waiting_for_approval", "waiting_for_human", "paused", "stopped",
    "error", "failed",
}


def waits_for_external_signal(status: str) -> bool:
    """Return whether another company cycle needs an explicit external wake-up."""
    return status in WAITING_STATUSES


@workflow.defn(name="CompanyLoopWorkflowV3")
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
                    continue
                if self._shutdown:
                    break

            result = await workflow.execute_activity(
                "advance_company",
                {"company_id": company_id,
                 "execution_key": f"{company_id}:execution:{self._execution_sequence + 1}"},
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(minutes=1),
                ),
            )
            self._cycles += 1
            self._execution_sequence += 1
            self._last_status = str(result.get("status", "unknown"))
            if waits_for_external_signal(self._last_status):
                self._running = False
            else:
                await workflow.sleep(1)

            if self._cycles >= 500:
                workflow.continue_as_new({
                    "company_id": company_id,
                    "running": self._running and not self._paused,
                    "execution_sequence": self._execution_sequence,
                })

        return {"status": "shutdown", "cycles": self._cycles}
