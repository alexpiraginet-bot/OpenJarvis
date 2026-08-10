"""Tests for the Life API — the surface the mobile app talks to.

Every test builds an isolated FastAPI app over a temp database, so nothing
touches the developer's real ``~/.openjarvis/life.db``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server.auth_middleware import AuthMiddleware
from openjarvis.server.life_routes import create_life_router


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient over a Life router backed by a temp database."""
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    app = FastAPI()
    router = create_life_router(str(tmp_path / "life.db"))
    app.include_router(router)
    with TestClient(app) as test_client:
        test_client.life = router.life_context
        yield test_client
    router.life_context.close()


@pytest.fixture()
def auth(client):
    """Register a client and return ready-to-use Authorization headers."""
    response = client.post(
        "/v1/life/auth/register",
        json={
            "email": "alex@exemplo.com",
            "password": "senha-forte-123",
            "name": "Alex Silva",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['token']}"}


# -- Identity ----------------------------------------------------------------


def test_register_returns_token_and_profile(client):
    response = client.post(
        "/v1/life/auth/register",
        json={"email": "novo@exemplo.com", "password": "senha-forte-123"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token"]
    assert body["user"]["email"] == "novo@exemplo.com"
    assert "password_hash" not in body["user"]


def test_register_rejects_weak_password(client):
    response = client.post(
        "/v1/life/auth/register",
        json={"email": "fraco@exemplo.com", "password": "123"},
    )
    assert response.status_code == 400


def test_register_rejects_duplicate_email(client, auth):
    response = client.post(
        "/v1/life/auth/register",
        json={"email": "alex@exemplo.com", "password": "outra-senha-456"},
    )
    assert response.status_code == 400


def test_registration_is_closed_by_default_after_the_first_client(
    tmp_path, monkeypatch
):
    """A hosted server must not let strangers create accounts.

    The first account is exempt so a fresh deployment can be bootstrapped
    without shell access; every one after it requires opting in.
    """
    monkeypatch.delenv("OPENJARVIS_LIFE_OPEN_SIGNUP", raising=False)
    app = FastAPI()
    router = create_life_router(str(tmp_path / "life.db"))
    app.include_router(router)
    with TestClient(app) as test_client:
        first = test_client.post(
            "/v1/life/auth/register",
            json={"email": "dono@exemplo.com", "password": "senha-forte-123"},
        )
        second = test_client.post(
            "/v1/life/auth/register",
            json={"email": "intruso@exemplo.com", "password": "senha-forte-123"},
        )
    assert first.status_code == 201
    assert second.status_code == 403
    router.life_context.close()


def test_login_returns_a_token(client, auth):
    response = client.post(
        "/v1/life/auth/login",
        json={"email": "alex@exemplo.com", "password": "senha-forte-123"},
    )
    assert response.status_code == 200
    assert response.json()["token"]


def test_login_with_wrong_password_is_rejected(client, auth):
    response = client.post(
        "/v1/life/auth/login",
        json={"email": "alex@exemplo.com", "password": "errada-000"},
    )
    assert response.status_code == 401


def test_login_does_not_reveal_whether_an_email_exists(client, auth):
    """Both failure modes must return the same status and message."""
    unknown = client.post(
        "/v1/life/auth/login",
        json={"email": "ninguem@exemplo.com", "password": "senha-forte-123"},
    )
    wrong = client.post(
        "/v1/life/auth/login",
        json={"email": "alex@exemplo.com", "password": "errada-000"},
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_logout_revokes_the_token(client, auth):
    assert client.post("/v1/life/auth/logout", headers=auth).status_code == 200
    assert client.get("/v1/life/me", headers=auth).status_code == 401


def test_me_returns_profile_and_sessions(client, auth):
    body = client.get("/v1/life/me", headers=auth).json()
    assert body["user"]["name"] == "Alex Silva"
    assert body["sessions"][0]["label"] == "signup"


def test_patch_me_updates_profile(client, auth):
    response = client.patch(
        "/v1/life/me", headers=auth, json={"name": "Alex S.", "currency": "USD"}
    )
    assert response.json()["user"]["name"] == "Alex S."
    assert response.json()["user"]["currency"] == "USD"


# -- Authentication ----------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/v1/life/me"),
        ("get", "/v1/life/today"),
        ("get", "/v1/life/apps"),
        ("get", "/v1/life/summary/finance"),
        ("get", "/v1/life/records/accounts"),
        ("post", "/v1/life/records/accounts"),
    ],
)
def test_endpoints_require_a_token(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_invalid_token_is_rejected(client):
    response = client.get(
        "/v1/life/me", headers={"Authorization": "Bearer nao-existe"}
    )
    assert response.status_code == 401


def test_life_routes_bypass_the_global_api_key(tmp_path, monkeypatch):
    """The phone holds a user token, never the operator's server key.

    Without this exemption the app could not reach the API at all on a server
    that sets ``OPENJARVIS_API_KEY``.
    """
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    app = FastAPI()
    router = create_life_router(str(tmp_path / "life.db"))
    app.include_router(router)
    app.add_middleware(AuthMiddleware, api_key="chave-do-servidor")

    with TestClient(app) as test_client:
        registered = test_client.post(
            "/v1/life/auth/register",
            json={"email": "app@exemplo.com", "password": "senha-forte-123"},
        )
        assert registered.status_code == 201
        token = registered.json()["token"]
        # A user token works without the server key…
        assert (
            test_client.get(
                "/v1/life/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 200
        )
        # …and the server key alone is not a user credential.
        assert (
            test_client.get(
                "/v1/life/me",
                headers={"Authorization": "Bearer chave-do-servidor"},
            ).status_code
            == 401
        )
    router.life_context.close()


# -- Records -----------------------------------------------------------------


def test_create_and_read_a_record(client, auth):
    created = client.post(
        "/v1/life/records/accounts",
        headers=auth,
        json={"fields": {"name": "Nubank", "balance_cents": 250000}},
    )
    assert created.status_code == 201
    record_id = created.json()["record"]["id"]

    fetched = client.get(f"/v1/life/records/accounts/{record_id}", headers=auth)
    assert fetched.json()["record"]["name"] == "Nubank"


def test_unknown_table_is_404(client, auth):
    assert client.get("/v1/life/records/bitcoin", headers=auth).status_code == 404


def test_unknown_column_is_400(client, auth):
    response = client.post(
        "/v1/life/records/accounts", headers=auth, json={"fields": {"nmae": "X"}}
    )
    assert response.status_code == 400


def test_patch_and_delete_a_record(client, auth):
    record_id = client.post(
        "/v1/life/records/habits", headers=auth, json={"fields": {"name": "Ler"}}
    ).json()["record"]["id"]

    patched = client.patch(
        f"/v1/life/records/habits/{record_id}",
        headers=auth,
        json={"fields": {"name": "Ler 30min"}},
    )
    assert patched.json()["record"]["name"] == "Ler 30min"

    assert client.delete(
        f"/v1/life/records/habits/{record_id}", headers=auth
    ).status_code == 200
    assert client.get(
        f"/v1/life/records/habits/{record_id}", headers=auth
    ).status_code == 404


def test_list_filters_by_equality_and_comparison(client, auth):
    for name, due in (("Luz", "2026-08-01"), ("Net", "2026-08-20")):
        client.post(
            "/v1/life/records/bills",
            headers=auth,
            json={"fields": {"name": name, "amount_cents": 100, "due_on": due}},
        )
    equality = client.get(
        "/v1/life/records/bills?status=pending", headers=auth
    ).json()
    assert equality["count"] == 2

    comparison = client.get(
        "/v1/life/records/bills?due_on__lte=2026-08-10", headers=auth
    ).json()
    assert [r["name"] for r in comparison["records"]] == ["Luz"]


def test_list_with_a_bad_filter_column_is_400(client, auth):
    response = client.get("/v1/life/records/bills?vencimento=hoje", headers=auth)
    assert response.status_code == 400


def test_records_are_isolated_between_clients(client, auth):
    """The core promise of a hosted assistant."""
    client.post(
        "/v1/life/records/accounts",
        headers=auth,
        json={"fields": {"name": "Conta do Alex", "balance_cents": 999}},
    )
    other = client.post(
        "/v1/life/auth/register",
        json={"email": "bruna@exemplo.com", "password": "senha-forte-456"},
    ).json()
    other_auth = {"Authorization": f"Bearer {other['token']}"}

    listed = client.get("/v1/life/records/accounts", headers=other_auth).json()
    assert listed["count"] == 0


def test_reading_another_clients_record_by_id_is_404(client, auth):
    record_id = client.post(
        "/v1/life/records/accounts",
        headers=auth,
        json={"fields": {"name": "Privada"}},
    ).json()["record"]["id"]
    other = client.post(
        "/v1/life/auth/register",
        json={"email": "bruna@exemplo.com", "password": "senha-forte-456"},
    ).json()
    other_auth = {"Authorization": f"Bearer {other['token']}"}

    assert client.get(
        f"/v1/life/records/accounts/{record_id}", headers=other_auth
    ).status_code == 404


# -- Home and summaries ------------------------------------------------------


def test_apps_manifest_lists_every_springboard_icon(client, auth):
    body = client.get("/v1/life/apps", headers=auth).json()
    assert [app["id"] for app in body["apps"]] == [
        "finance",
        "fitness",
        "routine",
        "family",
        "work",
    ]
    assert set(body["badges"]) == {
        "finance",
        "fitness",
        "routine",
        "family",
        "work",
    }


def test_today_reflects_stored_data(client, auth):
    client.post(
        "/v1/life/records/bills",
        headers=auth,
        json={
            "fields": {"name": "Luz", "amount_cents": 18000, "due_on": "2020-01-01"}
        },
    )
    body = client.get("/v1/life/today", headers=auth).json()
    assert body["badges"]["finance"] == 1
    assert body["alerts"][0]["severity"] == "critical"
    assert body["greeting"].endswith("Alex")


@pytest.mark.parametrize(
    "app", ["finance", "fitness", "routine", "family", "work"]
)
def test_every_app_has_a_summary(client, auth, app):
    assert client.get(f"/v1/life/summary/{app}", headers=auth).status_code == 200


def test_unknown_app_summary_is_404(client, auth):
    assert client.get("/v1/life/summary/astrologia", headers=auth).status_code == 404


# -- Actions -----------------------------------------------------------------


def test_pay_bill_action(client, auth):
    account = client.post(
        "/v1/life/records/accounts",
        headers=auth,
        json={"fields": {"name": "Nubank", "balance_cents": 100000}},
    ).json()["record"]["id"]
    bill = client.post(
        "/v1/life/records/bills",
        headers=auth,
        json={
            "fields": {
                "name": "Luz",
                "amount_cents": 18000,
                "due_on": "2026-08-07",
                "recurrence": "monthly",
            }
        },
    ).json()["record"]["id"]

    body = client.post(
        f"/v1/life/actions/pay-bill/{bill}",
        headers=auth,
        json={"account_id": account},
    ).json()
    assert body["bill"]["status"] == "paid"
    assert body["next_bill_id"]

    balance = client.get(
        f"/v1/life/records/accounts/{account}", headers=auth
    ).json()["record"]["balance_cents"]
    assert balance == 82000


def test_paying_a_bill_twice_is_400(client, auth):
    bill = client.post(
        "/v1/life/records/bills",
        headers=auth,
        json={
            "fields": {"name": "Net", "amount_cents": 9900, "due_on": "2026-08-07"}
        },
    ).json()["record"]["id"]
    client.post(f"/v1/life/actions/pay-bill/{bill}", headers=auth, json={})
    second = client.post(f"/v1/life/actions/pay-bill/{bill}", headers=auth, json={})
    assert second.status_code == 400


def test_complete_workout_action(client, auth):
    workout = client.post(
        "/v1/life/records/workouts",
        headers=auth,
        json={"fields": {"name": "Peito", "scheduled_on": "2026-08-10"}},
    ).json()["record"]["id"]
    body = client.post(
        f"/v1/life/actions/complete-workout/{workout}",
        headers=auth,
        json={"duration_min": 55},
    ).json()
    assert body["workout"]["completed_at"]
    assert body["workout"]["duration_min"] == 55


def test_check_and_uncheck_a_habit(client, auth):
    habit = client.post(
        "/v1/life/records/habits", headers=auth, json={"fields": {"name": "Ler"}}
    ).json()["record"]["id"]

    checked = client.post(
        f"/v1/life/actions/check-habit/{habit}", headers=auth, json={}
    ).json()
    assert checked["created"] is True
    assert checked["streak"] == 1

    again = client.post(
        f"/v1/life/actions/check-habit/{habit}", headers=auth, json={}
    ).json()
    assert again["created"] is False

    removed = client.delete(
        f"/v1/life/actions/check-habit/{habit}", headers=auth
    ).json()
    assert removed["removed"] is True
    assert removed["streak"] == 0


def test_complete_task_action(client, auth):
    task = client.post(
        "/v1/life/records/work_tasks",
        headers=auth,
        json={"fields": {"title": "Proposta"}},
    ).json()["record"]["id"]
    body = client.post(f"/v1/life/actions/complete-task/{task}", headers=auth).json()
    assert body["task"]["status"] == "done"


# -- Ask (voice) -------------------------------------------------------------


def test_ask_answers_from_data_when_no_engine(client, auth):
    """The voice screen must never go mute for want of a model."""
    client.post(
        "/v1/life/records/bills",
        headers=auth,
        json={
            "fields": {"name": "Luz", "amount_cents": 18000, "due_on": "2020-01-01"}
        },
    )
    body = client.post(
        "/v1/life/ask", headers=auth, json={"question": "o que tá vencendo?"}
    ).json()
    assert body["source"] == "data"
    assert "Luz" in body["answer"]
    assert body["context"]["badges"]["finance"] == 1


def test_ask_with_nothing_pending_says_so(client, auth):
    body = client.post(
        "/v1/life/ask", headers=auth, json={"question": "como estou?"}
    ).json()
    assert "Tudo em dia" in body["answer"]


def test_ask_rejects_an_empty_question(client, auth):
    response = client.post("/v1/life/ask", headers=auth, json={"question": "   "})
    assert response.status_code == 400


def test_ask_requires_a_token(client):
    assert client.post("/v1/life/ask", json={"question": "oi"}).status_code == 401


def test_ask_uses_the_engine_when_one_is_wired(tmp_path, monkeypatch):
    """With an engine present, the model's answer is what gets spoken."""
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")

    class FakeEngine:
        def __init__(self):
            self.seen = []

        def generate(self, messages, **kwargs):
            self.seen.append(messages)
            return {"content": "Você gastou R$450 este mês."}

    engine = FakeEngine()
    app = FastAPI()
    router = create_life_router(str(tmp_path / "life.db"))
    app.include_router(router)
    app.state.engine = engine

    with TestClient(app) as test_client:
        token = test_client.post(
            "/v1/life/auth/register",
            json={"email": "voz@exemplo.com", "password": "senha-forte-123"},
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        body = test_client.post(
            "/v1/life/ask", headers=headers, json={"question": "quanto gastei?"}
        ).json()

    assert body["source"] == "model"
    assert body["answer"] == "Você gastou R$450 este mês."
    # The client's own data must reach the model, or it answers about nobody.
    system_prompt = engine.seen[0][0].content
    assert "DADOS DO CLIENTE" in system_prompt
    assert "Saldo:" in system_prompt
    router.life_context.close()


def test_ask_degrades_to_data_when_the_engine_fails(tmp_path, monkeypatch):
    """A broken model must not 500 the microphone."""
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")

    class BrokenEngine:
        def generate(self, messages, **kwargs):
            raise RuntimeError("modelo fora do ar")

    app = FastAPI()
    router = create_life_router(str(tmp_path / "life.db"))
    app.include_router(router)
    app.state.engine = BrokenEngine()

    with TestClient(app) as test_client:
        token = test_client.post(
            "/v1/life/auth/register",
            json={"email": "voz2@exemplo.com", "password": "senha-forte-123"},
        ).json()["token"]
        response = test_client.post(
            "/v1/life/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "e aí?"},
        )

    assert response.status_code == 200
    assert response.json()["source"] == "data"
    router.life_context.close()


def test_action_on_another_clients_record_is_400(client, auth):
    bill = client.post(
        "/v1/life/records/bills",
        headers=auth,
        json={
            "fields": {"name": "Privada", "amount_cents": 100, "due_on": "2026-08-01"}
        },
    ).json()["record"]["id"]
    other = client.post(
        "/v1/life/auth/register",
        json={"email": "bruna@exemplo.com", "password": "senha-forte-456"},
    ).json()
    other_auth = {"Authorization": f"Bearer {other['token']}"}

    response = client.post(
        f"/v1/life/actions/pay-bill/{bill}", headers=other_auth, json={}
    )
    assert response.status_code == 400
