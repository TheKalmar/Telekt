"""Built-in agent types with typed setup fields and bounded responsibilities."""

from __future__ import annotations

from digital_company.text_encoding import repair_text_encoding


AGENT_TYPES = [
    {
        "id": "custom",
        "name": "Custom agent",
        "description": "A manually defined role. Plugin grants still enforce capabilities.",
        "default_role": "specialist",
        "default_purpose": "Complete work inside the configured mandate.",
        "default_token_limit": 100_000,
        "default_spend_limit_eur": 10,
        "suggested_plugins": [],
        "allowed_actions": None,
        "config_schema": {
            "type": "object", "properties": {}, "additionalProperties": True,
        },
    },
    {
        "id": "ceo",
        "name": "CEO / General Manager",
        "description": "Chooses priorities, coordinates work, and escalates only real blockers.",
        "default_role": "ceo",
        "default_purpose": "Advance company goals through evidence-backed, reversible decisions.",
        "default_token_limit": 250_000,
        "default_spend_limit_eur": 30,
        "suggested_plugins": ["web-research", "email-communication"],
        "allowed_actions": None,
        "config_schema": {
            "type": "object",
            "properties": {
                "decision_cadence": {
                    "type": "string", "enum": ["continuous", "daily", "on_event"],
                    "default": "continuous",
                },
                "stakeholder_brief": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "id": "content_seo",
        "name": "Content & SEO",
        "description": "Researches topics, creates reviewable content, and manages publishing steps.",
        "default_role": "content and SEO strategist",
        "default_purpose": "Grow qualified organic visibility with accurate, useful content.",
        "default_token_limit": 150_000,
        "default_spend_limit_eur": 15,
        "suggested_plugins": [
            "web-research", "workspace-content", "wordpress-content",
            "featured-image-generation", "email-communication",
        ],
        "allowed_actions": [
            "research_content", "create_content_draft", "save_content_draft",
            "publish_content", "browser_operate", "request_human_handoff",
            "request_platform_access", "discover_tool", "stop",
        ],
        "config_schema": {
            "type": "object",
            "required": ["content_language", "target_audience", "content_scope"],
            "properties": {
                "content_language": {"type": "string", "default": "sr-Latn"},
                "target_audience": {"type": "string"},
                "content_scope": {"type": "string"},
                "brand_voice": {"type": "string", "default": "professional and clear"},
                "require_official_sources": {"type": "boolean", "default": True},
                "seo_target_score": {"type": "integer", "default": 70, "minimum": 0, "maximum": 100},
                "minimum_word_count": {"type": "integer", "default": 700, "minimum": 300, "maximum": 5000},
            },
            "additionalProperties": False,
        },
    },
]


def get_agent_type(agent_type: str) -> dict:
    value = next((item for item in AGENT_TYPES if item["id"] == agent_type), None)
    if not value:
        raise ValueError(f"Unknown agent type: {agent_type}")
    return value


def validate_agent_config(agent_type: str, config: dict) -> dict:
    """Validate the small schema subset used by the configuration UI."""
    definition = get_agent_type(agent_type)
    schema = definition["config_schema"]
    properties = schema.get("properties", {})
    normalized = repair_text_encoding(dict(config or {}))
    if schema.get("additionalProperties") is False:
        unknown = set(normalized) - set(properties)
        if unknown:
            raise ValueError("Unknown agent configuration field(s): " + ", ".join(sorted(unknown)))
    for name, rules in properties.items():
        if name not in normalized and "default" in rules:
            normalized[name] = rules["default"]
        if name not in normalized:
            continue
        value = normalized[name]
        if rules.get("type") == "integer" and isinstance(value, str) and value.strip().isdigit():
            value = normalized[name] = int(value)
        if rules.get("type") == "string" and not isinstance(value, str):
            raise ValueError(f"Agent field {name} must be a string")
        if rules.get("type") == "boolean" and not isinstance(value, bool):
            raise ValueError(f"Agent field {name} must be true or false")
        if rules.get("type") == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"Agent field {name} must be an integer")
            if value < rules.get("minimum", value) or value > rules.get("maximum", value):
                raise ValueError(f"Agent field {name} is outside the supported range")
        if "enum" in rules and value not in rules["enum"]:
            raise ValueError(f"Agent field {name} has an unsupported value")
    missing = [
        name for name in schema.get("required", [])
        if name not in normalized or not str(normalized[name]).strip()
    ]
    if missing:
        raise ValueError("Missing agent configuration field(s): " + ", ".join(missing))
    return normalized


def action_is_allowed(agent_type: str, action: str) -> bool:
    allowed = get_agent_type(agent_type)["allowed_actions"]
    return allowed is None or action in allowed
