from pathlib import Path

from digital_company.integration_connectors import secret_name
from digital_company.runtime_secrets import save_secret
from digital_company.store import CompanyStore
from digital_company.wordpress_plugin import WordPressPluginRuntime, markdown_to_html


def configured_runtime(tmp_path: Path, monkeypatch) -> tuple[CompanyStore, dict]:
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path / "runtime-data"))
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Publish accurate legal content", 0)
    save_secret(secret_name("wordpress-gk", "username"), "api-user")
    save_secret(secret_name("wordpress-gk", "password"), "application-password")
    store.upsert_integration_connection({
        "id": "wordpress-gk", "name": "G&K WordPress", "adapter": "http_basic",
        "provider": "WordPress", "location": "cloud",
        "base_url": "https://gkadvokati.com/wp-json/wp/v2",
        "capabilities": [
            "wordpress.posts.read", "wordpress.posts.write_drafts", "wordpress.posts.publish",
        ],
        "config": {}, "enabled": True,
    })
    grant = {
        "connection_id": "wordpress-gk",
        "permissions": ["read_posts", "write_drafts", "publish_posts"],
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
        return {"id": 42, "status": "draft", "slug": "unpaid-wages", "link": "https://gkadvokati.com/?p=42"}

    monkeypatch.setattr(runtime, "_request", fake_request)
    draft = {
        "task_id": "content-task-1", "path": "content/drafts/unpaid-wages.md",
        "title": "Employment law article", "content": "# Unpaid wages\n\nVerified article.",
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
