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
from digital_company.content_rendering import sanitize_article_html
from digital_company.models import ContentPackage
from digital_company.runtime_secrets import get_secret


class WordPressPluginRuntime:
    """Create/update one deterministic draft and publish that same post later."""

    def __init__(self, store, grant: dict, execution_key: str, image_runtime=None) -> None:
        if not grant.get("connection_id"):
            raise ValueError("WordPress REST execution requires a plugin connection")
        self.store = store
        self.grant = grant
        self.execution_key = execution_key
        self.connection = store.get_integration_connection(grant["connection_id"])
        self.image_runtime = image_runtime
        if self.connection["status"] != "ready":
            raise RuntimeError(f"WordPress connection is not ready: {self.connection['status']}")

    def save_draft(self, draft: dict) -> dict:
        package_value = draft.get("content_package")
        package = ContentPackage.model_validate(package_value) if package_value else None
        slug = package.slug if package else self._slug(draft["path"])
        title = package.title if package else self._title(draft["content"], draft["title"])
        content = (
            sanitize_article_html(package.html_content)
            if package else markdown_to_html(draft["content"])
        )
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
            term_names = {"categories": [], "tags": []}
            image = None
            if package:
                self._require("manage_terms", "wordpress.terms.manage")
                self._require("write_seo_metadata", "wordpress.seo.write")
                term_names = {
                    "categories": list(dict.fromkeys(package.categories)),
                    "tags": list(dict.fromkeys(package.tags)),
                }
                payload.update({
                    "excerpt": package.excerpt,
                    "categories": [self._resolve_term("categories", name) for name in term_names["categories"]],
                    "tags": [self._resolve_term("tags", name) for name in term_names["tags"]],
                    "aioseo_meta_data": {
                        "title": package.seo_title,
                        "description": package.meta_description,
                    },
                })
                if package.featured_image and self.image_runtime:
                    self._require("upload_media", "wordpress.media.upload")
                    image = self._ensure_featured_image(package)
                    payload["featured_media"] = image["id"]
            response = self._request(
                "POST", f"/posts/{existing['id']}" if existing else "/posts", payload,
            )
            # Fetch edit context because many SEO plugins expose computed metadata
            # only on the canonical post response, not the mutation response.
            verified = (
                self._request("GET", f"/posts/{response['id']}?context=edit")
                if package else response
            )
            if not isinstance(verified, dict):
                verified = response
            result = self._safe_post_result(
                verified, source_task_id=draft["task_id"], term_names=term_names, image=image,
            )
            result["telekt_quality"] = draft.get("quality_report")
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

    def _require(self, permission: str, capability: str) -> None:
        if permission not in self.grant.get("permissions", []):
            raise RuntimeError(f"WordPress plugin grant is missing permission: {permission}")
        if capability not in self.connection.get("capabilities", []):
            raise RuntimeError(f"WordPress connection is missing capability: {capability}")

    def _resolve_term(self, kind: str, name: str) -> int:
        query = urllib.parse.urlencode({"search": name, "per_page": 100, "context": "edit"})
        values = self._request("GET", f"/{kind}?{query}")
        expected = name.strip().casefold()
        for value in values if isinstance(values, list) else []:
            if str(value.get("name", "")).strip().casefold() == expected:
                return int(value["id"])
        created = self._request("POST", f"/{kind}", {"name": name.strip()})
        return int(created["id"])

    def _ensure_featured_image(self, package: ContentPackage) -> dict:
        spec = package.featured_image
        slug = self._slug(spec.filename)
        query = urllib.parse.urlencode({"slug": slug, "context": "edit", "per_page": 1})
        existing = self._request("GET", f"/media?{query}")
        if isinstance(existing, list) and existing:
            item = existing[0]
            return {"id": item["id"], "source_url": item.get("source_url"), "alt_text": spec.alt_text}
        data, content_type = self.image_runtime.generate(spec.prompt)
        filename = slug + (".jpg" if content_type == "image/jpeg" else ".png")
        uploaded = self._request_binary("POST", "/media", data, content_type, filename)
        updated = self._request("POST", f"/media/{uploaded['id']}", {
            "alt_text": spec.alt_text, "caption": package.title,
        })
        return {
            "id": updated["id"], "source_url": updated.get("source_url"),
            "alt_text": updated.get("alt_text") or spec.alt_text,
        }

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

    def _request_binary(
        self, method: str, path: str, payload: bytes, content_type: str, filename: str,
    ) -> dict:
        if not path.startswith("/") or "://" in path:
            raise ValueError("WordPress path must be relative")
        request = urllib.request.Request(
            self.connection["base_url"].rstrip("/") + path, data=payload, method=method,
            headers={
                "Accept": "application/json", "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
        self._authorize_request(request)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"WordPress media upload returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("WordPress media upload failed") from exc

    def _authorize_request(self, request: urllib.request.Request) -> None:
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

    @staticmethod
    def _cached(operation: dict) -> dict | None:
        if operation.get("status") != "completed" or not operation.get("response_json"):
            return None
        return json.loads(operation["response_json"])

    @staticmethod
    def _safe_post_result(
        response: dict, source_task_id: str, term_names: dict | None = None,
        image: dict | None = None,
    ) -> dict:
        aioseo = response.get("aioseo_meta_data") or response.get("aioseo_head_json") or {}
        return {
            "post_id": response.get("id"),
            "status": response.get("status"),
            "link": response.get("link"),
            "slug": response.get("slug"),
            "source_task_id": source_task_id,
            "categories": (term_names or {}).get("categories", []),
            "tags": (term_names or {}).get("tags", []),
            "featured_image": image,
            "aioseo": aioseo if isinstance(aioseo, dict) else {},
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
