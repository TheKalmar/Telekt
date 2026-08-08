"""Durable polling worker for autonomous company cycles.

The web process only persists operator intent. This worker observes canonical
state and executes one bounded cycle at a time. Because the intent is stored in
SQLite, restarting either container does not lose a running company.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv

from digital_company.orchestrator import CompanyOrchestrator
from digital_company.registry import CompanyRegistry


def run_once(registry: CompanyRegistry, owner: str) -> int:
    """Advance every runnable company by at most one atomic agent cycle."""
    advanced = 0
    for company in registry.list():
        company_id = company["id"]
        store = registry.store_for(company_id)
        if store.get_control()["state"] != "running":
            continue
        if not registry.claim_work(company_id, owner):
            continue
        try:
            result = CompanyOrchestrator(
                store, registry.artifacts_for(company_id), company_id=company_id
            ).run(max_cycles=1)
            status = result["status"]
            if status == "waiting_for_approval":
                store.set_control("waiting_approval", "Human decision required")
            elif status in {"stopped", "paused"}:
                store.set_control(status, result.get("reason"))
            elif status == "failed":
                store.set_control("error", "Approved task failed policy revalidation")
            advanced += 1
        except Exception as exc:
            store.set_control("error", f"{type(exc).__name__}: {exc}")
        finally:
            registry.release_work(company_id, owner)
    return advanced


def main() -> None:
    """Continuously recover and advance companies whose durable state is running."""
    root = Path.cwd()
    load_dotenv(root / ".env.local")
    state_dir = Path(os.getenv("COMPANY_DATA_DIR", str(root / ".company"))).expanduser().resolve()
    owner = os.getenv("WORKER_ID", "worker-1")
    poll_seconds = max(0.25, float(os.getenv("WORKER_POLL_SECONDS", "2")))
    registry = CompanyRegistry(state_dir)
    while True:
        registry.heartbeat_worker(owner)
        advanced = run_once(registry, owner)
        if not advanced:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
