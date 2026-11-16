"""Official TradingView MCP OAuth client. No scraping or unofficial data endpoints."""
from __future__ import annotations
from contextlib import asynccontextmanager
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SERVER = 'https://mcp.tradingview.com/mcp'
ROOT = Path(__file__).resolve().parent
TOKEN_PATH = Path(os.environ.get('STATE_DIR', str(ROOT / '.state'))) / 'tradingview_oauth.json'

def has_saved_tokens() -> bool:
    try:
        data = json.loads(TOKEN_PATH.read_text(encoding='utf-8'))
        tokens = data.get('tokens') or {}
        return isinstance(tokens.get('access_token'), str) and bool(tokens['access_token'])
    except (OSError, ValueError, TypeError, AttributeError):
        return False

class AuthNeeded(RuntimeError): pass
class RemoteDataError(RuntimeError): pass


class FileTokenStorage:
    def __init__(self, path: Path = TOKEN_PATH): self.path = path
    def _read(self):
        if not self.path.exists(): return {}
        return json.loads(self.path.read_text(encoding='utf-8'))
    def _write(self, obj):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)
    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        data = self._read().get('tokens')
        return OAuthToken.model_validate(data) if data else None
    async def set_tokens(self, tokens):
        data = self._read(); data['tokens'] = tokens.model_dump(mode='json'); self._write(data)
    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        data = self._read().get('client_info')
        return OAuthClientInformationFull.model_validate(data) if data else None
    async def set_client_info(self, client_info):
        data = self._read(); data['client_info'] = client_info.model_dump(mode='json'); self._write(data)

async def show_auth_url(url):
    import webbrowser
    print('\n[TradingView 인증] 다음 URL을 브라우저에서 열어 로그인하고 권한을 허용하세요:\n' + url)
    webbrowser.open(url)

async def read_callback():
    from mcp.client.auth import AuthorizationCodeResult
    url = await asyncio.to_thread(input, '\n인증 후 이동한 전체 URL을 복사해 붙여넣으세요: ')
    q = parse_qs(urlparse(url.strip()).query)
    if not q.get('code') or not q.get('state'): raise AuthNeeded('인증 완료 URL에서 code/state를 찾지 못했습니다.')
    return AuthorizationCodeResult(code=q['code'][0], state=q['state'][0], iss=q.get('iss', [None])[0])

async def unavailable_auth_url(url):
    raise AuthNeeded('TradingView 인증 필요: 터미널에서 python auth_tv.py 를 실행해 로그인하세요.')

async def unavailable_callback():
    raise AuthNeeded('TradingView 인증이 필요합니다.')


@asynccontextmanager
async def connect(interactive: bool = False, redirect_handler=None, callback_handler=None, redirect_uri: str = "http://localhost:8765/callback"):
    try:
        import httpx2
        from pydantic import AnyUrl
        from mcp import Client
        from mcp.client.auth import OAuthClientProvider
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared.auth import OAuthClientMetadata
    except ImportError as exc:
        raise AuthNeeded('MCP SDK 미설치: pip install -r requirements.txt') from exc
    if not interactive and not has_saved_tokens():
        raise AuthNeeded('연동 대기: 터미널에서 python auth_tv.py 를 실행하세요.')
    provider = OAuthClientProvider(
        server_url=SERVER,
        client_metadata=OAuthClientMetadata(
            client_name='Trading Pulse Personal Dashboard',
            redirect_uris=[AnyUrl(redirect_uri)],
            grant_types=['authorization_code', 'refresh_token'],
            response_types=['code'],
            scope='user',
        ),
        storage=FileTokenStorage(),
        redirect_handler=(redirect_handler or show_auth_url) if interactive else unavailable_auth_url,
        callback_handler=(callback_handler or read_callback) if interactive else unavailable_callback,
    )
    async with httpx2.AsyncClient(auth=provider, timeout=httpx2.Timeout(30, read=100)) as http_client:
        transport = streamable_http_client(SERVER, http_client=http_client)
        async with Client(transport) as client:
            yield client


def _parse_result(result):
    if getattr(result, 'is_error', False):
        texts = [getattr(item, 'text', '') for item in result.content]
        raise RemoteDataError('TradingView 응답 오류: ' + ' '.join(texts)[:500])
    obj = getattr(result, 'structured_content', None)
    if not obj:
        for item in getattr(result, 'content', []):
            content = getattr(item, 'text', '')
            if content:
                try: obj = json.loads(content)
                except (ValueError, TypeError): obj = {'text': content}
                break
    for _ in range(3):
        if isinstance(obj, dict) and len(obj) <= 3 and ('result' in obj or 'data' in obj):
            obj = obj.get('result', obj.get('data'))
            if isinstance(obj, str):
                try: obj = json.loads(obj)
                except ValueError: break
        else: break
    if obj is None: raise RemoteDataError('TradingView 응답이 비어 있습니다.')
    return obj

async def call(client, name: str, args: dict):
    return _parse_result(await client.call_tool(name, args))
