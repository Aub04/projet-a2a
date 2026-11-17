"""Transports utilisés par les agents pour s'envoyer des messages.

- `HttpTransport` : vrai client A2A (JSON-RPC sur HTTP) vers les serveurs des autres agents.
- `InMemoryTransport` : appel direct du gestionnaire, mais avec exactement les mêmes objets Message A2A
  (construction et relecture du DataPart). Utilisé par les tests et, si besoin, pour accélérer les campagnes.

Chaque envoi (requête et réponse) est écrit dans la trace.
"""

from __future__ import annotations

from typing import Any

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers.proto_helpers import new_data_message
from a2a.types import Role, SendMessageRequest

from a2a_team.a2a_layer.server import Handler, payload_of
from a2a_team.a2a_layer.trace import Tracer


def envelope(type_: str, sender: str, recipient: str, content: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": type_, "sender": sender, "recipient": recipient, "content": content or {}}


class Transport:
    def __init__(self, tracer: Tracer):
        self.tracer = tracer
        self.phase = "negociation"

    async def _deliver(self, recipient: str, payload: dict) -> dict:
        raise NotImplementedError

    async def send(self, sender: str, recipient: str, type_: str, content: dict | None = None) -> dict:
        payload = envelope(type_, sender, recipient, content)
        self.tracer.message(sender, recipient, payload, self.phase)
        reply = await self._deliver(recipient, payload)
        self.tracer.message(reply.get("sender", recipient), sender, reply, self.phase)
        return reply

    async def close(self) -> None:
        pass


class InMemoryTransport(Transport):
    def __init__(self, tracer: Tracer, handlers: dict[str, Handler] | None = None):
        super().__init__(tracer)
        self.handlers: dict[str, Handler] = handlers or {}

    async def _deliver(self, recipient: str, payload: dict) -> dict:
        msg = new_data_message(payload, role=Role.ROLE_USER)          # même objet que sur le réseau
        reply = await self.handlers[recipient](payload_of(msg))
        return payload_of(new_data_message(reply))


class HttpTransport(Transport):
    def __init__(self, tracer: Tracer, urls: dict[str, str], timeout: float | None = None):
        super().__init__(tracer)
        self.urls = urls
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self._clients: dict[str, Any] = {}

    async def _client(self, recipient: str):
        if recipient not in self._clients:
            self._clients[recipient] = await create_client(
                self.urls[recipient], client_config=ClientConfig(streaming=False, httpx_client=self.http))
        return self._clients[recipient]

    async def _deliver(self, recipient: str, payload: dict) -> dict:
        client = await self._client(recipient)
        request = SendMessageRequest(message=new_data_message(payload, role=Role.ROLE_USER))
        async for resp in client.send_message(request):
            if resp.HasField("message"):
                return payload_of(resp.message)
        raise RuntimeError(f"pas de message en réponse de {recipient}")

    async def close(self) -> None:
        await self.http.aclose()
