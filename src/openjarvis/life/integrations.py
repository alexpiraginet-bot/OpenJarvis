"""Hub de integrações do Life — catálogo curado e conexões por usuário.

Os conectores genéricos do OpenJarvis (``openjarvis.connectors``) são
single-operator: instâncias globais de módulo, tokens em
``~/.openjarvis/connectors/*.json`` e um callback OAuth que sobrescreve a
credencial de todo mundo. Nada disso pode encostar num servidor multi-cliente
— o usuário B "conectando" o Gmail passaria a ler o e-mail do usuário A. Este
módulo é a fundação oposta, no mesmo espírito de ``life.store``:

* **Catálogo curado e explícito.** ``PROVIDERS`` lista só o que o produto
  decidiu oferecer, com capacidades, escopos e pré-requisitos verdadeiros.
  A disponibilidade é derivada do ambiente (fail-closed): sem app OAuth
  configurado o provedor aparece como ``needs_setup``, nunca como conectável.
* **Estado em duas camadas.** O catálogo responde "o que existe aqui"
  (``available`` / ``needs_setup`` / ``device_only`` / ``coming_soon``) e o
  registro por usuário responde "como está a SUA conexão" (``connected`` /
  ``error`` / ``expired`` / ``revoked``, mais o pending derivado de uma
  autorização em voo). Estado desconhecido resolve para desconectado.
* **Nenhum token em claro no banco.** A conexão guarda apenas uma referência
  opaca produzida por um :class:`CredentialVault`; o padrão é
  :class:`NullCredentialVault`, que se recusa a guardar segredos — então uma
  instalação sem cofre configurado não consegue, nem por acidente, persistir
  um token.
* **OAuth com ``state`` single-use e PKCE.** O ``state`` volta por um callback
  não autenticado, então é ele que identifica o usuário; só o hash fica no
  banco (como em ``auth_tokens``) e a linha expira em minutos. "Conectado" só
  existe depois de :meth:`IntegrationsStore.complete_authorization` — iniciar
  o fluxo nunca muda o status.

A troca code→token por provedor, o HealthKit nativo e o motor de sync chegam
depois, por cima destas interfaces (``complete_authorization``,
``register_device_grant``, ``record_sync``, ``record_auth_error``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)
from urllib.parse import urlencode

from openjarvis.life import LifeContext
from openjarvis.life.db import Database

#: Uma autorização iniciada e abandonada não pode virar lixo permanente nem
#: janela de ataque: dez minutos cobrem qualquer consent screen real.
AUTH_REQUEST_TTL_SECONDS = 600

#: URL pública do deployment — necessária para montar o redirect_uri do OAuth.
_BASE_URL_ENV = "OPENJARVIS_LIFE_PUBLIC_BASE_URL"

#: A rota de callback (`/v1/life/integrations/{provider}/callback`) e a troca
#: code→token chegam na fase seguinte deste hub. Enquanto não existirem,
#: nenhum provedor OAuth pode se anunciar conectável — um "Conectar" que
#: termina em 404 depois do consentimento é o oposto de fail-closed. O gate
#: vira ``True`` no mesmo commit que publicar o callback; os testes o ligam
#: via monkeypatch para exercer o contrato completo de connect-intent.
OAUTH_CALLBACK_IMPLEMENTED = False

_CALLBACK_PENDING_PREREQUISITE = (
    "Callback OAuth do servidor ainda não publicado (próxima fase deste hub)"
)

logger = logging.getLogger(__name__)


class IntegrationsError(RuntimeError):
    """Raiz dos erros do hub — sempre uma recusa segura, nunca um estado falso."""


class UnknownProviderError(IntegrationsError):
    """Provedor fora do catálogo curado."""


class IntegrationUnavailableError(IntegrationsError):
    """O provedor existe mas não está conectável agora (e o motivo é dito)."""

    def __init__(
        self, message: str, *, reason: str, missing: Sequence[str] = ()
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.missing = tuple(missing)


class IntegrationAuthError(IntegrationsError):
    """``state`` desconhecido, expirado ou já consumido."""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Um provedor curado e a verdade sobre o que ele exige.

    ``env_vars`` é ordenada: o primeiro nome é sempre o client_id (é o único
    valor que aparece na URL de autorização; o segredo jamais sai do ambiente).
    ``capabilities`` e ``prerequisites`` são frases humanas em pt-BR — é o que
    o app mostra, então precisam ser verdadeiras, não aspiracionais.
    """

    id: str
    label: str
    category: str
    description: str
    capabilities: Tuple[str, ...]
    scopes: Tuple[str, ...]
    auth_kind: str  # 'oauth' | 'device' | 'none'
    pkce: bool
    stage: str  # 'available' | 'device_only' | 'coming_soon'
    authorize_endpoint: str = ""
    scope_separator: str = " "
    extra_authorize_params: Tuple[Tuple[str, str], ...] = ()
    env_vars: Tuple[str, ...] = ()
    prerequisites: Tuple[str, ...] = ()
    icon: str = "plug"
    tint: str = "blue"


