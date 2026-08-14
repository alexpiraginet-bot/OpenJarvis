"""OAuth code exchange and refresh for Life's curated providers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

import httpx

from openjarvis.life.integrations import PROVIDERS, IntegrationsError

_GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_MICROSOFT_TOKEN_ENDPOINT = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_STRAVA_TOKEN_ENDPOINT = "https://www.strava.com/oauth/token"
_REQUEST_TIMEOUT_SECONDS = 15.0
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class OAuthProviderError(IntegrationsError):
    """A sanitized provider failure with no code, token or response body."""

    def __init__(self, provider: str, status_code: int, error_code: str) -> None:
        safe_code = error_code if _SAFE_ERROR_CODE.fullmatch(error_code) else "unknown"
        super().__init__(
            f"Falha OAuth em {provider} (HTTP {status_code}, {safe_code})."
        )
        self.provider = provider
        self.status_code = status_code
        self.error_code = safe_code


@dataclass(frozen=True, slots=True)
class OAuthRequestContext:
    provider: str
    redirect_uri: str
    code_verifier: str
    requested_scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OAuthCredential:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: str
    token_type: str
    granted_scopes: tuple[str, ...]
    account_label: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def to_vault_payload(self) -> dict[str, Any]:
        """Return the minimal long-lived payload stored in encrypted Vault."""
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "granted_scopes": list(self.granted_scopes),
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
        }


class OAuthProviderClient:
    """Exchange and refresh provider credentials through bounded HTTP calls."""

    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._http = http or httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def exchange(self, context: OAuthRequestContext, code: str) -> OAuthCredential:
        endpoint, client_id, client_secret = self._configuration(context.provider)
        form = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        }
        if context.provider != "strava":
            form["redirect_uri"] = context.redirect_uri
        if context.code_verifier:
            form["code_verifier"] = context.code_verifier
        if context.provider == "outlook":
            form["scope"] = " ".join(context.requested_scopes)
        payload = self._post(context.provider, endpoint, form)
        return self._credential(
            context.provider,
            payload,
            fallback_refresh_token="",
            fallback_scopes=context.requested_scopes,
        )

    def refresh(
        self,
        provider: str,
        credential_payload: Mapping[str, Any],
    ) -> OAuthCredential:
        endpoint, client_id, client_secret = self._configuration(provider)
        refresh_token = str(credential_payload.get("refresh_token") or "")
        if not refresh_token:
            raise OAuthProviderError(provider, 0, "missing_refresh_token")
        fallback_scopes = _sequence_of_strings(credential_payload.get("granted_scopes"))
        form = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if provider == "outlook" and fallback_scopes:
            form["scope"] = " ".join(fallback_scopes)
        payload = self._post(provider, endpoint, form)
        return self._credential(
            provider,
            payload,
            fallback_refresh_token=refresh_token,
            fallback_scopes=fallback_scopes,
        )

    def fetch_items(
        self,
        provider: str,
        credential_payload: Mapping[str, Any],
        now: datetime,
    ) -> list[Any]:
        """Fetch one bounded page and normalize it before anything is stored."""
        access_token = str(credential_payload.get("access_token") or "")
        if not access_token:
            raise OAuthProviderError(provider, 0, "missing_access_token")
        if provider == "gmail":
            return self._fetch_gmail(access_token)
        if provider == "google_calendar":
            return self._fetch_google_calendar(access_token, now)
        if provider == "outlook":
            return self._fetch_outlook(access_token, now)
        if provider == "strava":
            return self._fetch_strava(access_token, now)
        raise OAuthProviderError(provider, 0, "unsupported_provider")

    def _fetch_gmail(self, access_token: str) -> list[Any]:
        listing = self._get(
            "gmail",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            access_token,
            params={"maxResults": "25", "q": "newer_than:31d"},
        )
        messages = listing.get("messages") if isinstance(listing, dict) else None
        if not isinstance(messages, list):
            return []
        items = []
        for entry in messages[:25]:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            message_id = str(entry["id"])
            message = self._get(
                "gmail",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                f"{quote(message_id, safe='')}",
                access_token,
                params=[
                    ("format", "metadata"),
                    ("metadataHeaders", "From"),
                    ("metadataHeaders", "Subject"),
                    ("metadataHeaders", "Date"),
                ],
            )
            item = _gmail_item(message)
            if item is not None:
                items.append(item)
        return items

    def _fetch_google_calendar(
        self,
        access_token: str,
        now: datetime,
    ) -> list[Any]:
        payload = self._get(
            "google_calendar",
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            access_token,
            params={
                "maxResults": "100",
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": now.isoformat(),
                "timeMax": (now + timedelta(days=31)).isoformat(),
            },
        )
        events = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return []
        return [
            item
            for event in events[:100]
            if isinstance(event, dict)
            and (item := _google_calendar_item(event)) is not None
        ]

    def _fetch_outlook(self, access_token: str, now: datetime) -> list[Any]:
        mail_payload = self._get(
            "outlook",
            "https://graph.microsoft.com/v1.0/me/messages",
            access_token,
            params={
                "$top": "25",
                "$select": (
                    "id,subject,receivedDateTime,from,bodyPreview,isRead,webLink"
                ),
                "$orderby": "receivedDateTime desc",
            },
        )
        calendar_payload = self._get(
            "outlook",
            "https://graph.microsoft.com/v1.0/me/calendarView",
            access_token,
            params={
                "startDateTime": now.isoformat(),
                "endDateTime": (now + timedelta(days=31)).isoformat(),
                "$top": "100",
                "$select": (
                    "id,subject,start,end,location,bodyPreview,webLink,isAllDay"
                ),
            },
        )
        messages = mail_payload.get("value") if isinstance(mail_payload, dict) else None
        events = (
            calendar_payload.get("value")
            if isinstance(calendar_payload, dict)
            else None
        )
        items = []
        if isinstance(messages, list):
            items.extend(
                item
                for message in messages[:25]
                if isinstance(message, dict)
                and (item := _outlook_mail_item(message)) is not None
            )
        if isinstance(events, list):
            items.extend(
                item
                for event in events[:100]
                if isinstance(event, dict)
                and (item := _outlook_calendar_item(event)) is not None
            )
        return items

    def _fetch_strava(self, access_token: str, now: datetime) -> list[Any]:
        payload = self._get(
            "strava",
            "https://www.strava.com/api/v3/athlete/activities",
            access_token,
            params={
                "before": str(int(now.timestamp())),
                "after": str(int((now - timedelta(days=90)).timestamp())),
                "page": "1",
                "per_page": "100",
            },
        )
        if not isinstance(payload, list):
            return []
        return [
            item
            for activity in payload[:100]
            if isinstance(activity, dict)
            and (item := _strava_item(activity)) is not None
        ]

    @staticmethod
    def _configuration(provider: str) -> tuple[str, str, str]:
        spec = PROVIDERS.get(provider)
        if spec is None or spec.auth_kind != "oauth" or len(spec.env_vars) < 2:
            raise OAuthProviderError(provider, 0, "unsupported_provider")
        client_id = os.environ.get(spec.env_vars[0], "")
        client_secret = os.environ.get(spec.env_vars[1], "")
        if not client_id or not client_secret:
            raise OAuthProviderError(provider, 0, "provider_not_configured")
        if provider in {"gmail", "google_calendar"}:
            endpoint = _GOOGLE_TOKEN_ENDPOINT
        elif provider == "outlook":
            endpoint = _MICROSOFT_TOKEN_ENDPOINT
        elif provider == "strava":
            endpoint = _STRAVA_TOKEN_ENDPOINT
        else:  # defensive: curated OAuth providers must map explicitly
            raise OAuthProviderError(provider, 0, "unsupported_provider")
        return endpoint, client_id, client_secret

    def _post(
        self,
        provider: str,
        endpoint: str,
        form: Mapping[str, str],
    ) -> dict[str, Any]:
        try:
            response = self._http.post(
                endpoint,
                data=form,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise OAuthProviderError(provider, 0, "network_error") from exc
        if response.status_code < 200 or response.status_code >= 300:
            error_code = "unknown"
            try:
                error_payload = response.json()
                if isinstance(error_payload, dict):
                    error_code = str(error_payload.get("error") or "unknown")
            except (TypeError, ValueError):
                pass
            raise OAuthProviderError(provider, response.status_code, error_code)
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise OAuthProviderError(
                provider, response.status_code, "invalid_json"
            ) from exc
        if not isinstance(payload, dict):
            raise OAuthProviderError(provider, response.status_code, "invalid_response")
        return payload

    def _get(
        self,
        provider: str,
        endpoint: str,
        access_token: str,
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]],
    ) -> Any:
        try:
            response = self._http.get(
                endpoint,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise OAuthProviderError(provider, 0, "network_error") from exc
        if response.status_code < 200 or response.status_code >= 300:
            code = "unauthorized" if response.status_code == 401 else "api_error"
            raise OAuthProviderError(provider, response.status_code, code)
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise OAuthProviderError(
                provider, response.status_code, "invalid_json"
            ) from exc

    def _credential(
        self,
        provider: str,
        payload: Mapping[str, Any],
        *,
        fallback_refresh_token: str,
        fallback_scopes: Sequence[str],
    ) -> OAuthCredential:
        access_token = str(payload.get("access_token") or "")
        refresh_token = str(payload.get("refresh_token") or fallback_refresh_token)
        if not access_token or not refresh_token:
            raise OAuthProviderError(provider, 200, "missing_token")
        expires_at = _expires_at(provider, payload, self._now())
        scopes = _parse_scopes(payload.get("scope"), fallback_scopes)
        token_type = str(payload.get("token_type") or "Bearer")
        account_label = _account_label(provider, payload)
        raw = {
            key: payload[key]
            for key in ("expires_at", "expires_in", "scope", "token_type")
            if key in payload
        }
        return OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            token_type=token_type,
            granted_scopes=scopes,
            account_label=account_label,
            raw=raw,
        )


def _sequence_of_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _parse_scopes(value: Any, fallback: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        scopes = tuple(part for part in re.split(r"[\s,]+", value.strip()) if part)
        if scopes:
            return scopes
    return tuple(str(scope) for scope in fallback if str(scope))


def _expires_at(provider: str, payload: Mapping[str, Any], now: datetime) -> str:
    absolute = payload.get("expires_at")
    if isinstance(absolute, (int, float)) and absolute > 0:
        return datetime.fromtimestamp(absolute, tz=timezone.utc).isoformat()
    relative = payload.get("expires_in")
    if not isinstance(relative, (int, float)) or relative <= 0:
        raise OAuthProviderError(provider, 200, "missing_expiry")
    return (now + timedelta(seconds=float(relative))).isoformat()


def _account_label(provider: str, payload: Mapping[str, Any]) -> str:
    if provider != "strava" or not isinstance(payload.get("athlete"), dict):
        return ""
    athlete = payload["athlete"]
    label = " ".join(
        part
        for part in (
            str(athlete.get("firstname") or "").strip(),
            str(athlete.get("lastname") or "").strip(),
        )
        if part
    )
    return label[:160]


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _https_url(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate[:1000] if candidate.startswith("https://") else ""


def _millis_iso(value: Any) -> str:
    try:
        milliseconds = int(str(value))
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def _date_time(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    date_time = str(value.get("dateTime") or "").strip()
    if date_time:
        return date_time[:80]
    date_value = str(value.get("date") or "").strip()
    try:
        parsed = date.fromisoformat(date_value)
    except ValueError:
        return ""
    return datetime.combine(
        parsed, datetime.min.time(), tzinfo=timezone.utc
    ).isoformat()


def _gmail_item(payload: Any) -> Any:
    from openjarvis.life.integration_sync import IntegrationItem

    if not isinstance(payload, Mapping):
        return None
    external_id = _text(payload.get("id"), 256)
    occurred_at = _millis_iso(payload.get("internalDate"))
    if not external_id or not occurred_at:
        return None
    container = payload.get("payload")
    headers = container.get("headers") if isinstance(container, Mapping) else []
    header_map = {}
    if isinstance(headers, list):
        for header in headers:
            if not isinstance(header, Mapping):
                continue
            name = str(header.get("name") or "").casefold()
            if name in {"from", "subject"}:
                header_map[name] = _text(header.get("value"), 300)
    labels = payload.get("labelIds")
    labels = labels if isinstance(labels, list) else []
    return IntegrationItem(
        external_id=external_id,
        kind="mail",
        title=header_map.get("subject") or "E-mail sem assunto",
        summary=_text(payload.get("snippet"), 800),
        occurred_at=occurred_at,
        source_url=(
            f"https://mail.google.com/mail/u/0/#inbox/{quote(external_id, safe='')}"
        ),
        metadata={
            "from": header_map.get("from", ""),
            "thread_id": _text(payload.get("threadId"), 256),
            "unread": "UNREAD" in labels,
        },
    )


def _google_calendar_item(payload: Mapping[str, Any]) -> Any:
    from openjarvis.life.integration_sync import IntegrationItem

    if str(payload.get("status") or "") == "cancelled":
        return None
    external_id = _text(payload.get("id"), 256)
    occurred_at = _date_time(payload.get("start"))
    if not external_id or not occurred_at:
        return None
    start = payload.get("start")
    all_day = isinstance(start, Mapping) and bool(start.get("date"))
    return IntegrationItem(
        external_id=external_id,
        kind="calendar",
        title=_text(payload.get("summary"), 300) or "Compromisso",
        summary=_text(payload.get("description"), 800),
        occurred_at=occurred_at,
        source_url=_https_url(payload.get("htmlLink")),
        metadata={
            "all_day": all_day,
            "end": _date_time(payload.get("end")),
            "location": _text(payload.get("location"), 300),
        },
    )


def _outlook_mail_item(payload: Mapping[str, Any]) -> Any:
    from openjarvis.life.integration_sync import IntegrationItem

    external_id = _text(payload.get("id"), 256)
    occurred_at = _text(payload.get("receivedDateTime"), 80)
    if not external_id or not occurred_at:
        return None
    sender = payload.get("from")
    sender = sender.get("emailAddress") if isinstance(sender, Mapping) else None
    name = _text(sender.get("name"), 160) if isinstance(sender, Mapping) else ""
    address = _text(sender.get("address"), 200) if isinstance(sender, Mapping) else ""
    from_label = f"{name} <{address}>" if name and address else name or address
    return IntegrationItem(
        external_id=external_id,
        kind="mail",
        title=_text(payload.get("subject"), 300) or "E-mail sem assunto",
        summary=_text(payload.get("bodyPreview"), 800),
        occurred_at=occurred_at,
        source_url=_https_url(payload.get("webLink")),
        metadata={
            "from": from_label,
            "unread": not bool(payload.get("isRead", False)),
        },
    )


def _outlook_calendar_item(payload: Mapping[str, Any]) -> Any:
    from openjarvis.life.integration_sync import IntegrationItem

    external_id = _text(payload.get("id"), 256)
    occurred_at = _date_time(payload.get("start"))
    if not external_id or not occurred_at:
        return None
    location = payload.get("location")
    location_name = (
        _text(location.get("displayName"), 300) if isinstance(location, Mapping) else ""
    )
    return IntegrationItem(
        external_id=external_id,
        kind="calendar",
        title=_text(payload.get("subject"), 300) or "Compromisso",
        summary=_text(payload.get("bodyPreview"), 800),
        occurred_at=occurred_at,
        source_url=_https_url(payload.get("webLink")),
        metadata={
            "all_day": bool(payload.get("isAllDay", False)),
            "end": _date_time(payload.get("end")),
            "location": location_name,
        },
    )


def _number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


def _strava_item(payload: Mapping[str, Any]) -> Any:
    from openjarvis.life.integration_sync import IntegrationItem

    external_id = _text(payload.get("id"), 64)
    occurred_at = _text(payload.get("start_date"), 80)
    if not external_id or not occurred_at:
        return None
    distance_m = _number(payload.get("distance"))
    moving_time = _number(payload.get("moving_time"))
    sport = _text(payload.get("sport_type") or payload.get("type"), 80)
    summary = f"{sport or 'Atividade'} · {distance_m / 1000:.2f} km"
    return IntegrationItem(
        external_id=external_id,
        kind="activity",
        title=_text(payload.get("name"), 300) or sport or "Atividade",
        summary=summary,
        occurred_at=occurred_at,
        source_url=f"https://www.strava.com/activities/{external_id}",
        metadata={
            "distance_m": distance_m,
            "elapsed_sec": _number(payload.get("elapsed_time")),
            "elevation_gain_m": _number(payload.get("total_elevation_gain")),
            "moving_sec": moving_time,
            "sport": sport,
        },
    )
