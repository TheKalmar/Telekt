"""Small DB-API compatibility layer used while CompanyStore moves to PostgreSQL."""

from __future__ import annotations

import atexit
import os
import re
from threading import Lock
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_POOLS: dict[str, ConnectionPool] = {}
_POOLS_LOCK = Lock()


def _pool_for(dsn: str) -> ConnectionPool:
    """Return one bounded process-local pool without logging the credential DSN."""
    with _POOLS_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None:
            maximum = max(2, int(os.getenv("POSTGRES_POOL_MAX_SIZE", "10")))
            timeout = max(1.0, float(os.getenv("POSTGRES_POOL_TIMEOUT_SECONDS", "10")))
            pool = ConnectionPool(
                dsn,
                min_size=0,
                max_size=maximum,
                timeout=timeout,
                kwargs={"row_factory": dict_row},
                open=True,
                name="telekt-company-store",
            )
            _POOLS[dsn] = pool
        return pool


def pool_stats() -> dict:
    """Return non-secret aggregate pool telemetry for health diagnostics."""
    totals: dict[str, int] = {}
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
    for pool in pools:
        for key, value in pool.get_stats().items():
            totals[key] = totals.get(key, 0) + int(value)
    return {"pool_count": len(pools), **totals}


def close_pools() -> None:
    """Close all pools during process shutdown."""
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


atexit.register(close_pools)


def company_schema(company_id: str) -> str:
    try:
        value = UUID(company_id)
    except ValueError:
        value = uuid5(NAMESPACE_URL, "telekt-company:" + company_id)
    return "company_" + value.hex


class PostgresCompat:
    """Expose the subset of sqlite connection behavior used by CompanyStore."""

    is_postgres = True

    def __init__(self, dsn: str, schema: str):
        self.pool = _pool_for(dsn)
        self.connection = self.pool.getconn()
        self._closed = False
        try:
            self.connection.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
            )
            self.connection.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            self.pool.putconn(self.connection)
            self._closed = True
            raise

    @staticmethod
    def _translate(query: str) -> str:
        query = query.strip()
        if query.upper() == "BEGIN IMMEDIATE":
            return "BEGIN"
        query = query.replace("?", "%s")
        if re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO", query, re.I):
            query = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", query, flags=re.I)
            query += " ON CONFLICT DO NOTHING"
        return query

    def execute(self, query: str, params=()):
        normalized = query.strip().upper()
        if (
            normalized in {"BEGIN", "BEGIN IMMEDIATE"}
            and self.connection.info.transaction_status != TransactionStatus.IDLE
        ):
            # Read projections may leave psycopg's implicit transaction open.
            # Explicit write transactions always start at a clean boundary.
            self.connection.commit()
        if query.strip().upper().startswith("PRAGMA TABLE_INFO("):
            table = query[query.find("(") + 1 : query.rfind(")")].strip()
            return self.connection.execute(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position",
                (table,),
            )
        return self.connection.execute(self._translate(query), params)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.connection.rollback()
        finally:
            self.pool.putconn(self.connection)
            self._closed = True
