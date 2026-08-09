from pathlib import Path
from types import SimpleNamespace
import os

from digital_company.store import CompanyStore
from digital_company import web


def configured_store(tmp_path: Path, mode: str = "local") -> CompanyStore:
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Test runtime readiness", 1000)
    store.set_model_settings(mode, "deepseek-company:8b")
    return store


def stub_services(monkeypatch, models=None):
    monkeypatch.setattr(web, "registry", SimpleNamespace(
        worker_status=lambda: {"status": "online"}
    ))
    def ready(connection, verify_model=False):
        if connection and connection["location"] == "local":
            ok = connection["model"] in (models or [])
            return ok, "installed" if ok else "model is not installed"
        ok = bool(os.getenv("OPENAI_API_KEY"))
        return ok, "ready" if ok else "credential missing"
    monkeypatch.setattr(web, "connection_ready", ready)
    monkeypatch.setattr(web, "browser_health", lambda: {"status": "ok", "browser": "chromium"})


def test_local_preflight_requires_exact_installed_model(tmp_path, monkeypatch):
    store = configured_store(tmp_path)
    stub_services(monkeypatch, ["qwen3:8b"])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = web.runtime_preflight(store)
    assert result["ready"] is False
    assert "not installed" in result["blockers"][0]


def test_local_preflight_allows_no_cloud_key(tmp_path, monkeypatch):
    store = configured_store(tmp_path)
    stub_services(monkeypatch, ["deepseek-company:8b"])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = web.runtime_preflight(store)
    assert result["ready"] is True
    assert next(x for x in result["checks"] if x["id"] == "cloud_connection")["status"] == "warn"


def test_cloud_preflight_requires_key_but_not_ollama(tmp_path, monkeypatch):
    store = configured_store(tmp_path, "cloud")
    stub_services(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert web.runtime_preflight(store)["ready"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    assert web.runtime_preflight(store)["ready"] is True
