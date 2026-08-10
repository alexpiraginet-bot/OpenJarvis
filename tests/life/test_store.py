"""Tests for LifeStore — tenant-scoped CRUD and the schema allow-list.

The isolation tests matter most: a hosted assistant that leaks one client's
bank balance to another is not a bug, it is the end of the product.
"""

from __future__ import annotations

import pytest

from openjarvis.life.store import Filter, LifeStore, LifeStoreError


def test_insert_returns_id_and_stamps_owner(life, user):
    record_id = life.store.insert("accounts", user.id, {"name": "Nubank"})
    record = life.store.get("accounts", user.id, record_id)
    assert record["name"] == "Nubank"
    assert record["user_id"] == user.id
    assert record["created_at"]


def test_insert_rejects_unknown_table(life, user):
    with pytest.raises(LifeStoreError):
        life.store.insert("bitcoin_wallets", user.id, {"name": "x"})


def test_insert_rejects_unknown_column(life, user):
    """A typo'd field is a 400, not a silently dropped value."""
    with pytest.raises(LifeStoreError):
        life.store.insert("accounts", user.id, {"nmae": "Nubank"})


def test_insert_rejects_store_owned_columns(life, user):
    with pytest.raises(LifeStoreError):
        life.store.insert("accounts", user.id, {"name": "X", "user_id": "outro"})


def test_update_patches_only_given_columns(life, user):
    record_id = life.store.insert(
        "accounts", user.id, {"name": "Nubank", "balance_cents": 1000}
    )
    life.store.update("accounts", user.id, record_id, {"balance_cents": 2000})
    record = life.store.get("accounts", user.id, record_id)
    assert record["name"] == "Nubank"
    assert record["balance_cents"] == 2000


def test_update_missing_record_returns_false(life, user):
    assert life.store.update("accounts", user.id, "nao-existe", {"name": "x"}) is False


def test_delete_removes_record(life, user):
    record_id = life.store.insert("accounts", user.id, {"name": "Itaú"})
    assert life.store.delete("accounts", user.id, record_id) is True
    assert life.store.get("accounts", user.id, record_id) is None


def test_list_applies_filters_and_ordering(life, user):
    for day, name in (("2026-08-01", "a"), ("2026-08-05", "b"), ("2026-08-09", "c")):
        life.store.insert(
            "transactions",
            user.id,
            {"amount_cents": 100, "occurred_on": day, "description": name},
        )
    rows = life.store.list_records(
        "transactions",
        user.id,
        filters=(Filter("occurred_on", ">=", "2026-08-05"),),
        order_by="occurred_on",
        descending=False,
    )
    assert [row["description"] for row in rows] == ["b", "c"]


def test_list_in_operator(life, user):
    for status in ("todo", "doing", "done"):
        life.store.insert("work_tasks", user.id, {"title": status, "status": status})
    rows = life.store.list_records(
        "work_tasks", user.id, filters=(Filter("status", "IN", ["todo", "doing"]),)
    )
    assert {row["status"] for row in rows} == {"todo", "doing"}


def test_empty_in_matches_nothing_instead_of_erroring(life, user):
    """``IN ()`` is a SQLite syntax error; "match nothing" is the honest read."""
    life.store.insert("work_tasks", user.id, {"title": "t"})
    rows = life.store.list_records(
        "work_tasks", user.id, filters=(Filter("status", "IN", []),)
    )
    assert rows == []


def test_is_not_null_filter(life, user):
    life.store.insert("workouts", user.id, {"name": "A", "scheduled_on": "2026-08-01"})
    life.store.insert(
        "workouts",
        user.id,
        {
            "name": "B",
            "scheduled_on": "2026-08-02",
            "completed_at": "2026-08-02T10:00:00Z",
        },
    )
    rows = life.store.list_records(
        "workouts", user.id, filters=(Filter("completed_at", "IS NOT NULL"),)
    )
    assert [row["name"] for row in rows] == ["B"]


def test_unknown_filter_column_raises(life, user):
    with pytest.raises(LifeStoreError):
        life.store.list_records(
            "accounts", user.id, filters=(Filter("saldo", "=", 1),)
        )


