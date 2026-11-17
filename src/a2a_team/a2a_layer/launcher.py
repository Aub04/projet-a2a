"""Démarre des serveurs A2A (uvicorn) dans la boucle asyncio courante et attend qu'ils publient leur carte."""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import uvicorn
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from starlette.applications import Starlette

HOST = "127.0.0.1"
ORCHESTRATOR_PORT = 9100
MEMBER_BASE_PORT = 9101


def url_for(port: int) -> str:
    return f"http://{HOST}:{port}"


class ServerGroup:
    def __init__(self):
        self.servers: list[uvicorn.Server] = []
        self.tasks: list[asyncio.Task] = []

    async def start(self, apps: dict[int, Starlette], timeout: float = 20) -> None:
        for port, app in apps.items():
            server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning", lifespan="off"))
            server.install_signal_handlers = lambda: None   # type: ignore[method-assign]
            self.servers.append(server)
            self.tasks.append(asyncio.create_task(server.serve()))
        async with httpx.AsyncClient() as http:
            for port in apps:
                deadline = asyncio.get_running_loop().time() + timeout
                while True:
                    with contextlib.suppress(httpx.HTTPError):
                        r = await http.get(url_for(port) + AGENT_CARD_WELL_KNOWN_PATH)
                        if r.status_code == 200:
                            break
                    if asyncio.get_running_loop().time() > deadline:
                        raise TimeoutError(f"le serveur du port {port} ne répond pas")
                    await asyncio.sleep(0.1)

    async def stop(self) -> None:
        for s in self.servers:
            s.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.gather(*self.tasks, return_exceptions=True), 10)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.stop()
