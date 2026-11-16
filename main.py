"""Trading Pulse: single-user local research dashboard, NOT an order execution service.

Official TradingView MCP beta is the ONLY market-data source. Never substitutes fake quotes.
"""
from __future__ import annotations
import asyncio
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Form
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from market_clock import market_status
from signals import clean_bars, analyze_all
from storage import get_scan, save_scan, add_watchlist, get_watchlist, remove_watchlist
from tradingview_mcp import AuthNeeded, RemoteDataError, TOKEN_PATH, has_saved_tokens, connect, call
from web_oauth import start_flow, get_flow, complete_flow
from secure_web import install as install_security, login_page, BASE_URL, PASSWORD

ROOT = Path(__file__).resolve().parent
app = FastAPI(title='Trading Pulse', description='TradingView MCP 기반 국내·미국 주식 단기 매매 보조', version='0.1.0')
app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
install_security(app)
_market_locks = {'KR': asyncio.Lock(), 'US': asyncio.Lock()}
_last_scan_time = {'KR': 0.0, 'US': 0.0}
MARKET_MAP = {'KR': 'korea', 'US': 'america'}
STARTERS = {
    'US': ['NASDAQ:NVDA','NASDAQ:MSFT','NASDAQ:AAPL','NASDAQ:AMD','NASDAQ:AMZN','NASDAQ:GOOGL','NASDAQ:TSLA','NASDAQ:META','NASDAQ:PLTR','NASDAQ:AVGO'],
    'KR': ['KRX:005930','KRX:000660','KRX:035420','KRX:005380','KRX:051910','KRX:068270','KRX:207940','KRX:035720','KRX:042700','KRX:012450'],
}

class SymbolReq(BaseModel):
    market: str = Field(pattern='^(KR|US)$')
    symbol: str = Field(min_length=3, max_length=40, pattern=r'^[A-Za-z0-9._:-]+$')

class ScanReq(BaseModel):
    market: str = Field(pattern='^(KR|US)$')
    force: bool = False


def _safe_sym(symbol: Any, market: str) -> str | None:
    s = str(symbol).strip().upper()
    if not re.fullmatch(r'[A-Z0-9._-]+:[A-Z0-9._-]+',s): return None
    # Limit symbols to the target exchange family; protect users against wrong-market rank.
    exchange = s.split(':',1)[0]
    if market == 'US' and exchange not in ('NASDAQ','NYSE','AMEX','NYSEARCA','BATS','CBOE','NYSEMKT'): return None
    if market == 'KR' and exchange not in ('KRX','KOSPI','KOSDAQ'): return None
    return s


def _unwrap_rows(raw: Any) -> list[dict]:
    if isinstance(raw,list): return [x for x in raw if isinstance(x,dict)]
    if not isinstance(raw,dict): return []
    for key in ('rows','symbols','data','results','items'):
        if isinstance(raw.get(key),list): return [x for x in raw[key] if isinstance(x,dict)]
        if isinstance(raw.get(key),dict):
            found = _unwrap_rows(raw[key])
            if found: return found
    return []


def _rows_to_candidates(raw: Any, market: str):
    arr = _unwrap_rows(raw)
    result = []
    columns = ['name','description','close','change','volume','market_cap_basic']
    # Some MCP servers return scanner rows as {s:'NASDAQ:AAPL', d:[...]}.
    if isinstance(raw,dict) and isinstance(raw.get('columns'), list): columns = raw['columns']
    for row in arr:
        data = row.get('data') or row.get('d') or row.get('columns') or {}
        if isinstance(data,list):
            data = dict(zip(columns,data))
        if not isinstance(data,dict): data = {}
        symbol = _safe_sym(row.get('s') or row.get('symbol') or row.get('ticker') or data.get('symbol') or data.get('name'), market)
        if not symbol: continue
        def numeric(key):
            val = data.get(key, row.get(key))
            try: return float(val) if val is not None else None
            except (ValueError,TypeError): return None
        result.append({'symbol':symbol, 'name':str(data.get('description') or row.get('description') or symbol.split(':')[1]),
                       'price':numeric('close'), 'change':numeric('change'), 'volume':numeric('volume'),
                       'source':'TradingView MCP screener'})
    return result


def _unwrap_bars(raw: Any) -> list[dict]:
    if isinstance(raw,list): return [x for x in raw if isinstance(x,dict)]
    if not isinstance(raw,dict): return []
    for key in ('bars','ohlcv','candles','data','result','items'):
        if isinstance(raw.get(key),list): return [x for x in raw[key] if isinstance(x,dict)]
        if isinstance(raw.get(key),dict):
            inner = _unwrap_bars(raw[key])
            if inner: return inner
    return []


