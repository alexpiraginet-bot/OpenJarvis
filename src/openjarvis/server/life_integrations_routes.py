"""Rotas do hub de integrações do Life (`/v1/life/integrations`).

Sub-router autocontido, montado por ``create_life_router`` dentro do prefixo
``/v1/life`` — a mesma autenticação por bearer de usuário, repetida aqui de
propósito para que este módulo não dependa do router principal.

Não confundir com ``openjarvis.server.connectors_router``: aquele é a
superfície single-operator (instâncias globais, tokens em ~/.openjarvis) e
não pode ser montado num app multi-cliente. Este fala apenas com
:class:`openjarvis.life.integrations.IntegrationsStore`, que é tenant-scoped
e fail-closed por construção.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from openjarvis.life import LifeContext
from openjarvis.life.integrations import (
    CredentialVault,
    IntegrationsStore,
    IntegrationUnavailableError,
    UnknownProviderError,
)
from openjarvis.life.tenancy import User

_bearer = HTTPBearer(auto_error=False)


def create_integrations_router(
    life: LifeContext, *, vault: Optional[CredentialVault] = None
) -> APIRouter:
    """Monta as rotas de integrações sobre um contexto Life já aberto.

    Sem ``vault`` o padrão é o ``NullCredentialVault`` fail-closed do store:
    este deployment consegue listar, iniciar e desfazer conexões, mas nenhuma
    credencial tem onde ser guardada até um cofre real ser configurado.
    """
    router = APIRouter(tags=["life-integrations"])
    integrations = IntegrationsStore(life, vault=vault)

    def current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    ) -> User:
        """Resolve o bearer do usuário, ou rejeita com 401."""
        if credentials is None or not credentials.credentials:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        user_id = life.users.resolve_token(credentials.credentials)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user = life.users.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Unknown user")
        return user

    @router.get("/integrations")
    async def catalog(user: User = Depends(current_user)) -> Dict[str, Any]:
        """Catálogo curado + estado das conexões deste usuário."""
        return integrations.overview(user.id)

    @router.post("/integrations/{provider}/connect")
    async def connect(
        provider: str, user: User = Depends(current_user)
    ) -> Dict[str, Any]:
        """Inicia a conexão: devolve a URL real de autorização, ou o motivo.

        Iniciar nunca conecta nada — o catálogo continua mostrando a conexão
        como inexistente até o callback (futuro) concluir a troca de código.
        """
        try:
            return integrations.begin_authorization(user.id, provider)
        except UnknownProviderError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except IntegrationUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.delete("/integrations/{provider}")
    async def disconnect(
        provider: str, user: User = Depends(current_user)
    ) -> Dict[str, Any]:
        """Cancela a autorização pendente e/ou revoga a conexão do usuário."""
        try:
            result = integrations.disconnect(user.id, provider)
        except UnknownProviderError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(
                status_code=404,
                detail="Nenhuma conexão ou autorização pendente deste provedor.",
            )
        return {"provider": provider, "result": result}

    # Exposto para testes e para o wiring futuro (callback OAuth, iOS bridge).
    router.integrations_store = integrations  # type: ignore[attr-defined]
    return router
