"""Losslessly copy the SQLite portfolio into PostgreSQL without mutating source files."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg.types.json import Jsonb
from digital_company.store import CompanyStore

TABLES = [
    "company", "tasks", "approvals", "ledger", "audit_events", "runtime_control",
    "stakeholder_messages", "runtime_settings", "company_profile", "integrations",
    "human_handoffs", "model_usage",
]


def canonical_uuid(source_id: str) -> UUID:
    try:
        return UUID(source_id)
    except ValueError:
        return uuid5(NAMESPACE_URL, "telekt-company:" + source_id)


def rows(db: sqlite3.Connection, table: str) -> list[dict]:
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}"')] if exists else []


def migrate(state_dir: Path, database_url: str) -> dict:
    registry_path = state_dir / "registry.db"
    if not registry_path.exists():
        raise RuntimeError(f"Registry not found: {registry_path}")
    registry = sqlite3.connect(f"file:{registry_path.as_posix()}?mode=ro", uri=True)
    registry.row_factory = sqlite3.Row
    companies = [dict(row) for row in registry.execute("SELECT * FROM companies ORDER BY created_at")]
    verification, native_verification, total = {}, {}, 0
    with psycopg.connect(database_url) as target:
        with target.transaction():
            for item in companies:
                company_id = canonical_uuid(item["id"])
                source_db = sqlite3.connect(f"file:{Path(item['db_path']).as_posix()}?mode=ro", uri=True)
                source_db.row_factory = sqlite3.Row
                header = source_db.execute("SELECT * FROM company WHERE id=1").fetchone()
                profile_row = source_db.execute("SELECT profile_json FROM company_profile WHERE id=1").fetchone()
                profile = json.loads(profile_row[0]) if profile_row else {}
                target.execute(
                    "INSERT INTO telekt.companies(id,source_id,name,company_type,concept,goal,initial_budget,profile,runtime_state,artifacts_path) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name,company_type=excluded.company_type,concept=excluded.concept,goal=excluded.goal,"
                    "initial_budget=excluded.initial_budget,profile=excluded.profile,runtime_state=excluded.runtime_state,"
                    "artifacts_path=excluded.artifacts_path,updated_at=now()",
                    (company_id, item["id"], item["name"], item["company_type"], item["concept"],
                     header["goal"], header["initial_budget_eur"], Jsonb(profile),
                     source_db.execute("SELECT state FROM runtime_control WHERE id=1").fetchone()[0],
                     item["artifacts_path"]),
                )
                counts = {}
                for table in TABLES:
                    source_rows = rows(source_db, table)
                    counts[table] = len(source_rows)
                    for index, payload in enumerate(source_rows):
                        record_id = str(payload.get("id", index))
                        target.execute(
                            "INSERT INTO telekt.company_records(company_id,record_type,record_id,payload,source_created_at) "
                            "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(company_id,record_type,record_id) DO UPDATE SET "
                            "payload=excluded.payload,source_created_at=excluded.source_created_at,imported_at=now()",
                            (company_id, table, record_id, Jsonb(payload), payload.get("created_at")),
                        )
                    imported = target.execute(
                        "SELECT count(*) FROM telekt.company_records WHERE company_id=%s AND record_type=%s",
                        (company_id, table),
                    ).fetchone()[0]
                    if imported != len(source_rows):
                        raise RuntimeError(f"Verification failed for {item['id']}:{table}: {len(source_rows)} != {imported}")
                    total += len(source_rows)
                verification[item["id"]] = counts
                # Populate the native per-company schema used by CompanyStore.
                native = CompanyStore(Path(item["db_path"]), database_url, item["id"])
                for table in reversed(TABLES):
                    native.db.execute(f'DELETE FROM "{table}"')
                native.db.commit()
                native_counts = {}
                for table in TABLES:
                    source_rows = rows(source_db, table)
                    if source_rows:
                        columns = list(source_rows[0])
                        names = ",".join(f'"{name}"' for name in columns)
                        placeholders = ",".join("?" for _ in columns)
                        for payload in source_rows:
                            native.db.execute(
                                f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                                tuple(payload[name] for name in columns),
                            )
                    native.db.commit()
                    imported = native.db.execute(f'SELECT count(*) AS value FROM "{table}"').fetchone()["value"]
                    if imported != len(source_rows):
                        raise RuntimeError(f"Native verification failed for {item['id']}:{table}")
                    native_counts[table] = imported
                native_verification[item["id"]] = native_counts
                native.db.close()
            run_id = uuid4()
            target.execute(
                "INSERT INTO telekt.migration_runs(id,source_path,company_count,record_count,verification) VALUES(%s,%s,%s,%s,%s)",
                (run_id, str(state_dir.resolve()), len(companies), total, Jsonb(verification)),
            )
    return {"status": "verified", "companies": len(companies), "records": total,
            "verification": verification, "native_verification": native_verification}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.getenv("COMPANY_DATA_DIR", ".company"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    print(json.dumps(migrate(Path(args.state_dir), args.database_url), indent=2))


if __name__ == "__main__":
    main()
