from pathlib import Path

import pytest

from digital_company.store import CompanyStore
from digital_company.agent_templates import action_is_allowed
from digital_company.capability_plugins import authorize_action
from digital_company.integration_connectors import secret_name
from digital_company.runtime_secrets import save_secret


def initialized_store(tmp_path: Path) -> CompanyStore:
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Operate a configurable organization", 1000, {"name": "Acme"})
    return store


def content_agent() -> dict:
    return {
        "name": "Content & SEO",
        "role": "content strategist",
        "purpose": "Grow qualified organic demand",
        "instructions": "Verify legal claims before drafting.",
        "model_connection_id": "cloud-default",
        "autonomy_mode": "governed",
        "token_limit": 100_000,
        "spend_limit_eur": 20,
        "schedule": {"kind": "daily"},
        "config": {"language": "sr"},
    }


def test_company_migration_creates_explicit_legacy_ceo(tmp_path: Path):
    store = initialized_store(tmp_path)
    agents = store.list_agents()

    assert store.schema_version() == 9
    assert agents[0]["id"] == "legacy-ceo"
    assert agents[0]["role"] == "ceo"
    assert agents[0]["agent_type"] == "ceo"


def test_agents_have_independent_models_limits_and_lifecycle(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())

    assert agent["model_connection_id"] == "cloud-default"
    assert agent["token_limit"] == 100_000
    assert agent["spend_limit_eur"] == 20
    assert store.set_agent_status(agent["id"], "running")["status"] == "running"
    with pytest.raises(KeyError):
        store.get_agent("legacy-ceo")


