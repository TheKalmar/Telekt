from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from digital_company import execution_runtime


def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(execution_runtime, "DATA_DIR", tmp_path.resolve())
    monkeypatch.setattr(execution_runtime, "TOKEN", "test-token")
    monkeypatch.setattr(execution_runtime, "COMMANDS_ENABLED", True)
    return TestClient(execution_runtime.app, headers={
        "X-Telekt-Execution-Token": "test-token",
    })


def checkpoint_payload(key: str | None = None) -> dict:
    return {
        "idempotency_key": key or f"checkpoint-{uuid4()}",
        "files": [{"path": "mvp/index.html", "content": "<html><body>Safe</body></html>"}],
        "branch": "agent/task-123",
        "message": "Checkpoint task task-123",
    }


def test_repository_checkpoint_is_per_company_and_idempotent(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = checkpoint_payload()

    first = api.post("/workspaces/company-1/repository/checkpoints", json=payload)
    second = api.post("/workspaces/company-1/repository/checkpoints", json=payload)

    assert first.status_code == 200
    assert first.json()["status"] == "committed"
    assert first.json()["commit"]
    assert second.json()["cached"] is True
    assert (tmp_path / "company-1" / ".git").is_dir()
    assert not (tmp_path / "company-2").exists()


def test_checkpoint_rejects_key_reuse_with_different_content(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = checkpoint_payload("same-checkpoint-key")
    assert api.post("/workspaces/company-1/repository/checkpoints", json=payload).status_code == 200
    payload["files"][0]["content"] = "<html><body>Different</body></html>"

    response = api.post("/workspaces/company-1/repository/checkpoints", json=payload)

    assert response.status_code == 409


def test_runtime_rejects_traversal_and_network_git_commands(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = checkpoint_payload()
    payload["files"][0]["path"] = "../outside.py"
    assert api.post("/workspaces/company-1/repository/checkpoints", json=payload).status_code == 400

    command = {
        "idempotency_key": "dangerous-git-command",
        "executable": "git",
        "args": ["push", "origin", "main"],
        "cwd": ".",
        "timeout_seconds": 10,
    }
    assert api.post("/workspaces/company-1/commands", json=command).status_code == 400


def test_structured_command_runs_without_a_shell_and_caches_result(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    payload = {
        "idempotency_key": "python-command-checkpoint",
        "files": [{"path": "verify.py", "content": "print('runtime-ok')"}],
        "branch": "agent/python-check",
        "message": "Add deterministic verification",
    }
    assert api.post("/workspaces/company-1/repository/checkpoints", json=payload).status_code == 200
    command = {
        "idempotency_key": "python-command-execution",
        "executable": "python",
        "args": ["verify.py"],
        "cwd": ".",
        "timeout_seconds": 10,
    }

    first = api.post("/workspaces/company-1/commands", json=command)
    second = api.post("/workspaces/company-1/commands", json=command)

    assert first.json()["status"] == "completed"
    assert first.json()["stdout"].strip() == "runtime-ok"
    assert second.json()["cached"] is True


def test_runtime_requires_shared_internal_token(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    response = TestClient(execution_runtime.app).post(
        "/workspaces/company-1/repository/checkpoints", json=checkpoint_payload(),
    )
    assert response.status_code == 401


def test_general_commands_fail_closed_without_process_sandbox(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    monkeypatch.setattr(execution_runtime, "COMMANDS_ENABLED", False)
    response = api.post("/workspaces/company-1/commands", json={
        "idempotency_key": "disabled-command-check",
        "executable": "python",
        "args": ["verify.py"],
        "cwd": ".",
        "timeout_seconds": 10,
    })
    assert response.status_code == 503
