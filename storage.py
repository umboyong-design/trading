"""No broker credentials; store ranked analysis results only."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get('STATE_DIR', str(Path(__file__).resolve().parent / '.state'))) / 'pulse.sqlite3'

def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute('CREATE TABLE IF NOT EXISTS scans (market TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS watchlist (market TEXT NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY(market,symbol))')
    return c

def get_scan(market: str):
    with connection() as c: row = c.execute('SELECT payload FROM scans WHERE market=?',(market,)).fetchone()
    return json.loads(row[0]) if row else None

def save_scan(market: str, payload: dict):
    with connection() as c:
        c.execute('INSERT OR REPLACE INTO scans VALUES (?,?,?)',(market,json.dumps(payload,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))

def get_watchlist(market: str):
    with connection() as c:
        return [r[0] for r in c.execute('SELECT symbol FROM watchlist WHERE market=? ORDER BY symbol',(market,))]

def add_watchlist(market: str, symbol: str):
    with connection() as c: c.execute('INSERT OR IGNORE INTO watchlist VALUES (?,?)',(market,symbol))

def remove_watchlist(market: str, symbol: str):
    with connection() as c: c.execute('DELETE FROM watchlist WHERE market=? AND symbol=?',(market,symbol))
