"""Pure calculations for confirmed OHLCV bars. No trading recommendations or execution."""
from __future__ import annotations
from datetime import datetime, timezone
from math import isfinite
from typing import Any

TIMEFRAMES = (1, 5, 10, 30)
MIN_BARS = 25


def clean_bars(items: list[dict], timeframe: int = 1) -> list[dict]:
    result = {}
    now = datetime.now(timezone.utc).timestamp()
    for x in items:
        try:
            ts = float(x.get('t', x.get('time', x.get('timestamp'))))
            if ts > 1e12: ts /= 1000
            o = float(x.get('o', x.get('open')))
            h = float(x.get('h', x.get('high')))
            l = float(x.get('l', x.get('low')))
            c = float(x.get('c', x.get('close')))
            v = float(x.get('v', x.get('volume', 0)))
            # Only completed candles. t is bar start time in TradingView OHLCV.
            if not all(isfinite(z) for z in (ts, o, h, l, c, v)): continue
            if not (0 < l <= min(o, c) <= max(o, c) <= h and v >= 0): continue
            if ts + 60 * timeframe > now - 2: continue
            result[int(ts)] = {'t': int(ts), 'o': o, 'h': h, 'l': l, 'c': c, 'v': v}
        except (TypeError, ValueError):
            continue
    return [result[k] for k in sorted(result)]


