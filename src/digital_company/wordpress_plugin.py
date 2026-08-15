"""WordPress REST execution for the reusable content capability plugin."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import PurePosixPath

from digital_company.integration_connectors import secret_name
from digital_company.runtime_secrets import get_secret


class WordPressPluginRuntime:
    """Create/update one deterministic draft and publish that same post later."""

    def __init__(self, store, grant: dict, execution_key: str) -> None:
        if not grant.get("connection_id"):
            raise ValueError("WordPress REST execution requires a plugin connection")
        self.store = store
        self.grant = grant
        self.execution_key = execution_key
        self.connection = store.get_integration_connection(grant["connection_id"])
        if self.connection["status"] != "ready":
            raise RuntimeError(f"WordPress connection is not ready: {self.connection['status']}")

    def save_draft(self, draft: dict) -> dict:
        slug = self._slug(draft["path"])
        title = self._title(draft["content"], draft["title"])
        content = markdown_to_html(draft["content"])
        frozen = {
            "slug": slug, "title": title, "status": "draft",
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "source_task_id": draft["task_id"],
        }
        operation = self.store.prepare_integration_operation(
            self.execution_key + ":wordpress:draft:" + slug,
            self.connection["id"], "wordpress.posts.write_drafts",
            "POST", "/posts", frozen,
        )
        cached = self._cached(operation)
        if cached is not None:
            return {**cached, "cached": True}
        try:
            existing = self._find(slug)
            payload = {"title": title, "slug": slug, "content": content, "status": "draft"}
            response = self._request(
                "POST", f"/posts/{existing['id']}" if existing else "/posts", payload,
            )
            result = self._safe_post_result(response, source_task_id=draft["task_id"])
            return self.store.complete_integration_operation(operation["execution_key"], result)
        except Exception as exc:
            self.store.fail_integration_operation(operation["execution_key"], str(exc))
            raise

    def publish(self, draft: dict) -> dict:
        slug = self._slug(draft["path"])
        operation = self.store.prepare_integration_operation(
            self.execution_key + ":wordpress:publish:" + slug,
            self.connection["id"], "wordpress.posts.publish",
            "POST", f"/posts/by-slug/{slug}/publish",
            {"slug": slug, "source_task_id": draft["task_id"], "status": "publish"},
        )
        cached = self._cached(operation)
        if cached is not None:
            return {**cached, "cached": True}
        try:
            existing = self._find(slug)
            if not existing:
                raise RuntimeError(f"WordPress draft {slug!r} does not exist")
            response = self._request("POST", f"/posts/{existing['id']}", {"status": "publish"})
            result = self._safe_post_result(response, source_task_id=draft["task_id"])
            return self.store.complete_integration_operation(operation["execution_key"], result)
        except Exception as exc:
            self.store.fail_integration_operation(operation["execution_key"], str(exc))
            raise

    def _find(self, slug: str) -> dict | None:
        query = urllib.parse.urlencode({"slug": slug, "context": "edit", "status": "any"})
        result = self._request("GET", f"/posts?{query}")
        return result[0] if isinstance(result, list) and result else None

    def _request(self, method: str, path: str, payload: dict | None = None):
        if not path.startswith("/") or "://" in path:
            raise ValueError("WordPress path must be relative")
        url = self.connection["base_url"].rstrip("/") + path
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            url, data=body, method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        adapter = self.connection["adapter"]
        if adapter == "http_basic":
            username = get_secret(secret_name(self.connection["id"], "username")) or ""
            password = get_secret(secret_name(self.connection["id"], "password")) or ""
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            request.add_header("Authorization", "Basic " + encoded)
        elif adapter == "http_bearer":
            token = get_secret(secret_name(self.connection["id"], "token")) or ""
            request.add_header("Authorization", "Bearer " + token)
        elif adapter == "http_api_key":
            key = get_secret(secret_name(self.connection["id"], "api_key")) or ""
            header = self.connection.get("config", {}).get("api_key_header", "X-API-Key")
            request.add_header(header, key)
        else:
            raise ValueError(f"Unsupported WordPress connection adapter: {adapter}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"WordPress REST returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("WordPress REST connection failed") from exc

    @staticmethod
    def _cached(operation: dict) -> dict | None:
        if operation.get("status") != "completed" or not operation.get("response_json"):
            return None
        return json.loads(operation["response_json"])

    @staticmethod
    def _safe_post_result(response: dict, source_task_id: str) -> dict:
        return {
            "post_id": response.get("id"),
            "status": response.get("status"),
            "link": response.get("link"),
            "slug": response.get("slug"),
            "source_task_id": source_task_id,
        }

    @staticmethod
    def _slug(path: str) -> str:
        stem = PurePosixPath(path).stem.lower()
        value = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
        return (value or "telekt-content")[:120]

    @staticmethod
    def _title(content: str, fallback: str) -> str:
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return (match.group(1).strip() if match else fallback)[:200]


def markdown_to_html(value: str) -> str:
    """Convert the safe subset produced by the content agent into WordPress HTML."""
    blocks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(list_items) + "</ul>")
            list_items.clear()

    for raw in value.splitlines():
        line = raw.strip()
        if not line:
            flush_list()
            continue
        escaped = html.escape(line)
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
        elif line.startswith(("- ", "* ")):
            list_items.append("<li>" + html.escape(line[2:]) + "</li>")
        else:
            flush_list()
            blocks.append("<p>" + escaped + "</p>")
    flush_list()
    return "\n".join(blocks)
