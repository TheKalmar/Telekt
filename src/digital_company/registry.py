"""Portfolio registry that isolates multiple digital companies."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

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

    def claim_work(self, company_id: str, owner: str, lease_seconds: int = 3600) -> bool:
        """Atomically claim one company's next cycle across worker processes."""
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
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
        self.db.execute("DELETE FROM work_leases WHERE company_id=? AND owner=?", (company_id, owner))
        self.db.commit()

    def heartbeat_worker(self, owner: str) -> None:
        """Record worker liveness in shared durable state."""
        self.db.execute(
            "INSERT OR REPLACE INTO worker_heartbeats(owner,heartbeat_at) VALUES(?,?)",
            (owner, utc_now()),
        )
        self.db.commit()

    def worker_status(self, stale_after_seconds: int = 15) -> dict:
        """Return whether any worker heartbeat is recent enough to be operational."""
        row = self.db.execute(
            "SELECT owner,heartbeat_at FROM worker_heartbeats ORDER BY heartbeat_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"status": "offline", "owner": None, "heartbeat_at": None}
        heartbeat = datetime.fromisoformat(row["heartbeat_at"])
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
        database_url = __import__("os").getenv("DATABASE_URL")
        use_postgres = __import__("os").getenv("DATABASE_BACKEND", "sqlite") == "postgres"
        if use_postgres and not database_url:
            raise RuntimeError("DATABASE_URL is required when DATABASE_BACKEND=postgres")
        store = CompanyStore(db_path, database_url if use_postgres else None,
                             company_id if use_postgres else None)
        store.initialize(profile["goal"], float(profile["budget"]), profile)
        now = utc_now()
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
        for row in self.db.execute("SELECT * FROM companies ORDER BY created_at"):
            item = dict(row)
            store = self.store_for(item["id"])
            item["runtime"] = store.get_control()["state"]
            item["active"] = item["id"] == active
            result.append(item)
        return result

    def get(self, company_id: str) -> dict:
        """Return registry metadata or raise ``KeyError`` for an unknown ID."""
        row = self.db.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
        if not row:
            raise KeyError(company_id)
        return dict(row)

    def active_id(self) -> str:
        """Return the company currently selected in the control plane."""
        row = self.db.execute("SELECT active_company_id FROM registry_settings WHERE id=1").fetchone()
        if not row or not row["active_company_id"]:
            raise RuntimeError("No company exists yet")
        return row["active_company_id"]

    def select(self, company_id: str) -> dict:
        """Select a company for subsequent dashboard/API operations."""
        company = self.get(company_id)
        self.db.execute("UPDATE registry_settings SET active_company_id=? WHERE id=1", (company_id,))
        self.db.commit()
        return company

    def store_for(self, company_id: str | None = None) -> CompanyStore:
        """Open the selected or explicitly requested company's state store."""
        selected_id = company_id or self.active_id()
        company = self.get(selected_id)
        if __import__("os").getenv("DATABASE_BACKEND", "sqlite") == "postgres":
            database_url = __import__("os").getenv("DATABASE_URL")
            if not database_url:
                raise RuntimeError("DATABASE_URL is required when DATABASE_BACKEND=postgres")
            return CompanyStore(Path(company["db_path"]), database_url, selected_id)
        return CompanyStore(Path(company["db_path"]))

    def artifacts_for(self, company_id: str | None = None) -> Path:
        """Return the isolated artifact root for a company."""
        return Path(self.get(company_id or self.active_id())["artifacts_path"])
