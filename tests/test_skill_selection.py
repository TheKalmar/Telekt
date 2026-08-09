from digital_company.skill_selection import select_skills


CATALOG = [{
    "id": "product-skill", "status": "available",
    "roles": ["product"], "actions": ["define_product"],
}]


def test_non_strict_selection_recovers_invalid_model_choice():
    result = select_skills(
        CATALOG, ["invented"], "product", "define_product", strict=False,
    )
    assert [item["id"] for item in result.assigned] == ["product-skill"]
    assert result.rejected_ids == ["invented"]
