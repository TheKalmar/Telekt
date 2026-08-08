"""OpenAI Agents SDK definitions and local/cloud model routing.

No agent receives direct authority to perform external side effects. Agents only
produce typed proposals/results; :mod:`digital_company.policy` and the
orchestrator decide whether an action may proceed.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from uuid import uuid4

from agents import (
    Agent, AsyncOpenAI, ModelBehaviorError, ModelRetrySettings, ModelSettings,
    OpenAIChatCompletionsModel, Runner, WebSearchTool, retry_policies, set_tracing_disabled,
)
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from digital_company.models import ActionType, CompanySnapshot, SpecialistResult, TaskProposal
from digital_company.pricing import usage_payload


CEO_INSTRUCTIONS = """You are the CEO of a constrained autonomous digital company.
Your operating personality is resourceful, commercially skeptical, candid, and capital-efficient. Act like an
owner: search for leverage, negotiate scope before price, reuse proven infrastructure, notice quality debt and
customer dissatisfaction, and escalate problems before they become expensive. Initiative never overrides evidence,
permissions, law, platform terms, or the stakeholder's capital limits.
Choose exactly one next task that best advances the company goal. You do not execute it.
Use completed work and evidence, not a fixed checklist. You may repeat research or QA when evidence is weak.
Before proposing development, explicitly compare BUILD vs BUY vs INTEGRATE vs MANUAL VALIDATION.
Prefer the cheapest reversible path that tests the business hypothesis. For ecommerce, CRM, payments, email,
analytics, hosting, and similar commodity capabilities, evaluate established platforms before custom software.
Do not propose BUILD_MVP merely because a development specialist exists. Build only when custom software is
a real differentiator or existing platforms cannot validate the hypothesis economically.
If a chosen platform is missing from company capabilities, propose REQUEST_PLATFORM_ACCESS with
platform_candidate and the minimum required_capabilities. Never claim an account, credential, API access,
supplier relationship, product listing, or publication exists unless canonical capabilities/evidence proves it.
You are resourceful: consider tools, browser operation, APIs, contractors, agencies, marketplaces, templates,
and stakeholder knowledge instead of defaulting to internal development. You may research people or vendors and
prepare a ranked shortlist before requesting contact approval. Separate discovery from outreach, negotiation,
hiring, spending, and contract acceptance. If progress requires login, account ownership, CAPTCHA, 2FA, identity
verification, accepting terms, payment details, or another human-only step, propose REQUEST_HUMAN_HANDOFF with a
precise URL, numbered instructions, and resume_evidence. Do not try to bypass or solve CAPTCHA. Ask for the smallest
human action necessary, then continue autonomously from the recorded outcome.
For BROWSER_OPERATE, always use execution_mode="browser" and provide the exact HTTPS starting URL in handoff_url.
Prefer internal reversible work. Never hide costs. After a credible validation asset and QA result, propose external_outreach
so the Governor can request a human decision. Stop only when the goal is impossible or no useful action remains.
Do not repeat a completed action unless you explicitly identify the evidence gap it will close."""
CEO_INSTRUCTIONS += """
Stakeholder messages come from the capital owner. Consider every pending message before other work.
Try hard to honor directives, but never violate the company goal, budget, evidence standards, or Governor policy.
For every message considered, include its id in stakeholder_message_ids_considered and provide a direct
stakeholder_response explaining what you will do, adapt, defer, or refuse and why. Questions must receive
a direct response through stakeholder_response."""

SPECIALIST_INSTRUCTIONS = {
    "research": """You are a skeptical B2B market researcher. You must use web search before drawing market conclusions. Produce a concise opportunity brief that separates verified facts, inference, and assumptions. Put at least two distinct direct HTTP(S) source URLs in the sources field and connect every important claim to one of them in evidence. Prefer primary sources, official product/pricing pages, public datasets, and direct customer language. Never invent a source, statistic, quote, interview, customer reaction, or completed experiment. If credible evidence is unavailable, return failed rather than filling gaps with plausible prose.""",
    "platform": "You are a platform strategy lead. Compare build, buy, integrate, and manual validation using total cost, setup time, API capability, lock-in, operational burden, and reversibility. Recommend one path and list the minimum human setup and permissions. Never claim access already exists.",
    "operations": "You are a resourceful operations lead. Design browser missions, human handoffs, and contractor sourcing plans. Produce exact URLs, bounded steps, success evidence, fallback routes, and risks. Never claim a login, CAPTCHA, 2FA, outreach, agreement, or payment was completed.",
    "product": "You are a pragmatic product manager. Produce a narrow PRD with ICP, pain, workflow, acceptance criteria, non-goals, pricing hypothesis, and measurable validation test.",
    "development": "You are an MVP developer. Produce one self-contained HTML application as artifact_content. It must be functional without a build step, with clear UI and embedded JavaScript. Return artifact_path as mvp/index.html.",
    "qa": "You are an adversarial QA lead. Inspect the supplied company state and artifact context, list concrete checks, failures, risks, and a go/no-go recommendation.",
    "growth": "You are an ethical B2B growth lead. Prepare a validation plan and draft only; do not claim messages were sent or money was spent.",
    "ceo": "You are an executive analyst. Summarize the stopping decision and unresolved risks.",
}


class AgentEngine:
    """Construct and run the CEO plus role-specific specialist agents.

    Routing modes:
    - ``local``: every role uses Ollama through its OpenAI-compatible endpoint.
    - ``hybrid``: CEO and development use OpenAI; routine specialists are local.
    - ``cloud``: every role uses the configured OpenAI model.

    The local API key value is a required placeholder for the OpenAI client and
    is ignored by Ollama. Local mode disables OpenAI trace export so a local-only
    test does not make a hidden external tracing request.
    """
    def __init__(self, mode: str = "cloud", local_model_name: str = "deepseek-company:8b",
                 allow_cloud_fallback: bool = False,
                 reporter: Callable[[str, dict], None] | None = None,
                 remaining_budget: Callable[[], float] | None = None) -> None:
        self.cloud_model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
        self.allow_cloud_fallback = allow_cloud_fallback
        self.reporter = reporter or (lambda _event, _payload: None)
        self.remaining_budget = remaining_budget
        self.max_turns = int(os.getenv("AGENT_MAX_TURNS", "8"))
        self.structured_retries = int(os.getenv("STRUCTURED_OUTPUT_RETRIES", "1"))
        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
        timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "120"))
        self.local_model = OpenAIChatCompletionsModel(
            model=local_model_name,
            openai_client=AsyncOpenAI(
                base_url=ollama_base_url, api_key="ollama", timeout=timeout, max_retries=0,
            ),
            strict_feature_validation=False,
            buffer_streamed_tool_calls=True,
        )
        retry_settings = ModelSettings(retry=ModelRetrySettings(
            max_retries=int(os.getenv("MODEL_TRANSIENT_RETRIES", "2")),
            backoff={"initial_delay": 0.5, "max_delay": 5.0, "multiplier": 2.0, "jitter": True},
            policy=retry_policies.any(
                retry_policies.provider_suggested(), retry_policies.retry_after(),
                retry_policies.network_error(),
                retry_policies.http_status([408, 409, 429, 500, 502, 503, 504]),
            ),
        ))
        if mode == "local":
            set_tracing_disabled(True)
        ceo_local = mode == "local"
        self.ceo = self._agent("CEO", CEO_INSTRUCTIONS, TaskProposal, ceo_local, retry_settings)
        self.ceo_fallback = self._agent("CEO fallback", CEO_INSTRUCTIONS, TaskProposal, False, retry_settings) if ceo_local else None
        self.specialists = {}
        self.specialist_fallbacks = {}
        for name, instructions in SPECIALIST_INSTRUCTIONS.items():
            # Hosted web search only works on OpenAI Responses models. Hybrid
            # therefore routes Research to cloud while routine roles stay local.
            use_local = mode == "local" or (mode == "hybrid" and name not in {"development", "research"})
            tools = [WebSearchTool(search_context_size="medium")] if name == "research" and not use_local else []
            self.specialists[name] = self._agent(
                name.title(), instructions, SpecialistResult, use_local, retry_settings, tools,
            )
            self.specialist_fallbacks[name] = (
                self._agent(
                    name.title() + " fallback", instructions, SpecialistResult, False,
                    retry_settings,
                    [WebSearchTool(search_context_size="medium")] if name == "research" else [],
                )
                if use_local else None
            )

    def _agent(self, name, instructions, output_type, local: bool, settings: ModelSettings,
               tools: list | None = None):
        return Agent(
            name=name, model=self.local_model if local else self.cloud_model,
            instructions=instructions, output_type=output_type, model_settings=settings,
            tools=tools or [],
        )

    def _run(self, agent, fallback, prompt: str, role: str):
        """Run with SDK transient retries, structured repair, audit, and opt-in fallback."""
        started = time.monotonic()
        run_id = str(uuid4())
        provider = "local" if agent.model is self.local_model else "cloud"
        reserve = float(os.getenv("CLOUD_CALL_RESERVE_EUR", "0.05"))
        budget_reader = getattr(self, "remaining_budget", None)
        if provider == "cloud" and budget_reader and budget_reader() < reserve:
            self.reporter("budget.cloud_call_blocked", {"role": role, "required_reserve": reserve})
            raise RuntimeError(f"Cloud call blocked: less than {reserve:.2f} budget remains")
        self.reporter("model.started", {"run_id": run_id, "role": role, "provider": provider})
        last_error = None
        for repair_attempt in range(self.structured_retries + 1):
            try:
                run_result = Runner.run_sync(agent, prompt, max_turns=self.max_turns)
                usage = usage_payload(run_result, run_id=run_id, provider=provider,
                                      model=str(getattr(agent, "model", "unknown")))
                if usage:
                    self.reporter("model.usage", usage)
                result = run_result.final_output
                self.reporter("model.succeeded", {
                    "role": role, "provider": provider, "repair_attempt": repair_attempt,
                    "run_id": run_id,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                })
                return result
            except ModelBehaviorError as exc:
                last_error = exc
                self.reporter("model.structured_output_error", {
                    "role": role, "provider": provider, "attempt": repair_attempt + 1,
                    "run_id": run_id,
                    "error": str(exc)[:500],
                })
                prompt += "\n\nYour previous response failed schema validation. Return only a complete response matching the required structured output."
            except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError) as exc:
                last_error = exc
                self.reporter("model.provider_error", {
                    "role": role, "provider": provider, "error_type": type(exc).__name__,
                    "run_id": run_id,
                    "error": str(exc)[:500],
                    "latency_ms": round((time.monotonic() - started) * 1000),
                })
                break
        fallback_allowed = (
            self.allow_cloud_fallback and fallback is not None and bool(os.getenv("OPENAI_API_KEY"))
        )
        if fallback_allowed and isinstance(last_error, (
            ModelBehaviorError, APIConnectionError, APITimeoutError, InternalServerError, RateLimitError,
        )):
            if budget_reader and budget_reader() < reserve:
                self.reporter("budget.cloud_call_blocked", {"role": role, "required_reserve": reserve})
                raise RuntimeError(f"Cloud fallback blocked: less than {reserve:.2f} budget remains")
            self.reporter("model.cloud_fallback", {
                "run_id": run_id, "role": role, "reason": type(last_error).__name__,
            })
            fallback_started = time.monotonic()
            try:
                run_result = Runner.run_sync(fallback, prompt, max_turns=self.max_turns)
                usage = usage_payload(run_result, run_id=run_id + ":fallback", provider="cloud_fallback",
                                      model=str(getattr(fallback, "model", "unknown")))
                if usage:
                    self.reporter("model.usage", usage)
                output = run_result.final_output
                self.reporter("model.succeeded", {
                    "run_id": run_id, "role": role, "provider": "cloud_fallback",
                    "repair_attempt": 0,
                    "latency_ms": round((time.monotonic() - fallback_started) * 1000),
                })
                return output
            except Exception as exc:
                self.reporter("model.fallback_failed", {
                    "run_id": run_id, "role": role, "provider": "cloud_fallback",
                    "error_type": type(exc).__name__, "error": str(exc)[:500],
                })
                raise
        self.reporter("model.failed", {
            "run_id": run_id, "role": role, "provider": provider,
            "error_type": type(last_error).__name__, "error": str(last_error)[:500],
            "latency_ms": round((time.monotonic() - started) * 1000),
        })
        raise last_error

    def decide(self, snapshot: CompanySnapshot) -> TaskProposal:
        """Ask the CEO to select exactly one next task from canonical state."""
        prompt = "Current canonical company state:\n" + snapshot.model_dump_json(indent=2)
        return self._run(self.ceo, self.ceo_fallback, prompt, "ceo")

    def execute(
        self,
        proposal: TaskProposal,
        snapshot: CompanySnapshot,
        artifact_context: dict | None = None,
    ) -> SpecialistResult:
        """Execute an approved internal task with the selected specialist."""
        selected = self.specialists[proposal.specialist]
        if proposal.action == ActionType.RESEARCH_MARKET and selected.model is self.local_model:
            return SpecialistResult(
                status="failed",
                summary="Online market research was not run because the selected local model has no web-search capability.",
                evidence=["No external source was queried; fabricated market evidence is forbidden."],
                recommendation="Switch this company to Hybrid or Cloud for evidence-backed research, then retry.",
            )
        prompt = json.dumps({
            "assigned_task": proposal.model_dump(mode="json"),
            "company_state": snapshot.model_dump(mode="json"),
            "artifact_context": artifact_context,
        }, indent=2)
        result = self._run(
            selected, self.specialist_fallbacks[proposal.specialist],
            prompt, proposal.specialist,
        )
        if proposal.action == ActionType.RESEARCH_MARKET:
            valid_sources = {
                value for value in result.sources
                if value.startswith("https://") or value.startswith("http://")
            }
            if result.status == "completed" and len(valid_sources) < 2:
                return SpecialistResult(
                    status="failed",
                    summary="Research output failed the evidence gate: fewer than two source URLs were returned.",
                    evidence=result.evidence,
                    sources=sorted(valid_sources),
                    recommendation="Repeat research with web search and return direct, verifiable URLs.",
                )
        return result
