"""Deterministic Temporal workflow for one long-running digital company."""
from __future__ import annotations

from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class CompanyLoopWorkflow:
    """Durably schedule bounded company cycles; side effects stay in Activities."""

    def __init__(self) -> None:
        self._stopped = False
        self._paused = False
        self._cycles = 0

    @workflow.signal
    async def pause(self) -> None:
        self._paused = True

    @workflow.signal
    async def resume(self) -> None:
        self._paused = False

    @workflow.signal
    async def stop(self) -> None:
        self._stopped = True

    @workflow.query
    def state(self) -> dict:
        return {"paused": self._paused, "stopped": self._stopped, "cycles": self._cycles}

    @workflow.run
    async def run(self, company_id: str) -> dict:
        while not self._stopped:
            await workflow.wait_condition(lambda: not self._paused or self._stopped)
            if self._stopped:
                break
            result = await workflow.execute_activity(
                "advance_company", company_id,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            self._cycles += 1
            # Database control remains authoritative during the transition.
            # Poll slowly in wait states so dashboard approvals are discovered.
            delay = 10 if result.get("status", "").startswith("waiting") else 2
            await workflow.sleep(delay)
            if self._cycles >= 500:
                workflow.continue_as_new(company_id)
        return {"status": "stopped", "cycles": self._cycles}
