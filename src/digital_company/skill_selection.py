"""Pure role/action capability selection for versioned agent skills."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSelection:
    assigned: list[dict]
    rejected_ids: list[str]


def select_skills(
    catalog_items: list[dict],
    requested_ids: list[str],
    specialist: str,
    action: str,
    *,
    strict: bool = True,
) -> SkillSelection:
    """Validate model-selected skills and optionally recover to safe defaults."""
    catalog = {item["id"]: item for item in catalog_items}
    eligible = [
        item for item in catalog.values()
        if item["status"] == "available"
        and specialist in item["roles"]
        and action in item["actions"]
    ]
    skill_ids = requested_ids or [item["id"] for item in eligible[:3]]
    assigned = []
    rejected = []
    for skill_id in skill_ids:
        skill = catalog.get(skill_id)
        if not skill or skill["status"] != "available":
            if strict:
                raise ValueError(f"Skill is unavailable: {skill_id}")
            rejected.append(skill_id)
            continue
        if specialist not in skill["roles"] or action not in skill["actions"]:
            if strict:
                raise ValueError(f"Skill {skill_id} is not valid for {specialist}/{action}")
            rejected.append(skill_id)
            continue
        assigned.append(skill)
    if not strict and not assigned:
        assigned = eligible[:3]
    return SkillSelection(assigned, rejected)
