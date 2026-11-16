"""Market clock in exchange timezone; session-day detection uses exchange_calendars."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


def market_status(market: str) -> dict:
    market = market.upper()
    tz = ZoneInfo('Asia/Seoul' if market == 'KR' else 'America/New_York')
    now = datetime.now(tz)
    korea = now.astimezone(ZoneInfo('Asia/Seoul'))
    is_session = now.weekday() < 5
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar('XKRX' if market == 'KR' else 'XNYS')
        is_session = bool(cal.is_session(now.date().isoformat()))
    except Exception:
        # Holiday calendar unavailable = NOT a confirmed open market.
        return {'market': market, 'state': '확인 불가', 'open': False, 'phase': '달력 확인 불가',
                'local_time': now.isoformat(), 'kst_time': korea.isoformat(), 'session_verified': False}
    phase = '휴장' if not is_session else '장 종료'
    state = 'closed'
    if is_session:
        if market == 'KR':
            if time(9, 0) <= now.time() < time(15, 30): state, phase = 'regular', '정규장'
            elif time(8, 30) <= now.time() < time(9, 0): state, phase = 'pre', '장 시작 전'
        else:
            # Full day sessions are reduced on rare early-close days; use calendar close.
            close_local = cal.session_close(now.date().isoformat()).to_pydatetime().astimezone(tz).time()
            if time(4, 0) <= now.time() < time(9, 30): state, phase = 'pre', '프리마켓'
            elif time(9, 30) <= now.time() < close_local: state, phase = 'regular', '정규장'
            elif close_local <= now.time() < time(20, 0): state, phase = 'after', '애프터마켓'
    return {'market': market, 'state': state, 'open': state in ('pre','regular','after'),
            'phase': phase, 'local_time': now.isoformat(), 'kst_time': korea.isoformat(),
            'session_verified': True}
