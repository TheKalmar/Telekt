"""OpenAI Agents SDK model construction from transport connection profiles."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from agents import AsyncOpenAI, OpenAIChatCompletionsModel, OpenAIResponsesModel
from agents.extensions.models.litellm_model import LitellmModel

from digital_company.runtime_secrets import get_secret


@dataclass(frozen=True)
class ModelBinding:
    """Resolved SDK model plus capabilities needed by routing and preflight."""

    model: object
    ready: bool
    supports_hosted_tools: bool


class ModelAdapterFactory:
    """Translate data-driven connection profiles into Agents SDK adapters."""

    def __init__(
        self,
        *,
        secret_reader: Callable[[str], str | None] = get_secret,
        timeout_seconds: float | None = None,
    ):
        self.secret_reader = secret_reader
        self.timeout_seconds = timeout_seconds or float(os.getenv("MODEL_TIMEOUT_SECONDS", "240"))

    def build(
        self, connection: dict | None, *, default_model: str, default_cloud: bool
    ) -> ModelBinding:
        if not connection:
            if default_cloud:
                return ModelBinding(default_model, bool(os.getenv("OPENAI_API_KEY")), True)
            connection = {
                "id": "local-default",
                "adapter": "openai_compatible",
                "location": "local",
                "base_url": os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
                "model": default_model,
                "requires_api_key": False,
            }

        secret = self.secret_reader(f"MODEL_CONNECTION_{connection['id']}")
        adapter = connection["adapter"]
        model = connection["model"]
        requires_key = connection.get("requires_api_key", True)

        if adapter == "openai_responses":
            api_key = secret or os.getenv("OPENAI_API_KEY")
            ready = bool(api_key) or not requires_key
            return ModelBinding(
                OpenAIResponsesModel(
                    model=model,
                    openai_client=AsyncOpenAI(
                        api_key=api_key or "credential-not-required",
                        timeout=self.timeout_seconds,
                        max_retries=0,
                    ),
                ),
                ready,
                True,
            )

        if adapter == "litellm":
            return ModelBinding(
                LitellmModel(
                    model=model,
                    base_url=connection.get("base_url") or None,
                    api_key=secret,
                ),
                bool(secret) or not requires_key,
                False,
            )

        api_key = secret
        if not api_key and (connection.get("location") == "local" or not requires_key):
            # OpenAI-compatible SDK clients require a non-empty constructor value
            # even when the target transport intentionally ignores credentials.
            api_key = "credential-not-required"
        return ModelBinding(
            OpenAIChatCompletionsModel(
                model=model,
                openai_client=AsyncOpenAI(
                    base_url=connection["base_url"],
                    api_key=api_key,
                    timeout=self.timeout_seconds,
                    max_retries=0,
                ),
                strict_feature_validation=False,
                buffer_streamed_tool_calls=True,
            ),
            bool(api_key) or not requires_key,
            False,
        )
