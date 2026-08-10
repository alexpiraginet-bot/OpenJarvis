"""SQLite schema for the Life OS — the structured record of a user's life.

Everything the assistant knows about a client in a *queryable* shape lives
here: money, training, habits, family and work. Free-form recollections stay
in ``openjarvis.memory``; this module is the part you can chart, budget and
alert on.

Two invariants hold across every table:

1. **Tenancy is structural.** Every domain row carries ``user_id`` and every
   index leads with it, so a query that forgets to scope by user cannot
   accidentally read another client's data — :class:`~openjarvis.life.store.LifeStore`
   injects the predicate itself rather than trusting callers to remember.
2. **Money is integer cents.** Floats silently lose fractions of a cent and a
   personal-finance ledger that drifts is worse than no ledger at all.

:data:`SCHEMA` also doubles as the write allow-list. The store validates every
column name against it before interpolating one into SQL, which is what makes
the generic CRUD layer safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Tuple

if TYPE_CHECKING:
    from openjarvis.life.db import Database

CURRENT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TableSpec:
    """A domain table and the columns callers are allowed to write.

    ``columns`` deliberately excludes ``id``, ``user_id`` and ``created_at``:
    those are owned by the store, never by request payloads.
    """

    name: str
    columns: Tuple[str, ...]
    app: str


# -- Identity ---------------------------------------------------------------

_DDL_SCHEMA_MIGRATIONS = """\
CREATE TABLE IF NOT EXISTS life_schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

_DDL_USERS = """\
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    timezone      TEXT NOT NULL DEFAULT 'UTC',
    currency      TEXT NOT NULL DEFAULT 'BRL',
    locale        TEXT NOT NULL DEFAULT 'pt-BR',
    created_at    TEXT NOT NULL
);
"""

# Only the SHA-256 of a token is stored. A database leak therefore yields no
# usable credentials, and the plaintext exists solely in the client.
_DDL_TOKENS = """\
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    expires_at   TEXT
);
"""

# Keyed by the SHA-256 of the submitted email, never the address itself: this
# table would otherwise become a log of who tried to sign in, which is exactly
# the kind of data a breach should not find. Rows exist for addresses that were
# never registered too — throttling only real accounts would turn the lockout
# response into a user-enumeration oracle.
_DDL_LOGIN_ATTEMPTS = """\
CREATE TABLE IF NOT EXISTS login_attempts (
    key              TEXT PRIMARY KEY,
    failures         INTEGER NOT NULL DEFAULT 0,
    first_failure_at TEXT    NOT NULL,
    locked_until     TEXT
);
"""

# -- Finance ----------------------------------------------------------------

# The UNIQUE constraint is the dedupe: a scheduler that fires hourly must not
# tell a client four times that the same bill is due. Structural, so no caller
# can forget to check.
_DDL_NOTIFICATIONS = """\
CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    sent_on     TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (user_id, fingerprint, sent_on)
);
"""

_DDL_ACCOUNTS = """\
CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'checking',
    balance_cents INTEGER NOT NULL DEFAULT 0,
    currency      TEXT NOT NULL DEFAULT 'BRL',
    institution   TEXT NOT NULL DEFAULT '',
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
"""

_DDL_TRANSACTIONS = """\
CREATE TABLE IF NOT EXISTS transactions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    account_id   TEXT NOT NULL DEFAULT '',
    amount_cents INTEGER NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'expense',
    category     TEXT NOT NULL DEFAULT 'outros',
    description  TEXT NOT NULL DEFAULT '',
    occurred_on  TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'manual',
    created_at   TEXT NOT NULL
);
"""

_DDL_BILLS = """\
CREATE TABLE IF NOT EXISTS bills (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    due_on       TEXT NOT NULL,
    recurrence   TEXT NOT NULL DEFAULT 'none',
    status       TEXT NOT NULL DEFAULT 'pending',
    category     TEXT NOT NULL DEFAULT 'contas',
    paid_on      TEXT,
    autopay      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
"""

_DDL_BUDGETS = """\
CREATE TABLE IF NOT EXISTS budgets (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    category    TEXT NOT NULL,
    limit_cents INTEGER NOT NULL,
    period      TEXT NOT NULL DEFAULT 'monthly',
    created_at  TEXT NOT NULL
);
"""

