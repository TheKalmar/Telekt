"""Portfolio registry that isolates multiple digital companies."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from digital_company.postgres_compat import company_schema
from digital_company.store import CompanyStore, utc_now


class CompanyRegistry:
    """Map company IDs to isolated databases and artifact directories.

    The registry stores only portfolio metadata. Business state remains inside
    each company's own :class:`CompanyStore`, preventing cross-company context
    leakage and simplifying future per-tenant migration.
    """
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        self.is_postgres = os.getenv("DATABASE_BACKEND", "sqlite") == "postgres"
        if self.is_postgres:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise RuntimeError("DATABASE_URL is required when DATABASE_BACKEND=postgres")
            # Registry operations are short control-plane statements. Autocommit
            # prevents dashboard reads from remaining idle in transaction and
            # blocking another app/worker instance during schema reconciliation.
            # Multi-statement lease acquisition still opens an explicit
            # transaction in claim_work().
            self.db = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
            self._initialize_postgres()
            self._import_legacy_registry()
        else:
            self.db = sqlite3.connect(state_dir / "registry.db", timeout=30, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=30000")
            self.db.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, company_type TEXT NOT NULL,
          concept TEXT NOT NULL, db_path TEXT NOT NULL, artifacts_path TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS registry_settings (
          id INTEGER PRIMARY KEY CHECK(id=1), active_company_id TEXT
        );
        CREATE TABLE IF NOT EXISTS work_leases (
          company_id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS worker_heartbeats (
          owner TEXT PRIMARY KEY, heartbeat_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO registry_settings(id,active_company_id) VALUES(1,NULL);
        """)
            self.db.commit()
            self._adopt_legacy_company()

    def _initialize_postgres(self) -> None:
        """Create portfolio control tables for existing and fresh installations."""
        self.db.execute("CREATE SCHEMA IF NOT EXISTS telekt")
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS telekt.registry_settings (
          id integer PRIMARY KEY CHECK(id=1), active_company_id text
        )
        """)
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS telekt.work_leases (
          company_id text PRIMARY KEY, owner text NOT NULL, expires_at timestamptz NOT NULL
        )
        """)
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS telekt.worker_heartbeats (
          owner text PRIMARY KEY, heartbeat_at timestamptz NOT NULL
        )
        """)
        self.db.execute("ALTER TABLE telekt.companies ADD COLUMN IF NOT EXISTS db_path text")
        self.db.execute(
            "INSERT INTO telekt.registry_settings(id,active_company_id) VALUES(1,NULL) "
            "ON CONFLICT(id) DO NOTHING"
        )
        self.db.execute(
            "INSERT INTO telekt.schema_version(version,description) "
            "VALUES(3,'PostgreSQL portfolio registry') ON CONFLICT(version) DO NOTHING"
        )
        self.db.commit()

    def _import_legacy_registry(self) -> None:
        """Idempotently import portfolio metadata from registry.db when present.

        Company business state has its own migration. This cutover imports the
        portfolio selection and paths, while heartbeats are copied only as
        diagnostic history. Work leases are deliberately not copied because a
        lock held by a dead pre-cutover worker must never block new execution.
        """
        registry_path = self.state_dir / "registry.db"
        if not registry_path.exists():
            return
        source = sqlite3.connect(f"file:{registry_path.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "companies" not in tables:
                return
            for row in source.execute("SELECT * FROM companies"):
                company_id = row["id"]
                existing = self.db.execute(
                    "SELECT id FROM telekt.companies WHERE source_id=%s OR id::text=%s",
                    (company_id, company_id),
                ).fetchone()
                if existing:
                    self.db.execute(
                        "UPDATE telekt.companies SET source_id=COALESCE(source_id,%s),"
                        "db_path=COALESCE(db_path,%s),artifacts_path=COALESCE(artifacts_path,%s) WHERE id=%s",
                        (company_id, row["db_path"], row["artifacts_path"], existing["id"]),
                    )
                    continue
                source_store = sqlite3.connect(f"file:{Path(row['db_path']).as_posix()}?mode=ro", uri=True)
                source_store.row_factory = sqlite3.Row
                try:
                    header = source_store.execute("SELECT goal,initial_budget_eur FROM company WHERE id=1").fetchone()
                    control = source_store.execute("SELECT state FROM runtime_control WHERE id=1").fetchone()
                    profile_row = source_store.execute("SELECT profile_json FROM company_profile WHERE id=1").fetchone()
                    profile = json.loads(profile_row[0]) if profile_row else {}
                finally:
                    source_store.close()
                self.db.execute(
                    "INSERT INTO telekt.companies(id,source_id,name,company_type,concept,goal,initial_budget,"
                    "profile,runtime_state,db_path,artifacts_path,created_at,updated_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                    (company_schema(company_id).removeprefix("company_"), company_id, row["name"],
                     row["company_type"], row["concept"], header["goal"], header["initial_budget_eur"],
                     json.dumps(profile), control["state"], row["db_path"], row["artifacts_path"],
                     row["created_at"], row["updated_at"]),
                )
            if "registry_settings" in tables:
                setting = source.execute("SELECT active_company_id FROM registry_settings WHERE id=1").fetchone()
                if setting and setting["active_company_id"]:
                    self.db.execute(
                        "UPDATE telekt.registry_settings SET active_company_id=%s "
                        "WHERE id=1 AND active_company_id IS NULL",
                        (setting["active_company_id"],),
                    )
            if "worker_heartbeats" in tables:
                for heartbeat in source.execute("SELECT owner,heartbeat_at FROM worker_heartbeats"):
                    self.db.execute(
                        "INSERT INTO telekt.worker_heartbeats(owner,heartbeat_at) VALUES(%s,%s) "
                        "ON CONFLICT(owner) DO UPDATE SET heartbeat_at=GREATEST("
                        "telekt.worker_heartbeats.heartbeat_at,excluded.heartbeat_at)",
                        (heartbeat["owner"], heartbeat["heartbeat_at"]),
                    )
            self.db.commit()
        finally:
            source.close()

    def claim_work(self, company_id: str, owner: str, lease_seconds: int = 3600) -> bool:
        """Atomically claim one company's next cycle across worker processes."""
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
        if self.is_postgres:
            row = self.db.execute(
                "INSERT INTO telekt.work_leases(company_id,owner,expires_at) VALUES(%s,%s,%s) "
                "ON CONFLICT(company_id) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at "
                "WHERE telekt.work_leases.owner=excluded.owner "
                "OR telekt.work_leases.expires_at<=now() RETURNING owner",
                (company_id, owner, expires_at),
            ).fetchone()
            return row is not None
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT owner,expires_at FROM work_leases WHERE company_id=?", (company_id,)
            ).fetchone()
            available = not row or row["owner"] == owner or row["expires_at"] <= now.isoformat()
            if available:
                self.db.execute(
                    "INSERT OR REPLACE INTO work_leases(company_id,owner,expires_at) VALUES(?,?,?)",
                    (company_id, owner, expires_at),
                )
            self.db.commit()
            return available
        except Exception:
            self.db.rollback()
            raise

    def release_work(self, company_id: str, owner: str) -> None:
        """Release a lease only when it still belongs to this worker."""
        query = ("DELETE FROM telekt.work_leases WHERE company_id=%s AND owner=%s" if self.is_postgres
                 else "DELETE FROM work_leases WHERE company_id=? AND owner=?")
        self.db.execute(query, (company_id, owner))
        self.db.commit()

    def heartbeat_worker(self, owner: str) -> None:
        """Record worker liveness in shared durable state."""
        if self.is_postgres:
            self.db.execute(
                "INSERT INTO telekt.worker_heartbeats(owner,heartbeat_at) VALUES(%s,%s) "
                "ON CONFLICT(owner) DO UPDATE SET heartbeat_at=excluded.heartbeat_at", (owner, utc_now())
            )
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO worker_heartbeats(owner,heartbeat_at) VALUES(?,?)",
                (owner, utc_now()),
            )
        self.db.commit()

    def worker_status(self, stale_after_seconds: int = 15) -> dict:
        """Return whether any worker heartbeat is recent enough to be operational."""
        table = "telekt.worker_heartbeats" if self.is_postgres else "worker_heartbeats"
        row = self.db.execute(f"SELECT owner,heartbeat_at FROM {table} ORDER BY heartbeat_at DESC LIMIT 1").fetchone()
        if not row:
            return {"status": "offline", "owner": None, "heartbeat_at": None}
        heartbeat = row["heartbeat_at"]
        if isinstance(heartbeat, str):
            heartbeat = datetime.fromisoformat(heartbeat)
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
        return {
            "status": "online" if age <= stale_after_seconds else "offline",
            "owner": row["owner"],
            "heartbeat_at": row["heartbeat_at"],
        }

    def _adopt_legacy_company(self) -> None:
        """Register the original POC database once without moving or rewriting it."""
        count = self.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        legacy_db = self.state_dir / "company.db"
        if count or not legacy_db.exists():
            return
        store = CompanyStore(legacy_db)
        if not store.is_initialized():
            return
        company_id = "invoice-chase-ventures"
        now = utc_now()
        self.db.execute(
            "INSERT INTO companies VALUES(?,?,?,?,?,?,?,?)",
            (company_id, "Invoice Chase Ventures", "SaaS",
             "B2B invoice tracking and collections software", str(legacy_db),
             str(self.state_dir / "artifacts"), now, now),
        )
        self.db.execute("UPDATE registry_settings SET active_company_id=? WHERE id=1", (company_id,))
        if not store.get_profile():
            snapshot = store.snapshot()
            store.db.execute(
                "INSERT OR REPLACE INTO company_profile VALUES(1,?,?)",
                (json.dumps({
                    "name": "Invoice Chase Ventures", "company_type": "SaaS",
                    "concept": "B2B invoice tracking and collections software",
                    "description": "Original autonomous company POC",
                    "target_market": "Small B2B service companies", "customer_type": "B2B",
                    "currency": "EUR", "time_horizon_days": 30,
                    "risk_tolerance": "medium", "autonomy_level": "balanced",
                    "constraints": ["Approval before external outreach", "Approval before spending"],
                    "success_criteria": ["Validated problem", "Functional MVP", "Pilot interest"],
                    "goal": snapshot.goal,
                }), now),
            )
            store.db.commit()
        self.db.commit()

    def create(self, profile: dict) -> dict:
        """Create an isolated company, make it active, and return registry metadata."""
        company_id = str(uuid4())
        company_dir = self.state_dir / "companies" / company_id
        db_path = company_dir / "company.db"
        artifacts_path = company_dir / "artifacts"
        database_url = os.getenv("DATABASE_URL")
        if self.is_postgres and not database_url:
            raise RuntimeError("DATABASE_URL is required when DATABASE_BACKEND=postgres")
        store = CompanyStore(db_path, database_url if self.is_postgres else None,
                             company_id if self.is_postgres else None)
        store.initialize(profile["goal"], float(profile["budget"]), profile)
        now = utc_now()
        if self.is_postgres:
            self.db.execute(
                "INSERT INTO telekt.companies(id,source_id,name,company_type,concept,goal,initial_budget,"
                "profile,runtime_state,db_path,artifacts_path,created_at,updated_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'stopped',%s,%s,%s,%s)",
                (company_id, company_id, profile["name"], profile["company_type"], profile["concept"],
                 profile["goal"], float(profile["budget"]), json.dumps(profile), str(db_path),
                 str(artifacts_path), now, now),
            )
            self.db.execute("UPDATE telekt.registry_settings SET active_company_id=%s WHERE id=1", (company_id,))
        else:
            self.db.execute(
                "INSERT INTO companies VALUES(?,?,?,?,?,?,?,?)",
                (company_id, profile["name"], profile["company_type"], profile["concept"],
                 str(db_path), str(artifacts_path), now, now),
            )
            self.db.execute("UPDATE registry_settings SET active_company_id=? WHERE id=1", (company_id,))
        self.db.commit()
        return self.get(company_id)

    def list(self) -> list[dict]:
        """List portfolio companies enriched with current runtime state."""
        try:
            active = self.active_id()
        except RuntimeError:
            active = None
        result = []
        query = ("SELECT COALESCE(source_id,id::text) AS id,name,company_type,concept,db_path,"
                 "artifacts_path,created_at,updated_at FROM telekt.companies ORDER BY created_at"
                 if self.is_postgres else "SELECT * FROM companies ORDER BY created_at")
        for row in self.db.execute(query):
            item = dict(row)
            item["created_at"] = str(item["created_at"])
            item["updated_at"] = str(item["updated_at"])
            store = self.store_for(item["id"])
            item["runtime"] = store.get_control()["state"]
            item["active"] = item["id"] == active
            result.append(item)
        return result

    def get(self, company_id: str) -> dict:
        """Return registry metadata or raise ``KeyError`` for an unknown ID."""
        if self.is_postgres:
            row = self.db.execute(
                "SELECT COALESCE(source_id,id::text) AS id,name,company_type,concept,db_path,"
                "artifacts_path,created_at,updated_at FROM telekt.companies "
                "WHERE source_id=%s OR id::text=%s", (company_id, company_id)
            ).fetchone()
        else:
            row = self.db.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
        if not row:
            raise KeyError(company_id)
        return dict(row)

    def active_id(self) -> str:
        """Return the company currently selected in the control plane."""
        table = "telekt.registry_settings" if self.is_postgres else "registry_settings"
        row = self.db.execute(f"SELECT active_company_id FROM {table} WHERE id=1").fetchone()
        if not row or not row["active_company_id"]:
            raise RuntimeError("No company exists yet")
        return row["active_company_id"]

    def select(self, company_id: str) -> dict:
        """Select a company for subsequent dashboard/API operations."""
        company = self.get(company_id)
        query = ("UPDATE telekt.registry_settings SET active_company_id=%s WHERE id=1" if self.is_postgres
                 else "UPDATE registry_settings SET active_company_id=? WHERE id=1")
        self.db.execute(query, (company_id,))
        self.db.commit()
        return company

    def store_for(self, company_id: str | None = None) -> CompanyStore:
        """Open the selected or explicitly requested company's state store."""
        selected_id = company_id or self.active_id()
        company = self.get(selected_id)
        if self.is_postgres:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise RuntimeError("DATABASE_URL is required when DATABASE_BACKEND=postgres")
            # PostgreSQL holds all business data; this path is only a local
            # compatibility argument. Never reuse a host-specific path inside
            # another runtime (for example Windows metadata inside Docker).
            runtime_db = self.state_dir / "companies" / selected_id / "company.db"
            return CompanyStore(runtime_db, database_url, selected_id)
        return CompanyStore(Path(company["db_path"]))

    def artifacts_for(self, company_id: str | None = None) -> Path:
        """Return the isolated artifact root for a company."""
        selected_id = company_id or self.active_id()
        configured = self.get(selected_id)["artifacts_path"]
        path = Path(configured) if configured else self.state_dir / "companies" / selected_id / "artifacts"
        if self.is_postgres and not path.exists():
            path = self.state_dir / "companies" / selected_id / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path
