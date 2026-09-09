"""The autonomous observe-decide-authorize-execute-record loop."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from digital_company.agent_templates import action_is_allowed
from digital_company.agents import AgentEngine
from digital_company.browser_client import browser_runtime_request
from digital_company.capability_plugins import authorize_action
from digital_company.computer_use import BrowserMissionRunner
from digital_company.errors import BudgetLimitError
from digital_company.execution_client import ExecutionRuntimeClient
from digital_company.image_generation import ImageGenerationRuntime
from digital_company.model_connections import ModelConnectionRegistry
from digital_company.models import ActionType, SpecialistResult, TaskProposal
from digital_company.policy import Governor
from digital_company.store import CompanyStore
from digital_company.wordpress_plugin import WordPressPluginRuntime
from digital_company.workspace import WorkspaceRuntime


class CompanyOrchestrator:
    """Coordinate one company's durable agent loop.

    The orchestrator owns control flow; the CEO owns only the choice of proposed
    work. This distinction prevents model output from bypassing policy, budget,
    approval, persistence, or artifact path checks.
    """

    def __init__(
        self,
        store: CompanyStore,
        artifacts_dir: Path,
        engine: AgentEngine | None = None,
        company_id: str | None = None,
        mailer: object | None = None,
        execution_key: str | None = None,
        agent_id: str | None = None,
        *,
        governor: Governor | None = None,
        workspace: WorkspaceRuntime | None = None,
        model_connections: ModelConnectionRegistry | None = None,
        execution_runtime: ExecutionRuntimeClient | None = None,
    ):
        self.store = store
        self.company_id = company_id
        self.agent_id = agent_id
        self.agent = store.get_agent(agent_id) if agent_id else None
        self.plugins = store.list_agent_plugins(agent_id) if agent_id else []
        self.workspace = workspace or WorkspaceRuntime(artifacts_dir)
        settings = store.get_settings()
        connections = (model_connections or ModelConnectionRegistry()).ensure_defaults(
            settings["local_model"], settings["cloud_model"]
        )
        by_id = {item["id"]: item for item in connections}
        selected_connection = by_id.get(self.agent["model_connection_id"]) if self.agent else None
        self.model_connection = selected_connection
        if self.agent and (not selected_connection or not selected_connection.get("enabled", True)):
            raise ValueError(
                f"Agent model connection is missing or disabled: {self.agent['model_connection_id']}"
            )
        model_mode = (
            selected_connection["location"] if selected_connection else settings["model_mode"]
        )
        local_connection = (
            selected_connection
            if selected_connection and model_mode == "local"
            else by_id.get(settings["local_connection_id"])
        )
        cloud_connection = (
            selected_connection
            if selected_connection and model_mode == "cloud"
            else by_id.get(settings["cloud_connection_id"])
        )
        self.engine = engine or AgentEngine(
            model_mode,
            (selected_connection or {}).get("model", settings["local_model"]),
            False if self.agent else bool(settings["allow_cloud_fallback"]),
            cloud_provider=(selected_connection or {}).get("name", settings["cloud_provider"]),
            cloud_model_name=(selected_connection or {}).get("model", settings["cloud_model"]),
            local_connection=local_connection,
            cloud_connection=cloud_connection,
            reporter=self._report,
            remaining_budget=(
                (lambda: self.store.agent_remaining_budget(self.agent_id))
                if self.agent_id
                else (lambda: self.store.snapshot().remaining_budget_eur)
            ),
            remaining_tokens=(
                (lambda: self.store.agent_remaining_tokens(self.agent_id))
                if self.agent_id
                else None
            ),
            agent_id=self.agent_id,
            instance_instructions=self._instance_instructions(),
        )
        self.governor = governor or Governor(store.get_policy()["document"])
        self.execution_runtime = execution_runtime
        if self.execution_runtime is None and company_id and os.getenv("EXECUTION_RUNTIME_URL"):
            self.execution_runtime = ExecutionRuntimeClient()
        # ``mailer`` remains accepted for constructor compatibility. Stakeholder
        # notifications now belong to StakeholderBriefService, not orchestration.
        self.execution_key = execution_key

    def _instance_instructions(self) -> str:
        """Build a bounded mandate from the agent and its enabled plugin grants."""
        if not self.agent:
            return ""
        plugin_lines = []
        for grant in self.plugins:
            plugin_lines.append(
                f"- {grant['name']} ({grant['plugin_id']}): permissions={grant['permissions']}; "
                f"configuration={grant['config']}; instructions={grant['definition'].get('instructions', '')}"
            )
        capabilities = (
            "\n".join(plugin_lines) or "- No plugins are granted. Do not claim external access."
        )
        playbook = self.store.get_agent_playbook(self.agent_id)
        durable_rules = (
            "\n".join(f"- {rule}" for rule in playbook["document"]["rules"])
            or "- No durable owner rules have been saved yet."
        )
        examples = (
            "\n".join(f"- {value}" for value in playbook["document"]["reference_examples"])
            or "- None"
        )
        browser_grant = self._browser_grant()
        browser_permissions = set(browser_grant.get("permissions", [])) if browser_grant else set()
        can_take_over = bool(
            browser_grant
            and "request_human_takeover" in browser_permissions
            and (browser_grant.get("config") or {}).get("allow_human_takeover", True)
        )
        browser_boundary = (
            "Browser Automation permissions: "
            + ", ".join(sorted(browser_permissions))
            + ". "
            + (
                "A human takeover may be requested only for a genuine human-only checkpoint that blocks all "
                "useful autonomous work."
                if can_take_over
                else "Human takeover is disabled; never propose REQUEST_HUMAN_HANDOFF."
            )
            if browser_grant
            else "Browser Automation is not granted. Never propose BROWSER_OPERATE or REQUEST_HUMAN_HANDOFF. "
            "Prefer granted APIs and autonomous work; if no useful path remains, stop with a precise reason "
            "instead of interrupting the owner."
        )
        return (
            f"Name: {self.agent['name']}\nType: {self.agent['agent_type']}\nRole: {self.agent['role']}\n"
            f"Purpose: {self.agent['purpose']}\nInstructions: {self.agent['instructions']}\n"
            f"Autonomy mode: {self.agent['autonomy_mode']}\nGranted capability plugins:\n{capabilities}\n"
            f"Durable owner playbook v{playbook['version']} (authoritative):\n{durable_rules}\n"
            f"Reference examples:\n{examples}\nNotes: {playbook['document']['notes']}\n"
            f"Browser boundary: {browser_boundary}\n"
            "Stay inside this mandate. A configured URL or connection reference is not proof that login or "
            "credentials are ready. Use only granted permissions."
        )

    def _browser_grant(self, permission: str | None = None) -> dict | None:
        """Return this agent's explicit browser grant; other plugins never imply it."""
        return next(
            (
                item
                for item in self.plugins
                if item["plugin_id"] == "browser-automation"
                and item["status"] == "enabled"
                and (permission is None or permission in item.get("permissions", []))
            ),
            None,
        )

    def _agent_context(self) -> dict | None:
        if not self.agent:
            return None
        result = {
            key: self.agent[key]
            for key in (
                "id",
                "name",
                "role",
                "purpose",
                "instructions",
                "autonomy_mode",
                "agent_type",
                "token_limit",
                "spend_limit_eur",
                "config",
            )
        }
        result["playbook"] = self.store.get_agent_playbook(self.agent_id)
        return result

    def _control_state(self) -> str:
        return (
            self.store.get_agent(self.agent_id)["status"]
            if self.agent_id
            else self.store.get_control()["state"]
        )

    def _report(self, event: str, payload: dict) -> None:
        payload = dict(payload)
        if self.agent:
            payload.setdefault("agent_id", self.agent_id)
            payload.setdefault("agent_name", self.agent["name"])
            payload.setdefault("agent_role", self.agent["role"])
        if event == "model.usage":
            self.store.record_model_usage(payload)
        self.store.audit(event, payload)

    def run(self, max_cycles: int = 8) -> dict:
        """Run bounded cycles and return on pause, stop, approval, or cycle limit.

        Pause/stop are cooperative: they are checked between atomic agent steps.
        An in-flight model request or database write is allowed to finish safely.
        """
        if self.agent and self.agent["agent_type"] == "content_seo":
            self.store.reconcile_content_work_items(self.agent_id)
        for cycle in range(1, max_cycles + 1):
            control_state = self._control_state()
            if control_state in {"paused", "stopped", "error"}:
                return {"status": control_state, "cycles": cycle - 1}

            recovered = (
                self.store.recover_activity_task(self.execution_key) if self.execution_key else None
            )
            if recovered:
                task_id, proposal, status = recovered
                self.store.audit(
                    "activity.task_recovered",
                    {
                        "execution_key": self.execution_key,
                        "task_id": task_id,
                        "status": status,
                    },
                )
                if status == "executing":
                    return self._execute_claimed(task_id, proposal, cycle)
                self._execute_specialist(task_id, proposal)
                return {"status": "recovered_task_completed", "cycles": cycle, "task_id": task_id}

            approved = self.store.claim_approved_task(self.agent_id)
            if approved:
                task_id, proposal = approved
                if self.execution_key:
                    self.store.link_activity_task(self.execution_key, task_id, proposal)
                return self._execute_claimed(task_id, proposal, cycle)

            snapshot = self.store.snapshot(self.agent_id)
            proposal = (
                self.engine.decide(snapshot, self._agent_context())
                if self.agent_id
                else self.engine.decide(snapshot)
            )
            proposal = self._enforce_content_pipeline(proposal, snapshot)
            # A stakeholder response becomes durable before the proposed task is
            # evaluated, so the UI can show how the CEO handled the intervention.
            self.store.address_messages(
                proposal.stakeholder_message_ids_considered,
                proposal.stakeholder_response,
            )
            policy = self.governor.evaluate(proposal, snapshot.remaining_budget_eur)
            task_id = self.store.create_task(
                proposal,
                "proposed",
                self.execution_key,
                agent_id=self.agent_id,
            )
            if self.agent_id:
                # Content tasks must carry one durable topic identity. When a
                # small model omits it, bind the oldest eligible queue item
                # deterministically instead of operating on a global latest draft.
                proposal = self.store.resolve_content_work_item(
                    self.agent_id,
                    task_id,
                    proposal,
                )

            if self.agent_id:
                if not action_is_allowed(self.agent["agent_type"], proposal.action.value):
                    self.store.set_task_status(task_id, "denied")
                    self.store.audit(
                        "agent.scope_denied",
                        {
                            "agent_id": self.agent_id,
                            "task_id": task_id,
                            "agent_type": self.agent["agent_type"],
                            "action": proposal.action.value,
                        },
                    )
                    continue
                plugin_allowed, plugin_reason = authorize_action(
                    proposal.action.value,
                    self.plugins,
                )
                if not plugin_allowed:
                    self.store.set_task_status(task_id, "denied")
                    self.store.audit(
                        "agent.plugin_denied",
                        {
                            "agent_id": self.agent_id,
                            "task_id": task_id,
                            "action": proposal.action.value,
                            "reason": plugin_reason,
                        },
                    )
                    continue

            if policy.outcome == "deny":
                self.store.set_task_status(task_id, "denied")
                self.store.audit("task.denied", {"task_id": task_id, "reason": policy.reason})
                continue
            if proposal.action == ActionType.REQUEST_HUMAN_HANDOFF:
                handoff_id = self.store.create_handoff(task_id, proposal)
                return {
                    "status": "waiting_for_human",
                    "cycles": cycle,
                    "handoff_id": handoff_id,
                    "task": proposal.model_dump(mode="json"),
                }
            if policy.outcome == "require_approval":
                if self.store.has_pending_equivalent_approval(proposal, self.agent_id):
                    self.store.set_task_status(task_id, "superseded")
                    self.store.audit("company.blocked_by_pending_approval", {"task_id": task_id})
                    if (
                        self.agent
                        and self.agent["agent_type"] == "content_seo"
                        and proposal.action == ActionType.PUBLISH_CONTENT
                    ):
                        return self._sleep_until_next_cadence(
                            cycle,
                            "Content review is pending; follow up on the next cadence",
                        )
                    return {
                        "status": "waiting_for_approval",
                        "cycles": cycle,
                        "reason": "No useful autonomous work remains; pending human decision is blocking",
                    }
                if proposal.action == ActionType.REQUEST_PLATFORM_ACCESS:
                    self.store.request_integration(
                        proposal.platform_candidate or "unknown",
                        proposal.required_capabilities,
                    )
                approval_id = self.store.request_approval(
                    task_id,
                    proposal,
                    policy.reason,
                    required_approvals=policy.approval_quorum,
                    ttl_hours=policy.approval_ttl_hours,
                )
                self.store.audit("approval.queued_without_pause", {"approval_id": approval_id})
                continue
            if proposal.action == ActionType.STOP:
                if self.agent and self.agent["agent_type"] == "content_seo":
                    self.store.set_task_status(task_id, "deferred")
                    return self._sleep_until_next_cadence(cycle, proposal.rationale)
                self.store.set_task_status(task_id, "stopped")
                return {"status": "stopped", "cycles": cycle, "reason": proposal.rationale}

            if proposal.action in {
                ActionType.BROWSER_OPERATE,
                ActionType.SAVE_CONTENT_DRAFT,
                ActionType.PUBLISH_CONTENT,
            }:
                return self._execute_browser_mission(task_id, proposal, cycle)

            self._execute_specialist(task_id, proposal, snapshot)

        return {"status": "cycle_limit_reached", "cycles": max_cycles}

    def _enforce_content_pipeline(self, proposal: TaskProposal, snapshot) -> TaskProposal:
        """Replace a premature content-agent STOP with the next required stage.

        Models choose *how* to perform useful work, but the durable queue owns
        whether work is complete. This guard prevents a content agent from
        sleeping after one article merely because the planner returned STOP.
        It also keeps separate topics moving while publication reviews wait.
        """
        if not (self.agent and self.agent["agent_type"] == "content_seo"):
            return proposal

        if proposal.action == ActionType.RESEARCH_CONTENT:
            excluded = list(
                dict.fromkeys(" ".join(item["topic"].split())[:32] for item in snapshot.work_queue)
            )[-5:]
            exclusion_text = "; ".join(excluded) or "none"
            payload = proposal.model_dump(mode="json")
            payload.update(
                {
                    "objective": (
                        "Choose one materially distinct topic. Never repeat: "
                        f"{exclusion_text}. Verify it with official sources."
                    )[:200],
                    "skill_ids": ["content-seo"],
                    "required_capabilities": ["read_public_web"],
                    "execution_mode": "api",
                }
            )
            return TaskProposal.model_validate(payload)

        if proposal.action != ActionType.STOP:
            return proposal

        queue = [
            item for item in snapshot.work_queue if item["status"] not in {"published", "archived"}
        ]
        target = int((self.agent.get("config") or {}).get("active_topic_target", 5))
        replacement: TaskProposal | None = None

        if len(queue) < target and self._plugin_authorizes(ActionType.RESEARCH_CONTENT):
            existing_topics = [item["topic"] for item in queue]
            distinct_from = "; ".join(existing_topics[-5:]) or "no active topics"
            replacement = TaskProposal(
                action=ActionType.RESEARCH_CONTENT,
                title=f"Research content opportunity {len(queue) + 1} of {target}",
                objective=(
                    "Find and verify one new high-value topic distinct from the active queue: "
                    + distinct_from
                )[:200],
                rationale=f"The durable content queue has {len(queue)} of {target} active topics",
                expected_evidence=[
                    "Distinct search intent and business value",
                    "At least two direct authoritative sources",
                ],
                estimated_cost_eur=0,
                specialist="research",
                stakeholder_response=(
                    f"The pipeline is below target ({len(queue)}/{target}); "
                    "I am continuing autonomously with a distinct topic."
                ),
                stakeholder_message_ids_considered=(proposal.stakeholder_message_ids_considered),
                skill_ids=["content-seo"],
                required_capabilities=["read_public_web"],
                execution_mode="api",
            )
        elif len(queue) >= target:
            stages = (
                (
                    {"changes_requested", "researched"},
                    ActionType.CREATE_CONTENT_DRAFT,
                    "growth",
                    "Create the complete reviewable content package",
                    "api",
                ),
                (
                    {"draft_ready"},
                    ActionType.SAVE_CONTENT_DRAFT,
                    "operations",
                    "Save the prepared package as an unpublished WordPress draft",
                    "browser",
                ),
                (
                    {"draft_saved"},
                    ActionType.PUBLISH_CONTENT,
                    "operations",
                    "Send the exact saved draft through owner review before publication",
                    "browser",
                ),
            )
            for statuses, action, specialist, objective, execution_mode in stages:
                item = next((value for value in queue if value["status"] in statuses), None)
                if not item or not self._plugin_authorizes(action):
                    continue
                handoff_url = self._wordpress_editor_url() if execution_mode == "browser" else None
                if execution_mode == "browser" and not handoff_url:
                    continue
                replacement = TaskProposal(
                    action=action,
                    title=f"Advance content: {item['topic']}"[:120],
                    objective=objective,
                    rationale=(
                        f"Topic {item['topic']} is at {item['status']} and has an autonomous next step"
                    )[:200],
                    expected_evidence=[
                        "Topic-scoped result is stored",
                        "The durable work item advances exactly one stage",
                    ],
                    estimated_cost_eur=0,
                    specialist=specialist,
                    stakeholder_response=(
                        "I am advancing existing content work instead of stopping early."
                    ),
                    stakeholder_message_ids_considered=(
                        proposal.stakeholder_message_ids_considered
                    ),
                    skill_ids=["content-seo"],
                    work_item_id=item["id"],
                    execution_mode=execution_mode,
                    handoff_url=handoff_url,
                )
                break

        if replacement:
            self.store.audit(
                "content.pipeline_stop_overridden",
                {
                    "agent_id": self.agent_id,
                    "active_topics": len(queue),
                    "target_topics": target,
                    "replacement_action": replacement.action.value,
                    "work_item_id": replacement.work_item_id,
                },
            )
            return replacement
        return proposal

    def _plugin_authorizes(self, action: ActionType) -> bool:
        """Return whether the current immutable plugin grant permits an action."""
        allowed, _ = authorize_action(action.value, self.plugins)
        return allowed

    def _wordpress_editor_url(self) -> str | None:
        """Return the configured HTTPS editor URL for typed content proposals."""
        grant = next(
            (
                item
                for item in self.plugins
                if item["plugin_id"] == "wordpress-content" and item["status"] == "enabled"
            ),
            None,
        )
        url = ((grant or {}).get("config") or {}).get("posts_url")
        return url if url and urlparse(url).scheme == "https" else None

    def _sleep_until_next_cadence(self, cycle: int, reason: str) -> dict:
        """Convert content-planner idleness into a restartable durable timer."""
        interval_minutes = int((self.agent.get("config") or {}).get("wake_interval_minutes", 1440))
        scheduled = self.store.schedule_agent_wake(
            self.agent_id,
            interval_minutes * 60,
            reason,
        )
        return {
            "status": "sleeping",
            "cycles": cycle,
            "reason": reason,
            **scheduled,
        }

    def _execute_claimed(self, task_id: str, proposal, cycle: int) -> dict:
        """Execute one human-approved frozen proposal without asking the CEO again."""
        snapshot = self.store.snapshot(self.agent_id)
        policy = self.governor.evaluate(proposal, snapshot.remaining_budget_eur)
        if self.agent_id:
            if not action_is_allowed(self.agent["agent_type"], proposal.action.value):
                self.store.fail_task(task_id, "Action is outside the configured agent type")
                return {"status": "failed", "cycles": cycle, "task_id": task_id}
            plugin_allowed, plugin_reason = authorize_action(proposal.action.value, self.plugins)
            if not plugin_allowed:
                self.store.fail_task(task_id, "Plugin grant was revoked: " + plugin_reason)
                return {"status": "failed", "cycles": cycle, "task_id": task_id}
        if policy.outcome == "deny":
            self.store.fail_task(task_id, "Approved task no longer passes policy: " + policy.reason)
            return {"status": "failed", "cycles": cycle, "task_id": task_id}
        try:
            if proposal.action in {
                ActionType.BROWSER_OPERATE,
                ActionType.SAVE_CONTENT_DRAFT,
                ActionType.PUBLISH_CONTENT,
            }:
                return self._execute_browser_mission(task_id, proposal, cycle)
            self._execute_specialist(task_id, proposal, snapshot)
        except Exception as exc:
            self.store.fail_task(task_id, f"{type(exc).__name__}: {exc}")
            raise
        return {"status": "approved_task_completed", "cycles": cycle, "task_id": task_id}

    def _execute_specialist(self, task_id: str, proposal, snapshot=None) -> None:
        """Resolve capabilities and run the one shared specialist execution path."""
        snapshot = snapshot or self.store.snapshot(self.agent_id)
        skills = self.store.resolve_skills(
            proposal.skill_ids,
            proposal.specialist,
            proposal.action.value,
            strict=False,
        )
        self.store.audit(
            "skills.assigned",
            {
                "task_id": task_id,
                "skill_ids": [skill["id"] for skill in skills],
            },
        )
        if self.agent_id:
            result = self.engine.execute(
                proposal,
                snapshot,
                self._artifact_context(proposal.specialist),
                skills,
                self._agent_context(),
                self.plugins,
            )
        else:
            result = self.engine.execute(
                proposal,
                snapshot,
                self._artifact_context(proposal.specialist),
                skills,
            )
        self._persist_result(task_id, proposal, result)

    def _execute_browser_mission(self, task_id: str, proposal, cycle: int) -> dict:
        """Run an approved mission, but create a fresh handoff for any human checkpoint."""
        wordpress_grant = next(
            (
                item
                for item in self.plugins
                if item["plugin_id"] == "wordpress-content"
                and item["status"] == "enabled"
                and item.get("connection_id")
            ),
            None,
        )
        if wordpress_grant and proposal.action in {
            ActionType.SAVE_CONTENT_DRAFT,
            ActionType.PUBLISH_CONTENT,
        }:
            return self._execute_wordpress_api(
                task_id,
                proposal,
                cycle,
                wordpress_grant,
            )
        browser_grant = self._browser_grant("operate_browser")
        if self.agent_id and not browser_grant:
            raise RuntimeError(
                "Browser Automation plugin is disabled for this agent; configure the required API plugin instead"
            )
        hostname = urlparse(proposal.handoff_url).hostname
        if not hostname:
            raise RuntimeError("Browser mission has no valid starting hostname")
        allowed_domains = list(
            dict.fromkeys(
                [
                    hostname,
                    *(proposal.handoff_allowed_domains or []),
                ]
            )
        )
        mutation_scope = (
            "draft"
            if proposal.action == ActionType.SAVE_CONTENT_DRAFT
            else "publish"
            if proposal.action == ActionType.PUBLISH_CONTENT
            else "read"
        )
        session_id = self.company_id if not self.agent_id else f"{self.company_id}--{self.agent_id}"
        browser_runtime_request(
            "PUT",
            f"/sessions/{session_id}",
            {
                "url": proposal.handoff_url,
                "allowed_domains": allowed_domains,
                "mutation_scope": mutation_scope,
            },
        )
        configured_steps = (
            (browser_grant.get("config") or {}).get("max_steps") if browser_grant else None
        )
        max_steps = max(
            1,
            min(
                30, int(configured_steps or __import__("os").getenv("COMPUTER_USE_MAX_STEPS", "12"))
            ),
        )
        self.store.audit(
            "browser.mission_started",
            {
                "task_id": task_id,
                "objective": proposal.objective,
                "allowed_domains": allowed_domains,
                "max_steps": max_steps,
            },
        )
        objective = proposal.objective
        if proposal.action in {ActionType.SAVE_CONTENT_DRAFT, ActionType.PUBLISH_CONTENT}:
            draft = self.store.latest_content_draft(self.agent_id)
            if not draft:
                raise RuntimeError("No completed content draft exists in the company workspace")
            objective = (
                f"{proposal.objective}\n\nUse this exact prepared draft ({draft['path']}):\n\n"
                f"{draft['content']}"
            )
        outcome = BrowserMissionRunner(
            reporter=lambda event, payload: self.store.audit(
                event, {"task_id": task_id, **payload}
            ),
            control_state=self._control_state,
        ).run(
            session_id,
            objective,
            max_steps=max_steps,
            mutation_scope=mutation_scope,
        )
        if outcome.status in {"waiting_human", "blocked"}:
            if browser_grant and not (browser_grant.get("config") or {}).get(
                "allow_human_takeover",
                True,
            ):
                self.store.fail_task(task_id, outcome.summary)
                return {
                    "status": "failed",
                    "cycles": cycle,
                    "task_id": task_id,
                    "reason": "Human takeover is disabled in the Browser Automation plugin",
                }
            handoff = proposal.model_copy(
                update={
                    "handoff_instructions": [
                        outcome.summary,
                        "Open the guided Telekt browser below and complete only the detected checkpoint",
                    ],
                    "resume_evidence": [
                        "Describe exactly what was completed and what access is now available"
                    ],
                }
            )
            handoff_id = self.store.create_handoff(task_id, handoff)
            self.store.audit(
                "browser.mission_handoff",
                {
                    "task_id": task_id,
                    "handoff_id": handoff_id,
                    "reason": outcome.summary,
                },
            )
            return {
                "status": "waiting_for_human",
                "cycles": cycle,
                "task_id": task_id,
                "handoff_id": handoff_id,
                "reason": outcome.summary,
            }
        if outcome.status != "completed":
            self.store.fail_task(task_id, outcome.summary)
            status = outcome.status if outcome.status in {"paused", "stopped"} else "failed"
            return {
                "status": status,
                "cycles": cycle,
                "task_id": task_id,
                "mission_status": outcome.status,
            }
        result = SpecialistResult(
            status="completed",
            summary=outcome.summary,
            evidence=[
                f"Computer Use mission executed {outcome.steps} audited step(s) on {hostname}"
            ],
            recommendation="CEO should inspect the mission evidence and choose the next reversible action",
        )
        self._persist_result(task_id, proposal, result)
        return {
            "status": "approved_task_completed",
            "cycles": cycle,
            "task_id": task_id,
            "mission_status": outcome.status,
        }

    def _execute_wordpress_api(
        self,
        task_id: str,
        proposal,
        cycle: int,
        grant: dict,
    ) -> dict:
        """Use a granted REST connection before considering browser automation."""
        draft = self.store.latest_content_draft(self.agent_id, proposal.work_item_id)
        if not draft:
            raise RuntimeError("No completed content draft exists for this agent")
        runtime = WordPressPluginRuntime(
            self.store,
            grant,
            self.execution_key or f"task:{task_id}",
            image_runtime=self._image_runtime(),
        )
        if proposal.action == ActionType.SAVE_CONTENT_DRAFT:
            response = runtime.save_draft(draft)
            summary = f"Saved WordPress draft {response.get('post_id')}"
            recommendation = "Review the unpublished WordPress draft before requesting publication"
        else:
            response = runtime.publish(draft)
            summary = f"Published WordPress post {response.get('post_id')}"
            recommendation = "Monitor indexing, search visibility, and user response"
        evidence = [
            f"WordPress REST status={response.get('status')}; slug={response.get('slug')}",
            f"Source content task={response.get('source_task_id')}",
        ]
        if response.get("link"):
            evidence.append("WordPress post URL: " + response["link"])
        if response.get("categories"):
            evidence.append("WordPress categories: " + ", ".join(response["categories"]))
        if response.get("tags"):
            evidence.append("WordPress tags: " + ", ".join(response["tags"]))
        if (response.get("featured_image") or {}).get("source_url"):
            evidence.append("Featured image: " + response["featured_image"]["source_url"])
        self._persist_result(
            task_id,
            proposal,
            SpecialistResult(
                status="completed",
                summary=summary,
                evidence=evidence,
                sources=[response["link"]] if response.get("link") else [],
                publication_state=response,
                recommendation=recommendation,
            ),
        )
        self.store.audit(
            "plugin.wordpress_executed",
            {
                "agent_id": self.agent_id,
                "task_id": task_id,
                "connection_id": grant["connection_id"],
                "action": proposal.action.value,
                "post_id": response.get("post_id"),
                "cached": response.get("cached", False),
            },
        )
        return {
            "status": "approved_task_completed",
            "cycles": cycle,
            "task_id": task_id,
            "plugin": "wordpress-content",
        }

    def _image_runtime(self):
        """Resolve optional image generation from an explicit least-privilege grant."""
        grant = next(
            (
                item
                for item in self.plugins
                if item["plugin_id"] == "featured-image-generation"
                and item["status"] == "enabled"
                and "generate_image" in item.get("permissions", [])
                and item.get("config", {}).get("enabled", True)
            ),
            None,
        )
        if not grant:
            return None
        if not self.model_connection:
            raise RuntimeError("Featured-image plugin needs the agent's model connection")
        config = grant.get("config") or {}
        estimate = float(config.get("estimated_cost_eur", 0.25))

        def budget_check(required: float) -> None:
            remaining = self.store.agent_remaining_budget(self.agent_id)
            if remaining < required:
                raise BudgetLimitError(
                    f"Featured image estimate EUR {required:.2f} exceeds the agent's "
                    f"remaining model budget EUR {remaining:.2f}"
                )

        def record_usage() -> None:
            rate = float(os.getenv("BILLING_USD_TO_BUDGET_RATE", "1.0"))
            self.store.record_model_usage(
                {
                    "run_id": f"image:{self.execution_key or self.agent_id}",
                    "provider": self.model_connection.get("name", "cloud image connection"),
                    "model": config.get("model", "gpt-image-2"),
                    "requests": 1,
                    "input_tokens": 0,
                    "cached_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                    "estimated_usd": round(estimate / rate, 8) if rate else None,
                    "estimated_budget_cost": estimate,
                    "pricing_status": "configured_estimate",
                    "agent_id": self.agent_id,
                }
            )

        return ImageGenerationRuntime(
            self.model_connection,
            config,
            budget_check=budget_check,
            on_generated=record_usage,
        )

    def _persist_result(self, task_id: str, proposal, result) -> None:
        """Persist a specialist result and confine any model-provided artifact path."""
        if result.status == "failed":
            self.store.fail_task(task_id, result.summary, result)
            return
        if result.artifact_path and result.artifact_content:
            try:
                metadata = self.workspace.write_text(result.artifact_path, result.artifact_content)
            except ValueError as exc:
                raise RuntimeError(f"Specialist returned an unsafe artifact: {exc}") from exc
            self.store.audit(
                "artifact.written",
                {
                    "task_id": task_id,
                    "role": proposal.specialist,
                    **metadata,
                },
            )
            result.evidence.append(
                f"Workspace validation {'passed' if metadata['checks']['passed'] else 'failed'}; "
                f"sha256={metadata['sha256'][:12]}, bytes={metadata['size_bytes']}"
            )
            if self.execution_runtime and self.company_id:
                checkpoint = self.execution_runtime.checkpoint(
                    self.company_id,
                    task_id,
                    result.artifact_path,
                    result.artifact_content,
                    f"{self.execution_key or task_id}:artifact:{result.artifact_path}",
                )
                self.store.audit(
                    "execution.repository_checkpointed",
                    {
                        "task_id": task_id,
                        "branch": checkpoint["branch"],
                        "commit": checkpoint.get("commit"),
                        "status": checkpoint["status"],
                        "cached": checkpoint.get("cached", False),
                    },
                )
                result.evidence.append(
                    f"Isolated Git checkpoint {checkpoint.get('commit') or 'unborn'} "
                    f"on {checkpoint['branch']}"
                )
        self.store.complete_task(task_id, result, proposal.estimated_cost_eur)
        if not self.agent_id:
            return
        if proposal.action == ActionType.RESEARCH_CONTENT:
            self.store.create_content_work_item(
                self.agent_id,
                task_id,
                result.researched_topic or proposal.title,
                result.summary,
            )
        elif proposal.action == ActionType.CREATE_CONTENT_DRAFT:
            if result.content_package:
                self.store.rename_content_work_item(
                    proposal.work_item_id,
                    result.content_package.title,
                )
            self.store.transition_content_work_item(
                proposal.work_item_id,
                "draft_ready",
                task_id=task_id,
            )
        elif proposal.action == ActionType.SAVE_CONTENT_DRAFT:
            self.store.transition_content_work_item(
                proposal.work_item_id,
                "draft_saved",
                task_id=task_id,
            )
        elif proposal.action == ActionType.PUBLISH_CONTENT:
            self.store.transition_content_work_item(
                proposal.work_item_id,
                "published",
                task_id=task_id,
            )

    def _artifact_context(self, specialist: str) -> dict | None:
        """Provide QA with the current MVP and cheap deterministic preflight data."""
        if specialist != "qa":
            return None
        target = self.workspace.resolve("mvp/index.html")
        if not target.exists():
            return {
                "exists": False,
                "preflight": {"passed": False, "reason": "MVP artifact missing"},
            }
        content = target.read_text(encoding="utf-8")
        checks = self.workspace.validate("mvp/index.html", content)
        return {
            "exists": True,
            "path": str(target),
            "size_bytes": len(content.encode("utf-8")),
            "preflight": {"passed": checks["passed"], "checks": checks},
            "content": content,
        }
