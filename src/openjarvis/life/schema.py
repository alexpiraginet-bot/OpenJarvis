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

# v12: adaptive training plus reviewed financial document intake.
# v11: link-code cooldown/window counters are durable across serverless workers.
# v10: durable receipts make direct financial mutations exactly-once across
# client/network retries, even when a fresh App Attest challenge is issued.
# O bump faz um Postgres já migrado reexecutar o DDL idempotente
# (``ensure_schema`` retorna cedo quando a versão gravada é a atual).
CURRENT_SCHEMA_VERSION = 12


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

_DDL_FINANCIAL_DOCUMENTS = """\
CREATE TABLE IF NOT EXISTS financial_documents (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    filename        TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    document_kind   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'review_required',
    extracted_json  TEXT NOT NULL,
    proposals_json  TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    UNIQUE (user_id, sha256)
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

_DDL_TRAINING_PROFILES = """\
CREATE TABLE IF NOT EXISTS training_profiles (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL UNIQUE,
    primary_sport           TEXT NOT NULL DEFAULT 'running',
    secondary_sports_json   TEXT NOT NULL DEFAULT '[]',
    primary_goal            TEXT NOT NULL DEFAULT 'general_fitness',
    target_distance_km      REAL NOT NULL DEFAULT 0,
    target_date             TEXT,
    level                   TEXT NOT NULL DEFAULT 'beginner',
    weekly_days             INTEGER NOT NULL DEFAULT 3,
    available_weekdays_json TEXT NOT NULL DEFAULT '[1,3,5]',
    session_minutes         INTEGER NOT NULL DEFAULT 45,
    current_weekly_km       REAL NOT NULL DEFAULT 0,
    longest_recent_run_km   REAL NOT NULL DEFAULT 0,
    equipment_json          TEXT NOT NULL DEFAULT '[]',
    limitations             TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL
);
"""

_DDL_TRAINING_PLANS = """\
CREATE TABLE IF NOT EXISTS training_plans (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    profile_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    primary_sport TEXT NOT NULL DEFAULT 'running',
    goal         TEXT NOT NULL,
    start_on     TEXT NOT NULL,
    end_on       TEXT NOT NULL,
    weeks        INTEGER NOT NULL,
    current_week INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'active',
    source       TEXT NOT NULL DEFAULT 'coach',
    created_at   TEXT NOT NULL
);
"""

_DDL_TRAINING_SESSIONS = """\
CREATE TABLE IF NOT EXISTS training_sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    plan_id         TEXT NOT NULL,
    workout_id      TEXT NOT NULL DEFAULT '',
    scheduled_on    TEXT NOT NULL,
    week_index      INTEGER NOT NULL,
    day_index       INTEGER NOT NULL,
    title           TEXT NOT NULL,
    sport           TEXT NOT NULL DEFAULT 'running',
    session_type    TEXT NOT NULL,
    objective       TEXT NOT NULL DEFAULT '',
    rationale       TEXT NOT NULL DEFAULT '',
    estimated_min   INTEGER NOT NULL DEFAULT 0,
    target_rpe      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'planned',
    adaptation_note TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