def test_unsupported_operator_raises(life, user):
    """Operators come from an allow-list, never from caller strings."""
    with pytest.raises(LifeStoreError):
        life.store.list_records(
            "accounts", user.id, filters=(Filter("name", "; DROP TABLE", "x"),)
        )


def test_unknown_order_column_raises(life, user):
    with pytest.raises(LifeStoreError):
        life.store.list_records("accounts", user.id, order_by="; DROP TABLE users")


def test_limit_is_clamped(life, user):
    for i in range(5):
        life.store.insert("accounts", user.id, {"name": f"conta-{i}"})
    assert len(life.store.list_records("accounts", user.id, limit=2)) == 2


def test_sum_and_group_sum(life, user):
    for category, cents in (("mercado", 5000), ("mercado", 2500), ("uber", 1800)):
        life.store.insert(
            "transactions",
            user.id,
            {
                "amount_cents": cents,
                "category": category,
                "occurred_on": "2026-08-01",
            },
        )
    assert life.store.sum_column("transactions", user.id, "amount_cents") == 9300
    grouped = life.store.group_sum(
        "transactions", user.id, "category", "amount_cents"
    )
    assert grouped[0] == {"label": "mercado", "total": 7500}


def test_sum_of_nothing_is_zero(life, user):
    assert life.store.sum_column("transactions", user.id, "amount_cents") == 0


def test_count_respects_filters(life, user):
    life.store.insert(
        "bills", user.id, {"name": "a", "amount_cents": 1, "due_on": "2026-08-01"}
    )
    life.store.insert(
        "bills",
        user.id,
        {"name": "b", "amount_cents": 1, "due_on": "2026-08-02", "status": "paid"},
    )
    assert life.store.count("bills", user.id) == 2
    assert (
        life.store.count("bills", user.id, filters=(Filter("status", "=", "paid"),))
        == 1
    )


def test_delete_where_removes_matching_rows(life, user):
    for status in ("todo", "todo", "done"):
        life.store.insert("work_tasks", user.id, {"title": "t", "status": status})
    removed = life.store.delete_where(
        "work_tasks", user.id, (Filter("status", "=", "todo"),)
    )
    assert removed == 2
    assert life.store.count("work_tasks", user.id) == 1


# -- Tenant isolation --------------------------------------------------------


def test_list_never_returns_another_users_records(life, user, other_user):
    life.store.insert("accounts", user.id, {"name": "Conta do Alex"})
    life.store.insert("accounts", other_user.id, {"name": "Conta da Bruna"})
    rows = life.store.list_records("accounts", user.id)
    assert [row["name"] for row in rows] == ["Conta do Alex"]


def test_get_across_tenants_returns_none(life, user, other_user):
    record_id = life.store.insert("accounts", user.id, {"name": "Privada"})
    assert life.store.get("accounts", other_user.id, record_id) is None


def test_update_across_tenants_is_a_no_op(life, user, other_user):
    record_id = life.store.insert("accounts", user.id, {"name": "Privada"})
    updated = life.store.update("accounts", other_user.id, record_id, {"name": "X"})
    assert updated is False
    assert life.store.get("accounts", user.id, record_id)["name"] == "Privada"


def test_delete_across_tenants_is_a_no_op(life, user, other_user):
    record_id = life.store.insert("accounts", user.id, {"name": "Privada"})
    assert life.store.delete("accounts", other_user.id, record_id) is False
    assert life.store.get("accounts", user.id, record_id) is not None


def test_aggregates_are_scoped_per_tenant(life, user, other_user):
    life.store.insert(
        "transactions",
        user.id,
        {"amount_cents": 1000, "occurred_on": "2026-08-01"},
    )
    life.store.insert(
        "transactions",
        other_user.id,
        {"amount_cents": 9999, "occurred_on": "2026-08-01"},
    )
    assert life.store.sum_column("transactions", user.id, "amount_cents") == 1000


def test_standalone_store_owns_and_closes_its_connection(tmp_path):
    store = LifeStore(tmp_path / "solo.db")
    record_id = store.insert("accounts", "u1", {"name": "Solo"})
    assert store.get("accounts", "u1", record_id)["name"] == "Solo"
    store.close()
