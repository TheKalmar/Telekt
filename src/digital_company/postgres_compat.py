"""Small DB-API compatibility layer used while CompanyStore moves to PostgreSQL."""
from __future__ import annotations

import re
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


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
        self.connection = psycopg.connect(dsn, row_factory=dict_row)
        self.connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        self.connection.commit()

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
        if query.strip().upper().startswith("PRAGMA TABLE_INFO("):
            table = query[query.find("(") + 1:query.rfind(")")].strip()
            return self.connection.execute(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position", (table,),
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
        self.connection.close()