_GOOGLE_ENV = (
    "OPENJARVIS_LIFE_GOOGLE_CLIENT_ID",
    "OPENJARVIS_LIFE_GOOGLE_CLIENT_SECRET",
)
_GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
#: ``access_type=offline`` + ``prompt=consent`` garantem refresh_token na
#: primeira troca — sem eles o Google só emite access token de uma hora.
_GOOGLE_EXTRA = (("access_type", "offline"), ("prompt", "consent"))

#: A ordem é a ordem de exibição no app.
PROVIDERS: Dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in (
        ProviderSpec(
            id="gmail",
            label="Gmail",
            category="Comunicação",
            description="E-mails importantes viram contexto e ações no seu dia.",
            capabilities=(
                "Ler e-mails (somente leitura)",
                "Detectar cobranças e contas recebidas",
                "Resumir mensagens importantes no briefing",
            ),
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
            auth_kind="oauth",
            pkce=True,
            stage="available",
            authorize_endpoint=_GOOGLE_AUTHORIZE,
            extra_authorize_params=_GOOGLE_EXTRA,
            env_vars=_GOOGLE_ENV,
            prerequisites=(
                "App OAuth no Google Cloud Console com o escopo gmail.readonly",
            ),
            icon="mail",
            tint="red",
        ),
        ProviderSpec(
            id="google_calendar",
            label="Google Agenda",
            category="Agenda",
            description="Compromissos reais alimentando o briefing e os lembretes.",
            capabilities=(
                "Ler eventos e compromissos (somente leitura)",
                "Alimentar o briefing de hoje com a agenda real",
            ),
            scopes=("https://www.googleapis.com/auth/calendar.readonly",),
            auth_kind="oauth",
            pkce=True,
            stage="available",
            authorize_endpoint=_GOOGLE_AUTHORIZE,
            extra_authorize_params=_GOOGLE_EXTRA,
            env_vars=_GOOGLE_ENV,
            prerequisites=(
                "App OAuth no Google Cloud Console com o escopo calendar.readonly",
            ),
            icon="calendar",
            tint="blue",
        ),
        ProviderSpec(
            id="apple_calendar",
            label="Calendário do iPhone",
            category="Agenda",
            description="Compromissos do aparelho no briefing e nas ações por voz.",
            capabilities=(
                "Ler os próximos compromissos no aparelho",
                "Criar eventos após confirmação explícita",
            ),
            scopes=(),
            auth_kind="device",
            pkce=False,
            stage="device_only",
            prerequisites=(
                "Acesso completo ao Calendário concedido no app iOS do Jarvis",
            ),
            icon="calendar",
            tint="cyan",
        ),
        ProviderSpec(
            id="outlook",
            label="Outlook / Microsoft 365",
            category="Comunicação",
            description="E-mail e agenda corporativos da conta Microsoft.",
            capabilities=(
                "Ler e-mails (somente leitura)",
                "Ler eventos do calendário corporativo",
            ),
            scopes=("offline_access", "User.Read", "Mail.Read", "Calendars.Read"),
            auth_kind="oauth",
            pkce=True,
            stage="available",
            authorize_endpoint=(
                "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
            ),
            extra_authorize_params=(("response_mode", "query"),),
            env_vars=(
                "OPENJARVIS_LIFE_MICROSOFT_CLIENT_ID",
                "OPENJARVIS_LIFE_MICROSOFT_CLIENT_SECRET",
            ),
            prerequisites=(
                "Registro de app no Microsoft Entra ID com Mail.Read e Calendars.Read",
            ),
            icon="briefcase",
            tint="indigo",
        ),
        ProviderSpec(
            id="strava",
            label="Strava",
            category="Treino",
            description="Atividades reais mantendo o coach a par dos seus treinos.",
            capabilities=(
                "Importar atividades e treinos",
                "Atualizar o volume semanal automaticamente",
            ),
            scopes=("read", "activity:read_all"),
            auth_kind="oauth",
            # O Strava não suporta PKCE; a proteção do fluxo fica no state
            # single-use + client_secret confinado ao servidor.
            pkce=False,
            stage="available",
            authorize_endpoint="https://www.strava.com/oauth/authorize",
            scope_separator=",",
            extra_authorize_params=(("approval_prompt", "auto"),),
            env_vars=(
                "OPENJARVIS_LIFE_STRAVA_CLIENT_ID",
                "OPENJARVIS_LIFE_STRAVA_CLIENT_SECRET",
            ),
            prerequisites=("App de API criado no painel de desenvolvedor Strava",),
            icon="activity",
            tint="orange",
        ),
        ProviderSpec(
            id="apple_health",
            label="Apple Health",
            category="Saúde",
            description="Passos, sono e frequência cardíaca direto do iPhone.",
            capabilities=(
                "Importar passos, sono e frequência cardíaca",
                "Alimentar as medidas do app Treino",
            ),
            scopes=(),
            # A autorização do HealthKit só existe no aparelho: o servidor
            # nunca vê OAuth nem token — recebe dados que o app iOS empurra
            # depois que o sistema concedeu a permissão.
            auth_kind="device",
            pkce=False,
            stage="device_only",
            prerequisites=("Permissão do HealthKit concedida no app iOS do Jarvis",),
            icon="heart-pulse",
            tint="rose",
        ),
        ProviderSpec(
            id="whatsapp",
            label="WhatsApp",
            category="Comunicação",
            description="Briefing diário e conversas com o Jarvis por mensagem.",
            capabilities=(
                "Receber o briefing diário no WhatsApp",
                "Conversar com o Jarvis por mensagem",
            ),
            scopes=(),
            auth_kind="none",
            pkce=False,
            stage="coming_soon",
            prerequisites=(
                "Conta WhatsApp Business (Cloud API da Meta) com número dedicado",
                "Webhook público configurado no app da Meta",
            ),
            icon="message-circle",
            tint="emerald",
        ),
        ProviderSpec(
            id="open_finance",
            label="Open Finance",
            category="Financeiro",
            description="Saldos e transações dos seus bancos, com consentimento"
            " revogável.",
            capabilities=(
                "Importar saldos e transações das instituições autorizadas",
                "Conciliar contas e faturas automaticamente",
            ),
            scopes=(),
            auth_kind="none",
            pkce=False,
            stage="coming_soon",
            prerequisites=(
                "Agregador Open Finance (ex.: Pluggy ou Belvo) com credenciais"
                " de produção",
                "Consentimento por instituição, com validade e revogação",
            ),
            icon="landmark",
            tint="cyan",
        ),
    )
}


