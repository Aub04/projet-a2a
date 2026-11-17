"""Serveur A2A générique (a2a-sdk 1.3.0) : chaque agent est une application Starlette avec sa carte d'agent
et un point d'entrée JSON-RPC. Les messages reçus et renvoyés sont des Message A2A contenant un DataPart JSON.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def make_card(name: str, description: str, url: str, skill_id: str = "negotiate_planning",
              skill_name: str = "Négocier le planning du groupe") -> AgentCard:
    return AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        supported_interfaces=[AgentInterface(url=url, protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(id=skill_id, name=skill_name, description=description,
                           tags=["planning", "negociation"], input_modes=["application/json"],
                           output_modes=["application/json"])],
    )


def _ints(x: Any) -> Any:
    """google.protobuf.Value stocke les nombres en double : 3 revient en 3.0. On rétablit les entiers."""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, dict):
        return {k: _ints(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_ints(v) for v in x]
    return x


def payload_of(message) -> dict[str, Any]:
    data = get_data_parts(message.parts) if message is not None else []
    return _ints(data[0]) if data else {}


class HandlerExecutor(AgentExecutor):
    """Passe le DataPart reçu au gestionnaire de l'agent et renvoie sa réponse dans un unique Message."""

    def __init__(self, handler: Handler):
        self.handler = handler

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        reply = await self.handler(payload_of(context.message))
        await event_queue.enqueue_event(new_data_message(reply, context_id=context.context_id))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:  # pragma: no cover
        raise NotImplementedError("annulation non prise en charge")


def build_app(card: AgentCard, handler: Handler) -> Starlette:
    request_handler = DefaultRequestHandler(
        agent_executor=HandlerExecutor(handler), task_store=InMemoryTaskStore(), agent_card=card)
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(request_handler, rpc_url="/")
    return Starlette(routes=routes)
