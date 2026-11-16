"""Single-user browser OAuth bridge for TradingView's official MCP beta.

This server MUST be private and protected with APP_PASSWORD. Never serve OAuth tokens to the browser.
The bridge is process-local. Use one worker and persistent STATE_DIR on production.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from urllib.parse import parse_qs
from typing import Callable, Awaitable

from tradingview_mcp import connect

@dataclass
class OAuthFlow:
    phase: str = 'starting'
    url: str | None = None
    error: str | None = None
    callback: asyncio.Future | None = None
    finished: bool = False
    task: asyncio.Task | None = None
    redirect_ready: asyncio.Event = field(default_factory=asyncio.Event)

    async def redirect_handler(self, url: str):
        if not url.startswith('https://'):
            raise RuntimeError('OAuth 인증 주소는 HTTPS이어야 합니다.')
        self.url = url
        self.phase = 'awaiting_consent'
        self.redirect_ready.set()

    async def callback_handler(self):
        if not self.callback:
            raise RuntimeError('OAuth callback handler missing')
        return await asyncio.wait_for(self.callback, timeout=240)

    async def worker(self, redirect_uri: str):
        try:
            async with connect(interactive=True, redirect_handler=self.redirect_handler,
                               callback_handler=self.callback_handler, redirect_uri=redirect_uri) as client:
                await client.list_tools()
            self.finished = True
            self.phase = 'connected'
        except asyncio.CancelledError:
            self.phase = 'cancelled'
            raise
        except Exception as exc:
            self.error = str(exc)[:400]
            self.phase = 'error'
        finally:
            self.redirect_ready.set()

_current: OAuthFlow | None = None

def get_flow() -> OAuthFlow | None:
    return _current

def start_flow(redirect_uri: str) -> OAuthFlow:
    global _current
    if _current and _current.task and not _current.task.done():
        return _current
    loop = asyncio.get_running_loop()
    flow = OAuthFlow(callback=loop.create_future())
    flow.task = loop.create_task(flow.worker(redirect_uri))
    _current = flow
    return flow


def complete_flow(code: str, state: str | None, iss: str | None):
    from mcp.client.auth import AuthorizationCodeResult
    flow = get_flow()
    if flow is None or flow.callback is None or flow.callback.done():
        raise ValueError('진행 중인 TradingView 인증 요청이 없습니다.')
    if not code or not state:
        raise ValueError('TradingView 인증 응답에 code/state가 없습니다.')
    flow.callback.set_result(AuthorizationCodeResult(code=code, state=state, iss=iss))
    flow.phase = 'verifying'