def aggregate(bars: list[dict], minutes: int) -> list[dict]:
    if minutes == 1: return bars[:]
    buckets: dict[int, list[dict]] = {}
    for b in bars:
        k = (b['t'] // (minutes * 60)) * minutes * 60
        buckets.setdefault(k, []).append(b)
    out = []
    for k in sorted(buckets):
        batch = sorted(buckets[k], key=lambda b: b['t'])
        # Gaps and not-yet-closed bins must never be treated as confirmed bars.
        if len(batch) != minutes: continue
        if any(b['t'] != k + i * 60 for i, b in enumerate(batch)): continue
        out.append({'t': k, 'o': batch[0]['o'], 'h': max(b['h'] for b in batch),
                    'l': min(b['l'] for b in batch), 'c': batch[-1]['c'],
                    'v': sum(b['v'] for b in batch)})
    return out


def ema(vals: list[float], length: int) -> float | None:
    if len(vals) < length: return None
    smooth = sum(vals[:length]) / length
    multiplier = 2 / (length + 1)
    for val in vals[length:]: smooth = val * multiplier + smooth * (1 - multiplier)
    return smooth


def rsi(vals: list[float], length: int = 14) -> float | None:
    if len(vals) <= length: return None
    changes = [b - a for a, b in zip(vals, vals[1:])]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    avg_gain, avg_loss = sum(gains[:length]) / length, sum(losses[:length]) / length
    for g, l in zip(gains[length:], losses[length:]):
        avg_gain = (avg_gain * (length - 1) + g) / length
        avg_loss = (avg_loss * (length - 1) + l) / length
    if avg_gain == 0 and avg_loss == 0: return 50.0
    if avg_loss == 0: return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def atr(bars: list[dict], length: int = 14) -> float | None:
    if len(bars) < length + 1: return None
    trs = []
    for prev, now in zip(bars, bars[1:]):
        trs.append(max(now['h'] - now['l'], abs(now['h'] - prev['c']), abs(now['l'] - prev['c'])))
    value = sum(trs[:length]) / length
    for tr in trs[length:]: value = (value * (length - 1) + tr) / length
    return value


def intraday_vwap(bars: list[dict], market: str) -> float | None:
    if not bars: return None
    from zoneinfo import ZoneInfo
    tz = ZoneInfo('Asia/Seoul') if market == 'KR' else ZoneInfo('America/New_York')
    last_date = datetime.fromtimestamp(bars[-1]['t'], tz).date()
    current = [b for b in bars if datetime.fromtimestamp(b['t'], tz).date() == last_date]
    total_v = sum(b['v'] for b in current)
    return sum(((b['h'] + b['l'] + b['c']) / 3) * b['v'] for b in current) / total_v if total_v else None


def price_round(x: float | None, market: str) -> float | None:
    if x is None: return None
    # Presentation rounding ONLY. Actual tick size, order type & spread must be checked in broker.
    return round(x, 0 if market == 'KR' else (4 if x < 1 else 2))


def analyze(bars: list[dict], minutes: int, market: str, latest_ts: int | None = None) -> dict:
    if len(bars) < MIN_BARS:
        return {'minutes': minutes, 'status': '데이터 부족', 'bar_count': len(bars),
                'required_bars': MIN_BARS, 'signal': '판단 보류'}
    cls = [b['c'] for b in bars]
    latest = cls[-1]
    fast, slow = ema(cls, 9), ema(cls, 21)
    rv, a = rsi(cls), atr(bars)
    vw = intraday_vwap(bars, market)
    avg_volume = sum(b['v'] for b in bars[-21:-1]) / 20
    relative_volume = bars[-1]['v'] / avg_volume if avg_volume else None
    high10 = max(b['h'] for b in bars[-11:-1])
    low10 = min(b['l'] for b in bars[-10:])
    trend = bool(fast is not None and slow is not None and fast > slow and latest > slow)
    weakness = bool(fast is not None and slow is not None and fast < slow and (vw is None or latest < vw))
    overextended = bool(a and latest - fast > 2 * a)
    bullish = bool(trend and rv is not None and 50 <= rv <= 72 and (vw is None or latest >= vw) and
                   relative_volume is not None and relative_volume >= 1.2 and not overextended)
    sell_check = bool(weakness and rv is not None and rv < 47)
    if sell_check: signal = '매도 검토'
    elif bullish and latest > high10: signal = '돌파 확인'
    elif bullish: signal = '매수 감시'
    else: signal = '관망'
    # Conditional long scenario only, never imply a filled order.
    trigger = high10 if latest <= high10 else latest
    stop = min(low10, trigger - 1.5 * (a or 0))
    if stop >= trigger: stop = trigger * 0.985
    risk = trigger - stop
    target1 = trigger + 1.5 * risk
    target2 = trigger + 2.0 * risk
    momentum = (latest / cls[-11] - 1) * 100 if cls[-11] else 0.0
    components = [
        16 if trend else 0,
        12 if vw is not None and latest > vw else 0,
        12 if rv is not None and 50 <= rv <= 68 else 0,
        17 if relative_volume is not None and relative_volume >= 1.5 else (9 if relative_volume is not None and relative_volume >= 1.2 else 0),
        16 if latest > high10 else (8 if latest > sum(cls[-5:]) / 5 else 0),
        15 if 0 < momentum <= 3.5 else (7 if momentum > 0 else 0),
        12 if not overextended else 0,
    ]
    score = min(100, max(0, sum(components)))
    return {'minutes': minutes, 'status': '분석 완료', 'bar_count': len(bars),
            'bar_time': bars[-1]['t'], 'latest': price_round(latest, market),
            'ema9': price_round(fast, market), 'ema21': price_round(slow, market),
            'vwap': price_round(vw, market), 'rsi': round(rv, 1) if rv is not None else None,
            'atr': price_round(a, market), 'relative_volume': round(relative_volume, 2) if relative_volume is not None else None,
            'momentum_10bars_pct': round(momentum, 2), 'score': score,
            'signal': signal, 'entry_trigger': price_round(trigger, market),
            'stop': price_round(stop, market), 'target1': price_round(target1, market),
            'target2': price_round(target2, market), 'risk_reward': 2.0,
            'risk_pct': round((trigger - stop) / trigger * 100, 2),
            'reasons': [z for cond, z in [(trend, 'EMA 상승 배열'),
                                         (vw is not None and latest > vw, 'VWAP 위'),
                                         (relative_volume is not None and relative_volume >= 1.2, '거래량 증가'),
                                         (latest > high10, '최근 고점 돌파')] if cond]}


def analyze_all(bars1: list[dict], bars30: list[dict], market: str) -> dict:
    frames = {str(n): analyze(aggregate(bars1, n), n, market) for n in (1, 5, 10)}
    frames['30'] = analyze(bars30, 30, market)
    available = [x for x in (frames['5'], frames['1']) if x.get('status') == '분석 완료']
    rank = available[0]['score'] if available else None
    trend30 = frames['30'].get('signal')
    if rank is not None and frames['30'].get('status') == '분석 완료':
        rank = min(100, rank + (8 if frames['30']['ema9'] and frames['30']['ema21'] and frames['30']['ema9'] > frames['30']['ema21'] else -8))
    source_last_time = bars1[-1]['t'] if bars1 else (bars30[-1]['t'] if bars30 else None)
    return {'frames': frames, 'rank_score': rank, 'bar_time': source_last_time,
            'current': frames['1'].get('latest'), 'signal': (available[0]['signal'] if available else '판단 보류'),
            'reason': ', '.join(available[0].get('reasons', [])) if available else '분봉 데이터가 더 필요합니다.'}
