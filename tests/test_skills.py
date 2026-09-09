from pathlib import Path

import pytest

from digital_company.store import CompanyStore


def test_builtin_skills_are_versioned_and_visible_to_ceo(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a company", 1000)
    skills = store.snapshot().skills
    assert len(skills) >= 7
    assert all(skill["version"] and skill["status"] == "available" for skill in skills)


def test_skill_resolution_enforces_role_and_action(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a company", 1000)
    resolved = store.resolve_skills(["market-evidence"], "research", "research_market")
    assert resolved[0]["tools"] == ["web_search"]
    with pytest.raises(ValueError, match="not valid"):
        store.resolve_skills(["market-evidence"], "development", "build_mvp")
    with pytest.raises(ValueError, match="unavailable"):
        store.resolve_skills(["invented-superpower"], "research", "research_market")


def test_legacy_task_without_skill_ids_gets_safe_default(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a company", 1000)
    resolved = store.resolve_skills([], "qa", "qa_mvp")
    assert [skill["id"] for skill in resolved] == ["quality-gate"]


def test_agent_skill_mismatch_is_replaced_with_safe_matching_default(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a company", 1000)
    resolved = store.resolve_skills(
        ["market-evidence"],
        "product",
        "define_product",
        strict=False,
    )
    assert [skill["id"] for skill in resolved] == ["lean-product"]
