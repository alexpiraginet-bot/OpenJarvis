"""Life-owned WhatsApp linking and Meta webhook routes.

This router is separate from the framework-wide channel bridge: every inbound
address first resolves to one Life tenant, every provider message is replay
protected, and no public response contains a phone, verification code or
server secret.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import time
from typing import Any, Callable, Dict, Optional, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse

from openjarvis.channels._stubs import ChannelMessage
from openjarvis.channels.whatsapp import (
    normalize_webhook_messages,
    verify_webhook_signature,
)
from openjarvis.life import LifeContext
from openjarvis.life.jarvis import (
    JarvisActionError,
    JarvisActionStore,
    action_requires_authenticated_app,
)
from openjarvis.life.tenancy import User
from openjarvis.life.whatsapp import (
    AddressInUseError,
    ChannelAddressVault,
    ChannelConfigurationError,
    LinkRateLimitedError,
    LinkVerificationError,
    NewsProvider,
    WhatsAppBriefingRunner,
    WhatsAppLifeError,
    WhatsAppLifeStore,
)
from openjarvis.life.whatsapp_worker import WhatsAppOutboxWorker

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)
_CODE_RE = re.compile(r"^\d{6}$")
_ACTION_RE = re.compile(r"^action:([0-9a-f]{32}):(confirm|cancel)$")
_COMMAND_PROMPTS = {
    "command:priorities": "Quais são minhas prioridades de hoje?",
    "command:fitness": "Como está meu treino de hoje?",
    "command:finance": "Como estão minhas finanças hoje?",
}
CRON_DISPATCH_BUDGET_SECONDS = 240.0
CRON_BRIEFING_BATCH_SIZE = 4
CRON_OUTBOX_BATCH_SIZE = 10


class WhatsAppSender(Protocol):
    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool: ...


class LinkRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=32)


class BriefingPreferenceRequest(BaseModel):
    enabled: bool
    time: str = Field(min_length=5, max_length=5)
    sections: Optional[list[str]] = None
    news_topics: Optional[list[str]] = None
    delivery_days: Optional[list[int]] = None
    custom_instructions: Optional[str] = Field(default=None, max_length=500)


def create_life_whatsapp_router(
    life: LifeContext,
    *,
    channel_address_vault: Optional[ChannelAddressVault],
    channel_pepper: bytes,
    whatsapp_channel: Optional[WhatsAppSender],
    whatsapp_verify_token: str,
    whatsapp_app_secret: str,
    cron_secret: str = "",
    news_provider: Optional[NewsProvider] = None,
    inbound_handler: Optional[Callable[[ChannelMessage], Any]] = None,
    turn_service: Any = None,
) -> APIRouter:
    """Build fail-closed tenant routes over one shared Life context."""
    router = APIRouter(tags=["life-whatsapp"])
    channels = WhatsAppLifeStore(
        life,
        pepper=channel_pepper,
        vault=channel_address_vault,
    )
    actions = JarvisActionStore(life)
    briefing_runner = WhatsAppBriefingRunner(
        life,
        channels,
        news_provider=news_provider,
    )
    outbox_worker = (
        WhatsAppOutboxWorker(channels, whatsapp_channel)  # type: ignore[arg-type]
        if whatsapp_channel is not None
        and callable(getattr(whatsapp_channel, "send_message", None))
        else None
    )

    def current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    ) -> User:
        if credentials is None or not credentials.credentials:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        user_id = life.users.resolve_token(credentials.credentials)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user = life.users.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Unknown user")
        return user

    def briefing_payload(preference: Any) -> Dict[str, Any]:
        return {
            "enabled": preference.enabled,
            "time": preference.local_time,
            "sections": list(preference.sections),
            "news_topics": list(preference.news_topics),
            "delivery_days": list(preference.delivery_days),
            "custom_instructions": preference.custom_instructions,
        }

    @router.post("/channels/whatsapp/link", status_code=202)
    async def begin_link(
        body: LinkRequest,
        user: User = Depends(current_user),
    ) -> Dict[str, str]:
        if whatsapp_channel is None:
            raise HTTPException(
                status_code=503,
                detail="O envio oficial do WhatsApp ainda não está configurado.",
            )
        try:
            channels.consume_link_code_attempt(user.id, body.phone)
            challenge = channels.begin_link(user.id, body.phone)
        except LinkRateLimitedError as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except AddressInUseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ChannelConfigurationError, WhatsAppLifeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        content = (
            f"{challenge.code} é seu código Jarvis. "
            "Responda somente com os seis dígitos para ativar."
        )
        try:
            sent = whatsapp_channel.send(body.phone, content)
        except Exception:  # noqa: BLE001 - provider errors stay server-side
            logger.exception("WhatsApp link-code delivery failed")
            sent = False
        if not sent:
            channels.revoke_link(user.id, challenge.id)
            raise HTTPException(
                status_code=502,
                detail="O WhatsApp não aceitou o código de ativação.",
            )
        return {
            "status": "pending",
            "expires_at": challenge.expires_at,
        }

    @router.get("/channels/whatsapp")
    async def channel_status(user: User = Depends(current_user)) -> Dict[str, str]:
        link = channels.get_link(user.id)
        if link is None:
            raise HTTPException(status_code=404, detail="Canal não vinculado.")
        return {
            "id": link.id,
            "channel": link.channel,
            "status": link.status,
            "verified_at": link.verified_at,
        }

    @router.delete("/channels/whatsapp")
    async def revoke_channel(user: User = Depends(current_user)) -> Dict[str, str]:
        link = channels.get_link(user.id)
        if link is None:
            raise HTTPException(status_code=404, detail="Canal não vinculado.")
        channels.revoke_link(user.id, link.id)
        return {"status": "revoked"}

    @router.get("/channels/whatsapp/briefing")
    async def briefing_preference(
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        preference = channels.get_briefing_preference(user.id)
        return {"briefing": briefing_payload(preference)}

    @router.put("/channels/whatsapp/briefing")
    async def update_briefing_preference(
        body: BriefingPreferenceRequest,
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        try:
            preference = channels.set_briefing_preference(
                user.id,
                enabled=body.enabled,
                local_time=body.time,
                sections=body.sections,
                news_topics=body.news_topics,
                delivery_days=body.delivery_days,
                custom_instructions=body.custom_instructions,
            )
        except (ChannelConfigurationError, WhatsAppLifeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"briefing": briefing_payload(preference)}

    @router.get("/internal/whatsapp/briefings")
    async def dispatch_briefings(request: Request) -> Dict[str, int]:
        if not cron_secret:
            raise HTTPException(status_code=503, detail="Cron não configurado.")
        authorization = request.headers.get("Authorization", "")
        if not hmac.compare_digest(authorization, f"Bearer {cron_secret}"):
            raise HTTPException(status_code=401, detail="Cron não autorizado.")
        if outbox_worker is None:
            raise HTTPException(
                status_code=503,
                detail="O envio estruturado do WhatsApp não está configurado.",
            )
        deadline = time.monotonic() + CRON_DISPATCH_BUDGET_SECONDS
        queued = await run_in_threadpool(
            briefing_runner.enqueue_due,
            limit=CRON_BRIEFING_BATCH_SIZE,
            deadline=deadline,
        )
        delivered = await run_in_threadpool(
            outbox_worker.run_once,
            CRON_OUTBOX_BATCH_SIZE,
            deadline=deadline,
        )
        return {
            **queued.to_dict(),
            "claimed": delivered.claimed,
            "sent": delivered.sent,
            "retried": delivered.retried,
            "failed": delivered.failed,
        }

    @router.get("/webhooks/whatsapp")
    async def verify_subscription(request: Request) -> Response:
        mode = request.query_params.get("hub.mode", "")
        token = request.query_params.get("hub.verify_token", "")
        challenge = request.query_params.get("hub.challenge", "")
        if (
            whatsapp_verify_token
            and mode == "subscribe"
            and hmac.compare_digest(token, whatsapp_verify_token)
        ):
            return PlainTextResponse(challenge)
        return Response("Forbidden", status_code=403)

    @router.post("/webhooks/whatsapp")
    async def receive_webhook(request: Request) -> Response:
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_webhook_signature(whatsapp_app_secret, body, signature):
            return Response("Invalid signature", status_code=403)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response("Invalid JSON", status_code=400)
        if not isinstance(payload, dict):
            return Response("Invalid payload", status_code=400)

        for message in normalize_webhook_messages(payload):
            if message.metadata.get("kind") == "delivery_status":
                channels.record_delivery_status(
                    message.message_id,
                    str(message.metadata.get("status", "")),
                )
                continue
            link = None
            try:
                link = channels.resolve_sender(
                    message.sender,
                    channel=message.channel,
                )
            except WhatsAppLifeError:
                logger.info("Rejected malformed WhatsApp sender address")
            if link is None:
                candidate = message.content.strip()
                if _CODE_RE.fullmatch(candidate):
                    try:
                        channels.verify_link(
                            message.sender,
                            candidate,
                            channel=message.channel,
                        )
                        channels.accept_inbound(message)
                    except (LinkVerificationError, WhatsAppLifeError):
                        logger.info("Rejected WhatsApp link verification")
                continue
            receipt = channels.accept_inbound(message)
            if receipt.duplicate:
                continue
            if inbound_handler is not None:
                inbound_handler(message)
            action_id = str(message.metadata.get("action_id", ""))
            action_match = _ACTION_RE.fullmatch(action_id)
            if action_match is not None:
                proposal_id, decision = action_match.groups()
                response_body = ""
                try:
                    proposal = actions.get(link.user_id, proposal_id)
                    if proposal is None:
                        response_body = (
                            "Essa ação não existe ou não pertence à sua conta."
                        )
                    elif decision == "cancel":
                        actions.cancel(link.user_id, proposal_id)
                        response_body = "Ação cancelada. Nada foi alterado."
                    elif proposal["tool_name"] == "calendar_create":
                        response_body = (
                            "Para gravar no Calendário do iPhone, abra o Jarvis e "
                            "conclua a confirmação no aparelho."
                        )
                    elif action_requires_authenticated_app(
                        proposal["tool_name"], proposal["arguments"]
                    ):
                        response_body = (
                            "Por segurança, ações financeiras precisam ser confirmadas "
                            "no app com Face ID."
                        )
                    else:
                        outcome = actions.confirm(
                            link.user_id,
                            proposal_id,
                            confirmation_method="explicit",
                        )
                        status = outcome["proposal"]["status"]
                        response_body = (
                            "Ação executada com segurança."
                            if status == "confirmed"
                            else "A ação não foi executada. Abra o Jarvis para revisar."
                        )
                    channels.enqueue_outbound(
                        link.user_id,
                        link.id,
                        idempotency_key=f"whatsapp-action:{message.message_id}",
                        payload={
                            "kind": "text",
                            "body": response_body,
                            "reply_to_message_id": message.message_id,
                        },
                    )
                    if outbox_worker is not None:
                        from starlette.concurrency import run_in_threadpool

                        await run_in_threadpool(outbox_worker.run_once, 10)
                except JarvisActionError as exc:
                    logger.info("WhatsApp action was not resolved: %s", exc)
                    channels.enqueue_outbound(
                        link.user_id,
                        link.id,
                        idempotency_key=f"whatsapp-action:{message.message_id}",
                        payload={
                            "kind": "text",
                            "body": "Essa ação expirou ou já foi resolvida.",
                            "reply_to_message_id": message.message_id,
                        },
                    )
                except Exception:  # noqa: BLE001 - provider can retry idempotently
                    channels.release_inbound(receipt.id, receipt.user_id)
                    logger.exception("WhatsApp action failed after receipt acceptance")
                    return PlainTextResponse("Retry later", status_code=503)
                continue
            command_question = _COMMAND_PROMPTS.get(action_id)
            if action_id and command_question is None:
                continue
            question = command_question or message.content
            if turn_service is None or not question.strip():
                continue
            user = life.users.get_user(link.user_id)
            if user is None:
                logger.error("Linked WhatsApp tenant no longer exists")
                continue
            try:
                result = await turn_service.run_turn(
                    user,
                    question=question,
                    conversation_id=f"whatsapp:{link.id}",
                    turn_id=message.message_id,
                    expected_revision=None,
                    engine=getattr(request.app.state, "engine", None),
                    model=str(
                        getattr(
                            getattr(request.app.state, "config", None),
                            "model",
                            "",
                        )
                    ),
                )
                answer = str(result.get("answer", "")).strip()
                proposals = result.get("proposals", [])
                outbound: Dict[str, Any] = {
                    "kind": "text",
                    "body": answer,
                    "reply_to_message_id": message.message_id,
                }
                if proposals and isinstance(proposals[0], dict):
                    proposal_id = str(proposals[0].get("id", ""))
                    if proposal_id:
                        outbound.update(
                            {
                                "kind": "reply_buttons",
                                "buttons": [
                                    [f"action:{proposal_id}:confirm", "Confirmar"],
                                    [f"action:{proposal_id}:cancel", "Cancelar"],
                                ],
                            }
                        )
                channels.enqueue_outbound(
                    user.id,
                    link.id,
                    idempotency_key=f"whatsapp-reply:{message.message_id}",
                    payload=outbound,
                )
                if outbox_worker is not None:
                    from starlette.concurrency import run_in_threadpool

                    await run_in_threadpool(outbox_worker.run_once, 10)
            except Exception:  # noqa: BLE001 - provider retry is idempotent
                channels.release_inbound(receipt.id, receipt.user_id)
                logger.exception("WhatsApp Life turn failed after receipt acceptance")
                return PlainTextResponse("Retry later", status_code=503)
        return PlainTextResponse("OK")

    router.whatsapp_store = channels  # type: ignore[attr-defined]
    return router


__all__ = ["LinkRequest", "WhatsAppSender", "create_life_whatsapp_router"]
