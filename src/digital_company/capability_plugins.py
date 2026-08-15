"""Trusted reusable capability packages assignable to independent agents."""

from __future__ import annotations

import re
from urllib.parse import urlparse


BUILTIN_PLUGINS = [
    {
        "id": "web-research",
        "name": "Web Research",
        "version": "1.0.0",
        "description": "Search and inspect public web sources with evidence requirements.",
        "tools": ["web_search"],
        "connection_kinds": [],
        "permissions": ["read_public_web"],
        "config_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "instructions": "Cite direct sources, distinguish facts from inference, and never invent demand or evidence.",
    },
    {
        "id": "workspace-content",
        "name": "Content Workspace",
        "version": "1.0.0",
        "description": "Create and revise reviewable Markdown content inside the company workspace.",
        "tools": ["workspace"],
        "connection_kinds": [],
        "permissions": ["read_workspace", "write_workspace"],
        "config_schema": {
            "type": "object",
            "properties": {
                "draft_directory": {"type": "string", "default": "content/drafts"},
                "default_language": {"type": "string", "default": "sr"},
            },
            "additionalProperties": False,
        },
        "instructions": "Keep research, draft, review and publication as separately auditable stages.",
    },
    {
        "id": "wordpress-content",
        "name": "WordPress Content Publishing",
        "version": "1.0.0",
        "description": "Inspect posts, create unpublished drafts, preview them, and publish only with granted authority.",
        "tools": ["browser", "platform_api"],
        "connection_kinds": ["http_basic", "http_bearer", "http_api_key"],
        "permissions": ["read_posts", "write_drafts", "publish_posts"],
        "connection_capabilities": {
            "read_posts": "wordpress.posts.read",
            "write_drafts": "wordpress.posts.write_drafts",
            "publish_posts": "wordpress.posts.publish",
        },
        "config_schema": {
            "type": "object",
            "required": ["site_url", "posts_url"],
            "properties": {
                "site_url": {"type": "string", "format": "uri"},
                "posts_url": {"type": "string", "format": "uri"},
                "review_email": {"type": "string", "format": "email"},
                "default_post_status": {"type": "string", "enum": ["draft"], "default": "draft"},
            },
            "additionalProperties": False,
        },
        "instructions": "Drafting is reversible. Never publish unless publish_posts is granted and an exact content approval is active.",
    },
    {
        "id": "email-communication",
        "name": "Email Communication",
        "version": "1.0.1",
        "description": "Prepare email internally and send it through a separately configured SMTP connection.",
        "tools": ["email"],
        "connection_kinds": ["smtp"],
        "permissions": ["draft_email", "send_email"],
        "connection_capabilities": {
            "send_email": "email.send",
        },
        "config_schema": {
            "type": "object",
            "properties": {"sender_name": {"type": "string"}},
            "additionalProperties": False,
        },
        "instructions": "Drafting is internal and needs no mailbox capability. SMTP only sends; inbox reading requires a future inbound-mail connector. Sending requires the agent grant and active company policy authority.",
    },
]


ACTION_PLUGIN_REQUIREMENTS = {
    "research_market": ("web-research", "read_public_web"),
    "research_content": ("web-research", "read_public_web"),
    "create_content_draft": ("workspace-content", "write_workspace"),
    "save_content_draft": ("wordpress-content", "write_drafts"),
    "publish_content": ("wordpress-content", "publish_posts"),
    "external_outreach": ("email-communication", "send_email"),
}


def authorize_action(action: str, grants: list[dict]) -> tuple[bool, str]:
    """Apply least privilege independently of the model's own reasoning."""
    enabled = [item for item in grants if item.get("status") == "enabled"]
    requirement = ACTION_PLUGIN_REQUIREMENTS.get(action)
    if requirement:
        plugin_id, permission = requirement
        allowed = any(
            item.get("plugin_id") == plugin_id and permission in item.get("permissions", [])
            for item in enabled
        )
        if not allowed:
            return False, f"Action requires {plugin_id}:{permission}"
    if action == "browser_operate" and not any(
        "browser" in item.get("definition", {}).get("tools", []) for item in enabled
    ):
        return False, "Browser operation requires an enabled browser-capable plugin"
    return True, "Action is inside enabled plugin grants"


def validate_plugin_config(definition: dict, config: dict) -> dict:
    """Validate the small supported JSON-schema subset without executing plugin code."""
    schema = definition.get("config_schema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if schema.get("additionalProperties") is False:
        unknown = set(config) - set(properties)
        if unknown:
            raise ValueError("Unknown plugin configuration field(s): " + ", ".join(sorted(unknown)))
    missing = [name for name in required if not str(config.get(name, "")).strip()]
    if missing:
        raise ValueError("Missing plugin configuration field(s): " + ", ".join(sorted(missing)))
    normalized = dict(config)
    for name, rules in properties.items():
        if name not in normalized and "default" in rules:
            normalized[name] = rules["default"]
        if name not in normalized:
            continue
        value = normalized[name]
        if rules.get("type") == "string" and not isinstance(value, str):
            raise ValueError(f"Plugin field {name} must be a string")
        if "enum" in rules and value not in rules["enum"]:
            raise ValueError(f"Plugin field {name} has an unsupported value")
        if rules.get("format") == "uri":
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(f"Plugin field {name} must be a public HTTPS URL")
        if rules.get("format") == "email" and value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError(f"Plugin field {name} must be an email address")
    return normalized
