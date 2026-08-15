import json
import sqlite3
from pathlib import Path

from digital_company.store import CompanyStore
from digital_company.text_encoding import repair_text_encoding


def _broken(value: str) -> str:
    return value.encode("utf-8").decode("latin-1")


def test_repair_text_encoding_is_recursive_and_idempotent():
    broken = {
        "purpose": _broken("Povećati tržišnu vidljivost → više sadržaja"),
        "rules": [_broken("Čuvaj tačne činjenice")],
    }
    expected = {
        "purpose": "Povećati tržišnu vidljivost → više sadržaja",
        "rules": ["Čuvaj tačne činjenice"],
    }
    assert repair_text_encoding(broken) == expected
    assert repair_text_encoding(expected) == expected
    assert repair_text_encoding("Café G&K") == "Café G&K"


def test_schema_v11_repairs_existing_company_and_agent_text(tmp_path: Path):
    path = tmp_path / "company.db"
    with CompanyStore(path) as store:
        store.initialize(
            "Ispravan cilj", 10,
            {"name": "G&K", "goal": "Ispravan cilj", "concept": "Ispravan koncept"},
            bootstrap_legacy_agent=False,
        )
        agent = store.create_agent({
            "name": "Content & SEO", "agent_type": "content_seo",
            "role": "content strategist", "purpose": "Ispravna svrha",
            "instructions": "Ispravne instrukcije", "model_connection_id": "cloud-default",
            "config": {
                "content_language": "sr-Latn", "target_audience": "Građani",
                "content_scope": "Pravni vodiči", "brand_voice": "Jasan",
            },
        })

    db = sqlite3.connect(path)
    db.execute("DELETE FROM company_schema_versions WHERE version=11")
    db.execute("UPDATE company SET goal=? WHERE id=1", (_broken("Povećati vidljivost"),))
    db.execute(
        "UPDATE company_profile SET profile_json=? WHERE id=1",
        (json.dumps({"name": "G&K", "goal": _broken("Povećati vidljivost")}),),
    )
    db.execute(
        "UPDATE agent_instances SET purpose=?,config_json=? WHERE id=?",
        (
            _broken("Povećavati organsku vidljivost"),
            json.dumps({
                "content_language": "sr-Latn",
                "target_audience": _broken("Građani"),
                "content_scope": _broken("Pravni vodiči"),
                "brand_voice": "Jasan",
                "require_official_sources": True,
                "seo_target_score": 70,
                "minimum_word_count": 700,
            }),
            agent["id"],
        ),
    )
    db.commit()
    db.close()

    with CompanyStore(path) as reopened:
        repaired = reopened.get_agent(agent["id"])
        assert reopened.snapshot().goal == "Povećati vidljivost"
        assert reopened.get_profile()["goal"] == "Povećati vidljivost"
        assert repaired["purpose"] == "Povećavati organsku vidljivost"
        assert repaired["config"]["target_audience"] == "Građani"
        assert reopened.schema_version() == 12