async def _fetch_symbol(client, candidate: dict, market: str) -> dict:
    symbol = candidate['symbol']
    try:
        raw1 = await call(client, 'get_ohlcv', {'symbol':symbol,'interval':'1m','count':480,'summary':False})
        raw30 = await call(client, 'get_ohlcv', {'symbol':symbol,'interval':'30m','count':80,'summary':False})
        bars1 = clean_bars(_unwrap_bars(raw1), 1)
        bars30 = clean_bars(_unwrap_bars(raw30), 30)
        result = analyze_all(bars1, bars30, market)
        result['symbol'] = symbol
        result['name'] = candidate.get('name', symbol.split(':',1)[1])
        result['market'] = market
        result['screener_change_pct'] = candidate.get('change')
        result['screener_volume'] = candidate.get('volume')
        result['source'] = 'TradingView 공식 MCP'
        result['quote_is_1m_close'] = True
        return result
    except Exception as exc:
        return {'symbol':symbol, 'name':candidate.get('name',symbol), 'market':market,
                'rank_score':None,'signal':'데이터 오류','frames':{},'error':str(exc)[:240], 'source':'TradingView 공식 MCP'}


def _decorate(rows: list[dict], market: str, clock: dict):
    now = datetime.now(timezone.utc).timestamp()
    for x in rows:
        ts = x.get('bar_time')
        delay_s = round(now-ts-60) if ts else None
        x['data_age_sec'] = max(0,delay_s) if delay_s is not None else None
        x['fresh'] = bool(clock.get('state') == 'regular' and delay_s is not None and delay_s <= 150)
        x['quote_state'] = '최근 확정 1분봉' if x['fresh'] else ('지난 분봉 / 지연 가능' if ts else '데이터 없음')
        # Explicitly prevent stale/after-hours bars from appearing as a live buy alert.
        x['live_signal'] = x['signal'] if x['fresh'] else '실시간 신호 아님'
    return rows


async def _screen_candidates(client, market: str):
    warnings = []
    merged = {}
    for preset in ('breakout_with_volume','relative_strength'):
        args = {'market':MARKET_MAP[market], 'filter_preset':preset,
                'sort_by':'volume','sort_order':'desc','limit':35,
                'columns':['name','description','close','change','volume','market_cap_basic'],
                'symbol_types':['stock']}
        try:
            resp = await call(client,'run_screener',args)
            found = _rows_to_candidates(resp, market)
            for candidate in found: merged[candidate['symbol']] = candidate
            if not found: warnings.append(preset + ': 스크리너 결과를 해석할 수 없어 사용자 관심종목을 사용합니다.')
        except Exception as exc:
            warnings.append(preset + ': ' + str(exc)[:180])
    # User-managed universe supplements screener. Starter symbols are examples, not recommendations.
    manual = get_watchlist(market)
    symbols = manual or STARTERS[market]
    for symbol in symbols:
        safe = _safe_sym(symbol,market)
        if safe and safe not in merged:
            merged[safe] = {'symbol':safe, 'name':safe.split(':',1)[1], 'source':'사용자 관심종목'}
    # Up to 14 OHLCV-intensive candidates per user action to respect MCP rate caps.
    candidates = list(merged.values())
    candidates.sort(key=lambda c: ((c.get('volume') or 0), (c.get('change') or 0)), reverse=True)
    return candidates[:14], warnings, len(merged)


@app.get('/')
def home(): return FileResponse(ROOT / 'static' / 'index.html')

@app.get('/login')
def login_form(): return login_page()

@app.post('/login')
async def login(request: Request, password: str = Form(default='')):
    if not PASSWORD or not secrets.compare_digest(password.encode('utf-8'), PASSWORD.encode('utf-8')):
        return login_page('비밀번호가 올바르지 않거나 서버가 설정되지 않았습니다.')
    request.session['signed_in'] = True
    return RedirectResponse('/', status_code=303)

