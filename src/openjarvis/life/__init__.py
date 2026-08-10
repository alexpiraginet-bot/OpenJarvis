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

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.life.schema import APPS, SCHEMA, TableSpec, ensure_schema
from openjarvis.life.service import LifeService, LifeServiceError
from openjarvis.life.store import Filter, LifeStore, LifeStoreError
from openjarvis.life.tenancy import AuthError, User, UserStore
from openjarvis.life.today import build_today

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
    connection: sqlite3.Connection

    def today(
        self,
        user: User,
        *,
        anchor: Optional[date] = None,
        now_hour: int = 9,
    ) -> Dict[str, Any]:
        """Build the cross-domain Today briefing for ``user``."""
        return build_today(self.service, user, anchor=anchor, now_hour=now_hour)

    def close(self) -> None:
        """Close the shared connection.

        Both stores borrowed it, so neither may close it — the context that
        opened it owns it.
        """
        self.connection.close()


def open_life(db_path: str | Path | None = None) -> LifeContext:
    """Open the Life database and return identity + storage + service.

    One connection is shared across both stores so a bill payment (which
    touches bills, transactions and accounts) commits atomically instead of
    racing itself across two handles on the same file.
    """
    resolved = Path(db_path) if db_path is not None else default_db_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_schema(conn)

    store = LifeStore(resolved, conn=conn)
    users = UserStore(resolved, conn=conn)
    return LifeContext(
        users=users,
        store=store,
        service=LifeService(store),
        connection=conn,
    )
