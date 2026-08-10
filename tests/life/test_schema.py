"""Schema initialization security tests."""

from __future__ import annotations

from typing import Any

from openjarvis.life.schema import ensure_schema


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _PostgresDatabase:
    backend = "postgres"

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    def executescript(self, statements: Any) -> None:
        self.statements.extend(statements)

    def execute(self, statement: str, params: object = ()) -> _Rows:
        self.statements.append(statement)
        if "FROM pg_roles" in statement:
            return _Rows([{"rolname": "anon"}, {"rolname": "authenticated"}])
        return _Rows([])

    def commit(self) -> None:
        self.commits += 1


def test_postgres_schema_blocks_direct_supabase_data_api_access() -> None:
    database = _PostgresDatabase()

    ensure_schema(database)  # type: ignore[arg-type]

    assert 'ALTER TABLE "users" ENABLE ROW LEVEL SECURITY' in database.statements
    assert 'REVOKE ALL ON TABLE "users" FROM "anon"' in database.statements
    assert (
        'REVOKE ALL ON TABLE "jarvis_ai_cost_events" FROM "authenticated"'
        in database.statements
    )
    assert any(
        "INSERT INTO life_schema_migrations" in statement
        for statement in database.statements
    )
    assert database.commits == 1