def test_agent_run_can_stop_cleanly_when_budget_guard_blocks(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    execution_key = "agent-budget-execution"
    run_id = store.begin_agent_run(agent["id"], execution_key)

    store.complete_agent_run_by_execution(
        execution_key, "stopped", "BudgetLimitError: reserve unavailable",
    )

    current = store.get_agent(agent["id"])
    assert current["last_run"]["id"] == run_id
    assert current["last_run"]["status"] == "stopped"


def test_first_explicit_agent_retires_only_unused_legacy_projection(tmp_path: Path):
    store = initialized_store(tmp_path)
    assert store.get_agent("legacy-ceo")["status"] == "stopped"

    store.create_agent(content_agent())
    assert {agent["name"] for agent in store.list_agents()} == {"Content & SEO"}
    store.close()

    reopened = CompanyStore(tmp_path / "company.db")
    assert {agent["name"] for agent in reopened.list_agents()} == {"Content & SEO"}


def test_wordpress_plugin_is_reusable_and_least_privilege(tmp_path: Path):
    store = initialized_store(tmp_path)
    first = store.create_agent(content_agent())
    second = store.create_agent({**content_agent(), "name": "Second content agent"})
    grant = {
        "permissions": ["read_posts", "write_drafts"],
        "connection_id": None,
        "config": {
            "site_url": "https://gkadvokati.com",
            "posts_url": "https://gkadvokati.com/wp-admin/post-new.php",
            "review_email": "owner@example.com",
        },
    }

    store.grant_agent_plugin(first["id"], "wordpress-content", grant)
    store.grant_agent_plugin(second["id"], "wordpress-content", grant)

    assert store.get_agent(first["id"])["plugins"][0]["permissions"] == [
        "read_posts", "write_drafts"
    ]
    assert store.get_agent(second["id"])["plugins"][0]["plugin_id"] == "wordpress-content"
    with pytest.raises(ValueError, match="does not declare"):
        store.grant_agent_plugin(first["id"], "wordpress-content", {
            **grant, "permissions": ["manage_users"],
        })


def test_plugin_config_schema_is_enforced(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())

    with pytest.raises(ValueError, match="Missing plugin configuration"):
        store.grant_agent_plugin(agent["id"], "wordpress-content", {
            "permissions": ["write_drafts"], "config": {},
        })
    with pytest.raises(ValueError, match="public HTTPS URL"):
        store.grant_agent_plugin(agent["id"], "wordpress-content", {
            "permissions": ["write_drafts"],
            "config": {"site_url": "http://localhost", "posts_url": "http://localhost/new"},
        })


def test_content_agent_type_has_typed_setup_and_bounded_actions(tmp_path: Path):
    store = initialized_store(tmp_path)
    value = content_agent() | {
        "agent_type": "content_seo",
        "config": {
            "content_language": "sr-Latn",
            "target_audience": "Potential legal clients in Banja Luka",
            "content_scope": "Employment and commercial law",
        },
    }
    agent = store.create_agent(value)

    assert agent["config"]["require_official_sources"] is True
    assert action_is_allowed("content_seo", "research_content") is True
    assert action_is_allowed("content_seo", "build_mvp") is False


def test_plugin_permissions_are_runtime_authority_not_prompt_decoration(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    store.grant_agent_plugin(agent["id"], "wordpress-content", {
        "permissions": ["read_posts", "write_drafts"],
        "config": {
            "site_url": "https://gkadvokati.com",
            "posts_url": "https://gkadvokati.com/wp-admin/post-new.php",
        },
    })
    grants = store.list_agent_plugins(agent["id"])

    assert authorize_action("save_content_draft", grants)[0] is True
    allowed, reason = authorize_action("publish_content", grants)
    assert allowed is False
    assert "publish_posts" in reason


def test_temporal_activity_identity_cannot_cross_agent_boundary(tmp_path: Path):
    store = initialized_store(tmp_path)
    first = store.create_agent(content_agent())
    second = store.create_agent({**content_agent(), "name": "Second"})

    assert store.begin_activity("same-key", agent_id=first["id"]) is None
    with pytest.raises(RuntimeError, match="different agent"):
        store.begin_activity("same-key", agent_id=second["id"])


def test_stakeholder_directive_is_scoped_to_one_agent(tmp_path: Path):
    store = initialized_store(tmp_path)
    first = store.create_agent(content_agent())
    second = store.create_agent({**content_agent(), "name": "Second"})

    message_id = store.add_stakeholder_message(
        "Prioritize employment-law topics", agent_id=first["id"],
    )

    assert store.snapshot(first["id"]).stakeholder_messages[0]["id"] == message_id
    assert store.snapshot(second["id"]).stakeholder_messages == []


def test_plugin_connection_cannot_escalate_beyond_declared_capabilities(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path / "runtime-data"))
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    save_secret(secret_name("wp-readonly", "username"), "api-user")
    save_secret(secret_name("wp-readonly", "password"), "application-password")
    store.upsert_integration_connection({
        "id": "wp-readonly", "name": "Read-only WordPress", "adapter": "http_basic",
        "provider": "WordPress", "location": "cloud",
        "base_url": "https://example.com/wp-json/wp/v2",
        "capabilities": ["wordpress.posts.read"], "config": {}, "enabled": True,
    })

    with pytest.raises(ValueError, match="wordpress.posts.publish"):
        store.grant_agent_plugin(agent["id"], "wordpress-content", {
            "connection_id": "wp-readonly", "permissions": ["publish_posts"],
            "config": {
                "site_url": "https://example.com",
                "posts_url": "https://example.com/wp-admin/post-new.php",
            },
        })


def test_smtp_plugin_draft_is_internal_and_only_send_needs_transport_capability(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    store.upsert_integration_connection({
        "id": "smtp-local", "name": "SMTP", "adapter": "smtp",
        "provider": "Mailpit", "location": "local",
        "base_url": "smtp://mailpit:1025", "capabilities": ["email.send"],
        "config": {"security": "plain", "authentication": "none"},
        "enabled": True,
    })

    grant = store.grant_agent_plugin(agent["id"], "email-communication", {
        "connection_id": "smtp-local",
        "permissions": ["draft_email", "send_email"],
        "config": {"sender_name": "Acme"},
    })

    assert grant["permissions"] == ["draft_email", "send_email"]
    with pytest.raises(ValueError, match="does not declare"):
        store.grant_agent_plugin(agent["id"], "email-communication", {
            "connection_id": "smtp-local", "permissions": ["read_email"], "config": {},
        })
