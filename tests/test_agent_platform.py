import json
from pathlib import Path

import pytest

from digital_company.agent_templates import action_is_allowed
from digital_company.capability_plugins import authorize_action
from digital_company.integration_connectors import secret_name
from digital_company.models import ActionType, SpecialistResult, TaskProposal
from digital_company.orchestrator import CompanyOrchestrator
from digital_company.runtime_secrets import save_secret
from digital_company.store import CompanyStore


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

    assert store.schema_version() == 12
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
        execution_key,
        "stopped",
        "BudgetLimitError: reserve unavailable",
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
        "read_posts",
        "write_drafts",
    ]
    assert store.get_agent(second["id"])["plugins"][0]["plugin_id"] == "wordpress-content"
    with pytest.raises(ValueError, match="does not declare"):
        store.grant_agent_plugin(
            first["id"],
            "wordpress-content",
            {
                **grant,
                "permissions": ["manage_users"],
            },
        )


def test_plugin_config_schema_is_enforced(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())

    with pytest.raises(ValueError, match="Missing plugin configuration"):
        store.grant_agent_plugin(
            agent["id"],
            "wordpress-content",
            {
                "permissions": ["write_drafts"],
                "config": {},
            },
        )
    with pytest.raises(ValueError, match="public HTTPS URL"):
        store.grant_agent_plugin(
            agent["id"],
            "wordpress-content",
            {
                "permissions": ["write_drafts"],
                "config": {"site_url": "http://localhost", "posts_url": "http://localhost/new"},
            },
        )


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


