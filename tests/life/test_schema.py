"""Schema initialization security tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from openjarvis.life.db import connect
from openjarvis.life.schema import CURRENT_SCHEMA_VERSION, ensure_schema


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _PostgresDatabase:
    backend = "postgres"

    def __init__(self, schema_version: int | None = None) -> None:
        self.statements: list[str] = []
        self.commits = 0
        self.transactions = 0
        self.schema_version = schema_version

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    def executescript(self, statements: Any) -> None:
        self.statements.extend(statements)

    def execute(self, statement: str, params: object = ()) -> _Rows:
        self.statements.append(statement)
        if "to_regclass" in statement:
            if self.schema_version is None:
                return _Rows([])
            return _Rows([{"table_name": "life_schema_migrations"}])
        if "COALESCE(MAX(version)" in statement:
            return _Rows([{"version": self.schema_version or 0}])
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
    assert (
        'REVOKE ALL ON TABLE "jarvis_dialog_sessions" FROM "authenticated"'
        in database.statements
    )
    assert any(
        "INSERT INTO life_schema_migrations" in statement
        for statement in database.statements
    )
    assert database.transactions == 1
    assert "pg_advisory_xact_lock" in database.statements[0]


def test_postgres_v2_migration_only_locks_new_dialogue_tables() -> None:
    database = _PostgresDatabase(schema_version=2)

    ensure_schema(database)  # type: ignore[arg-type]

    assert (
        'ALTER TABLE "jarvis_dialog_sessions" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert (
        'ALTER TABLE "jarvis_turn_receipts" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert (
        'ALTER TABLE "integration_connections" ENABLE ROW LEVEL SECURITY'
        not in database.statements
    )
    assert 'ALTER TABLE "users" ENABLE ROW LEVEL SECURITY' not in database.statements


def test_postgres_v3_migration_only_locks_native_claim_and_device_grant_tables() -> (
    None
):
    database = _PostgresDatabase(schema_version=3)

    ensure_schema(database)  # type: ignore[arg-type]

    assert (
        'ALTER TABLE "jarvis_native_action_claims" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert (
        'ALTER TABLE "integration_device_grants" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert (
        'ALTER TABLE "jarvis_dialog_sessions" ENABLE ROW LEVEL SECURITY'
        not in database.statements
    )


def test_postgres_v4_migration_only_locks_health_tables() -> None:
    database = _PostgresDatabase(schema_version=4)

    ensure_schema(database)  # type: ignore[arg-type]

    assert (
        'ALTER TABLE "health_profiles" ENABLE ROW LEVEL SECURITY' in database.statements
    )
    assert (
        'REVOKE ALL ON TABLE "health_documents" FROM "authenticated"'
        in database.statements
    )
    assert (
        'ALTER TABLE "jarvis_native_action_claims" ENABLE ROW LEVEL SECURITY'
        not in database.statements
    )
    assert database.commits == 1


def test_postgres_v5_migration_only_locks_channel_control_plane_tables() -> None:
    database = _PostgresDatabase(schema_version=5)

    ensure_schema(database)  # type: ignore[arg-type]

    for table in ("channel_links", "channel_messages", "message_outbox"):
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in database.statements
        assert (
            f'REVOKE ALL ON TABLE "{table}" FROM "authenticated"' in database.statements
        )
    assert (
        'ALTER TABLE "health_profiles" ENABLE ROW LEVEL SECURITY'
        not in database.statements
    )


def test_postgres_v6_migration_only_locks_channel_preferences() -> None:
    database = _PostgresDatabase(schema_version=6)

    ensure_schema(database)  # type: ignore[arg-type]

    assert (
        'ALTER TABLE "channel_preferences" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert (
        'REVOKE ALL ON TABLE "channel_preferences" FROM "authenticated"'
        in database.statements
    )
    assert (
        'ALTER TABLE "message_outbox" ENABLE ROW LEVEL SECURITY'
        not in database.statements
    )
    assert database.commits == 1


def test_postgres_v7_migration_adds_outbox_lease_token() -> None:
    database = _PostgresDatabase(schema_version=7)

    ensure_schema(database)  # type: ignore[arg-type]

    assert any(
        "ALTER TABLE message_outbox ADD COLUMN IF NOT EXISTS lease_token" in statement
        for statement in database.statements
    )


def test_postgres_v8_migration_secures_app_attest_control_plane() -> None:
    database = _PostgresDatabase(schema_version=8)

    ensure_schema(database)  # type: ignore[arg-type]

    for table in ("jarvis_app_attest_challenges", "jarvis_app_attest_keys"):
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in database.statements
        assert (
            f'REVOKE ALL ON TABLE "{table}" FROM "authenticated"' in database.statements
        )


def test_postgres_v9_migration_secures_finance_operation_receipts() -> None:
    database = _PostgresDatabase(schema_version=9)

    ensure_schema(database)  # type: ignore[arg-type]

    assert (
        'ALTER TABLE "finance_operation_receipts" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert (
        'REVOKE ALL ON TABLE "finance_operation_receipts" FROM "authenticated"'
        in database.statements
    )


def test_postgres_v10_migration_secures_channel_link_rate_limits() -> None:
    database = _PostgresDatabase(schema_version=10)

    ensure_schema(database)  # type: ignore[arg-type]

    assert (
        'ALTER TABLE "channel_link_rate_limits" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert (
        'REVOKE ALL ON TABLE "channel_link_rate_limits" FROM "authenticated"'
        in database.statements
    )


def test_postgres_v11_migration_secures_adaptive_training_tables() -> None:
    database = _PostgresDatabase(schema_version=11)

    ensure_schema(database)  # type: ignore[arg-type]

    for table in (
        "financial_documents",
        "training_profiles",
        "training_plans",
        "training_sessions",
        "training_steps",
        "training_checkins",
        "training_feedback",
    ):
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in database.statements
        assert (
            f'REVOKE ALL ON TABLE "{table}" FROM "authenticated"' in database.statements
        )


def test_sqlite_v11_migration_creates_adaptive_training_schema(tmp_path) -> None:
    database = connect(str(tmp_path / "life-v11.db"))
    try:
        database.execute("PRAGMA user_version = 11")
        database.commit()

        ensure_schema(database)

        expected_columns = {
            "financial_documents": {
                "user_id",
                "sha256",
                "document_kind",
                "extracted_json",
                "proposals_json",
            },
            "training_profiles": {"user_id", "primary_goal", "weekly_days"},
            "training_plans": {"user_id", "profile_id", "status", "weeks"},
            "training_sessions": {
                "user_id",
                "plan_id",
                "workout_id",
                "scheduled_on",
                "session_type",
            },
            "training_steps": {
                "user_id",
                "session_id",
                "step_index",
                "target_rpe",
            },
            "training_checkins": {
                "user_id",
                "session_id",
                "readiness_score",
            },
            "training_feedback": {
                "user_id",
                "session_id",
                "completion_pct",
                "rpe",
                "pain",
            },
        }
        actual = {
            table: {
                row["name"]
                for row in database.execute(f"PRAGMA table_info('{table}')").fetchall()
            }
            for table in expected_columns
        }
        version = database.execute("PRAGMA user_version").fetchone()[0]
    finally:
        database.close()

    for table, columns in expected_columns.items():
        assert actual[table] >= columns
    assert version == CURRENT_SCHEMA_VERSION == 12


def test_sqlite_v10_migration_creates_channel_link_rate_limits(tmp_path) -> None:
    database = connect(str(tmp_path / "life-v10.db"))
    try:
        database.execute("PRAGMA user_version = 10")
        database.commit()

        ensure_schema(database)

        columns = database.execute(
            "PRAGMA table_info('channel_link_rate_limits')"
        ).fetchall()
        version = database.execute("PRAGMA user_version").fetchone()[0]
    finally:
        database.close()

    assert {row["name"] for row in columns} >= {
        "user_id",
        "channel",
        "scope",
        "address_hash",
        "attempts",
        "window_started_at",
        "last_attempt_at",
    }
    assert version == CURRENT_SCHEMA_VERSION == 12


def test_sqlite_v9_migration_creates_finance_operation_receipts(tmp_path) -> None:
    database = connect(str(tmp_path / "life-v9.db"))
    try:
        database.execute("PRAGMA user_version = 9")
        database.commit()

        ensure_schema(database)

        columns = database.execute(
            "PRAGMA table_info('finance_operation_receipts')"
        ).fetchall()
        version = database.execute("PRAGMA user_version").fetchone()[0]
    finally:
        database.close()

    assert {row["name"] for row in columns} >= {
        "user_id",
        "operation_id",
        "request_hash",
        "resource_id",
        "status",
        "result_json",
    }
    assert version == CURRENT_SCHEMA_VERSION == 12


def test_postgres_v1_migration_only_locks_new_integration_tables() -> None:
    database = _PostgresDatabase(schema_version=1)

    ensure_schema(database)  # type: ignore[arg-type]

    assert (
        'ALTER TABLE "integration_connections" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert (
        'ALTER TABLE "integration_auth_requests" ENABLE ROW LEVEL SECURITY'
        in database.statements
    )
    assert 'ALTER TABLE "users" ENABLE ROW LEVEL SECURITY' not in database.statements
    assert any(
        "INSERT INTO life_schema_migrations" in statement
        for statement in database.statements
    )


def test_channel_briefing_due_index_matches_scheduler_filter(tmp_path) -> None:
    database = connect(str(tmp_path / "life.db"))
    try:
        ensure_schema(database)
        columns = database.execute(
            "PRAGMA index_info('idx_channel_preferences_due')"
        ).fetchall()
    finally:
        database.close()

    assert [row["name"] for row in columns] == ["channel", "briefing_enabled"]


def test_sqlite_v7_migration_adds_outbox_lease_token(tmp_path) -> None:
    database = connect(str(tmp_path / "life-v7.db"))
    try:
        database.execute(
            "CREATE TABLE message_outbox ("
            " id TEXT PRIMARY KEY, user_id TEXT NOT NULL, channel TEXT NOT NULL,"
            " channel_link_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,"
            " payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',"
            " attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,"
            " provider_message_id TEXT NOT NULL DEFAULT '',"
            " last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL, UNIQUE (user_id, idempotency_key))"
        )
        database.execute("PRAGMA user_version = 7")
        database.commit()

        ensure_schema(database)

        columns = database.execute("PRAGMA table_info('message_outbox')").fetchall()
    finally:
        database.close()

    assert "lease_token" in {row["name"] for row in columns}
