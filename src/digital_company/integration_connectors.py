"""Provider-neutral integration transport definitions and validation."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse


ADAPTERS = {
    "smtp": {
        "label": "SMTP mailbox",
        "credential_fields": ["username", "password"],
        "supports_idempotency_header": False,
    },
    "http_basic": {
        "label": "HTTP API · Basic authentication",
        "credential_fields": ["username", "password"],
        "supports_idempotency_header": False,
    },
    "http_bearer": {
        "label": "HTTP API · Bearer token",
        "credential_fields": ["token"],
        "supports_idempotency_header": True,
    },
    "http_api_key": {
        "label": "HTTP API · API-key header",
        "credential_fields": ["api_key"],
        "supports_idempotency_header": True,
    },
    "oauth2_client_credentials": {
        "label": "OAuth 2 · Client credentials",
        "credential_fields": ["client_id", "client_secret"],
        "supports_idempotency_header": True,
    },
    "oauth2_authorization_code": {
        "label": "OAuth 2 · User authorization",
        "credential_fields": ["client_id", "client_secret"],
        "supports_idempotency_header": True,
        "requires_human_authorization": True,
    },
    "webhook": {
        "label": "HTTPS webhook",
        "credential_fields": ["signing_secret"],
        "supports_idempotency_header": True,
    },
}

CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,79}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def validate_connection(value: dict) -> dict:
    adapter = str(value.get("adapter", ""))
    if adapter not in ADAPTERS:
        raise ValueError("Unsupported integration adapter")
    name = str(value.get("name", "")).strip()
    provider = str(value.get("provider", "")).strip()
    connection_id = str(value.get("id", "")).strip()
    location = str(value.get("location", "cloud"))
    base_url = str(value.get("base_url", "")).strip().rstrip("/")
    if not name or len(name) > 100 or not provider or len(provider) > 100:
        raise ValueError("Connection name and provider label are required")
    if not ID_RE.fullmatch(connection_id):
        raise ValueError("Invalid integration connection ID")
    if location not in {"local", "cloud"}:
        raise ValueError("Connection location must be local or cloud")
    parsed = urlparse(base_url)
    allowed_schemes = (
        {"smtp", "smtps"} if adapter == "smtp"
        else {"http", "https"} if location == "local"
        else {"https"}
    )
    if parsed.scheme not in allowed_schemes or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Connection base URL is invalid for its location")
    capabilities = list(dict.fromkeys(str(item).strip() for item in value.get("capabilities", [])))
    if not capabilities or any(not CAPABILITY_RE.fullmatch(item) for item in capabilities):
        raise ValueError("At least one valid capability is required")
    config = dict(value.get("config") or {})
    if len(str(config)) > 4000:
        raise ValueError("Integration configuration is too large")
    return {
        "id": connection_id,
        "name": name,
        "adapter": adapter,
        "provider": provider,
        "location": location,
        "base_url": base_url,
        "capabilities": capabilities,
        "config": config,
        "enabled": bool(value.get("enabled", True)),
        "credential_fields": ADAPTERS[adapter]["credential_fields"],
    }


def secret_name(connection_id: str, field: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "_", connection_id).upper()[:40]
    digest = hashlib.sha256(connection_id.encode()).hexdigest()[:12].upper()
    return f"INTEGRATION_CONNECTION_{normalized}_{digest}_{field.upper()}"