def test_content_topics_keep_independent_durable_pipeline_ids(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(
        content_agent()
        | {
            "agent_type": "content_seo",
            "config": {
                "content_language": "sr-Latn",
                "target_audience": "Banja Luka",
                "content_scope": "Employment law",
                "active_topic_target": 5,
                "wake_interval_minutes": 10,
            },
        }
    )
    research = TaskProposal(
        action=ActionType.RESEARCH_CONTENT,
        title="Neisplaćena plata u RS",
        objective="Verify demand and legal sources",
        rationale="Strong local intent",
        expected_evidence=["Official sources"],
        estimated_cost_eur=0,
        specialist="research",
    )
    research_task = store.create_task(research, "proposed", agent_id=agent["id"])
    store.complete_task(
        research_task,
        SpecialistResult(
            status="completed",
            summary="Verified topic opportunity",
            evidence=["Evidence"],
            recommendation="Draft it",
        ),
        0,
    )
    work_id = store.create_content_work_item(
        agent["id"],
        research_task,
        research.title,
        "Verified topic opportunity",
    )
    draft = TaskProposal(
        action=ActionType.CREATE_CONTENT_DRAFT,
        title="Draft unpaid wages guide",
        objective="Create a reviewable article",
        rationale="Research is complete",
        expected_evidence=["Complete draft"],
        estimated_cost_eur=0,
        specialist="growth",
    )
    draft_task = store.create_task(draft, "proposed", agent_id=agent["id"])
    bound = store.resolve_content_work_item(agent["id"], draft_task, draft)

    assert bound.work_item_id == work_id
    store.transition_content_work_item(work_id, "draft_ready", task_id=draft_task)
    assert store.list_content_work_items(agent["id"])[0]["status"] == "draft_ready"
    assert store.get_agent(agent["id"])["work_queue"]["active"] == 1


def test_stale_model_work_item_id_is_rebound_to_eligible_topic(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(
        content_agent()
        | {
            "agent_type": "content_seo",
            "config": {
                "content_language": "sr-Latn",
                "target_audience": "Banja Luka",
                "content_scope": "Legal SEO",
            },
        }
    )
    research = TaskProposal(
        action=ActionType.RESEARCH_CONTENT,
        title="First legal topic",
        objective="Verify it",
        rationale="Fill queue",
        expected_evidence=["Sources"],
        estimated_cost_eur=0,
        specialist="research",
    )
    first_task = store.create_task(research, "proposed", agent_id=agent["id"])
    stale = store.create_content_work_item(
        agent["id"],
        first_task,
        "First legal topic",
        "Already drafted",
    )
    store.transition_content_work_item(stale, "draft_saved")
    second_task = store.create_task(research, "proposed", agent_id=agent["id"])
    eligible = store.create_content_work_item(
        agent["id"],
        second_task,
        "Second distinct topic",
        "Ready to draft",
    )
    draft = TaskProposal(
        action=ActionType.CREATE_CONTENT_DRAFT,
        title="Draft second topic",
        objective="Create the draft",
        rationale="Research is ready",
        expected_evidence=["Complete package"],
        estimated_cost_eur=0,
        specialist="growth",
        work_item_id=stale,
    )
    draft_task = store.create_task(draft, "proposed", agent_id=agent["id"])

    rebound = store.resolve_content_work_item(agent["id"], draft_task, draft)

    assert rebound.work_item_id == eligible


def test_legacy_content_placeholders_are_named_and_deduplicated(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(
        content_agent()
        | {
            "agent_type": "content_seo",
            "config": {
                "content_language": "sr-Latn",
                "target_audience": "Banja Luka",
                "content_scope": "Legal SEO",
            },
        }
    )
    proposal = TaskProposal(
        action=ActionType.RESEARCH_CONTENT,
        title="Generic research topic",
        objective="Verify one topic",
        rationale="Fill the durable queue",
        expected_evidence=["Official sources"],
        estimated_cost_eur=0,
        specialist="research",
    )
    first_task = store.create_task(proposal, "proposed", agent_id=agent["id"])
    first = store.create_content_work_item(
        agent["id"],
        first_task,
        proposal.title,
        "Older complete topic",
    )
    draft_task = store.create_task(
        proposal.model_copy(update={"action": ActionType.CREATE_CONTENT_DRAFT}),
        "proposed",
        agent_id=agent["id"],
    )
    store.db.execute(
        "UPDATE tasks SET result_json=? WHERE id=?",
        (json.dumps({"content_package": {"title": "Actual legal topic"}}), draft_task),
    )
    store.transition_content_work_item(first, "draft_ready", task_id=draft_task)
    store.transition_content_work_item(first, "draft_saved")

    second_task = store.create_task(proposal, "proposed", agent_id=agent["id"])
    second = store.create_content_work_item(
        agent["id"],
        second_task,
        "Actual legal topic with practical local guidance",
        "Repeated research",
    )

    repaired = store.reconcile_content_work_items(agent["id"])

    items = {item["id"]: item for item in store.list_content_work_items(agent["id"])}
    assert repaired["renamed"] == [first]
    assert repaired["archived_duplicates"] == [second]
    assert items[first]["topic"] == "Actual legal topic"
    assert items[first]["status"] == "draft_saved"
    assert items[second]["status"] == "archived"


class StopEngine:
    def decide(self, snapshot, agent_context=None):
        return TaskProposal(
            action=ActionType.STOP,
            title="End this content shift",
            objective="Wait for the next cadence",
            rationale="Five topics are already active",
            expected_evidence=["Queue remains durable"],
            estimated_cost_eur=0,
            specialist="ceo",
        )

    def execute(self, proposal, *args, **kwargs):
        return SpecialistResult(
            status="completed",
            summary="Verified a distinct local legal content opportunity",
            evidence=["Two authoritative sources were checked"],
            sources=["https://example.com/source-one", "https://example.com/source-two"],
            recommendation="Create the complete content draft",
        )


def test_underfilled_content_queue_overrides_model_stop(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(
        content_agent()
        | {
            "agent_type": "content_seo",
            "config": {
                "content_language": "sr-Latn",
                "target_audience": "Banja Luka",
                "content_scope": "Legal SEO",
                "active_topic_target": 5,
            },
        }
    )
    store.grant_agent_plugin(
        agent["id"],
        "web-research",
        {
            "permissions": ["read_public_web"],
            "config": {},
        },
    )
    store.set_agent_status(agent["id"], "running")

    result = CompanyOrchestrator(
        store,
        tmp_path / "artifacts",
        engine=StopEngine(),
        agent_id=agent["id"],
    ).run(max_cycles=1)

    assert result["status"] == "cycle_limit_reached"
    assert store.get_agent(agent["id"])["status"] == "running"
    queue = store.list_content_work_items(agent["id"])
    assert len(queue) == 1
    assert queue[0]["status"] == "researched"
    assert store.snapshot(agent["id"]).completed_tasks[-1]["action"] == "research_content"


def test_scheduled_content_stop_sleeps_instead_of_terminating(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(
        content_agent()
        | {
            "agent_type": "content_seo",
            "config": {
                "content_language": "sr-Latn",
                "target_audience": "Banja Luka",
                "content_scope": "Legal SEO",
                "active_topic_target": 1,
                "wake_interval_minutes": 10,
            },
        }
    )
    store.grant_agent_plugin(
        agent["id"],
        "web-research",
        {
            "permissions": ["read_public_web"],
            "config": {},
        },
    )
    research = TaskProposal(
        action=ActionType.RESEARCH_CONTENT,
        title="Active review topic",
        objective="Verify the topic",
        rationale="Maintain the queue",
        expected_evidence=["Authoritative sources"],
        estimated_cost_eur=0,
        specialist="research",
    )
    research_task = store.create_task(research, "proposed", agent_id=agent["id"])
    work_id = store.create_content_work_item(
        agent["id"],
        research_task,
        research.title,
        "Waiting for owner review",
    )
    store.transition_content_work_item(work_id, "awaiting_review")
    store.set_agent_status(agent["id"], "running")

    result = CompanyOrchestrator(
        store,
        tmp_path / "artifacts",
        engine=StopEngine(),
        agent_id=agent["id"],
    ).run(max_cycles=1)

    assert result["status"] == "sleeping"
    assert result["wake_after_seconds"] == 600
    current = store.get_agent(agent["id"])
    assert current["status"] == "sleeping"
    assert current["next_wake_at"] is not None


def test_plugin_permissions_are_runtime_authority_not_prompt_decoration(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    store.grant_agent_plugin(
        agent["id"],
        "wordpress-content",
        {
            "permissions": ["read_posts", "write_drafts"],
            "config": {
                "site_url": "https://gkadvokati.com",
                "posts_url": "https://gkadvokati.com/wp-admin/post-new.php",
            },
        },
    )
    grants = store.list_agent_plugins(agent["id"])

    assert authorize_action("save_content_draft", grants)[0] is True
    allowed, reason = authorize_action("publish_content", grants)
    assert allowed is False
    assert "publish_posts" in reason


def test_browser_and_human_takeover_require_their_own_agent_plugin(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    store.grant_agent_plugin(
        agent["id"],
        "wordpress-content",
        {
            "permissions": ["read_posts", "write_drafts"],
            "config": {
                "site_url": "https://gkadvokati.com",
                "posts_url": "https://gkadvokati.com/wp-admin/post-new.php",
            },
        },
    )
    grants = store.list_agent_plugins(agent["id"])

    assert authorize_action("browser_operate", grants)[0] is False
    assert authorize_action("request_human_handoff", grants)[0] is False
    assert (
        "browser"
        not in next(item for item in grants if item["plugin_id"] == "wordpress-content")[
            "definition"
        ]["tools"]
    )

    store.grant_agent_plugin(
        agent["id"],
        "browser-automation",
        {
            "permissions": ["operate_browser", "request_human_takeover"],
            "config": {"max_steps": 8, "allow_human_takeover": False},
        },
    )
    grants = store.list_agent_plugins(agent["id"])
    assert authorize_action("browser_operate", grants)[0] is True
    assert authorize_action("request_human_handoff", grants)[0] is False

    store.grant_agent_plugin(
        agent["id"],
        "browser-automation",
        {
            "permissions": ["operate_browser", "request_human_takeover"],
            "config": {"max_steps": 8, "allow_human_takeover": True},
        },
    )
    grants = store.list_agent_plugins(agent["id"])
    assert authorize_action("request_human_handoff", grants)[0] is True


def test_disabling_browser_plugin_supersedes_agent_handoff_without_restarting(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    store.grant_agent_plugin(
        agent["id"],
        "browser-automation",
        {
            "permissions": ["operate_browser", "request_human_takeover"],
            "config": {"max_steps": 8, "allow_human_takeover": True},
        },
    )
    proposal = TaskProposal(
        action=ActionType.REQUEST_HUMAN_HANDOFF,
        title="Complete login",
        objective="Access a protected editor",
        rationale="The account owner must authenticate",
        expected_evidence=["Authenticated editor"],
        specialist="operations",
        execution_mode="manual",
        handoff_url="https://example.com/login",
        handoff_instructions=["Sign in"],
        resume_evidence=["Confirm access"],
    )
    task_id = store.create_task(proposal, "waiting_human", agent_id=agent["id"])
    store.create_handoff(task_id, proposal)

    store.revoke_agent_plugin(agent["id"], "browser-automation")

    assert store.list_handoffs()[0]["status"] == "superseded"
    assert store.get_agent(agent["id"])["status"] == "stopped"


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
        "Prioritize employment-law topics",
        agent_id=first["id"],
    )

    assert store.snapshot(first["id"]).stakeholder_messages[0]["id"] == message_id
    assert store.snapshot(second["id"]).stakeholder_messages == []


def test_plugin_connection_cannot_escalate_beyond_declared_capabilities(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path / "runtime-data"))
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    save_secret(secret_name("wp-readonly", "username"), "api-user")
    save_secret(secret_name("wp-readonly", "password"), "application-password")
    store.upsert_integration_connection(
        {
            "id": "wp-readonly",
            "name": "Read-only WordPress",
            "adapter": "http_basic",
            "provider": "WordPress",
            "location": "cloud",
            "base_url": "https://example.com/wp-json/wp/v2",
            "capabilities": ["wordpress.posts.read"],
            "config": {},
            "enabled": True,
        }
    )

    with pytest.raises(ValueError, match=r"wordpress\.posts\.publish"):
        store.grant_agent_plugin(
            agent["id"],
            "wordpress-content",
            {
                "connection_id": "wp-readonly",
                "permissions": ["publish_posts"],
                "config": {
                    "site_url": "https://example.com",
                    "posts_url": "https://example.com/wp-admin/post-new.php",
                },
            },
        )


def test_smtp_plugin_draft_is_internal_and_only_send_needs_transport_capability(tmp_path: Path):
    store = initialized_store(tmp_path)
    agent = store.create_agent(content_agent())
    store.upsert_integration_connection(
        {
            "id": "smtp-local",
            "name": "SMTP",
            "adapter": "smtp",
            "provider": "Mailpit",
            "location": "local",
            "base_url": "smtp://mailpit:1025",
            "capabilities": ["email.send"],
            "config": {"security": "plain", "authentication": "none"},
            "enabled": True,
        }
    )

    grant = store.grant_agent_plugin(
        agent["id"],
        "email-communication",
        {
            "connection_id": "smtp-local",
            "permissions": ["draft_email", "send_email"],
            "config": {"sender_name": "Acme"},
        },
    )

    assert grant["permissions"] == ["draft_email", "send_email"]
    with pytest.raises(ValueError, match="does not declare"):
        store.grant_agent_plugin(
            agent["id"],
            "email-communication",
            {
                "connection_id": "smtp-local",
                "permissions": ["read_email"],
                "config": {},
            },
        )
