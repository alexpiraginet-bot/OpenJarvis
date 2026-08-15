"""Tests for UserStore — client identity and revocable bearer tokens."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.life.tenancy import AuthError, UserStore


def test_create_user_normalizes_email_and_defaults_name(life):
    user = life.users.create_user("  ALEX@Exemplo.COM ", "senha-forte-123")
    assert user.email == "alex@exemplo.com"
    # A blank name falls back to the local part rather than rendering as an
    # empty greeting on the Today screen.
    assert user.name == "alex"
    assert user.currency == "BRL"


def test_create_user_rejects_short_password(life):
    with pytest.raises(AuthError):
        life.users.create_user("curto@exemplo.com", "1234")


def test_create_user_rejects_invalid_email(life):
    with pytest.raises(AuthError):
        life.users.create_user("sem-arroba", "senha-forte-123")


def test_create_user_rejects_duplicate_email(life, user):
    with pytest.raises(AuthError):
        life.users.create_user(user.email, "outra-senha-789")


def test_authenticate_accepts_correct_password(life, user):
    assert life.users.authenticate(user.email, "senha-forte-123").id == user.id


def test_authenticate_rejects_wrong_password(life, user):
    assert life.users.authenticate(user.email, "senha-errada-000") is None


def test_authenticate_rejects_unknown_email(life):
    assert life.users.authenticate("ninguem@exemplo.com", "qualquer-coisa") is None


def test_password_is_never_stored_in_plaintext(life, user):
    row = life.connection.execute(
        "SELECT password_hash, salt FROM users WHERE id = ?", (user.id,)
    ).fetchone()
    assert "senha-forte-123" not in row["password_hash"]
    assert len(row["salt"]) == 32  # 16 random bytes, hex-encoded


def test_token_round_trip(life, user):
    token = life.users.issue_token(user.id, label="iphone")
    assert life.users.resolve_token(token) == user.id


def test_parallel_token_resolution_never_drops_a_valid_session(life, user):
    token = life.users.issue_token(user.id, label="parallel")

    with ThreadPoolExecutor(max_workers=16) as executor:
        resolved = list(executor.map(life.users.resolve_token, [token] * 160))

    assert resolved == [user.id] * 160


def test_token_plaintext_is_not_stored(life, user):
    token = life.users.issue_token(user.id)
    rows = life.connection.execute("SELECT token_hash FROM auth_tokens").fetchall()
    assert all(row["token_hash"] != token for row in rows)


def test_resolve_rejects_unknown_and_empty_tokens(life, user):
    life.users.issue_token(user.id)
    assert life.users.resolve_token("nao-existe") is None
    assert life.users.resolve_token("") is None


def test_revoke_token_ends_the_session(life, user):
    token = life.users.issue_token(user.id)
    assert life.users.revoke_token(token) is True
    assert life.users.resolve_token(token) is None


def test_revoke_all_tokens_logs_out_every_device(life, user):
    tokens = [life.users.issue_token(user.id) for _ in range(3)]
    assert life.users.revoke_all_tokens(user.id) == 3
    assert all(life.users.resolve_token(t) is None for t in tokens)


def test_user_write_does_not_commit_enclosing_life_transaction(life, user):
    token = ""
    with pytest.raises(RuntimeError, match="rollback requested"):
        with life.store.transaction():
            life.store.insert("accounts", user.id, {"name": "Temporary"})
            token = life.users.issue_token(user.id, label="inside-transaction")
            raise RuntimeError("rollback requested")

    assert life.store.count("accounts", user.id) == 0
    assert life.users.resolve_token(token) is None


def test_expired_token_is_rejected_and_purged(life, user):
    """An expired token must not authenticate, and must not linger as a row."""
    token = life.users.issue_token(user.id)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    life.connection.execute("UPDATE auth_tokens SET expires_at = ?", (past,))
    life.connection.commit()

    assert life.users.resolve_token(token) is None
    remaining = life.connection.execute(
        "SELECT COUNT(*) AS n FROM auth_tokens"
    ).fetchone()
    assert remaining["n"] == 0


def test_corrupt_expiry_is_treated_as_invalid(life, user):
    token = life.users.issue_token(user.id)
    life.connection.execute("UPDATE auth_tokens SET expires_at = 'não-é-data'")
    life.connection.commit()
    assert life.users.resolve_token(token) is None


def test_resolve_records_last_used(life, user):
    token = life.users.issue_token(user.id)
    life.users.resolve_token(token)
    sessions = life.users.list_sessions(user.id)
    assert sessions[0]["last_used_at"]


def test_list_sessions_never_exposes_token_hashes(life, user):
    life.users.issue_token(user.id, label="android")
    session = life.users.list_sessions(user.id)[0]
    assert "token_hash" not in session
    assert session["label"] == "android"


def test_tokens_of_one_user_never_resolve_to_another(life, user, other_user):
    token = life.users.issue_token(other_user.id)
    assert life.users.resolve_token(token) == other_user.id
    assert life.users.resolve_token(token) != user.id


def test_update_user_changes_profile_fields(life, user):
    updated = life.users.update_user(user.id, name="Alex S.", currency="USD")
    assert updated.name == "Alex S."
    assert updated.currency == "USD"


def test_update_user_ignores_unknown_fields(life, user):
    updated = life.users.update_user(user.id, password_hash="hackeado")
    assert updated.id == user.id
    row = life.connection.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user.id,)
    ).fetchone()
    assert row["password_hash"] != "hackeado"


def test_count_users_tracks_registrations(life):
    assert life.users.count_users() == 0
    life.users.create_user("um@exemplo.com", "senha-forte-123")
    life.users.create_user("dois@exemplo.com", "senha-forte-123")
    assert life.users.count_users() == 2


def test_standalone_store_owns_and_closes_its_connection(tmp_path):
    store = UserStore(tmp_path / "solo.db")
    created = store.create_user("solo@exemplo.com", "senha-forte-123")
    assert store.get_user(created.id).email == "solo@exemplo.com"
    store.close()
