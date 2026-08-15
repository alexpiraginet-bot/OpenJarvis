"""Tests for login throttling — brute-force protection without an oracle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openjarvis.life.tenancy import (
    FAILURE_WINDOW_SECONDS,
    LOCKOUT_SECONDS,
    MAX_LOGIN_FAILURES,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def test_a_fresh_address_is_not_locked(life):
    assert life.users.seconds_until_unlocked("qualquer@exemplo.com") == 0


def test_failures_below_the_threshold_do_not_lock(life, user):
    for _ in range(MAX_LOGIN_FAILURES - 1):
        life.users.record_login_failure(user.email, now=NOW)
    assert life.users.seconds_until_unlocked(user.email, now=NOW) == 0


def test_crossing_the_threshold_locks_the_address(life, user):
    for _ in range(MAX_LOGIN_FAILURES):
        life.users.record_login_failure(user.email, now=NOW)
    remaining = life.users.seconds_until_unlocked(user.email, now=NOW)
    assert 0 < remaining <= LOCKOUT_SECONDS


def test_the_lock_expires(life, user):
    for _ in range(MAX_LOGIN_FAILURES):
        life.users.record_login_failure(user.email, now=NOW)
    later = NOW + timedelta(seconds=LOCKOUT_SECONDS + 1)
    assert life.users.seconds_until_unlocked(user.email, now=later) == 0


def test_a_quiet_period_resets_the_counter(life, user):
    """A typo last month plus a typo today is not a brute-force attempt."""
    for _ in range(MAX_LOGIN_FAILURES - 1):
        life.users.record_login_failure(user.email, now=NOW)
    much_later = NOW + timedelta(seconds=FAILURE_WINDOW_SECONDS + 60)
    life.users.record_login_failure(user.email, now=much_later)
    assert life.users.seconds_until_unlocked(user.email, now=much_later) == 0


def test_a_successful_login_clears_the_counter(life, user):
    for _ in range(MAX_LOGIN_FAILURES - 1):
        life.users.record_login_failure(user.email, now=NOW)
    life.users.clear_login_failures(user.email)
    for _ in range(MAX_LOGIN_FAILURES - 1):
        life.users.record_login_failure(user.email, now=NOW)
    assert life.users.seconds_until_unlocked(user.email, now=NOW) == 0


def test_unregistered_addresses_are_throttled_too(life):
    """Throttling only real accounts would make the 429 an enumeration oracle."""
    ghost = "naoexiste@exemplo.com"
    for _ in range(MAX_LOGIN_FAILURES):
        life.users.record_login_failure(ghost, now=NOW)
    assert life.users.seconds_until_unlocked(ghost, now=NOW) > 0


def test_throttling_is_per_address(life, user, other_user):
    for _ in range(MAX_LOGIN_FAILURES):
        life.users.record_login_failure(user.email, now=NOW)
    assert life.users.seconds_until_unlocked(other_user.email, now=NOW) == 0


def test_the_table_stores_no_email_addresses(life, user):
    """A breach of this table must not yield a list of who tried to sign in."""
    life.users.record_login_failure(user.email, now=NOW)
    rows = life.connection.execute("SELECT key FROM login_attempts").fetchall()
    assert rows
    assert all(user.email not in row["key"] for row in rows)


def test_email_case_and_spacing_do_not_dodge_the_throttle(life, user):
    for _ in range(MAX_LOGIN_FAILURES):
        life.users.record_login_failure("  ALEX@Exemplo.COM ", now=NOW)
    assert life.users.seconds_until_unlocked("alex@exemplo.com", now=NOW) > 0


def test_a_corrupt_lock_timestamp_fails_open(life, user):
    """A bad row must not lock a paying client out of their own money."""
    life.users.record_login_failure(user.email, now=NOW)
    life.connection.execute("UPDATE login_attempts SET locked_until = 'lixo'")
    life.connection.commit()
    assert life.users.seconds_until_unlocked(user.email, now=NOW) == 0