"""

_DDL_TRAINING_STEPS = """\
CREATE TABLE IF NOT EXISTS training_steps (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    session_id           TEXT NOT NULL,
    step_index           INTEGER NOT NULL,
    kind                 TEXT NOT NULL,
    title                TEXT NOT NULL,
    instructions         TEXT NOT NULL DEFAULT '',
    duration_sec         INTEGER NOT NULL DEFAULT 0,
    distance_m           INTEGER NOT NULL DEFAULT 0,
    target_pace_min_km   REAL NOT NULL DEFAULT 0,
    target_rpe           INTEGER NOT NULL DEFAULT 0,
    sets                 INTEGER NOT NULL DEFAULT 0,
    reps                 INTEGER NOT NULL DEFAULT 0,
    rest_sec             INTEGER NOT NULL DEFAULT 0,
    alternative          TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    UNIQUE (user_id, session_id, step_index)
);
"""

_DDL_TRAINING_CHECKINS = """\
CREATE TABLE IF NOT EXISTS training_checkins (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    sleep_quality    INTEGER NOT NULL DEFAULT 5,
    soreness         INTEGER NOT NULL DEFAULT 0,
    stress           INTEGER NOT NULL DEFAULT 0,
    motivation       INTEGER NOT NULL DEFAULT 5,
    pain             INTEGER NOT NULL DEFAULT 0,
    readiness_score  INTEGER NOT NULL DEFAULT 0,
    recommendation   TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    UNIQUE (user_id, session_id)
);
"""

_DDL_TRAINING_FEEDBACK = """\
CREATE TABLE IF NOT EXISTS training_feedback (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    completed_at        TEXT NOT NULL,
    completion_pct      INTEGER NOT NULL DEFAULT 100,
    actual_duration_min INTEGER NOT NULL DEFAULT 0,
    rpe                 INTEGER NOT NULL DEFAULT 0,
    energy              INTEGER NOT NULL DEFAULT 5,
    pain                INTEGER NOT NULL DEFAULT 0,
    notes               TEXT NOT NULL DEFAULT '',
    adaptation          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    UNIQUE (user_id, session_id)
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

# -- Health -----------------------------------------------------------------

# Health data stays structured and tenant-scoped.  The first release stores
# user-confirmed facts and document metadata only; raw exam files require the
# separate encrypted-storage and retention work described in the architecture.
_DDL_HEALTH_PROFILES = """\
CREATE TABLE IF NOT EXISTS health_profiles (
    id                    TEXT PRIMARY KEY,
    user_id               TEXT NOT NULL UNIQUE,
    birth_date            TEXT,
    sex_at_birth          TEXT NOT NULL DEFAULT '',
    height_cm             REAL NOT NULL DEFAULT 0,
    blood_type            TEXT NOT NULL DEFAULT '',
    goals                 TEXT NOT NULL DEFAULT '',
    emergency_contact     TEXT NOT NULL DEFAULT '',
    consent_health_memory INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL
);
"""

_DDL_HEALTH_CONDITIONS = """\
CREATE TABLE IF NOT EXISTS health_conditions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    diagnosed_on TEXT,
    notes        TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'manual',
    confirmed_at TEXT,
    created_at   TEXT NOT NULL
);
"""

_DDL_MEDICATIONS = """\
CREATE TABLE IF NOT EXISTS medications (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    dose_text    TEXT NOT NULL DEFAULT '',
    frequency    TEXT NOT NULL DEFAULT '',
    started_on   TEXT,
    ended_on     TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    notes        TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'manual',
    confirmed_at TEXT,
    created_at   TEXT NOT NULL
);
"""

_DDL_ALLERGIES = """\
CREATE TABLE IF NOT EXISTS allergies (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    substance    TEXT NOT NULL,
    reaction     TEXT NOT NULL DEFAULT '',
    severity     TEXT NOT NULL DEFAULT 'unknown',
    notes        TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'manual',
    confirmed_at TEXT,
    created_at   TEXT NOT NULL
);
"""

_DDL_HEALTH_OBSERVATIONS = """\
CREATE TABLE IF NOT EXISTS health_observations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    value       REAL NOT NULL,
    unit        TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
"""

_DDL_HYDRATION_LOGS = """\
CREATE TABLE IF NOT EXISTS hydration_logs (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    amount_ml   INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  TEXT NOT NULL
);
"""

_DDL_NUTRITION_LOGS = """\
CREATE TABLE IF NOT EXISTS nutrition_logs (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    meal_type   TEXT NOT NULL DEFAULT 'meal',
    description TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  TEXT NOT NULL
);
"""

_DDL_HEALTH_DOCUMENTS = """\
CREATE TABLE IF NOT EXISTS health_documents (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'exam',
    document_date TEXT,
    provider      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'registered',
    notes         TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL
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

# Conversation control-plane state is intentionally outside ``SCHEMA``. The
# generic records API and model-supplied table names must never read or mutate
# history, pending intents or idempotency receipts. Only user/assistant text is
# stored here; bearer tokens, API keys and audio have no columns to land in.
_DDL_JARVIS_DIALOG_SESSIONS = """\
CREATE TABLE IF NOT EXISTS jarvis_dialog_sessions (
    id                  TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    revision            INTEGER NOT NULL DEFAULT 0,
    history_json        TEXT NOT NULL DEFAULT '[]',
    pending_intent_json TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);
"""

_DDL_JARVIS_TURN_RECEIPTS = """\
CREATE TABLE IF NOT EXISTS jarvis_turn_receipts (
    user_id         TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    turn_id         TEXT NOT NULL,
    request_hash    TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, conversation_id, turn_id)
);
"""

# A proposta continua sendo a fonte da intenção; esta tabela guarda somente o
# claim efêmero que autoriza um aparelho específico a executar a escrita
# nativa. O segredo nunca é persistido, apenas seu SHA-256.
_DDL_JARVIS_NATIVE_ACTION_CLAIMS = """\
CREATE TABLE IF NOT EXISTS jarvis_native_action_claims (
    proposal_id        TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    device_id          TEXT NOT NULL,
    claim_token_hash   TEXT NOT NULL,
    confirmation_method TEXT NOT NULL,
    claimed_at         TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    finalized_at       TEXT,
    receipt_hmac       TEXT NOT NULL DEFAULT '',
    event_id           TEXT NOT NULL DEFAULT ''
);
"""

_DDL_JARVIS_APP_ATTEST_CHALLENGES = """\
CREATE TABLE IF NOT EXISTS jarvis_app_attest_challenges (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    resource_id    TEXT NOT NULL DEFAULT '',
    challenge_hash TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    consumed_at    TEXT
);
"""

_DDL_JARVIS_APP_ATTEST_KEYS = """\
CREATE TABLE IF NOT EXISTS jarvis_app_attest_keys (
    key_id         TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    device_id      TEXT NOT NULL,
    device_label   TEXT NOT NULL DEFAULT '',
    public_key_der TEXT NOT NULL,
    receipt        TEXT NOT NULL,
    sign_counter   INTEGER NOT NULL DEFAULT 0,
    environment    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',
    attested_at    TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (user_id, device_id)
);
"""

# One row is the durable authority for one client-generated financial intent.
# The request hash prevents reusing an operation id for different data; the
# result lets a lost HTTP response be replayed without repeating the ledger
# write or consuming another App Attest challenge.
_DDL_FINANCE_OPERATION_RECEIPTS = """\
CREATE TABLE IF NOT EXISTS finance_operation_receipts (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    operation_id  TEXT NOT NULL,
    operation     TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    resource_id   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'preparing',
    result_json   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (user_id, operation_id)
);
"""

# -- Integrações -------------------------------------------------------------

# Fora de SCHEMA de propósito, como as tabelas do Jarvis acima: conexão com um
# provedor é control-plane e jamais pode ser criada ou alterada pelo CRUD
# genérico. ``credential_ref`` guarda apenas uma referência opaca para um cofre
# externo — não existe coluna onde um token em texto claro possa viver.
_DDL_INTEGRATION_CONNECTIONS = """\
CREATE TABLE IF NOT EXISTS integration_connections (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    provider         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'connected',
    granted_scopes   TEXT NOT NULL DEFAULT '[]',
    account_label    TEXT NOT NULL DEFAULT '',
    credential_ref   TEXT NOT NULL DEFAULT '',
    connected_at     TEXT,
    last_sync_at     TEXT,
    last_sync_status TEXT NOT NULL DEFAULT '',
    last_error       TEXT NOT NULL DEFAULT '',
    revoked_at       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (user_id, provider)
);
"""

# O ``state`` do OAuth volta por um callback não autenticado, então é esta
# linha que nomeia o usuário — e só o hash fica gravado, como em auth_tokens.
# O ``code_verifier`` PKCE precisa sobreviver até a troca do code; sozinho ele
# não concede nada, e a linha expira em minutos. UNIQUE (user_id, provider) é
# o que garante "uma intenção viva por provedor" também entre instâncias
# concorrentes — dentro de um processo o lock já serializa, entre processos
# só a constraint segura.
_DDL_INTEGRATION_AUTH_REQUESTS = """\
CREATE TABLE IF NOT EXISTS integration_auth_requests (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    provider      TEXT NOT NULL,
    state_hash    TEXT NOT NULL UNIQUE,
    code_verifier TEXT NOT NULL DEFAULT '',
    redirect_uri  TEXT NOT NULL DEFAULT '',
    scopes        TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    UNIQUE (user_id, provider)
);
"""

# Autorizações de APIs do sistema pertencem ao aparelho, não apenas ao usuário.
# A linha agregada em ``integration_connections`` continua alimentando o
# catálogo, enquanto esta tabela é a autoridade usada antes de uma escrita.
_DDL_INTEGRATION_DEVICE_GRANTS = """\
CREATE TABLE IF NOT EXISTS integration_device_grants (
    user_id        TEXT NOT NULL,
    provider       TEXT NOT NULL,
    device_id      TEXT NOT NULL,
    device_label   TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'connected',
    granted_scopes TEXT NOT NULL DEFAULT '[]',
    granted_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    revoked_at     TEXT,
    PRIMARY KEY (user_id, provider, device_id)
);
"""

# -- Canais operacionais -----------------------------------------------------

# Endereços pessoais nunca ficam em claro: ``address_hash`` resolve o remetente
# de forma determinística e ``address_ref`` aponta para um cofre externo. Os
# códigos de verificação também entram somente como HMAC e são single-use.
_DDL_CHANNEL_LINKS = """\
CREATE TABLE IF NOT EXISTS channel_links (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    channel                 TEXT NOT NULL,
    address_hash            TEXT NOT NULL,
    address_ref             TEXT NOT NULL DEFAULT '',
    status                  TEXT NOT NULL DEFAULT 'pending',
    verification_hash       TEXT NOT NULL DEFAULT '',
    verification_expires_at TEXT,
    verified_at             TEXT,
    revoked_at              TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE (channel, address_hash),
    UNIQUE (user_id, channel)
);
"""

# Conteúdo de conversa permanece no DialogueStore; esta tabela é somente o
# recibo técnico necessário para impedir reprocessamento de webhooks.
_DDL_CHANNEL_MESSAGES = """\
CREATE TABLE IF NOT EXISTS channel_messages (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    channel             TEXT NOT NULL,
    provider_message_id TEXT NOT NULL,
    direction           TEXT NOT NULL,
    kind                TEXT NOT NULL,
    conversation_id     TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (channel, provider_message_id)
);
"""

_DDL_CHANNEL_PREFERENCES = """\
CREATE TABLE IF NOT EXISTS channel_preferences (
    user_id          TEXT NOT NULL,
    channel          TEXT NOT NULL,
    briefing_enabled INTEGER NOT NULL DEFAULT 0,
    briefing_time    TEXT NOT NULL DEFAULT '08:00',
    sections_json    TEXT NOT NULL DEFAULT '[]',
    news_topics_json TEXT NOT NULL DEFAULT '[]',
    delivery_days_json TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]',
    custom_instructions TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (user_id, channel)
);
"""

# No phone is retained: address_hash is the same environment-bound HMAC used
# by channel_links. The user bucket has an empty hash and prevents rotation
# through arbitrary target numbers from bypassing the per-address window.
_DDL_CHANNEL_LINK_RATE_LIMITS = """\
CREATE TABLE IF NOT EXISTS channel_link_rate_limits (
    user_id           TEXT NOT NULL,
    channel           TEXT NOT NULL,
    scope             TEXT NOT NULL,
    address_hash      TEXT NOT NULL DEFAULT '',
    attempts          INTEGER NOT NULL DEFAULT 0,
    window_started_at TEXT NOT NULL,
    last_attempt_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, channel, scope, address_hash)
);
"""

# A outbox torna a resposta durável: a transação grava a intenção de envio e
# um worker separado conversa com a Meta. Repetir a mesma rotina não duplica.
_DDL_MESSAGE_OUTBOX = """\
CREATE TABLE IF NOT EXISTS message_outbox (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    channel             TEXT NOT NULL,
    channel_link_id     TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued',
    attempts            INTEGER NOT NULL DEFAULT 0,
    lease_token         TEXT NOT NULL DEFAULT '',
    next_attempt_at     TEXT,
    provider_message_id TEXT NOT NULL DEFAULT '',
    last_error          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (user_id, idempotency_key)
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
    _DDL_FINANCIAL_DOCUMENTS,
    _DDL_WORKOUTS,
    _DDL_EXERCISE_SETS,
    _DDL_MEASUREMENTS,
    _DDL_TRAINING_PROFILES,
    _DDL_TRAINING_PLANS,
    _DDL_TRAINING_SESSIONS,
    _DDL_TRAINING_STEPS,
    _DDL_TRAINING_CHECKINS,
    _DDL_TRAINING_FEEDBACK,
    _DDL_HABITS,
    _DDL_HABIT_CHECKINS,
    _DDL_FAMILY_MEMBERS,
    _DDL_FAMILY_EVENTS,
    _DDL_PROJECTS,
    _DDL_WORK_TASKS,
    _DDL_HEALTH_PROFILES,
    _DDL_HEALTH_CONDITIONS,
    _DDL_MEDICATIONS,
    _DDL_ALLERGIES,
    _DDL_HEALTH_OBSERVATIONS,
    _DDL_HYDRATION_LOGS,
    _DDL_NUTRITION_LOGS,
    _DDL_HEALTH_DOCUMENTS,
    _DDL_JARVIS_ACTION_PROPOSALS,
    _DDL_JARVIS_AI_COST_EVENTS,
    _DDL_JARVIS_DIALOG_SESSIONS,
    _DDL_JARVIS_TURN_RECEIPTS,
    _DDL_JARVIS_NATIVE_ACTION_CLAIMS,
    _DDL_JARVIS_APP_ATTEST_CHALLENGES,
    _DDL_JARVIS_APP_ATTEST_KEYS,
    _DDL_FINANCE_OPERATION_RECEIPTS,
    _DDL_INTEGRATION_CONNECTIONS,
    _DDL_INTEGRATION_AUTH_REQUESTS,
    _DDL_INTEGRATION_DEVICE_GRANTS,
    _DDL_CHANNEL_LINKS,
    _DDL_CHANNEL_MESSAGES,
    _DDL_CHANNEL_PREFERENCES,
    _DDL_CHANNEL_LINK_RATE_LIMITS,
    _DDL_MESSAGE_OUTBOX,
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
    "CREATE INDEX IF NOT EXISTS idx_financial_documents_user_created"
    " ON financial_documents (user_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_workouts_user_date"
    " ON workouts (user_id, scheduled_on);",
    "CREATE INDEX IF NOT EXISTS idx_sets_user_workout"
    " ON exercise_sets (user_id, workout_id);",
    "CREATE INDEX IF NOT EXISTS idx_meas_user_date"
    " ON measurements (user_id, taken_on);",
    "CREATE INDEX IF NOT EXISTS idx_training_plans_user_status"
    " ON training_plans (user_id, status, start_on);",
    "CREATE INDEX IF NOT EXISTS idx_training_sessions_user_date"
    " ON training_sessions (user_id, scheduled_on, status);",
    "CREATE INDEX IF NOT EXISTS idx_training_sessions_user_plan"
    " ON training_sessions (user_id, plan_id, week_index);",
    "CREATE INDEX IF NOT EXISTS idx_training_steps_user_session"
    " ON training_steps (user_id, session_id, step_index);",
    "CREATE INDEX IF NOT EXISTS idx_training_checkins_user_session"
    " ON training_checkins (user_id, session_id);",
    "CREATE INDEX IF NOT EXISTS idx_training_feedback_user_session"
    " ON training_feedback (user_id, session_id);",
    "CREATE INDEX IF NOT EXISTS idx_habits_user ON habits (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_checkins_user_date"
    " ON habit_checkins (user_id, done_on);",
    "CREATE INDEX IF NOT EXISTS idx_family_user ON family_members (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_famevents_user_date"
    " ON family_events (user_id, event_on);",
    "CREATE INDEX IF NOT EXISTS idx_projects_user ON projects (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON work_tasks (user_id, due_on);",
    "CREATE INDEX IF NOT EXISTS idx_health_conditions_user_status"
    " ON health_conditions (user_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_medications_user_status"
    " ON medications (user_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_allergies_user ON allergies (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_health_observations_user_date"
    " ON health_observations (user_id, observed_at);",
    "CREATE INDEX IF NOT EXISTS idx_hydration_user_date"
    " ON hydration_logs (user_id, occurred_at);",
    "CREATE INDEX IF NOT EXISTS idx_nutrition_user_date"
    " ON nutrition_logs (user_id, occurred_at);",
    "CREATE INDEX IF NOT EXISTS idx_health_documents_user_date"
    " ON health_documents (user_id, document_date);",
    "CREATE INDEX IF NOT EXISTS idx_jarvis_actions_user_status"
    " ON jarvis_action_proposals (user_id, status, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_jarvis_cost_month_status"
    " ON jarvis_ai_cost_events (month, status, expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_dialog_sessions_user_expiry"
    " ON jarvis_dialog_sessions (user_id, expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_turn_receipts_user_expiry"
    " ON jarvis_turn_receipts (user_id, conversation_id, expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_native_claims_user_expiry"
    " ON jarvis_native_action_claims (user_id, expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_app_attest_challenges_user_expiry"
    " ON jarvis_app_attest_challenges (user_id, expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_app_attest_keys_user_device"
    " ON jarvis_app_attest_keys (user_id, device_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_finance_receipts_user_status"
    " ON finance_operation_receipts (user_id, status, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_integration_conn_user"
    " ON integration_connections (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_device_grants_user_provider"
    " ON integration_device_grants (user_id, provider, status);",
    "CREATE INDEX IF NOT EXISTS idx_channel_links_user_status"
    " ON channel_links (user_id, channel, status);",
    "CREATE INDEX IF NOT EXISTS idx_channel_messages_user_date"
    " ON channel_messages (user_id, channel, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_message_outbox_due"
    " ON message_outbox (status, next_attempt_at, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_message_outbox_user"
    " ON message_outbox (user_id, channel, status);",
    "CREATE INDEX IF NOT EXISTS idx_channel_preferences_due"
    " ON channel_preferences (channel, briefing_enabled);",
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
    "training_profiles": TableSpec(
        "training_profiles",
        (
            "primary_sport",
            "secondary_sports_json",
            "primary_goal",
            "target_distance_km",
            "target_date",
            "level",
            "weekly_days",
            "available_weekdays_json",
            "session_minutes",
            "current_weekly_km",
            "longest_recent_run_km",
            "equipment_json",
            "limitations",
        ),
        "fitness",
    ),
    "training_plans": TableSpec(
        "training_plans",
        (
            "profile_id",
            "name",
            "primary_sport",
            "goal",
            "start_on",
            "end_on",
            "weeks",
            "current_week",
            "status",
            "source",
        ),
        "fitness",
    ),
    "training_sessions": TableSpec(
        "training_sessions",
        (
            "plan_id",
            "workout_id",
            "scheduled_on",
            "week_index",
            "day_index",
            "title",
            "sport",
            "session_type",
            "objective",
            "rationale",
            "estimated_min",
            "target_rpe",
            "status",
            "adaptation_note",
        ),
        "fitness",
    ),
    "training_steps": TableSpec(
        "training_steps",
        (
            "session_id",
            "step_index",
            "kind",
            "title",
            "instructions",
            "duration_sec",
            "distance_m",
            "target_pace_min_km",
            "target_rpe",
            "sets",
            "reps",
            "rest_sec",
            "alternative",
        ),
        "fitness",
    ),
    "training_checkins": TableSpec(
        "training_checkins",
        (
            "session_id",
            "observed_at",
            "sleep_quality",
            "soreness",
            "stress",
            "motivation",
            "pain",
            "readiness_score",
            "recommendation",
            "notes",
        ),
        "fitness",
    ),
    "training_feedback": TableSpec(
        "training_feedback",
        (
            "session_id",
            "completed_at",
            "completion_pct",
            "actual_duration_min",
            "rpe",
            "energy",
            "pain",
            "notes",
            "adaptation",
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
    "health_profiles": TableSpec(
        "health_profiles",
        (
            "birth_date",
            "sex_at_birth",
            "height_cm",
            "blood_type",
            "goals",
            "emergency_contact",
            "consent_health_memory",
        ),
        "health",
    ),
    "health_conditions": TableSpec(
        "health_conditions",
        ("name", "status", "diagnosed_on", "notes", "source", "confirmed_at"),
        "health",
    ),
    "medications": TableSpec(
        "medications",
        (
            "name",
            "dose_text",
            "frequency",
            "started_on",
            "ended_on",
            "status",
            "notes",
            "source",
            "confirmed_at",
        ),
        "health",
    ),
    "allergies": TableSpec(
        "allergies",
        ("substance", "reaction", "severity", "notes", "source", "confirmed_at"),
        "health",
    ),
    "health_observations": TableSpec(
        "health_observations",
        ("kind", "value", "unit", "observed_at", "source", "notes"),
        "health",
    ),
    "hydration_logs": TableSpec(
        "hydration_logs", ("amount_ml", "occurred_at", "source"), "health"
    ),
    "nutrition_logs": TableSpec(
        "nutrition_logs",
        ("meal_type", "description", "occurred_at", "source"),
        "health",
    ),
    "health_documents": TableSpec(
        "health_documents",
        ("name", "kind", "document_date", "provider", "status", "notes", "source"),
        "health",
    ),
}

#: Logical grouping surfaced to the client as the springboard icons.
APPS: Dict[str, Tuple[str, ...]] = {
    "finance": ("accounts", "transactions", "bills", "budgets", "goals"),
    "fitness": (
        "workouts",
        "exercise_sets",
        "measurements",
        "training_profiles",
        "training_plans",
        "training_sessions",
        "training_steps",
        "training_checkins",
        "training_feedback",
    ),
    "routine": ("habits", "habit_checkins"),
    "family": ("family_members", "family_events"),
    "work": ("projects", "work_tasks"),
    "health": (
        "health_profiles",
        "health_conditions",
        "medications",
        "allergies",
        "health_observations",
        "hydration_logs",
        "nutrition_logs",
        "health_documents",
    ),
}


def ensure_schema(db: "Database") -> None:
    """Create every table and index if missing. Safe to call repeatedly.

    The DDL is deliberately portable: only TEXT/INTEGER/REAL columns and text
    primary keys, which mean the same thing to SQLite and PostgreSQL. No
    AUTOINCREMENT, no SERIAL — so one definition serves both backends.
    """
    if db.backend == "postgres":
        with db.transaction():
            # Serialize Vercel cold starts and keep DDL, access hardening and
            # the version marker inside one all-or-nothing transaction.
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtext(?)) AS locked",
                ("openjarvis_life_schema",),
            ).fetchone()
            _ensure_schema(db)
        return
    _ensure_schema(db)


