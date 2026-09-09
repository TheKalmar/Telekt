"""Global registry of model connection profiles.

Profiles describe transport technology, not vendors. Model identifiers and
endpoint URLs are user data, so new hosted or local models need no code change.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from digital_company.network_policy import normalize_http_base_url

ADAPTERS = {
    "openai_responses": {"label": "Responses API", "supports_hosted_tools": True},
    "openai_compatible": {
        "label": "OpenAI-compatible Chat Completions",
        "supports_hosted_tools": False,
    },
    "litellm": {"label": "LiteLLM adapter", "supports_hosted_tools": False},
}


class ModelConnectionRegistry:
    def __init__(self, data_dir: Path | None = None):
        root = data_dir or Path(os.getenv("COMPANY_DATA_DIR", ".company"))
        self.path = root.expanduser().resolve() / "model-connections.json"

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, connections: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(connections, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def list(self) -> list[dict]:
        return self._read()

    def get(self, connection_id: str) -> dict | None:
        return next((item for item in self._read() if item["id"] == connection_id), None)

    def save(self, payload: dict) -> dict:
        adapter = payload.get("adapter", "")
        if adapter not in ADAPTERS:
            raise ValueError("Unsupported model adapter")
        name = str(payload.get("name", "")).strip()
        model = str(payload.get("model", "")).strip()
        location = payload.get("location", "cloud")
        if not name or len(name) > 100 or not model or len(model) > 200:
            raise ValueError("Connection name and model ID are required")
        if location not in {"local", "cloud"}:
            raise ValueError("Location must be local or cloud")
        connection_id = str(payload.get("id") or uuid4())
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", connection_id):
            raise ValueError("Invalid connection ID")
        base_url = str(payload.get("base_url", "")).strip().rstrip("/")
        if adapter == "openai_compatible" and not base_url:
            raise ValueError("OpenAI-compatible connections require a base URL")
        if base_url:
            base_url = normalize_http_base_url(
                base_url,
                allow_plain_http=location == "local",
                field_name="Model connection base URL",
            )
        item = {
            "id": connection_id,
            "name": name,
            "adapter": adapter,
            "location": location,
            "base_url": base_url,
            "model": model,
            "enabled": bool(payload.get("enabled", True)),
            "requires_api_key": bool(payload.get("requires_api_key", location == "cloud")),
        }
        values = self._read()
        values = [value for value in values if value["id"] != connection_id]
        values.append(item)
        self._write(values)
        return item

    def delete(self, connection_id: str) -> bool:
        values = self._read()
        remaining = [item for item in values if item["id"] != connection_id]
        if len(values) == len(remaining):
            return False
        self._write(remaining)
        return True

    def ensure_defaults(self, local_model: str, cloud_model: str) -> list[dict]:
        values = self._read()
        if values:
            return values
        self.save(
            {
                "id": "local-default",
                "name": "Local runtime",
                "adapter": "openai_compatible",
                "location": "local",
                "base_url": os.getenv("OLLAMA_BASE_URL", "http://ollama:11434/v1"),
                "model": local_model,
                "requires_api_key": False,
            }
        )
        self.save(
            {
                "id": "cloud-default",
                "name": "Cloud runtime",
                "adapter": "openai_responses",
                "location": "cloud",
                "model": cloud_model,
                "requires_api_key": True,
            }
        )
        return self._read()
