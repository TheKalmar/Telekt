"""Pure operational read-model projection from persisted company facts.

The write repository owns SQL queries; this module owns the interpretation of
audit events for the operations UI. Keeping that interpretation pure makes it
possible to test telemetry rules without a database or FastAPI process.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

MODEL_TERMINAL_EVENTS = {
    "model.succeeded",
    "model.failed",
    "model.fallback_failed",
    "model.abandoned",
}


def decode_audit_events(rows: Iterable[Mapping]) -> list[dict]:
    """Decode stored JSON defensively while preserving newest-first ordering."""
    events = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        events.append(
            {
                "event_type": row["event_type"],
                "created_at": str(row["created_at"]),
                "payload": payload,
            }
        )
    return events


def project_operations(
    events: list[dict],
    task_counts: Mapping[str, int],
    authorized_spend: float,
    usage: Mapping,
    *,
    stale_after_seconds: int,
    control: Mapping | None = None,
    now: datetime | None = None,
) -> dict:
    """Build the stable operations API contract from query results."""
    now = now or datetime.now(UTC)
    model_events = [event for event in events if event["event_type"].startswith("model.")]
    completed_run_ids = {
        event["payload"].get("run_id")
        for event in model_events
        if event["event_type"] in MODEL_TERMINAL_EVENTS
    }
    active = next(
        (
            event
            for event in model_events
            if event["event_type"] == "model.started"
            and event["payload"].get("run_id") not in completed_run_ids
            and _is_recent(event["created_at"], now, stale_after_seconds)
        ),
        None,
    )

    successes = [event for event in model_events if event["event_type"] == "model.succeeded"]
    latencies = sorted(
        event["payload"]["latency_ms"]
        for event in successes
        if isinstance(event["payload"].get("latency_ms"), (int, float))
    )
    provider_counts: dict[str, int] = {}
    for event in successes:
        provider = event["payload"].get("provider", "unknown")
        provider_counts[provider] = provider_counts.get(provider, 0) + 1

    usage_data = dict(usage)
    api_spend = float(usage_data.get("estimated_budget_cost") or 0)
    return {
        "active_model_run": active,
        "model": {
            "successful_calls": len(successes),
            "structured_errors": _event_count(model_events, "model.structured_output_error"),
            "provider_errors": _event_count(model_events, "model.provider_error"),
            "failed_calls": sum(
                event["event_type"] in {"model.failed", "model.fallback_failed"}
                for event in model_events
            ),
            "fallbacks": _event_count(model_events, "model.cloud_fallback"),
            "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
            "p95_latency_ms": latencies[max(0, int(len(latencies) * 0.95) - 1)]
            if latencies
            else None,
            "provider_counts": provider_counts,
            "token_usage": usage_data,
        },
        "tasks_by_status": dict(task_counts),
        "authorized_spend_eur": authorized_spend,
        "api_spend_eur": api_spend,
        "estimated_spend_eur": authorized_spend + api_spend,
        "recent_events": events[:40],
        "browser_mission": _project_browser_mission(events),
        "recovery": _project_recovery(events, control or {}),
    }


def _event_count(events: list[dict], event_type: str) -> int:
    return sum(event["event_type"] == event_type for event in events)


def _is_recent(created_at: str, now: datetime, stale_after_seconds: int) -> bool:
    """Malformed historical telemetry is ignored instead of breaking the UI."""
    try:
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (now - created).total_seconds() <= stale_after_seconds
    except (TypeError, ValueError):
        return False


def _project_browser_mission(events: list[dict]) -> dict | None:
    """Rebuild the latest browser mission from append-only audit records."""
    browser_events = [
        event for event in events if event["event_type"].startswith("browser.mission_")
    ]
    latest_start = next(
        (event for event in browser_events if event["event_type"] == "browser.mission_started"),
        None,
    )
    if not latest_start:
        return None

    task_id = latest_start["payload"].get("task_id")
    related = [event for event in browser_events if event["payload"].get("task_id") == task_id]
    terminal = next(
        (
            event
            for event in related
            if event["event_type"] in {"browser.mission_completed", "browser.mission_handoff"}
            or (
                event["event_type"] == "browser.mission_stopped"
                and event["payload"].get("status") != "step_limit"
            )
        ),
        None,
    )
    last_action = next(
        (event for event in related if event["event_type"] == "browser.mission_action"), None
    )
    last_stop = next(
        (event for event in related if event["event_type"] == "browser.mission_stopped"), None
    )
    latest_step = last_action or last_stop or {"payload": {}}
    return {
        "task_id": task_id,
        "status": (
            last_stop["payload"].get("status")
            if last_stop
            else "completed"
            if terminal and terminal["event_type"] == "browser.mission_completed"
            else "waiting_human"
            if terminal and terminal["event_type"] == "browser.mission_handoff"
            else "running"
        ),
        "objective": latest_start["payload"].get("objective"),
        "allowed_domains": latest_start["payload"].get("allowed_domains", []),
        "max_steps": latest_start["payload"].get("max_steps"),
        "step": latest_step["payload"].get(
            "step", (last_stop or {"payload": {}})["payload"].get("steps", 0)
        ),
        "last_action": last_action["payload"] if last_action else None,
        "detail": last_stop["payload"].get("summary") if last_stop else None,
        "started_at": latest_start["created_at"],
    }


def _project_recovery(events: list[dict], control: Mapping) -> dict:
    """Expose one actionable incident instead of making operators read raw audit JSON."""
    failure_types = {
        "temporal.activity_failed",
        "runtime.error",
        "task.failed",
        "model.failed",
        "model.fallback_failed",
    }
    incident = next((event for event in events if event["event_type"] in failure_types), None)
    state = str(control.get("state", "unknown"))
    required = state == "error"
    return {
        "required": required,
        "state": state,
        "detail": control.get("detail"),
        "incident": incident,
        "action": "retry_from_checkpoint" if required else None,
    }