def _ensure_schema(db: "Database") -> None:
    sqlite_version = 0
    postgres_version = 0
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
    if postgres_version < 8 and db.backend == "postgres":
        db.execute(
            "ALTER TABLE message_outbox ADD COLUMN IF NOT EXISTS lease_token"
            " TEXT NOT NULL DEFAULT ''"
        )
    if sqlite_version < 8 and db.backend == "sqlite":
        outbox_columns = db.execute("PRAGMA table_info('message_outbox')").fetchall()
        if "lease_token" not in {str(row["name"]) for row in outbox_columns}:
            db.execute(
                "ALTER TABLE message_outbox ADD COLUMN lease_token"
                " TEXT NOT NULL DEFAULT ''"
            )
    if db.backend == "postgres":
        # Supabase exposes ``public`` through its Data API. Life uses its own
        # bearer-token API, so those roles must never read or mutate the raw
        # tables directly. Generic PostgreSQL installs may not define the
        # Supabase roles; in that case there is nothing to revoke.
        role_rows = db.execute(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
        ).fetchall()
        roles = tuple(str(row["rolname"]) for row in role_rows)
        all_protected_tables = (
            "life_schema_migrations",
            "users",
            "auth_tokens",
            "login_attempts",
            "notifications",
            *SCHEMA,
            "jarvis_action_proposals",
            "jarvis_ai_cost_events",
            "jarvis_dialog_sessions",
            "jarvis_turn_receipts",
            "jarvis_native_action_claims",
            "jarvis_app_attest_challenges",
            "jarvis_app_attest_keys",
            "finance_operation_receipts",
            "integration_connections",
            "integration_auth_requests",
            "integration_device_grants",
            "channel_links",
            "channel_messages",
            "channel_preferences",
            "channel_link_rate_limits",
            "message_outbox",
        )
        # Existing databases already secured older tables. Re-running ALTER
        # TABLE across the whole live schema takes unnecessary exclusive locks,
        # so migrations secure only tables introduced after their version. A
        # fresh database still secures the complete schema.
        if postgres_version == 0:
            protected_tables = all_protected_tables
        else:
            protected_tables = ()
            if postgres_version < 2:
                protected_tables += (
                    "integration_connections",
                    "integration_auth_requests",
                )
            if postgres_version < 3:
                protected_tables += (
                    "jarvis_dialog_sessions",
                    "jarvis_turn_receipts",
                )
            if postgres_version < 4:
                protected_tables += (
                    "jarvis_native_action_claims",
                    "integration_device_grants",
                )
            if postgres_version < 5:
                protected_tables += APPS["health"]
            if postgres_version < 6:
                protected_tables += (
                    "channel_links",
                    "channel_messages",
                    "message_outbox",
                )
            if postgres_version < 7:
                protected_tables += ("channel_preferences",)
            if postgres_version < 9:
                protected_tables += (
                    "jarvis_app_attest_challenges",
                    "jarvis_app_attest_keys",
                )
            if postgres_version < 10:
                protected_tables += ("finance_operation_receipts",)
            if postgres_version < 11:
                protected_tables += ("channel_link_rate_limits",)
            if postgres_version < 12:
                protected_tables += (
                    "financial_documents",
                    "training_profiles",
                    "training_plans",
                    "training_sessions",
                    "training_steps",
                    "training_checkins",
                    "training_feedback",
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