_DDL_GOALS = """\
CREATE TABLE IF NOT EXISTS goals (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    target_cents INTEGER NOT NULL,
    saved_cents  INTEGER NOT NULL DEFAULT 0,
    target_date  TEXT,
    icon         TEXT NOT NULL DEFAULT 'target',
    created_at   TEXT NOT NULL
);
"""

# -- Fitness ----------------------------------------------------------------

_DDL_WORKOUTS = """\
CREATE TABLE IF NOT EXISTS workouts (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    scheduled_on  TEXT NOT NULL,
    completed_at  TEXT,
    duration_min  INTEGER NOT NULL DEFAULT 0,
    focus         TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL
);
"""

_DDL_EXERCISE_SETS = """\
CREATE TABLE IF NOT EXISTS exercise_sets (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    workout_id TEXT NOT NULL,
    exercise   TEXT NOT NULL,
    set_index  INTEGER NOT NULL DEFAULT 1,
    reps       INTEGER NOT NULL DEFAULT 0,
    weight_kg  REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

_DDL_MEASUREMENTS = """\
CREATE TABLE IF NOT EXISTS measurements (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    taken_on     TEXT NOT NULL,
    weight_kg    REAL NOT NULL DEFAULT 0,
    body_fat_pct REAL NOT NULL DEFAULT 0,
    waist_cm     REAL NOT NULL DEFAULT 0,
    resting_hr   INTEGER NOT NULL DEFAULT 0,
    sleep_hours  REAL NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
"""

# -- Routine ----------------------------------------------------------------

_DDL_HABITS = """\
CREATE TABLE IF NOT EXISTS habits (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    name              TEXT NOT NULL,
    cadence           TEXT NOT NULL DEFAULT 'daily',
    target_per_period INTEGER NOT NULL DEFAULT 1,
    icon              TEXT NOT NULL DEFAULT 'check',
    color             TEXT NOT NULL DEFAULT 'blue',
    archived          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
"""

# The UNIQUE constraint is what makes check-ins idempotent: tapping "done"
# twice in the app must not inflate a streak.
_DDL_HABIT_CHECKINS = """\
CREATE TABLE IF NOT EXISTS habit_checkins (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    habit_id   TEXT NOT NULL,
    done_on    TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (user_id, habit_id, done_on)
);
"""

# -- Family -----------------------------------------------------------------

_DDL_FAMILY_MEMBERS = """\
CREATE TABLE IF NOT EXISTS family_members (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    relation   TEXT NOT NULL DEFAULT '',
    birthday   TEXT,
    phone      TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    avatar     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

_DDL_FAMILY_EVENTS = """\
CREATE TABLE IF NOT EXISTS family_events (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    member_id  TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL,
    event_on   TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'event',
    recurring  INTEGER NOT NULL DEFAULT 0,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

# -- Work -------------------------------------------------------------------

_DDL_PROJECTS = """\
CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    due_on     TEXT,
    color      TEXT NOT NULL DEFAULT 'indigo',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

_DDL_WORK_TASKS = """\
CREATE TABLE IF NOT EXISTS work_tasks (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'todo',
    priority   TEXT NOT NULL DEFAULT 'normal',
    due_on     TEXT,
    done_at    TEXT,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

# Internal control-plane state is deliberately absent from SCHEMA so generic
# CRUD and model-supplied table names can never mutate approvals.
_DDL_JARVIS_ACTION_PROPOSALS = """\
CREATE TABLE IF NOT EXISTS jarvis_action_proposals (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    arguments_json      TEXT NOT NULL,
    summary             TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    result_json         TEXT,
    error               TEXT NOT NULL DEFAULT '',
    confirmation_method TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    resolved_at          TEXT
);
"""

_DDL_JARVIS_AI_COST_EVENTS = """\
CREATE TABLE IF NOT EXISTS jarvis_ai_cost_events (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    month              TEXT NOT NULL,
    model              TEXT NOT NULL,
    status             TEXT NOT NULL,
    reserved_microusd  INTEGER NOT NULL,
    actual_microusd    INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    resolved_at        TEXT
);
"""

_ALL_DDL = (
    _DDL_SCHEMA_MIGRATIONS,
    _DDL_USERS,
    _DDL_TOKENS,
    _DDL_LOGIN_ATTEMPTS,
    _DDL_NOTIFICATIONS,
    _DDL_ACCOUNTS,
    _DDL_TRANSACTIONS,
    _DDL_BILLS,
    _DDL_BUDGETS,
    _DDL_GOALS,
    _DDL_WORKOUTS,
    _DDL_EXERCISE_SETS,
    _DDL_MEASUREMENTS,
    _DDL_HABITS,
    _DDL_HABIT_CHECKINS,
    _DDL_FAMILY_MEMBERS,
    _DDL_FAMILY_EVENTS,
    _DDL_PROJECTS,
    _DDL_WORK_TASKS,
    _DDL_JARVIS_ACTION_PROPOSALS,
    _DDL_JARVIS_AI_COST_EVENTS,
)

# Every index leads with user_id: the tenant predicate is present in *every*
# query the store emits, so a leading-column index serves all of them.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_notif_user_date"
    " ON notifications (user_id, sent_on);",
    "CREATE INDEX IF NOT EXISTS idx_tx_user_date"
    " ON transactions (user_id, occurred_on);",
    "CREATE INDEX IF NOT EXISTS idx_tx_user_cat ON transactions (user_id, category);",
    "CREATE INDEX IF NOT EXISTS idx_bills_user_due ON bills (user_id, due_on);",
    "CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_budgets_user ON budgets (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_goals_user ON goals (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_workouts_user_date"
    " ON workouts (user_id, scheduled_on);",
    "CREATE INDEX IF NOT EXISTS idx_sets_user_workout"
    " ON exercise_sets (user_id, workout_id);",
    "CREATE INDEX IF NOT EXISTS idx_meas_user_date"
    " ON measurements (user_id, taken_on);",
    "CREATE INDEX IF NOT EXISTS idx_habits_user ON habits (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_checkins_user_date"
    " ON habit_checkins (user_id, done_on);",
    "CREATE INDEX IF NOT EXISTS idx_family_user ON family_members (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_famevents_user_date"
    " ON family_events (user_id, event_on);",
    "CREATE INDEX IF NOT EXISTS idx_projects_user ON projects (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON work_tasks (user_id, due_on);",
    "CREATE INDEX IF NOT EXISTS idx_jarvis_actions_user_status"
    " ON jarvis_action_proposals (user_id, status, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_jarvis_cost_month_status"
    " ON jarvis_ai_cost_events (month, status, expires_at);",
)


SCHEMA: Dict[str, TableSpec] = {
    "accounts": TableSpec(
        "accounts",
        ("name", "kind", "balance_cents", "currency", "institution", "archived"),
        "finance",
    ),
    "transactions": TableSpec(
        "transactions",
        (
            "account_id",
            "amount_cents",
            "kind",
            "category",
            "description",
            "occurred_on",
            "source",
        ),
        "finance",
    ),
    "bills": TableSpec(
        "bills",
        (
            "name",
            "amount_cents",
            "due_on",
            "recurrence",
            "status",
            "category",
            "paid_on",
            "autopay",
        ),
        "finance",
    ),
    "budgets": TableSpec("budgets", ("category", "limit_cents", "period"), "finance"),
    "goals": TableSpec(
        "goals",
        ("name", "target_cents", "saved_cents", "target_date", "icon"),
        "finance",
    ),
    "workouts": TableSpec(
        "workouts",
        (
            "name",
            "scheduled_on",
            "completed_at",
            "duration_min",
            "focus",
            "notes",
            "source",
        ),
        "fitness",
    ),
    "exercise_sets": TableSpec(
        "exercise_sets",
        ("workout_id", "exercise", "set_index", "reps", "weight_kg"),
        "fitness",
    ),
    "measurements": TableSpec(
        "measurements",
        (
            "taken_on",
            "weight_kg",
            "body_fat_pct",
            "waist_cm",
            "resting_hr",
            "sleep_hours",
        ),
        "fitness",
    ),
    "habits": TableSpec(
        "habits",
        ("name", "cadence", "target_per_period", "icon", "color", "archived"),
        "routine",
    ),
    "habit_checkins": TableSpec(
        "habit_checkins", ("habit_id", "done_on", "note"), "routine"
    ),
    "family_members": TableSpec(
        "family_members",
        ("name", "relation", "birthday", "phone", "notes", "avatar"),
        "family",
    ),
    "family_events": TableSpec(
        "family_events",
        ("member_id", "title", "event_on", "kind", "recurring", "notes"),
        "family",
    ),
    "projects": TableSpec(
        "projects", ("name", "status", "due_on", "color", "notes"), "work"
    ),
    "work_tasks": TableSpec(
        "work_tasks",
        ("project_id", "title", "status", "priority", "due_on", "done_at", "notes"),
        "work",
    ),
}

#: Logical grouping surfaced to the client as the springboard icons.
APPS: Dict[str, Tuple[str, ...]] = {
    "finance": ("accounts", "transactions", "bills", "budgets", "goals"),
    "fitness": ("workouts", "exercise_sets", "measurements"),
    "routine": ("habits", "habit_checkins"),
    "family": ("family_members", "family_events"),
    "work": ("projects", "work_tasks"),
}


def ensure_schema(db: "Database") -> None:
    """Create every table and index if missing. Safe to call repeatedly.

    The DDL is deliberately portable: only TEXT/INTEGER/REAL columns and text
    primary keys, which mean the same thing to SQLite and PostgreSQL. No
    AUTOINCREMENT, no SERIAL — so one definition serves both backends.
    """
    sqlite_version = 0
    if db.backend == "sqlite":
        row = db.execute("PRAGMA user_version").fetchone()
        sqlite_version = int(row[0]) if row else 0
        if sqlite_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "Life database schema is newer than this OpenJarvis build: "
                f"{sqlite_version} > {CURRENT_SCHEMA_VERSION}"
            )
    elif db.backend == "postgres":
        migration_table = db.execute(
            "SELECT to_regclass('public.life_schema_migrations') AS table_name"
        ).fetchone()
        if migration_table and migration_table["table_name"]:
            row = db.execute(
                "SELECT COALESCE(MAX(version), 0) AS version"
                " FROM life_schema_migrations"
            ).fetchone()
            postgres_version = int(row["version"]) if row else 0
            if postgres_version > CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    "Life database schema is newer than this OpenJarvis build: "
                    f"{postgres_version} > {CURRENT_SCHEMA_VERSION}"
                )
            if postgres_version == CURRENT_SCHEMA_VERSION:
                return

    db.executescript([*_ALL_DDL, *_INDEXES])
    if db.backend == "postgres":
        # Supabase exposes ``public`` through its Data API. Life uses its own
        # bearer-token API, so those roles must never read or mutate the raw
        # tables directly. Generic PostgreSQL installs may not define the
        # Supabase roles; in that case there is nothing to revoke.
        role_rows = db.execute(
            "SELECT rolname FROM pg_roles"
            " WHERE rolname IN ('anon', 'authenticated')"
        ).fetchall()
        roles = tuple(str(row["rolname"]) for row in role_rows)
        protected_tables = (
            "life_schema_migrations",
            "users",
            "auth_tokens",
            "login_attempts",
            "notifications",
            *SCHEMA,
            "jarvis_action_proposals",
            "jarvis_ai_cost_events",
        )
        security_statements = []
        for table in protected_tables:
            security_statements.append(
                f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'
            )
            security_statements.extend(
                f'REVOKE ALL ON TABLE "{table}" FROM "{role}"' for role in roles
            )
        db.executescript(security_statements)
        db.execute(
            "INSERT INTO life_schema_migrations (version, applied_at)"
            " VALUES (?, CAST(CURRENT_TIMESTAMP AS TEXT))"
            " ON CONFLICT(version) DO NOTHING",
            (CURRENT_SCHEMA_VERSION,),
        )
        db.commit()
    if db.backend == "sqlite" and sqlite_version < CURRENT_SCHEMA_VERSION:
        db.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        db.commit()
