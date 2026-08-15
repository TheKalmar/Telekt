"""File-backed runtime secrets shared by the web and worker containers."""

from __future__ import annotations

import json
import os
from pathlib import Path

ALLOWED_SECRETS = {"OPENAI_API_KEY", "APPROVAL_SIGNING_SECRET"}


def _allowed(name: str) -> bool:
    return (
        name in ALLOWED_SECRETS
        or name.startswith("MODEL_CONNECTION_")
        or name.startswith("INTEGRATION_CONNECTION_")
    )


def secret_file() -> Path:
    data_dir = Path(os.getenv("COMPANY_DATA_DIR", ".company")).expanduser().resolve()
    return data_dir / "runtime-secrets.json"


def _read() -> dict[str, str]:
    path = secret_file()
    if not path.exists():
        return {}
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: value for key, value in values.items()
            if _allowed(key) and isinstance(value, str) and value}


def apply_runtime_secrets() -> None:
    """Refresh this process from the shared vault before doing model work."""
    for key, value in _read().items():
        os.environ[key] = value


def secret_status() -> dict[str, bool]:
    apply_runtime_secrets()
    return {key: bool(os.getenv(key)) for key in sorted(ALLOWED_SECRETS)}


def save_secret(name: str, value: str) -> None:
    """Atomically replace an allow-listed secret without returning its value."""
    if not _allowed(name):
        raise ValueError("Unsupported secret")
    value = value.strip()
    if not value or len(value) > 500:
        raise ValueError("Secret must contain between 1 and 500 characters")
    path = secret_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    values = _read()
    values[name] = value
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    os.environ[name] = value


def get_secret(name: str) -> str | None:
    """Read one allow-listed secret without exposing it through an API."""
    if not _allowed(name):
        return None
    return _read().get(name) or os.getenv(name)
