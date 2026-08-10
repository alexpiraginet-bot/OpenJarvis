"""Life OS — the structured record of a client's life.

This package turns OpenJarvis from an agent framework into a personal
assistant *product*: money, training, habits, family and work as queryable
data rather than free-form notes, scoped per client so one hosted server can
serve many.

Typical use is the :func:`open_life` factory, which wires identity and domain
storage onto a single shared connection::

    from openjarvis.life import open_life

    life = open_life()
    user = life.users.create_user("cliente@exemplo.com", "senha-forte")
    life.service.add_transaction(user.id, amount_cents=4590, category="mercado")
    briefing = life.today(user)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.life.db import Database, connect
from openjarvis.life.schema import APPS, SCHEMA, TableSpec, ensure_schema
from openjarvis.life.service import LifeService, LifeServiceError
from openjarvis.life.store import Filter, LifeStore, LifeStoreError
from openjarvis.life.tenancy import AuthError, User, UserStore
from openjarvis.life.today import build_today, build_voice_today

__all__ = [
    "APPS",
    "SCHEMA",
    "AuthError",
    "Filter",
    "LifeContext",
    "LifeService",
    "LifeServiceError",
    "LifeStore",
    "LifeStoreError",
    "TableSpec",
    "User",
    "UserStore",
    "build_today",
    "build_voice_today",
    "default_db_path",
    "ensure_schema",
    "open_life",
]


def default_db_path() -> Path:
    """Location of the Life database under the OpenJarvis data directory."""
    from openjarvis.core.paths import get_data_dir  # noqa: PLC0415 — avoid cycle

    return get_data_dir() / "life.db"


@dataclass(frozen=True, slots=True)
class LifeContext:
    """Identity, storage and domain rules sharing one database connection."""

    users: UserStore
    store: LifeStore
    service: LifeService
    connection: Database

    def today(
        self,
        user: User,
        *,
        anchor: Optional[date] = None,
        now_hour: int = 9,
    ) -> Dict[str, Any]:
        """Build the cross-domain Today briefing for ``user``."""
        return build_today(self.service, user, anchor=anchor, now_hour=now_hour)

    def voice_today(
        self,
        user: User,
        *,
        anchor: Optional[date] = None,
        now_hour: int = 9,
    ) -> Dict[str, Any]:
        """Build the latency-sensitive briefing used by spoken turns."""
        return build_voice_today(self.service, user, anchor=anchor, now_hour=now_hour)

    def close(self) -> None:
        """Close the shared connection.

        Both stores borrowed it, so neither may close it — the context that
        opened it owns it.
        """
        self.connection.close()


def open_life(db_path: str | Path | None = None) -> LifeContext:
    """Open the Life database and return identity + storage + service.

    ``db_path`` may be a SQLite path or a PostgreSQL DSN; omitted, it falls
    back to ``OPENJARVIS_LIFE_DB`` and then to a file under the data directory.

    One connection is shared across both stores so a bill payment — which
    touches bills, transactions and accounts — commits atomically instead of
    racing itself across two handles on the same database.
    """
    target = str(db_path) if db_path is not None else None
    db = connect(target)
    ensure_schema(db)

    store = LifeStore(db=db)
    users = UserStore(db=db)
    return LifeContext(
        users=users,
        store=store,
        service=LifeService(store),
        connection=db,
    )
