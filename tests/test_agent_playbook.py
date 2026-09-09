from pathlib import Path

from digital_company.store import CompanyStore


def test_playbook_is_versioned_and_memory_message_is_durable(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Publish useful content", 100)

    first = store.set_agent_playbook(
        "legacy-ceo",
        {
            "rules": ["Use short introductions."],
            "reference_examples": ["https://example.com/style"],
            "notes": "Owner-approved style.",
        },
    )
    message_id = store.add_stakeholder_message(
        "Never put 'SEO draft' in a public title.",
        "memory",
        agent_id="legacy-ceo",
    )
    second = store.append_agent_playbook_rule(
        "legacy-ceo",
        "Never put 'SEO draft' in a public title.",
        message_id,
    )

    assert first["version"] == 1
    assert second["version"] == 2
    assert second["document"]["rules"] == [
        "Use short introductions.",
        "Never put 'SEO draft' in a public title.",
    ]
    assert store.snapshot("legacy-ceo").stakeholder_messages[0]["status"] == "addressed"


def test_playbook_rules_are_deduplicated(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Publish useful content", 100)
    result = store.set_agent_playbook(
        "legacy-ceo",
        {
            "rules": ["One rule", "One rule"],
            "reference_examples": [],
            "notes": "",
        },
    )
    assert result["document"]["rules"] == ["One rule"]
