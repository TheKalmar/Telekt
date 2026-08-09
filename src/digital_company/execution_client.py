"""Client for the fixed internal execution-runtime boundary."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class ExecutionRuntimeError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ExecutionRuntimeClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or os.getenv("EXECUTION_RUNTIME_URL", "")).rstrip("/")
        self.token = token if token is not None else os.getenv("EXECUTION_RUNTIME_TOKEN", "")
        if not self.base_url:
            raise ValueError("EXECUTION_RUNTIME_URL is not configured")

    def _request(self, method: str, path: str, payload: dict | None = None, timeout: int = 45) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Telekt-Execution-Token"] = self.token
        request = urllib.request.Request(
            self.base_url + path, data=body, method=method, headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExecutionRuntimeError(exc.code, detail) from exc
        except Exception as exc:
            raise ExecutionRuntimeError(
                503, f"Execution runtime unavailable: {type(exc).__name__}"
            ) from exc

    def checkpoint(
        self, company_id: str, task_id: str, artifact_path: str, content: str,
        idempotency_key: str,
    ) -> dict:
        return self._request(
            "POST", f"/workspaces/{company_id}/repository/checkpoints",
            {
                "idempotency_key": idempotency_key,
                "files": [{"path": artifact_path, "content": content}],
                "branch": f"agent/{task_id[:12]}",
                "message": f"Checkpoint task {task_id[:12]}",
            },
        )

    def health(self) -> dict:
        return self._request("GET", "/health", timeout=3)
