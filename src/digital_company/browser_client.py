"""HTTP client boundary for the fixed internal browser runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from digital_company.network_policy import normalize_http_base_url


class BrowserRuntimeError(RuntimeError):
    """Normalized browser-runtime transport or HTTP failure."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def browser_runtime_request(
    method: str,
    path: str,
    payload: dict | None = None,
    timeout: float = 40,
) -> tuple[bytes, str]:
    """Call only the configured service; request data never selects the host."""
    base = normalize_http_base_url(
        os.getenv("BROWSER_RUNTIME_URL", "http://127.0.0.1:8430"),
        allow_plain_http=True,
        field_name="Browser runtime URL",
    )
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        base + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        # The base URL is normalized above and the caller supplies only fixed API paths.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec
            return response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise BrowserRuntimeError(exc.code, detail) from exc
    except Exception as exc:
        raise BrowserRuntimeError(
            503, f"Browser runtime unavailable: {type(exc).__name__}"
        ) from exc
