from pathlib import Path

from digital_company.integration_connectors import secret_name
from digital_company.runtime_secrets import save_secret
from digital_company.store import CompanyStore
from digital_company.wordpress_plugin import WordPressPluginRuntime, markdown_to_html
from tests.test_content_quality import package


def configured_runtime(tmp_path: Path, monkeypatch) -> tuple[CompanyStore, dict]:
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path / "runtime-data"))
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Publish accurate legal content", 0)
    save_secret(secret_name("wordpress-gk", "username"), "api-user")
    save_secret(secret_name("wordpress-gk", "password"), "application-password")
    store.upsert_integration_connection(
        {
            "id": "wordpress-gk",
            "name": "G&K WordPress",
            "adapter": "http_basic",
            "provider": "WordPress",
            "location": "cloud",
            "base_url": "https://gkadvokati.com/wp-json/wp/v2",
            "capabilities": [
                "wordpress.posts.read",
                "wordpress.posts.write_drafts",
                "wordpress.posts.publish",
                "wordpress.terms.manage",
                "wordpress.media.upload",
                "wordpress.seo.write",
            ],
            "config": {},
            "enabled": True,
        }
    )
    grant = {
        "connection_id": "wordpress-gk",
        "permissions": [
            "read_posts",
            "write_drafts",
            "manage_terms",
            "upload_media",
            "write_seo_metadata",
            "publish_posts",
        ],
        "status": "enabled",
    }
    return store, grant


def test_wordpress_draft_is_idempotent_across_activity_retry(tmp_path: Path, monkeypatch):
    store, grant = configured_runtime(tmp_path, monkeypatch)
    runtime = WordPressPluginRuntime(store, grant, "agent-cycle-1")
    calls = []

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET":
            return []
        return {
            "id": 42,
            "status": "draft",
            "slug": "unpaid-wages",
            "link": "https://gkadvokati.com/?p=42",
        }

    monkeypatch.setattr(runtime, "_request", fake_request)
    draft = {
        "task_id": "content-task-1",
        "path": "content/drafts/unpaid-wages.md",
        "title": "Employment law article",
        "content": "# Unpaid wages\n\nVerified article.",
    }

    first = runtime.save_draft(draft)
    second = runtime.save_draft(draft)

    assert first["post_id"] == 42
    assert second["cached"] is True
    assert len(calls) == 2
    assert calls[1][2]["status"] == "draft"


def test_markdown_is_escaped_before_wordpress_html():
    rendered = markdown_to_html("# Safe title\n\n<script>alert(1)</script>\n- item")

    assert "<h1>Safe title</h1>" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<li>item</li>" in rendered


def test_structured_package_sets_terms_seo_and_featured_image(tmp_path: Path, monkeypatch):
    store, grant = configured_runtime(tmp_path, monkeypatch)

    class FakeImageRuntime:
        def generate(self, prompt):
            assert "employment law" in prompt.lower()
            return b"png-data", "image/png"

    runtime = WordPressPluginRuntime(store, grant, "agent-cycle-structured", FakeImageRuntime())
    payloads = []
    term_ids = {"categories": 11, "tags": 21}

    def fake_request(method, path, payload=None):
        payloads.append((method, path, payload))
        if path.startswith("/posts?"):
            return []
        if path.startswith(("/categories?", "/tags?")):
            return []
        if method == "POST" and path in {"/categories", "/tags"}:
            value = term_ids[path.strip("/")]
            term_ids[path.strip("/")] += 1
            return {"id": value, "name": payload["name"]}
        if path.startswith("/media?"):
            return []
        if method == "POST" and path == "/media/55":
            return {
                "id": 55,
                "source_url": "https://gkadvokati.com/image.png",
                "alt_text": payload["alt_text"],
            }
        if method == "POST" and path == "/posts":
            assert payload["categories"] == [11]
            assert payload["tags"] == [21, 22]
            assert payload["featured_media"] == 55
            assert payload["aioseo_meta_data"]["description"].startswith("Neisplaćena plata")
            return {
                "id": 42,
                "status": "draft",
                "slug": package().slug,
                "link": "https://gkadvokati.com/?p=42",
            }
        if path == "/posts/42?context=edit":
            return {
                "id": 42,
                "status": "draft",
                "slug": package().slug,
                "link": "https://gkadvokati.com/?p=42",
                "aioseo_meta_data": {"title": package().seo_title},
            }
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(runtime, "_request", fake_request)
    monkeypatch.setattr(
        runtime,
        "_request_binary",
        lambda method, path, data, content_type, filename: {"id": 55},
    )
    value = package()
    result = runtime.save_draft(
        {
            "task_id": "content-task-2",
            "path": f"content/drafts/{value.slug}.html",
            "title": value.title,
            "content": value.html_content,
            "content_package": value.model_dump(mode="json"),
            "quality_report": {"score": 92, "maximum": 100, "target": 70},
        }
    )

    assert result["categories"] == ["Radno pravo"]
    assert result["tags"] == ["plata", "radni odnos"]
    assert result["featured_image"]["source_url"].endswith("image.png")
    assert result["aioseo"]["title"] == value.seo_title