class CredentialVault(Protocol):
    """Onde tokens vivem — nunca no banco do Life.

    ``store`` devolve uma referência opaca; ``discard`` a invalida. A
    implementação real (KMS, keyring, tabela cifrada à parte) chega junto com
    a troca code→token por provedor.
    """

    def store(self, user_id: str, provider: str, payload: Mapping[str, Any]) -> str: ...

    def discard(self, ref: str) -> None: ...


class NullCredentialVault:
    """O padrão fail-closed: sem cofre configurado, segredo não tem onde existir."""

    def store(self, user_id: str, provider: str, payload: Mapping[str, Any]) -> str:
        raise IntegrationsError(
            "Nenhum cofre de credenciais está configurado neste servidor;"
            " a conexão não foi criada."
        )

    def discard(self, ref: str) -> None:
        # Nada pôde ser guardado, logo não há o que descartar.
        return None


def _availability(spec: ProviderSpec) -> Tuple[str, Tuple[str, ...]]:
    """Camada 1 do estado: o que este deployment pode oferecer de verdade.

    "Disponível" exige a cadeia inteira: app configurado no ambiente E o
    callback capaz de concluir a troca. Faltando qualquer elo, o estado é
    ``needs_setup`` — nunca um convite para um fluxo que morre no meio.
    """
    if spec.stage == "coming_soon":
        return "coming_soon", ()
    if spec.stage == "device_only":
        return "device_only", ()
    required = (*spec.env_vars, _BASE_URL_ENV)
    missing = tuple(name for name in required if not os.environ.get(name))
    if missing or not OAUTH_CALLBACK_IMPLEMENTED:
        return "needs_setup", missing
    return "available", ()


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _validated_device_id(device_id: str) -> str:
    """Return one bounded opaque native-install identifier."""
    value = device_id.strip() if isinstance(device_id, str) else ""
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    )
    if not 8 <= len(value) <= 128 or any(char not in allowed for char in value):
        raise IntegrationsError("Identificador do aparelho inválido.")
    return value


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class IntegrationsStore:
    """Conexões de provedores por usuário, sobre a mesma base do Life.

    ``now`` é injetável apenas para os testes de expiração; produção usa o
    relógio real.
    """

    def __init__(
        self,
        life: LifeContext,
        *,
        vault: Optional[CredentialVault] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._db: Database = life.connection
        self._vault: CredentialVault = (
            vault if vault is not None else NullCredentialVault()
        )
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def connection(self) -> Database:
        """A base subjacente — os testes inspecionam o que foi (não) gravado."""
        return self._db

    # -- Catálogo + estado ----------------------------------------------------

    def overview(self, user_id: str) -> Dict[str, Any]:
        """Catálogo completo com o estado de conexão deste usuário."""
        self._purge_expired(user_id)
        pending = {
            str(row["provider"]): str(row["expires_at"])
            for row in self._db.execute(
                "SELECT provider, expires_at FROM integration_auth_requests"
                " WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }
        connections = {
            str(row["provider"]): dict(row)
            for row in self._db.execute(
                "SELECT * FROM integration_connections WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }
        whatsapp_link = self._db.execute(
            "SELECT status, verified_at, revoked_at, updated_at"
            " FROM channel_links WHERE user_id = ? AND channel = 'whatsapp'",
            (user_id,),
        ).fetchone()

        providers: List[Dict[str, Any]] = []
        connected = attention = 0
        for spec in PROVIDERS.values():
            availability, missing = _availability(spec)
            prerequisites = list(spec.prerequisites)
            if (
                spec.auth_kind == "oauth"
                and spec.stage == "available"
                and not OAUTH_CALLBACK_IMPLEMENTED
            ):
                # Derivado, não estático: quando o callback existir e o gate
                # virar, esta linha some do catálogo sozinha.
                prerequisites.append(_CALLBACK_PENDING_PREREQUISITE)
            row = connections.get(spec.id)
            public = _public_connection(row) if row else None
            if (
                spec.id == "whatsapp"
                and whatsapp_link is not None
                and str(whatsapp_link["status"]) == "verified"
            ):
                public = {
                    "status": "connected",
                    "account_label": "WhatsApp oficial",
                    "granted_scopes": ["messages"],
                    "connected_at": whatsapp_link["verified_at"],
                    "last_sync_at": None,
                    "last_sync_status": "",
                    "last_error": "",
                    "revoked_at": whatsapp_link["revoked_at"],
                    "updated_at": whatsapp_link["updated_at"],
                    "has_credential": False,
                }
            if public:
                if public["status"] == "connected":
                    connected += 1
                elif public["status"] in ("error", "expired"):
                    attention += 1
            providers.append(
                {
                    "id": spec.id,
                    "label": spec.label,
                    "category": spec.category,
                    "description": spec.description,
                    "capabilities": list(spec.capabilities),
                    "scopes": list(spec.scopes),
                    "auth": {"kind": spec.auth_kind, "pkce": spec.pkce},
                    "availability": availability,
                    "missing_config": list(missing),
                    "prerequisites": prerequisites,
                    "icon": spec.icon,
                    "tint": spec.tint,
                    "connection": public,
                    "pending_auth": (
                        {"expires_at": pending[spec.id]} if spec.id in pending else None
                    ),
                }
            )
        return {
            "providers": providers,
            "summary": {
                "connected": connected,
                "attention": attention,
                "pending": len(pending),
            },
        }

    def is_connected(self, user_id: str, provider_id: str) -> bool:
        """Return whether one tenant has a live connection to a known provider."""
        spec = _require_provider(provider_id)
        if spec.id == "whatsapp":
            channel_row = self._db.execute(
                "SELECT status FROM channel_links"
                " WHERE user_id = ? AND channel = 'whatsapp'",
                (user_id,),
            ).fetchone()
            return channel_row is not None and str(channel_row["status"]) == "verified"
        row = self._db.execute(
            "SELECT status FROM integration_connections"
            " WHERE user_id = ? AND provider = ?",
            (user_id, spec.id),
        ).fetchone()
        return row is not None and str(row["status"]) == "connected"

    def is_device_connected(
        self,
        user_id: str,
        provider_id: str,
        device_id: str,
        *,
        required_scopes: Sequence[str] = (),
    ) -> bool:
        """Check a live native grant for this exact app installation."""
        spec = _require_provider(provider_id)
        try:
            normalized_device_id = _validated_device_id(device_id)
        except IntegrationsError:
            return False
        row = self._db.execute(
            "SELECT status, granted_scopes FROM integration_device_grants"
            " WHERE user_id = ? AND provider = ? AND device_id = ?",
            (user_id, spec.id, normalized_device_id),
        ).fetchone()
        if row is None or str(row["status"]) != "connected":
            return False
        try:
            granted = set(json.loads(str(row["granted_scopes"])))
        except (TypeError, json.JSONDecodeError):
            return False
        return set(required_scopes).issubset(granted)

    # -- Connect-intent -------------------------------------------------------

    def begin_authorization(self, user_id: str, provider_id: str) -> Dict[str, Any]:
        """Cria a intenção de conectar e devolve a URL real de autorização.

        Nada aqui muda o status da conexão: "conectado" só existe depois que
        :meth:`complete_authorization` validar o ``state`` e guardar a
        credencial no cofre. Falha fechado — com o motivo — para provedores
        não configurados, device-only ou ainda não disponíveis.
        """
        spec = _require_provider(provider_id)
        availability, missing = _availability(spec)
        if availability == "coming_soon":
            raise IntegrationUnavailableError(
                f"{spec.label} ainda não está disponível nesta versão.",
                reason="coming_soon",
            )
        if availability == "device_only":
            raise IntegrationUnavailableError(
                f"{spec.label} é autorizado no próprio iPhone, pelo app iOS do Jarvis.",
                reason="device_only",
            )
        if availability == "needs_setup":
            if missing:
                message = (
                    f"{spec.label} requer configuração do servidor: defina"
                    f" {', '.join(missing)}."
                )
            else:
                message = (
                    f"{spec.label} ainda não pode concluir a autorização"
                    " neste servidor: o callback OAuth será publicado na"
                    " próxima fase."
                )
            raise IntegrationUnavailableError(
                message, reason="needs_setup", missing=missing
            )

        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64) if spec.pkce else ""
        base_url = os.environ[_BASE_URL_ENV].rstrip("/")
        redirect_uri = f"{base_url}/v1/life/integrations/{spec.id}/callback"
        now = self._now()
        expires_at = (now + timedelta(seconds=AUTH_REQUEST_TTL_SECONDS)).isoformat()

        params: List[Tuple[str, str]] = [
            ("client_id", os.environ[spec.env_vars[0]]),
            ("redirect_uri", redirect_uri),
            ("response_type", "code"),
            ("scope", spec.scope_separator.join(spec.scopes)),
            ("state", state),
            *spec.extra_authorize_params,
        ]
        if spec.pkce:
            params.append(("code_challenge", _pkce_challenge(verifier)))
            params.append(("code_challenge_method", "S256"))
        authorize_url = f"{spec.authorize_endpoint}?{urlencode(params)}"

        # Uma intenção viva por provedor: o upsert sobre UNIQUE (user_id,
        # provider) mata o state anterior no mesmo comando, inclusive entre
        # instâncias concorrentes — um link de autorização antigo não conclui.
        self._db.execute(
            "INSERT INTO integration_auth_requests (id, user_id, provider,"
            " state_hash, code_verifier, redirect_uri, scopes, created_at,"
            " expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id, provider) DO UPDATE SET"
            " id = excluded.id,"
            " state_hash = excluded.state_hash,"
            " code_verifier = excluded.code_verifier,"
            " redirect_uri = excluded.redirect_uri,"
            " scopes = excluded.scopes,"
            " created_at = excluded.created_at,"
            " expires_at = excluded.expires_at",
            (
                uuid.uuid4().hex,
                user_id,
                spec.id,
                _hash_state(state),
                verifier,
                redirect_uri,
                json.dumps(list(spec.scopes)),
                now.isoformat(),
                expires_at,
            ),
        )
        self._db.commit()
        return {
            "provider": spec.id,
            "authorize_url": authorize_url,
            "state": state,
            "expires_at": expires_at,
        }

    def complete_authorization(
        self,
        state: str,
        *,
        granted_scopes: Sequence[str],
        account_label: str = "",
        credential_payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Conclui uma autorização: é o único caminho que cria ``connected``.

        É o ``state`` que nomeia usuário e provedor — o callback que chamará
        isto não é autenticado. A credencial entra somente como *payload* e
        vai para o cofre; referência avulsa não é aceita, senão qualquer
        chamador driblaria o cofre com uma string. Sem cofre (padrão
        :class:`NullCredentialVault`) a conexão não nasce.

        O ``state`` é consumido *antes* do cofre, com guarda de ``rowcount``:
        duas conclusões concorrentes do mesmo ``state`` não podem ambas
        passar — a perdedora falha na deleção e nada grava. Se o cofre falhar
        depois do consumo, nenhuma conexão nasce e o fluxo recomeça do zero
        (fail-closed; o code OAuth é single-use de qualquer forma).
        """
        if credential_payload is None:
            raise IntegrationsError(
                "Uma credencial é obrigatória para concluir a conexão."
            )
        row = self._db.execute(
            "SELECT * FROM integration_auth_requests WHERE state_hash = ?",
            (_hash_state(state),),
        ).fetchone()
        if row is None:
            raise IntegrationAuthError(
                "Autorização desconhecida, expirada ou já utilizada."
            )
        now = self._now()
        try:
            expired = datetime.fromisoformat(str(row["expires_at"])) < now
        except ValueError:
            expired = True
        if expired:
            self._db.execute(
                "DELETE FROM integration_auth_requests WHERE id = ?",
                (row["id"],),
            )
            self._db.commit()
            raise IntegrationAuthError(
                "Autorização expirada; comece a conexão de novo."
            )

        user_id = str(row["user_id"])
        provider_id = str(row["provider"])
        with self._db.transaction():
            consumed = self._db.execute(
                "DELETE FROM integration_auth_requests WHERE id = ?",
                (row["id"],),
            ).rowcount
        if not consumed:
            raise IntegrationAuthError(
                "Autorização desconhecida, expirada ou já utilizada."
            )
        ref = self._vault.store(user_id, provider_id, credential_payload)
        if not ref:
            # Um cofre que "aceita" e devolve referência vazia criaria uma
            # conexão connected sem credencial nenhuma — estado mentiroso.
            raise IntegrationsError(
                "O cofre de credenciais não devolveu uma referência válida;"
                " a conexão não foi criada."
            )

        previous_ref = ""
        now_iso = now.isoformat()
        try:
            with self._db.transaction():
                existing = self._db.execute(
                    "SELECT credential_ref FROM integration_connections"
                    " WHERE user_id = ? AND provider = ?",
                    (user_id, provider_id),
                ).fetchone()
                if existing:
                    previous_ref = str(existing["credential_ref"] or "")
                self._write_connected(
                    user_id, provider_id, granted_scopes, account_label, ref, now_iso
                )
        except Exception:
            # Compensação: a gravação falhou, então a credencial recém-criada
            # não tem dono — descartá-la evita um segredo órfão no cofre.
            self._discard_best_effort(ref)
            raise
        if previous_ref and previous_ref != ref:
            self._discard_best_effort(previous_ref)
        return self._public_connection_for(user_id, provider_id)

    def _write_connected(
        self,
        user_id: str,
        provider_id: str,
        granted_scopes: Sequence[str],
        account_label: str,
        ref: str,
        now_iso: str,
    ) -> None:
        self._db.execute(
            "INSERT INTO integration_connections (id, user_id, provider,"
            " status, granted_scopes, account_label, credential_ref,"
            " connected_at, last_sync_at, last_sync_status, last_error,"
            " revoked_at, created_at, updated_at)"
            " VALUES (?, ?, ?, 'connected', ?, ?, ?, ?, NULL, '', '',"
            " NULL, ?, ?)"
            " ON CONFLICT(user_id, provider) DO UPDATE SET"
            " status = 'connected',"
            " granted_scopes = excluded.granted_scopes,"
            " account_label = excluded.account_label,"
            " credential_ref = excluded.credential_ref,"
            " connected_at = excluded.connected_at,"
            " last_sync_status = '',"
            " last_error = '',"
            " revoked_at = NULL,"
            " updated_at = excluded.updated_at",
            (
                uuid.uuid4().hex,
                user_id,
                provider_id,
                json.dumps(list(granted_scopes)),
                account_label,
                ref,
                now_iso,
                now_iso,
                now_iso,
            ),
        )

    def register_device_grant(
        self,
        user_id: str,
        provider_id: str,
        *,
        granted: Sequence[str],
        device_id: str,
        device_label: str = "",
    ) -> Dict[str, Any]:
        """Registra uma permissão concedida no aparelho (HealthKit).

        O servidor não recebe credencial nenhuma — a autoridade fica no
        dispositivo; aqui só se registra, com carimbo, o que foi concedido.
        """
        spec = _require_provider(provider_id)
        if spec.auth_kind != "device":
            raise IntegrationsError(f"{spec.label} não usa autorização de dispositivo.")
        normalized_device_id = _validated_device_id(device_id)
        normalized_scopes = tuple(
            dict.fromkeys(
                scope.strip()
                for scope in granted
                if isinstance(scope, str) and scope.strip()
            )
        )
        if not normalized_scopes:
            raise IntegrationsError("A concessão do aparelho não contém permissões.")
        normalized_label = device_label.strip()[:120]
        now_iso = self._now().isoformat()
        scopes_json = json.dumps(list(normalized_scopes))
        with self._db.transaction():
            self._db.execute(
                "INSERT INTO integration_device_grants"
                " (user_id, provider, device_id, device_label, status,"
                " granted_scopes, granted_at, updated_at, revoked_at)"
                " VALUES (?, ?, ?, ?, 'connected', ?, ?, ?, NULL)"
                " ON CONFLICT(user_id, provider, device_id) DO UPDATE SET"
                " device_label = excluded.device_label,"
                " status = 'connected',"
                " granted_scopes = excluded.granted_scopes,"
                " granted_at = excluded.granted_at,"
                " updated_at = excluded.updated_at,"
                " revoked_at = NULL",
                (
                    user_id,
                    spec.id,
                    normalized_device_id,
                    normalized_label,
                    scopes_json,
                    now_iso,
                    now_iso,
                ),
            )
            self._db.execute(
                "INSERT INTO integration_connections (id, user_id, provider,"
                " status, granted_scopes, account_label, credential_ref,"
                " connected_at, last_sync_at, last_sync_status, last_error,"
                " revoked_at, created_at, updated_at)"
                " VALUES (?, ?, ?, 'connected', ?, ?, '', ?, NULL, '', '', NULL,"
                " ?, ?)"
                " ON CONFLICT(user_id, provider) DO UPDATE SET"
                " status = 'connected',"
                " granted_scopes = excluded.granted_scopes,"
                " account_label = excluded.account_label,"
                " credential_ref = '',"
                " connected_at = excluded.connected_at,"
                " last_sync_status = '',"
                " last_error = '',"
                " revoked_at = NULL,"
                " updated_at = excluded.updated_at",
                (
                    uuid.uuid4().hex,
                    user_id,
                    spec.id,
                    scopes_json,
                    normalized_label,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
        return self._public_connection_for(user_id, spec.id)

    # -- Sincronização e erros ------------------------------------------------

    def record_sync(
        self,
        user_id: str,
        provider_id: str,
        *,
        ok: bool,
        error: str = "",
        synced_at: str = "",
    ) -> bool:
        """Carimba o resultado de uma sincronização real.

        Erro de sync não derruba o status: é transitório e resolve com retry,
        sem pedir nada ao usuário (diferente de erro de auth, abaixo).
        """
        spec = _require_provider(provider_id)
        now_iso = self._now().isoformat()
        if ok:
            cur = self._db.execute(
                "UPDATE integration_connections SET last_sync_at = ?,"
                " last_sync_status = 'ok', last_error = '', updated_at = ?"
                " WHERE user_id = ? AND provider = ?",
                (synced_at or now_iso, now_iso, user_id, spec.id),
            )
        else:
            cur = self._db.execute(
                "UPDATE integration_connections SET last_sync_status = 'error',"
                " last_error = ?, updated_at = ?"
                " WHERE user_id = ? AND provider = ?",
                (error, now_iso, user_id, spec.id),
            )
        self._db.commit()
        return cur.rowcount > 0

    def record_auth_error(
        self,
        user_id: str,
        provider_id: str,
        *,
        message: str,
        expired: bool = False,
    ) -> bool:
        """Marca a conexão como precisando do usuário (reconectar).

        ``expired=True`` é o ``invalid_grant`` clássico — refresh token morto
        ou consentimento vencido; o app deve oferecer "Reconectar", nunca
        continuar exibindo verde.
        """
        spec = _require_provider(provider_id)
        status = "expired" if expired else "error"
        cur = self._db.execute(
            "UPDATE integration_connections SET status = ?, last_error = ?,"
            " updated_at = ? WHERE user_id = ? AND provider = ?",
            (status, message, self._now().isoformat(), user_id, spec.id),
        )
        self._db.commit()
        return cur.rowcount > 0

    # -- Desconectar ----------------------------------------------------------

    def disconnect(self, user_id: str, provider_id: str) -> Optional[str]:
        """Cancela a autorização pendente e/ou revoga a conexão.

        Devolve ``"revoked"``, ``"canceled"`` ou ``None`` quando não havia
        nada deste usuário para desfazer. A referência de credencial é
        descartada no cofre e limpa da linha — a revogação junto ao provedor
        é responsabilidade do executor futuro, e o app diz isso ao usuário.
        """
        spec = _require_provider(provider_id)
        discarded_ref = ""
        result: Optional[str] = None
        with self._db.transaction():
            pending = self._db.execute(
                "DELETE FROM integration_auth_requests"
                " WHERE user_id = ? AND provider = ?",
                (user_id, spec.id),
            ).rowcount
            row = self._db.execute(
                "SELECT status, credential_ref FROM integration_connections"
                " WHERE user_id = ? AND provider = ?",
                (user_id, spec.id),
            ).fetchone()
            if row is not None and str(row["status"]) != "revoked":
                discarded_ref = str(row["credential_ref"] or "")
                now_iso = self._now().isoformat()
                self._db.execute(
                    "UPDATE integration_connections SET status = 'revoked',"
                    " credential_ref = '', revoked_at = ?, updated_at = ?"
                    " WHERE user_id = ? AND provider = ?",
                    (now_iso, now_iso, user_id, spec.id),
                )
                self._db.execute(
                    "UPDATE integration_device_grants SET status = 'revoked',"
                    " revoked_at = ?, updated_at = ?"
                    " WHERE user_id = ? AND provider = ? AND status = 'connected'",
                    (now_iso, now_iso, user_id, spec.id),
                )
                result = "revoked"
            elif pending:
                result = "canceled"
        if discarded_ref:
            self._discard_best_effort(discarded_ref)
        return result

    # -- Internos -------------------------------------------------------------

    def _discard_best_effort(self, ref: str) -> None:
        """Descarta uma referência sem derrubar a operação que já concluiu.

        A revogação no banco é o que protege o usuário; um cofre
        temporariamente fora do ar não pode transformá-la em erro 500. A
        referência órfã fica para limpeza posterior — e sem a linha no banco
        ela não dá acesso a nada.
        """
        try:
            self._vault.discard(ref)
        except Exception:  # noqa: BLE001 - descarte é manutenção, não contrato
            logger.warning("Falha ao descartar credencial no cofre", exc_info=True)

    def _purge_expired(self, user_id: str) -> None:
        """Remove autorizações vencidas — "aguardando" nunca vira estado eterno."""
        self._db.execute(
            "DELETE FROM integration_auth_requests"
            " WHERE user_id = ? AND expires_at < ?",
            (user_id, self._now().isoformat()),
        )
        self._db.commit()

    def _public_connection_for(self, user_id: str, provider_id: str) -> Dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM integration_connections WHERE user_id = ? AND provider = ?",
            (user_id, provider_id),
        ).fetchone()
        if row is None:  # pragma: no cover - só alcançável por corrida externa
            raise IntegrationsError("Conexão não encontrada após gravar.")
        return _public_connection(dict(row))


def _require_provider(provider_id: str) -> ProviderSpec:
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        raise UnknownProviderError(f"Provedor desconhecido: {provider_id!r}")
    return spec


def _public_connection(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Projeção pública de uma conexão: estado sim, credencial jamais."""
    try:
        granted = json.loads(str(row["granted_scopes"] or "[]"))
    except (TypeError, json.JSONDecodeError):
        granted = []
    return {
        "status": row["status"],
        "account_label": row["account_label"],
        "granted_scopes": granted,
        "connected_at": row["connected_at"],
        "last_sync_at": row["last_sync_at"],
        "last_sync_status": row["last_sync_status"],
        "last_error": row["last_error"],
        "revoked_at": row["revoked_at"],
        "updated_at": row["updated_at"],
        "has_credential": bool(row["credential_ref"]),
    }


__all__ = [
    "AUTH_REQUEST_TTL_SECONDS",
    "PROVIDERS",
    "CredentialVault",
    "IntegrationAuthError",
    "IntegrationUnavailableError",
    "IntegrationsError",
    "IntegrationsStore",
    "NullCredentialVault",
    "ProviderSpec",
    "UnknownProviderError",
]
