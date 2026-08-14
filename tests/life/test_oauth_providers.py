"""Provider-bound OAuth exchange and refresh contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from openjarvis.life.oauth_providers import (
    OAuthProviderClient,
    OAuthProviderError,
    OAuthRequestContext,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _client(monkeypatch, handler) -> OAuthProviderClient:
    monkeypatch.setenv("OPENJARVIS_LIFE_GOOGLE_CLIENT_ID", "google-id")
    monkeypatch.setenv("OPENJARVIS_LIFE_GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.setenv("OPENJARVIS_LIFE_MICROSOFT_CLIENT_ID", "microsoft-id")
    monkeypatch.setenv("OPENJARVIS_LIFE_MICROSOFT_CLIENT_SECRET", "microsoft-secret")
    monkeypatch.setenv("OPENJARVIS_LIFE_STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("OPENJARVIS_LIFE_STRAVA_CLIENT_SECRET", "strava-secret")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OAuthProviderClient(http=http, now=lambda: NOW)


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def test_google_exchange_sends_server_secret_redirect_and_pkce(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://oauth2.googleapis.com/token"
        assert _form(request) == {
            "client_id": ["google-id"],
            "client_secret": ["google-secret"],
            "code": ["google-code"],
            "code_verifier": ["pkce-verifier"],
            "grant_type": ["authorization_code"],
            "redirect_uri": ["https://jarvis.example/callback"],
        }
        return httpx.Response(
            200,
            json={
                "access_token": "google-access",
                "refresh_token": "google-refresh",
                "expires_in": 3600,
                "scope": "scope.one scope.two",
                "token_type": "Bearer",
            },
        )

    client = _client(monkeypatch, handler)
    credential = client.exchange(
        OAuthRequestContext(
            provider="gmail",
            redirect_uri="https://jarvis.example/callback",
            code_verifier="pkce-verifier",
            requested_scopes=("scope.one", "scope.two"),
        ),
        "google-code",
    )

    assert credential.expires_at == "2026-08-14T13:00:00+00:00"
    assert credential.granted_scopes == ("scope.one", "scope.two")
    assert credential.to_vault_payload() == {
        "access_token": "google-access",
        "expires_at": "2026-08-14T13:00:00+00:00",
        "granted_scopes": ["scope.one", "scope.two"],
        "refresh_token": "google-refresh",
        "token_type": "Bearer",
    }


def test_microsoft_exchange_includes_scope_and_pkce(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == (
            "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        )
        assert _form(request) == {
            "client_id": ["microsoft-id"],
            "client_secret": ["microsoft-secret"],
            "code": ["microsoft-code"],
            "code_verifier": ["microsoft-verifier"],
            "grant_type": ["authorization_code"],
            "redirect_uri": ["https://jarvis.example/microsoft"],
            "scope": ["offline_access User.Read Mail.Read Calendars.Read"],
        }
        return httpx.Response(
            200,
            json={
                "access_token": "microsoft-access",
                "refresh_token": "microsoft-refresh",
                "expires_in": 3599,
                "scope": "offline_access User.Read Mail.Read Calendars.Read",
                "token_type": "Bearer",
            },
        )

    client = _client(monkeypatch, handler)
    credential = client.exchange(
        OAuthRequestContext(
            provider="outlook",
            redirect_uri="https://jarvis.example/microsoft",
            code_verifier="microsoft-verifier",
            requested_scopes=(
                "offline_access",
                "User.Read",
                "Mail.Read",
                "Calendars.Read",
            ),
        ),
        "microsoft-code",
    )

    assert credential.refresh_token == "microsoft-refresh"
    assert credential.expires_at == "2026-08-14T12:59:59+00:00"


def test_strava_exchange_omits_pkce_and_extracts_athlete_label(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://www.strava.com/oauth/token"
        assert _form(request) == {
            "client_id": ["12345"],
            "client_secret": ["strava-secret"],
            "code": ["strava-code"],
            "grant_type": ["authorization_code"],
        }
        return httpx.Response(
            200,
            json={
                "access_token": "strava-access",
                "refresh_token": "strava-refresh",
                "expires_at": 1786719600,
                "scope": "read,activity:read_all",
                "token_type": "Bearer",
                "athlete": {"firstname": "Alex", "lastname": "Teixeira"},
            },
        )

    client = _client(monkeypatch, handler)
    credential = client.exchange(
        OAuthRequestContext(
            provider="strava",
            redirect_uri="https://jarvis.example/strava",
            code_verifier="",
            requested_scopes=("read", "activity:read_all"),
        ),
        "strava-code",
    )

    assert credential.account_label == "Alex Teixeira"
    assert credential.granted_scopes == ("read", "activity:read_all")


@pytest.mark.parametrize("provider", ["gmail", "google_calendar", "outlook", "strava"])
def test_refresh_uses_latest_refresh_token_and_preserves_it_when_omitted(
    monkeypatch, provider: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        form = _form(request)
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == ["existing-refresh"]
        return httpx.Response(
            200,
            json={
                "access_token": "fresh-access",
                "expires_in": 1800,
                "scope": "scope.one scope.two",
                "token_type": "Bearer",
            },
        )

    client = _client(monkeypatch, handler)
    refreshed = client.refresh(
        provider,
        {
            "access_token": "expired-access",
            "refresh_token": "existing-refresh",
            "granted_scopes": ["scope.one", "scope.two"],
        },
    )

    assert refreshed.access_token == "fresh-access"
    assert refreshed.refresh_token == "existing-refresh"
    assert refreshed.expires_at == "2026-08-14T12:30:00+00:00"


def test_provider_error_never_repeats_response_or_secret(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "leaked google-secret and google-code",
            },
        )

    client = _client(monkeypatch, handler)
    with pytest.raises(OAuthProviderError) as caught:
        client.exchange(
            OAuthRequestContext(
                provider="gmail",
                redirect_uri="https://jarvis.example/callback",
                code_verifier="verifier",
                requested_scopes=("scope.one",),
            ),
            "google-code",
        )

    message = str(caught.value)
    assert "invalid_grant" in message
    assert "google-secret" not in message
    assert "google-code" not in message
    assert "leaked" not in message


def test_gmail_fetch_is_bounded_to_25_metadata_messages(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gmail-access"
        if request.url.path.endswith("/messages"):
            assert request.url.params["maxResults"] == "25"
            assert request.url.params["q"] == "newer_than:31d"
            return httpx.Response(200, json={"messages": [{"id": "m-1"}]})
        assert request.url.path.endswith("/messages/m-1")
        assert request.url.params["format"] == "metadata"
        assert request.url.params.get_list("metadataHeaders") == [
            "From",
            "Subject",
            "Date",
        ]
        return httpx.Response(
            200,
            json={
                "id": "m-1",
                "threadId": "t-1",
                "internalDate": "1786712400000",
                "labelIds": ["INBOX", "UNREAD"],
                "snippet": "Resumo seguro e curto.",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Cliente <c@example.com>"},
                        {"name": "Subject", "value": "Proposta comercial"},
                    ]
                },
            },
        )

    client = _client(monkeypatch, handler)
    items = client.fetch_items(
        "gmail",
        {"access_token": "gmail-access"},
        NOW,
    )

    assert len(items) == 1
    assert items[0].external_id == "m-1"
    assert items[0].kind == "mail"
    assert items[0].title == "Proposta comercial"
    assert items[0].metadata == {
        "from": "Cliente <c@example.com>",
        "thread_id": "t-1",
        "unread": True,
    }


def test_google_calendar_fetch_uses_31_day_100_event_window(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/calendars/primary/events")
        assert request.url.params["maxResults"] == "100"
        assert request.url.params["singleEvents"] == "true"
        assert request.url.params["orderBy"] == "startTime"
        assert request.url.params["timeMin"] == "2026-08-14T12:00:00+00:00"
        assert request.url.params["timeMax"] == "2026-09-14T12:00:00+00:00"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "event-1",
                        "summary": "Reunião de produto",
                        "description": "Definir próximos passos",
                        "start": {"dateTime": "2026-08-15T15:00:00-03:00"},
                        "end": {"dateTime": "2026-08-15T16:00:00-03:00"},
                        "location": "Meet",
                        "htmlLink": "https://calendar.google.com/event?eid=1",
                        "status": "confirmed",
                    }
                ]
            },
        )

    client = _client(monkeypatch, handler)
    items = client.fetch_items(
        "google_calendar",
        {"access_token": "calendar-access"},
        NOW,
    )

    assert len(items) == 1
    assert items[0].kind == "calendar"
    assert items[0].occurred_at == "2026-08-15T15:00:00-03:00"
    assert items[0].metadata["location"] == "Meet"


def test_outlook_fetches_25_mail_and_100_calendar_items(monkeypatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer graph-access"
        if request.url.path.endswith("/me/messages"):
            assert request.url.params["$top"] == "25"
            assert "bodyPreview" in request.url.params["$select"]
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "mail-1",
                            "subject": "Contrato",
                            "receivedDateTime": "2026-08-14T11:00:00Z",
                            "bodyPreview": "Favor revisar.",
                            "isRead": False,
                            "webLink": "https://outlook.office.com/mail/1",
                            "from": {
                                "emailAddress": {
                                    "name": "Jurídico",
                                    "address": "legal@example.com",
                                }
                            },
                        }
                    ]
                },
            )
        assert request.url.path.endswith("/me/calendarView")
        assert request.url.params["$top"] == "100"
        assert request.url.params["startDateTime"] == ("2026-08-14T12:00:00+00:00")
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "calendar-1",
                        "subject": "Conselho",
                        "start": {"dateTime": "2026-08-16T18:00:00Z"},
                        "end": {"dateTime": "2026-08-16T19:00:00Z"},
                        "location": {"displayName": "Sala 2"},
                        "bodyPreview": "Pauta mensal",
                        "webLink": "https://outlook.office.com/calendar/1",
                        "isAllDay": False,
                    }
                ]
            },
        )

    client = _client(monkeypatch, handler)
    items = client.fetch_items(
        "outlook",
        {"access_token": "graph-access"},
        NOW,
    )

    assert calls == ["/v1.0/me/messages", "/v1.0/me/calendarView"]
    assert [(item.kind, item.external_id) for item in items] == [
        ("mail", "mail-1"),
        ("calendar", "calendar-1"),
    ]


def test_strava_fetch_is_bounded_to_90_days_and_100_activities(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/athlete/activities"
        assert request.url.params["per_page"] == "100"
        assert request.url.params["page"] == "1"
        assert request.url.params["before"] == str(int(NOW.timestamp()))
        assert request.url.params["after"] == str(
            int((NOW.timestamp() - 90 * 24 * 60 * 60))
        )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 987,
                    "name": "Corrida leve",
                    "sport_type": "Run",
                    "start_date": "2026-08-13T10:00:00Z",
                    "distance": 5120.5,
                    "moving_time": 1800,
                    "elapsed_time": 1900,
                    "total_elevation_gain": 42.5,
                }
            ],
        )

    client = _client(monkeypatch, handler)
    items = client.fetch_items(
        "strava",
        {"access_token": "strava-access"},
        NOW,
    )

    assert len(items) == 1
    assert items[0].kind == "activity"
    assert items[0].external_id == "987"
    assert items[0].metadata["distance_m"] == 5120.5
