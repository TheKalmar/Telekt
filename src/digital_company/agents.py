"""OpenAI Agents SDK definitions and local/cloud model routing.

No agent receives direct authority to perform external side effects. Agents only
produce typed proposals/results; :mod:`digital_company.policy` and the
orchestrator decide whether an action may proceed.
"""

from __future__ import annotations

import json
import os

from agents import Agent, AsyncOpenAI, OpenAIChatCompletionsModel, Runner, set_tracing_disabled

from digital_company.models import CompanySnapshot, SpecialistResult, TaskProposal


CEO_INSTRUCTIONS = """You are the CEO of a constrained autonomous digital company.
Choose exactly one next task that best advances the company goal. You do not execute it.
Use completed work and evidence, not a fixed checklist. You may repeat research or QA when evidence is weak.
Prefer internal reversible work. Never hide costs. After a credible MVP and QA result, propose external_outreach
so the Governor can request a human decision. Stop only when the goal is impossible or no useful action remains.
Do not repeat a completed action unless you explicitly identify the evidence gap it will close."""
CEO_INSTRUCTIONS += """
Stakeholder messages come from the capital owner. Consider every pending message before other work.
Try hard to honor directives, but never violate the company goal, budget, evidence standards, or Governor policy.
For every message considered, include its id in stakeholder_message_ids_considered and provide a direct
stakeholder_response explaining what you will do, adapt, defer, or refuse and why. Questions must receive
a direct response through stakeholder_response."""

SPECIALIST_INSTRUCTIONS = {
    "research": "You are a skeptical B2B market researcher. Produce a concise evidence-based opportunity brief. Clearly label assumptions and avoid invented sources.",
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
    def __init__(self, mode: str = "cloud", local_model_name: str = "deepseek-company:8b") -> None:
        cloud_model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
        local_model = OpenAIChatCompletionsModel(
            model=local_model_name,
            openai_client=AsyncOpenAI(base_url=ollama_base_url, api_key="ollama"),
            strict_feature_validation=False,
            buffer_streamed_tool_calls=True,
        )
        if mode == "local":
            set_tracing_disabled(True)
        ceo_model = local_model if mode == "local" else cloud_model
        self.ceo = Agent(name="CEO", model=ceo_model, instructions=CEO_INSTRUCTIONS, output_type=TaskProposal)
        self.specialists = {
            name: Agent(
                name=name.title(),
                model=(cloud_model if mode == "cloud" or (mode == "hybrid" and name == "development") else local_model),
                instructions=instructions,
                output_type=SpecialistResult,
            )
            for name, instructions in SPECIALIST_INSTRUCTIONS.items()
        }

    def decide(self, snapshot: CompanySnapshot) -> TaskProposal:
        """Ask the CEO to select exactly one next task from canonical state."""
        prompt = "Current canonical company state:\n" + snapshot.model_dump_json(indent=2)
        return Runner.run_sync(self.ceo, prompt).final_output

    def execute(
        self,
        proposal: TaskProposal,
        snapshot: CompanySnapshot,
        artifact_context: dict | None = None,
    ) -> SpecialistResult:
        """Execute an approved internal task with the selected specialist."""
        prompt = json.dumps({
            "assigned_task": proposal.model_dump(mode="json"),
            "company_state": snapshot.model_dump(mode="json"),
            "artifact_context": artifact_context,
        }, indent=2)
        return Runner.run_sync(self.specialists[proposal.specialist], prompt).final_output