@app.post('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse('/login',status_code=303)

@app.post('/api/auth/start')
async def oauth_start(request: Request):
    base = BASE_URL or str(request.base_url).rstrip('/')
    if not base.startswith(('https://', 'http://localhost:', 'http://127.0.0.1:')):
        raise HTTPException(400, 'TradingView OAuth는 HTTPS 배포 주소가 필요합니다. APP_BASE_URL을 설정하세요.')
    flow = start_flow(base + '/auth/tradingview/callback')
    return {'status':flow.phase, 'url':flow.url, 'error':flow.error}

@app.get('/api/auth/status')
async def oauth_status():
    flow = get_flow()
    return {'connected':has_saved_tokens() and (flow is None or flow.phase != 'error'),
            'status':flow.phase if flow else 'not_started',
            'url':flow.url if flow else None,
            'error':flow.error if flow else None}

@app.get('/auth/tradingview/callback')
async def oauth_callback(code: str = '', state: str = '', iss: str | None = None, error: str | None = None):
    if error: raise HTTPException(400, 'TradingView가 인증을 거부했습니다: ' + error[:160])
    try: complete_flow(code, state, iss)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    # The code and state are removed from the browser URL after successful handoff.
    return RedirectResponse('/?tv_connected=pending',status_code=303)



@app.get('/api/state')
def state(market: str = Query(default='US',pattern='^(KR|US)$')):
    clock = market_status(market)
    cached = get_scan(market)
    if cached:
        cached = dict(cached)
        cached['results'] = _decorate(cached.get('results',[]),market,clock)
    return {'market':market, 'clock':clock, 'connected':has_saved_tokens(),
            'mode':'TradingView 공식 MCP (OAuth)', 'cached':cached,
            'watchlist':get_watchlist(market), 'examples':STARTERS[market],
            'system_time':datetime.now(timezone.utc).isoformat()}


@app.post('/api/scan')
async def scan(request: ScanReq):
    market = request.market
    lock = _market_locks[market]
    if lock.locked(): raise HTTPException(409,'이미 해당 시장을 스캔하는 중입니다.')
    if not has_saved_tokens(): raise HTTPException(412,'TradingView 계정 연동이 필요합니다. 웹앱의 연동 안내에서 로그인하세요.')
    now = datetime.now(timezone.utc).timestamp()
    if not request.force and now - _last_scan_time[market] < 50:
        cached = get_scan(market)
        if cached:
            cached['results'] = _decorate(cached['results'],market,market_status(market))
            return {**cached, 'from_cache': True}
    async with lock:
        try:
            async with connect() as client:
                candidates, warnings, total = await _screen_candidates(client,market)
                if not candidates: raise RemoteDataError('스크리너와 관심종목에 대상이 없습니다.')
                # Sequential reads avoid racing one MCP client; 2 * 14 + 2 requests, at most ~30 per scan.
                # The public beta may impose daily quotas and delayed prices.
                results = []
                for candidate in candidates:
                    results.append(await _fetch_symbol(client,candidate,market))
        except AuthNeeded as exc: raise HTTPException(412,str(exc)) from exc
        except Exception as exc: raise HTTPException(502,'TradingView MCP 호출 실패: '+str(exc)[:350]) from exc
        results = [x for x in results if x.get('rank_score') is not None]
        results.sort(key=lambda x: (x['rank_score'], x.get('screener_volume') or 0),reverse=True)
        payload = {'market':market, 'generated_at':datetime.now(timezone.utc).isoformat(),
                   'source':'TradingView 공식 MCP', 'candidate_count':total,
                   'analyzed_count':len(results), 'warnings':warnings,
                   'results':results[:10], 'all_analyzed':results}
        if not results:
            # Do not overwrite previous scan with fabricated zeros; tell client no ranked candidate.
            raise HTTPException(424,'TradingView에서 충분한 확정 분봉 데이터를 가져오지 못했습니다. 연결·지연·거래시간을 확인하세요.')
        save_scan(market,payload)
        _last_scan_time[market] = datetime.now(timezone.utc).timestamp()
        return {**payload,'results':_decorate(payload['results'],market,market_status(market)),'from_cache':False}


@app.post('/api/watchlist')
def add(req: SymbolReq):
    sym = _safe_sym(req.symbol,req.market)
    if sym is None: raise HTTPException(400,'거래소 포함 형식으로 입력하세요 (예: NASDAQ:NVDA / KRX:005930).')
    add_watchlist(req.market,sym)
    return {'ok':True,'symbols':get_watchlist(req.market)}


@app.delete('/api/watchlist')
def remove(req: SymbolReq):
    sym = _safe_sym(req.symbol,req.market)
    if sym is None: raise HTTPException(400,'종목코드 형식이 잘못됐습니다.')
    remove_watchlist(req.market,sym)
    return {'ok':True,'symbols':get_watchlist(req.market)}


@app.get('/api/health')
def health():
    return {'ok':True, 'app':'Trading Pulse Web', 'time':datetime.now(timezone.utc).isoformat()}
