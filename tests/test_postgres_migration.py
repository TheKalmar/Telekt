import sqlite3
from uuid import UUID

from digital_company.postgres_compat import company_schema
from scripts.migrate_sqlite_to_postgres import canonical_uuid, rows


def test_legacy_company_ids_map_to_stable_uuid():
    first = canonical_uuid("invoice-chase-ventures")
    assert isinstance(first, UUID)
    assert first == canonical_uuid("invoice-chase-ventures")
    assert canonical_uuid(str(first)) == first
    assert company_schema("invoice-chase-ventures") == "company_" + first.hex


def test_rows_handles_present_and_missing_tables():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT)")
    db.execute("INSERT INTO tasks VALUES('t1','Research')")
    assert rows(db, "tasks") == [{"id": "t1", "title": "Research"}]
    assert rows(db, "missing") == []
