from datetime import UTC, datetime, timedelta
from pathlib import Path

from digital_company.store import CompanyStore


def test_operations_projection_summarizes_model_and_task_events(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a product", 1000)
    store.audit("model.started", {"run_id": "run-1", "role": "research", "provider": "local"})
    store.audit(
        "model.structured_output_error",
        {
            "run_id": "run-1",
            "role": "research",
            "provider": "local",
            "attempt": 1,
        },
    )
    store.audit("model.cloud_fallback", {"run_id": "run-1", "role": "research"})
    store.audit(
        "model.succeeded",
        {
            "run_id": "run-1",
            "role": "research",
            "provider": "cloud_fallback",
            "latency_ms": 240,
        },
    )

    operations = store.operations_data()
    assert operations["active_model_run"] is None
    assert operations["model"]["successful_calls"] == 1
    assert operations["model"]["structured_errors"] == 1
    assert operations["model"]["fallbacks"] == 1
    assert operations["model"]["average_latency_ms"] == 240
    assert operations["model"]["provider_counts"] == {"cloud_fallback": 1}


def test_operations_projection_shows_unfinished_model_run(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a product", 1000)
    store.audit("model.started", {"run_id": "run-active", "role": "ceo", "provider": "local"})
    active = store.operations_data()["active_model_run"]
    assert active["payload"]["role"] == "ceo"


def test_operations_hides_stale_unfinished_model_run(tmp_path: Path, monkeypatch):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a company", 1000)
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    store.db.execute(
        "INSERT INTO audit_events VALUES(?,?,?,?)",
        ("old-event", "model.started", '{"run_id":"orphan","role":"ceo","provider":"local"}', old),
    )
    store.db.commit()
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "240")
    assert store.operations_data()["active_model_run"] is None


def test_operations_exposes_actionable_terminal_recovery(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a company", 1000)
    store.set_control("error", "TimeoutError: provider timed out")
    store.audit(
        "temporal.activity_failed",
        {
            "execution_key": "company:execution:7",
            "error": "TimeoutError: provider timed out",
        },
    )

    recovery = store.operations_data()["recovery"]

    assert recovery["required"] is True
    assert recovery["action"] == "retry_from_checkpoint"
    assert recovery["incident"]["event_type"] in {"runtime.error", "temporal.activity_failed"}
