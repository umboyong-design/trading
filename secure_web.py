"""Browser access controls for a personal, cloud-hosted Trading Pulse dashboard."""
from __future__ import annotations
import os
import secrets
from html import escape
from urllib.parse import urlparse
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

BASE_URL = os.getenv('APP_BASE_URL', '').strip().rstrip('/')
PASSWORD = os.getenv('APP_PASSWORD', '')
SESSION_SECRET = os.getenv('APP_SESSION_SECRET') or secrets.token_urlsafe(48)

def install(app):
    @app.middleware('http')
    async def gate(request: Request, call_next):
        path = request.url.path
        if path == '/login' or path == '/api/health' or path.startswith('/static/'):
            return await call_next(request)
        # A server without an administrator password MUST NOT expose financial data.
        if not PASSWORD:
            if path.startswith('/api/') or path.startswith('/auth/'):
                return JSONResponse({'detail':'APP_PASSWORD 서버 환경변수가 설정되지 않았습니다.'}, status_code=503)
            return RedirectResponse('/login', status_code=303)
        if not request.session.get('signed_in'):
            if path.startswith('/api/') or path.startswith('/auth/'):
                return JSONResponse({'detail':'세션이 만료되었습니다. /login 에서 로그인하세요.'}, status_code=401)
            return RedirectResponse('/login', status_code=303)
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('origin')
            if origin and urlparse(origin).netloc.lower() != request.headers.get('host','').lower():
                return JSONResponse({'detail':'다른 사이트에서 보낸 요청은 허용하지 않습니다.'}, status_code=403)
        return await call_next(request)

    # Session middleware must wrap the gate, to populate request.session.
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET,
                       https_only=BASE_URL.startswith('https://'), same_site='lax', max_age=86400)

def login_page(error: str = ''):
    msg = escape(error) if error else ''
    disabled = '' if PASSWORD else 'disabled'
    note = '개인 전용 트레이딩 분석 웹앱입니다.' if PASSWORD else '서버 관리자 설정이 필요합니다. 배포 환경변수 APP_PASSWORD를 지정하세요.'
    return HTMLResponse('''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Trading Pulse · 로그인</title><style>
    *{box-sizing:border-box}body{margin:0;min-height:100vh;background:#070e1b;color:#edf5ff;font-family:system-ui,Apple SD Gothic Neo,sans-serif;display:grid;place-items:center}main{max-width:400px;width:calc(100% - 38px);padding:34px;border-radius:20px;background:#111d30;border:1px solid #29394b;box-shadow:0 20px 60px #0004}h1{letter-spacing:1px;margin:0;font-size:28px}h1 b{color:#5ce5c4}p{color:#9bb2c5;line-height:1.7}label{display:block;margin:28px 0 8px;color:#b4c9d8;font-size:13px}input{width:100%;padding:13px;border-radius:10px;color:#fff;background:#091425;border:1px solid #40546a;font-size:16px}button{border:0;width:100%;margin-top:15px;padding:14px;border-radius:10px;background:#56e4c4;color:#0b1622;font-size:15px;font-weight:800;cursor:pointer}button:disabled{opacity:.5}.err{color:#ffa5a5}</style></head><body>
    <main><h1>TRADING <b>PULSE</b></h1><p>'''+escape(note)+'''</p><form method="post" action="/login"><label for="password">관리자 비밀번호</label><input id="password" type="password" name="password" autocomplete="current-password" required><button '''+disabled+'''>웹앱 열기</button></form><p class="err">'''+msg+'''</p><p style="font-size:12px">TradingView 공식 MCP 연결 전에는 실제 추천·매매가격을 표시하지 않습니다.</p></main></body></html>''',status_code=200)
