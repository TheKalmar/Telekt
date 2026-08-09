import os

import pytest

from digital_company import runtime_secrets


def test_runtime_secret_is_write_only_and_reloaded(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime_secrets.save_secret("OPENAI_API_KEY", "sk-test-value")
    assert runtime_secrets.secret_status() == {"OPENAI_API_KEY": True}
    assert "sk-test-value" not in repr(runtime_secrets.secret_status())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime_secrets.apply_runtime_secrets()
    assert os.environ["OPENAI_API_KEY"] == "sk-test-value"


def test_runtime_secret_rejects_unknown_names(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="Unsupported secret"):
        runtime_secrets.save_secret("UNSUPPORTED", "value")
