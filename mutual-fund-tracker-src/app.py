#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import math
import csv
import io
import re
import difflib
import os
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

_LOGGER = logging.getLogger(__name__)
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path('/data')
DB_PATH = DATA_DIR / 'mutual_fund_tracker.db'
WEB_DIR = APP_DIR / 'www'
HOST = '0.0.0.0'
PORT = 8099
MFAPI_BASE = 'https://api.mfapi.in'
DEFAULT_REFRESH_SECONDS = 7200
NAV_SNAPSHOT_CACHE_SECONDS = 7200
ALLOWED_REFRESH_SECONDS = (300, 900, 1800, 3600, 7200, 21600, 43200, 86400)
NAV_RETRY_ATTEMPTS = 5
NAV_RETRY_DELAY_SECONDS = 5
NAV_REQUEST_SPACING_SECONDS = 0.5
AUTO_REFRESH_RETRY_COUNT = 5
AUTO_REFRESH_RETRY_DELAY_SECONDS = 120
AMFI_NAV_URLS = [
    'https://portal.amfiindia.com/spages/NAVAll.txt',
]
REFRESH_SECONDS = DEFAULT_REFRESH_SECONDS
HTTP_TIMEOUT = 25
HTTP_RETRIES = 3
IMPORT_FORMAT_VERSION = 2
SUPPORTED_IMPORT_FORMATS = {1, 2}
MAX_IMPORT_FUNDS = 200
MAX_IMPORT_TRANSACTIONS = 10000
INTEGRATION_RELOAD_ACK_PATH = Path('/share/mutual_fund_tracker/integration_reload.ack')
NSE_HOLIDAY_JSON = DATA_DIR / 'nse_holiday_calendar.json'
UPSTOX_HOLIDAY_URL = 'https://api.upstox.com/v2/market/holidays'
HOLIDAY_REFRESH_HOUR = 1
HOLIDAY_REFRESH_MINUTE = 0
NSE_TIMEZONE = ZoneInfo('Asia/Kolkata')
NSE_MARKET_OPEN_MINUTE = 9 * 60
NSE_MARKET_CLOSE_MINUTE = 15 * 60 + 30
DEFAULT_NIFTY_POLL_INTERVAL_SECONDS = 60
ALLOWED_NIFTY_POLL_SECONDS = (0, 60, 300, 600, 900, 1800, 3600)
MARKET_STATUS_CHECK_INTERVAL_SECONDS = 5
NIFTY_YAHOO_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1m&range=1d'
NIFTY_CSV_URL = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTj0LbJz31u9xE_LtaWLKaf-xiUYTMhPsHvwPS0sQ-RNw058kjLCeoSVn6w544mJAcIG_TsD929ZVTT/pub?gid=0&single=true&output=csv'
NIFTY_CSV_FALLBACK_URL = NIFTY_CSV_URL
NIFTY_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/136 Safari/537.36'
GRAPH_NAV_CONCURRENCY = 3
GRAPH_NAV_TIMEOUT = 30
GRAPH_NAV_RETRIES = 3
GRAPH_MAX_POINTS = 1400


NSE_2026_SEED_HOLIDAYS = {
    '2026-01-26': 'Republic Day',
    '2026-03-03': 'Holi',
    '2026-03-26': 'Shri Ram Navami',
    '2026-03-31': 'Shri Mahavir Jayanti',
    '2026-04-03': 'Good Friday',
    '2026-04-14': 'Dr. Baba Saheb Ambedkar Jayanti',
    '2026-05-01': 'Maharashtra Day',
    '2026-05-28': 'Bakri Id',
    '2026-06-26': 'Muharram',
    '2026-09-14': 'Ganesh Chaturthi',
    '2026-10-02': 'Mahatma Gandhi Jayanti',
    '2026-10-20': 'Dussehra',
    '2026-11-10': 'Diwali – Balipratipada',
    '2026-11-24': 'Prakash Gurpurb Sri Guru Nanak Dev',
    '2026-12-25': 'Christmas',
}
# 2026 special session overrides: NSE was open on these otherwise non-weekdays.
NSE_2026_SPECIAL_OPEN = {
    '2026-02-01': 'Special trading session – Union Budget',
    '2026-11-08': 'Special Muhurat trading session',
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_now() -> str:
    return now_utc().replace(microsecond=0).isoformat() + 'Z'


def parse_money(value) -> float:
    try:
        d = Decimal(str(value).replace(',', '').strip())
        if d < 0:
            raise ValueError('amount must be non-negative')
        return float(d)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid non-negative amount') from exc


def parse_float(value) -> float:
    try:
        d = Decimal(str(value).replace(',', '').strip())
        if d < 0:
            raise ValueError('value must be non-negative')
        return float(d)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid non-negative number') from exc


def parse_signed_float(value, field='value') -> float:
    try:
        return float(Decimal(str(value).replace(',', '').strip()))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f'invalid {field}') from exc


def normalize_txn_type(value):
    v = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'systematic_investment': 'sip',
        'systematic_investment_new_purchase_with_sip': 'sip',
        'systematic_investment_existing_folio_with_sip': 'sip',
        'sip_purchase_bse': 'sip',
        'purchase_systematic_bse': 'sip',
        'purchase': 'lumpsum',
        'lump_sum': 'lumpsum',
        'lumpsum_purchase': 'lumpsum',
        'redemption': 'sell',
        'redemption_less_tds_stt': 'sell',
        'switch_in': 'switch_in',
        'switch_out': 'switch_out',
        'reversal': 'adjustment',
        'rejected': 'adjustment',
        'rejection': 'adjustment',
        'fee': 'fee',
        'stamp_duty': 'fee',
    }
    return aliases.get(v, v)


def parse_optional_date(value: str | date | None) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), '%Y-%m-%d').date()


def fmt_num(v, decimals=2):
    if v is None:
        return None
    return round(float(v), decimals)


def parse_nav_date(s: str) -> date:
    return datetime.strptime(s.strip(), '%d-%m-%Y').date()


def iso_date(d: date | None) -> str | None:
    return d.isoformat() if d else None


def normalize_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError('Investor profile must be an object')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('Investor name is required')
    emails = payload.get('emails')
    if emails is None:
        email = str(payload.get('email') or '').strip()
        emails = [email] if email else []
    if not isinstance(emails, list):
        emails = [str(emails)]
    emails = sorted({str(e).strip().lower() for e in emails if str(e).strip()})
    address = payload.get('address') if isinstance(payload.get('address'), dict) else {}
    pan = str(payload.get('pan') or '').strip().upper() or None
    if pan and not re.fullmatch(r'[A-Z]{5}[0-9]{4}[A-Z]', pan):
        raise ValueError('Invalid PAN format; expected 5 letters, 4 digits, 1 letter')
    return {
        'name': name,
        'emails': emails,
        'phone': str(payload.get('phone') or '').strip() or None,
        'address': {
            'line1': str(address.get('line1') or payload.get('address_line1') or '').strip(),
            'line2': str(address.get('line2') or payload.get('address_line2') or '').strip(),
            'city': str(address.get('city') or payload.get('city') or '').strip(),
            'state': str(address.get('state') or payload.get('state') or '').strip(),
            'postal_code': str(address.get('postal_code') or payload.get('postal_code') or '').strip(),
            'country': str(address.get('country') or payload.get('country') or '').strip(),
        },
        'pan': pan,
    }


def request_bytes(url: str, timeout: int = HTTP_TIMEOUT, retries: int | None = None):
    last_exc = None
    attempts = HTTP_RETRIES if retries is None else max(1, int(retries))
    for attempt in range(attempts):
        try:
            req = Request(url, headers={'User-Agent': 'HomeAssistant-MutualFundTracker/0.1.35'})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(0.75 * (2 ** attempt))
    raise last_exc or RuntimeError('HTTP request failed')


def request_json(url: str, retries: int | None = None):
    raw = request_bytes(url, retries=retries)
    return json.loads(raw.decode('utf-8'))


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._init()

    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA foreign_keys=ON')
        return c

    def _init(self):
        """Initialize the normalized relational schema for a fresh v0.1.78 install."""
        with self.conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                emails_json TEXT NOT NULL DEFAULT '[]',
                phone TEXT,
                address_json TEXT NOT NULL DEFAULT '{}',
                pan TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_pan
                ON profiles(pan) WHERE pan IS NOT NULL AND TRIM(pan) <> '';

            CREATE TABLE IF NOT EXISTS schemes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                isin TEXT UNIQUE,
                scheme_code TEXT,
                scheme_name TEXT NOT NULL,
                normalized_name TEXT,
                fund_house TEXT,
                scheme_type TEXT,
                scheme_category TEXT,
                isin_growth TEXT,
                isin_div_reinvestment TEXT,
                metadata_updated_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_schemes_code ON schemes(scheme_code);
            CREATE INDEX IF NOT EXISTS idx_schemes_name ON schemes(normalized_name);

            CREATE TABLE IF NOT EXISTS holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                scheme_id INTEGER NOT NULL,
                folio TEXT,
                normalized_folio TEXT,
                scheme_code TEXT,
                scheme_name TEXT NOT NULL,
                isin TEXT,
                units REAL NOT NULL DEFAULT 0,
                invested REAL NOT NULL DEFAULT 0,
                sip_enabled INTEGER NOT NULL DEFAULT 0,
                sip_amount REAL NOT NULL DEFAULT 0,
                sip_day INTEGER,
                initial_date TEXT,
                last_nav REAL,
                last_nav_date TEXT,
                previous_nav REAL,
                previous_nav_date TEXT,
                month_nav REAL,
                month_nav_date TEXT,
                year_nav REAL,
                year_nav_date TEXT,
                last_refresh TEXT,
                last_error TEXT,
                last_sip_cycle TEXT,
                last_sip_nav_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(scheme_id) REFERENCES schemes(id) ON DELETE RESTRICT,
                UNIQUE(profile_id, normalized_folio, scheme_id)
            );
            CREATE INDEX IF NOT EXISTS idx_holdings_profile ON holdings(profile_id);
            CREATE INDEX IF NOT EXISTS idx_holdings_folio ON holdings(profile_id, normalized_folio);
            CREATE INDEX IF NOT EXISTS idx_holdings_scheme ON holdings(scheme_id);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_holding_no_folio ON holdings(profile_id, scheme_id) WHERE normalized_folio IS NULL;

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holding_id INTEGER NOT NULL,
                txn_type TEXT NOT NULL CHECK(txn_type IN ('initial','sip','lumpsum','buy','sell','switch_in','switch_out','adjustment','fee')),
                txn_date TEXT NOT NULL,
                amount REAL NOT NULL,
                cashflow REAL NOT NULL,
                units REAL NOT NULL,
                nav REAL,
                charges REAL NOT NULL DEFAULT 0,
                folio TEXT,
                source TEXT,
                note TEXT,
                raw_type_text TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(holding_id) REFERENCES holdings(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_txn_holding_date ON transactions(holding_id, txn_date);

            CREATE TABLE IF NOT EXISTS sip_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holding_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                sip_day INTEGER NOT NULL,
                frequency TEXT NOT NULL DEFAULT 'monthly',
                effective_from TEXT,
                effective_to TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                source TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(holding_id) REFERENCES holdings(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sip_schedules_holding_active ON sip_schedules(holding_id, is_active);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_active_sip_schedule ON sip_schedules(holding_id) WHERE is_active=1;

            CREATE TABLE IF NOT EXISTS sip_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holding_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                fund_name TEXT NOT NULL,
                sip_date TEXT NOT NULL,
                nav_date TEXT NOT NULL,
                amount REAL NOT NULL,
                units REAL NOT NULL,
                nav REAL,
                executed_at TEXT NOT NULL,
                reconciled_with_cams INTEGER NOT NULL DEFAULT 0,
                cams_transaction_id INTEGER,
                cams_transaction_date TEXT,
                FOREIGN KEY(holding_id) REFERENCES holdings(id) ON DELETE CASCADE,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(cams_transaction_id) REFERENCES transactions(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sip_executions_profile_time ON sip_executions(profile_id, executed_at);
            CREATE INDEX IF NOT EXISTS idx_sip_executions_holding_time ON sip_executions(holding_id, executed_at);

            CREATE TABLE IF NOT EXISTS live_sip_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holding_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                fund_name TEXT NOT NULL,
                sip_date TEXT NOT NULL,
                nav_date TEXT NOT NULL,
                amount REAL NOT NULL,
                units REAL NOT NULL,
                nav REAL,
                execution_date TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                FOREIGN KEY(holding_id) REFERENCES holdings(id) ON DELETE CASCADE,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                UNIQUE(holding_id, sip_date)
            );
            CREATE INDEX IF NOT EXISTS idx_live_sip_executions_profile_date ON live_sip_executions(profile_id, execution_date);
            CREATE INDEX IF NOT EXISTS idx_live_sip_executions_holding_date ON live_sip_executions(holding_id, execution_date);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS graph_nav_history (
                scheme_code TEXT NOT NULL,
                nav_date TEXT NOT NULL,
                nav REAL NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (scheme_code, nav_date)
            );
            CREATE INDEX IF NOT EXISTS idx_graph_nav_history_date ON graph_nav_history(nav_date);
            CREATE TABLE IF NOT EXISTS daily_portfolio_snapshot (
                holding_id INTEGER NOT NULL,
                snapshot_date TEXT NOT NULL,
                units_held REAL NOT NULL,
                invested_amount REAL NOT NULL,
                value REAL NOT NULL,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (holding_id, snapshot_date),
                FOREIGN KEY(holding_id) REFERENCES holdings(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_daily_snapshot_date ON daily_portfolio_snapshot(snapshot_date);
            CREATE TABLE IF NOT EXISTS graph_cache_meta (
                holding_id INTEGER PRIMARY KEY,
                generation INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(holding_id) REFERENCES holdings(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS nav_reference (
                scheme_code TEXT PRIMARY KEY,
                latest_nav_date TEXT,
                latest_nav REAL,
                previous_nav_date TEXT,
                previous_nav REAL,
                month_nav_date TEXT,
                month_nav REAL,
                year_nav_date TEXT,
                year_nav REAL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_nav_reference_latest_date ON nav_reference(latest_nav_date);
            """)
            # Backward-compatible schema migration for scheme metadata used by table filters.
            existing_cols = {row[1] for row in c.execute("PRAGMA table_info(schemes)").fetchall()}
            for name, ddl in (
                ('fund_house', 'ALTER TABLE schemes ADD COLUMN fund_house TEXT'),
                ('scheme_type', 'ALTER TABLE schemes ADD COLUMN scheme_type TEXT'),
                ('scheme_category', 'ALTER TABLE schemes ADD COLUMN scheme_category TEXT'),
                ('isin_growth', 'ALTER TABLE schemes ADD COLUMN isin_growth TEXT'),
                ('isin_div_reinvestment', 'ALTER TABLE schemes ADD COLUMN isin_div_reinvestment TEXT'),
                ('metadata_updated_at', 'ALTER TABLE schemes ADD COLUMN metadata_updated_at TEXT'),
            ):
                if name not in existing_cols:
                    c.execute(ddl)
        self._ensure_default_profile()

    def _migrate_sip_execution_log(self):
        # Safe compatibility backfill: useful when a test restores an older database;
        # fresh v0.1.78 installs simply have no legacy rows to discover.
        with self.conn() as c:
            existing={(r['holding_id'], r['executed_at']) for r in c.execute('SELECT holding_id, executed_at FROM sip_executions').fetchall()}
            rows=c.execute('''SELECT t.holding_id,h.profile_id,h.scheme_name,t.txn_date,t.amount,t.units,t.nav,t.created_at
                              FROM transactions t JOIN holdings h ON h.id=t.holding_id
                              WHERE t.txn_type='sip' AND t.source='sip_execution' ''').fetchall()
            for row in rows:
                key=(row['holding_id'],row['created_at'])
                if key in existing: continue
                c.execute('''INSERT INTO sip_executions(holding_id,profile_id,fund_name,sip_date,nav_date,amount,units,nav,executed_at)
                             VALUES(?,?,?,?,?,?,?,?,?)''',(row['holding_id'],row['profile_id'],row['scheme_name'],row['txn_date'],row['txn_date'],float(row['amount'] or 0),float(row['units'] or 0),float(row['nav']) if row['nav'] is not None else None,row['created_at']))

    def _migrate_transaction_schema(self):
        return None

    def _migrate_profile_schema(self):
        return None

    def get_setting(self, key, default=None):
        with self.conn() as c:
            row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return row['value'] if row else default

    def set_setting(self, key, value):
        with self._lock, self.conn() as c:
            c.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
                      (key, str(value), iso_now()))

    def request_integration_reload(self):
        """Request one Home Assistant integration reload after a successful import.

        The request is persisted in SQLite so it survives process timing/restarts.
        The custom integration receives the exported token, performs the reload,
        and writes an acknowledgement token back to the shared filesystem.
        """
        token = uuid.uuid4().hex
        self.set_setting('integration_reload_requested', '1')
        self.set_setting('integration_reload_token', token)
        return token

    def acknowledge_integration_reload(self, token):
        current = self.get_setting('integration_reload_token', '')
        if not token or token != current:
            return False
        self.set_setting('integration_reload_requested', '0')
        self.set_setting('integration_reload_token', '')
        return True

    def get_refresh_interval(self):
        try:
            value=int(self.get_setting('refresh_interval_seconds', REFRESH_SECONDS))
            return max(60, min(86400, value))
        except Exception:
            return REFRESH_SECONDS

    def get_nifty_poll_interval(self):
        try:
            value = int(self.get_setting('nifty_poll_interval_seconds', DEFAULT_NIFTY_POLL_INTERVAL_SECONDS))
            if value in ALLOWED_NIFTY_POLL_SECONDS:
                return value
        except Exception:
            pass
        return DEFAULT_NIFTY_POLL_INTERVAL_SECONDS

    def last_refresh_time(self, profile_id=None):
        # A global refresh timestamp is written only after a complete NAV refresh
        # succeeds. This avoids the UI appearing stale when individual holding
        # timestamps are uneven or a refresh fails part-way through.
        if profile_id is None:
            global_refresh = self.get_setting('last_successful_nav_refresh', '')
            if global_refresh:
                return global_refresh
        with self.conn() as c:
            if profile_id is None:
                row=c.execute("SELECT MAX(last_refresh) AS v FROM holdings WHERE last_refresh IS NOT NULL").fetchone()
            else:
                row=c.execute("SELECT MAX(last_refresh) AS v FROM holdings WHERE profile_id=? AND last_refresh IS NOT NULL",(profile_id,)).fetchone()
            return row['v'] if row else None

    def nav_refresh_error(self):
        raw = self.get_setting('nav_refresh_error', '')
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return {'error': str(raw)}

    def set_nav_refresh_error(self, error, stage='NAV refresh', retry_count=0, next_retry_at=None):
        payload = {
            'error': str(error),
            'error_time': iso_now(),
            'error_stage': str(stage or 'NAV refresh'),
            'retry_count': int(retry_count or 0),
            'next_retry_at': next_retry_at,
            'last_successful_refresh': self.get_setting('last_successful_nav_refresh', '') or None,
        }
        self.set_setting('nav_refresh_error', json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        return payload

    def clear_nav_refresh_error(self):
        self.set_setting('nav_refresh_error', '')

    def _ensure_default_profile(self):
        with self._lock, self.conn() as c:
            row = c.execute('SELECT id FROM profiles ORDER BY id LIMIT 1').fetchone()
            if row:
                return row['id']
            now = iso_now()
            cur = c.execute('INSERT INTO profiles(name,emails_json,phone,address_json,pan,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                             ('Primary Investor', '[]', None, '{}', None, now, now))
            return cur.lastrowid

    def all_profiles(self):
        with self.conn() as c:
            rows = c.execute('''SELECT p.*, COUNT(f.id) AS fund_count
                                FROM profiles p LEFT JOIN holdings f ON f.profile_id=p.id
                                GROUP BY p.id ORDER BY p.created_at ASC, p.id ASC''').fetchall()
            return rows

    def get_profile(self, profile_id):
        with self.conn() as c:
            return c.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()

    def claim_empty_primary_profile(self, payload):
        profile=normalize_profile(payload)
        with self._lock, self.conn() as c:
            row=c.execute('SELECT * FROM profiles WHERE id=1').fetchone()
            if not row:
                return None
            fund_count=c.execute('SELECT COUNT(*) AS n FROM holdings WHERE profile_id=1').fetchone()['n']
            if row['name']!='Primary Investor' or fund_count or row['pan'] or row['phone'] or row['emails_json'] not in ('[]','[ ]'):
                return None
            c.execute('UPDATE profiles SET name=?,emails_json=?,phone=?,address_json=?,pan=?,updated_at=? WHERE id=1',
                      (profile['name'],json.dumps(profile['emails']),profile['phone'],json.dumps(profile['address']),profile['pan'],iso_now()))
            return 1

    def create_profile(self, payload):
        profile = normalize_profile(payload)
        now=iso_now()
        with self._lock, self.conn() as c:
            try:
                cur=c.execute('INSERT INTO profiles(name,emails_json,phone,address_json,pan,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                              (profile['name'], json.dumps(profile['emails']), profile['phone'], json.dumps(profile['address']), profile['pan'], now, now))
            except sqlite3.IntegrityError as exc:
                if profile.get('pan'):
                    existing = c.execute('SELECT id FROM profiles WHERE UPPER(TRIM(pan))=? LIMIT 1', (profile['pan'].upper(),)).fetchone()
                    if existing:
                        raise ValueError(f'An investor with PAN {profile["pan"]} already exists.') from exc
                raise
            return cur.lastrowid

    def update_profile(self, profile_id, payload):
        profile=normalize_profile(payload)
        now=iso_now()
        with self._lock, self.conn() as c:
            try:
                c.execute('UPDATE profiles SET name=?,emails_json=?,phone=?,address_json=?,pan=?,updated_at=? WHERE id=?',
                          (profile['name'], json.dumps(profile['emails']), profile['phone'], json.dumps(profile['address']), profile['pan'], now, profile_id))
            except sqlite3.IntegrityError as exc:
                if profile.get('pan'):
                    existing = c.execute('SELECT id FROM profiles WHERE UPPER(TRIM(pan))=? AND id<>? LIMIT 1', (profile['pan'].upper(), profile_id)).fetchone()
                    if existing:
                        raise ValueError(f'An investor with PAN {profile["pan"]} already exists.') from exc
                raise

    def delete_profile(self, profile_id):
        with self._lock, self.conn() as c:
            row=c.execute('SELECT id FROM profiles WHERE id=?',(profile_id,)).fetchone()
            if not row:
                raise ValueError('Investor profile not found')
            # Funds reference profiles with ON DELETE CASCADE, and transactions
            # reference funds with ON DELETE CASCADE. Deleting an investor
            # therefore intentionally removes that investor's funds and their
            # complete transaction history as one destructive operation.
            c.execute('PRAGMA foreign_keys=ON')
            c.execute('DELETE FROM profiles WHERE id=?',(profile_id,))

    def repair_primary_profile_id(self):
        # During early migrations the first real imported investor could become id=2 while
        # an empty placeholder remained at id=1. When there is exactly one real profile,
        # move it into id=1 so profile IDs remain intuitive and stable.
        with self._lock, self.conn() as c:
            rows=c.execute('''SELECT p.id,p.name,p.emails_json,p.phone,p.address_json,p.pan,COUNT(f.id) AS fund_count
                              FROM profiles p LEFT JOIN holdings f ON f.profile_id=p.id
                              GROUP BY p.id ORDER BY p.id''').fetchall()
            if len(rows)!=2: return False
            p1,p2=rows[0],rows[1]
            if p1['id']!=1 or p1['name']!='Primary Investor' or p1['fund_count']!=0: return False
            if p1['emails_json'] not in ('[]','[ ]') or p1['pan'] or p1['phone']: return False
            c.execute('PRAGMA foreign_keys=OFF')
            try:
                c.execute('DELETE FROM profiles WHERE id=1')
                c.execute('UPDATE profiles SET id=1 WHERE id=?',(p2['id'],))
                c.execute('UPDATE holdings SET profile_id=1 WHERE profile_id=?',(p2['id'],))
                try:
                    c.execute("UPDATE sqlite_sequence SET seq=1 WHERE name='profiles'")
                except sqlite3.OperationalError:
                    pass
                c.commit()
            finally:
                c.execute('PRAGMA foreign_keys=ON')
            return True

    def cleanup_empty_primary_profiles(self):
        with self._lock, self.conn() as c:
            rows=c.execute('''SELECT p.id,p.name,p.emails_json,p.phone,p.pan,COUNT(f.id) AS fund_count
                              FROM profiles p LEFT JOIN holdings f ON f.profile_id=p.id
                              GROUP BY p.id ORDER BY p.id''').fetchall()
            if len(rows)<=1:
                return []
            removed=[]
            for r in rows:
                if r['name']!='Primary Investor' or r['fund_count']:
                    continue
                emails=json.loads(r['emails_json'] or '[]')
                if emails or r['pan'] or r['phone']:
                    continue
                c.execute('DELETE FROM profiles WHERE id=?',(r['id'],))
                removed.append(r['id'])
            return removed

    def profile_dict(self, row):
        if not row:
            return None
        return {
            'id': row['id'], 'name': row['name'],
            'emails': json.loads(row['emails_json'] or '[]'),
            'phone': row['phone'],
            'address': json.loads(row['address_json'] or '{}'),
            'pan': row['pan'], 'fund_count': row['fund_count'] if 'fund_count' in row.keys() else None,
        }

    def _profile_details_differ(self, inv, row):
        existing = self.profile_dict(row)
        if not existing:
            return True, []
        diffs = []
        if inv.get('name','').strip().lower() != str(existing.get('name') or '').strip().lower():
            diffs.append('name')
        if set(inv.get('emails') or []) != set(existing.get('emails') or []):
            diffs.append('email')
        if (inv.get('phone') or '') != (existing.get('phone') or ''):
            diffs.append('phone')
        if (inv.get('address') or {}) != (existing.get('address') or {}):
            diffs.append('address')
        return bool(diffs), diffs

    def find_profile_matches(self, investor):
        inv=normalize_profile(investor or {'name':'Unknown'})
        pan=(inv.get('pan') or '').strip().upper()
        inv_emails=set(inv['emails'])
        with self.conn() as c:
            if pan:
                rows=c.execute('SELECT * FROM profiles WHERE UPPER(TRIM(pan))=? ORDER BY id', (pan,)).fetchall()
            else:
                rows=c.execute('SELECT * FROM profiles ORDER BY id').fetchall()
        candidates=[]
        if pan:
            # PAN is the primary import identity. A matching PAN is resolved before any
            # email/name fallback logic is considered.
            for r in rows:
                diff, diffs = self._profile_details_differ(inv, r)
                candidates.append({**self.profile_dict(r), 'match_type':'pan', 'details_differ':diff, 'different_fields':diffs})
        else:
            # PAN should normally be present for CAMS/KFintech statements.
            # When it is absent, only offer weak fallback candidates; never silently merge.
            for r in rows:
                emails=set(json.loads(r['emails_json'] or '[]'))
                if inv_emails and emails and inv_emails & emails:
                    diff, diffs = self._profile_details_differ(inv, r)
                    candidates.append({**self.profile_dict(r), 'match_type':'email_fallback', 'details_differ':diff, 'different_fields':diffs})
                elif inv['name'].strip().lower()==str(r['name']).strip().lower():
                    diff, diffs = self._profile_details_differ(inv, r)
                    candidates.append({**self.profile_dict(r), 'match_type':'name_fallback', 'details_differ':diff, 'different_fields':diffs})
        return candidates

    def find_profile_match(self, investor):
        matches = self.find_profile_matches(investor)
        if not matches:
            return None
        # Exact PAN + identical details is the only strong automatic match.
        exact = [m for m in matches if m.get('match_type')=='pan' and not m.get('details_differ')]
        if exact:
            return exact[0]
        return matches[0]

    def add_profile_email(self, profile_id, email):
        email=str(email or '').strip().lower()
        if not email: return
        row=self.get_profile(profile_id)
        if not row: raise ValueError('profile not found')
        emails=json.loads(row['emails_json'] or '[]')
        if email not in emails: emails.append(email)
        with self._lock, self.conn() as c:
            c.execute('UPDATE profiles SET emails_json=?, updated_at=? WHERE id=?',(json.dumps(sorted(set(emails))),iso_now(),profile_id))

    def resolve_or_create_scheme(self, scheme_code=None, scheme_name=None, isin=None, create=True):
        code = str(scheme_code or '').strip() or None
        name = str(scheme_name or '').strip()
        isin_norm = str(isin or '').strip().upper() or None
        normalized_name = self.normalize_scheme_name_for_match(name)
        with self.conn() as c:
            row = None
            if isin_norm:
                row = c.execute('SELECT * FROM schemes WHERE UPPER(isin)=? LIMIT 1', (isin_norm,)).fetchone()
            elif code:
                row = c.execute('SELECT * FROM schemes WHERE scheme_code=? LIMIT 1', (code,)).fetchone()
            elif normalized_name:
                row = c.execute('SELECT * FROM schemes WHERE normalized_name=? LIMIT 1', (normalized_name,)).fetchone()
            now = iso_now()
            if row:
                c.execute("""UPDATE schemes SET isin=COALESCE(NULLIF(?,' '), isin), scheme_code=COALESCE(NULLIF(?,' '), scheme_code), scheme_name=COALESCE(NULLIF(?,' '), scheme_name), normalized_name=COALESCE(NULLIF(?,' '), normalized_name), last_seen_at=? WHERE id=?""",
                          (isin_norm or ' ', code or ' ', name or ' ', normalized_name or ' ', now, row['id']))
                return row['id']
            if not create:
                return None
            cur = c.execute('''INSERT INTO schemes(isin,scheme_code,scheme_name,normalized_name,first_seen_at,last_seen_at)
                               VALUES(?,?,?,?,?,?)''',
                            (isin_norm, code, name or 'Unknown Scheme', normalized_name, now, now))
            return cur.lastrowid

    def update_scheme_metadata(self, scheme_code, metadata):
        code = str(scheme_code or '').strip()
        if not code or not isinstance(metadata, dict):
            return False
        with self._lock, self.conn() as c:
            cur = c.execute('SELECT id FROM schemes WHERE scheme_code=? ORDER BY id LIMIT 1', (code,)).fetchone()
            if not cur:
                return False
            c.execute("UPDATE schemes SET fund_house=?, scheme_type=?, scheme_category=?, isin_growth=?, isin_div_reinvestment=?, metadata_updated_at=? WHERE scheme_code=?",
                      (metadata.get('fundHouse'), metadata.get('schemeType'), metadata.get('schemeCategory'),
                       metadata.get('isinGrowth'), metadata.get('isinDivReinvestment'), iso_now(), code))
            return True

    def _sync_sip_schedule(self, c, holding_id, amount, sip_day, enabled, effective_from=None, source='unknown'):
        amount = float(amount or 0)
        day = int(sip_day) if sip_day not in (None, '') else None
        rows = c.execute('SELECT * FROM sip_schedules WHERE holding_id=? AND is_active=1 ORDER BY id DESC', (holding_id,)).fetchall()
        if enabled and amount > 0 and day:
            if rows and self._amount_close(rows[0]['amount'], amount) and int(rows[0]['sip_day']) == day:
                return rows[0]['id']
            if rows:
                c.execute('UPDATE sip_schedules SET is_active=0, effective_to=? WHERE id=?',
                          (effective_from or date.today().isoformat(), rows[0]['id']))
            cur = c.execute('''INSERT INTO sip_schedules(holding_id,amount,sip_day,frequency,effective_from,effective_to,is_active,source,created_at)
                               VALUES(?,?,?,?,?,?,?,?,?)''',
                            (holding_id, amount, day, 'monthly', effective_from, None, 1, source, iso_now()))
            return cur.lastrowid
        if rows:
            c.execute('UPDATE sip_schedules SET is_active=0, effective_to=? WHERE id=?',
                      (effective_from or date.today().isoformat(), rows[0]['id']))
        return None

    def all_funds(self, profile_id=None):
        with self.conn() as c:
            sql = '''SELECT h.*, json_array(h.folio) AS folios_json, s.scheme_code AS canonical_scheme_code, s.scheme_name AS canonical_scheme_name,
                            s.isin AS canonical_isin, s.id AS canonical_scheme_id, s.fund_house, s.scheme_type, s.scheme_category,
                            s.isin_growth, s.isin_div_reinvestment, s.metadata_updated_at
                     FROM holdings h JOIN schemes s ON s.id=h.scheme_id'''
            if profile_id is None:
                return c.execute(sql).fetchall()
            return c.execute(sql + ' WHERE h.profile_id=?', (profile_id,)).fetchall()

    def get_fund(self, holding_id: int):
        with self.conn() as c:
            return c.execute('''SELECT h.*, json_array(h.folio) AS folios_json, s.scheme_code AS canonical_scheme_code, s.scheme_name AS canonical_scheme_name,
                                       s.isin AS canonical_isin, s.id AS canonical_scheme_id
                                FROM holdings h JOIN schemes s ON s.id=h.scheme_id WHERE h.id=?''', (holding_id,)).fetchone()

    def get_fund_by_code(self, code: str, profile_id=None):
        with self.conn() as c:
            base = 'SELECT h.*, json_array(h.folio) AS folios_json FROM holdings h WHERE h.scheme_code=?'
            if profile_id is None:
                return c.execute(base + ' LIMIT 1', (str(code),)).fetchone()
            return c.execute(base + ' AND h.profile_id=? LIMIT 1', (str(code), profile_id)).fetchone()

    def _holding_folio(self, payload):
        folios = payload.get('folios') or []
        if len(folios) > 1:
            raise ValueError('A holding record can contain only one folio.')
        return self.raw_folio(folios[0]) if folios else None

    def _insert_holding(self, c, payload, transactions=None, source='import'):
        profile_id = int(payload.get('profile_id', 1))
        folio = self._holding_folio(payload)
        normalized_folio = self.normalize_folio(folio)
        scheme_id = self.resolve_or_create_scheme(payload.get('scheme_code'), payload.get('scheme_name'), payload.get('isin'), True)
        now = iso_now()
        cur = c.execute('''INSERT INTO holdings
            (profile_id,scheme_id,folio,normalized_folio,scheme_code,scheme_name,isin,units,invested,sip_enabled,sip_amount,sip_day,initial_date,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (profile_id, scheme_id, folio, normalized_folio, payload.get('scheme_code') or '', payload['scheme_name'], payload.get('isin'),
             payload.get('units', 0), payload.get('invested', 0),
             1 if payload.get('sip_enabled') else 0, payload.get('sip_amount', 0), payload.get('sip_day'), payload.get('initial_date'), now, now))
        holding_id = cur.lastrowid
        if payload.get('sip_enabled') and payload.get('sip_amount') and payload.get('sip_day'):
            self._sync_sip_schedule(c, holding_id, payload.get('sip_amount'), payload.get('sip_day'), True, payload.get('initial_date'), source)
        for tx in transactions or []:
            c.execute('''INSERT INTO transactions(holding_id,txn_type,txn_date,amount,cashflow,units,nav,charges,folio,source,note,raw_type_text,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (holding_id, tx['txn_type'], tx['txn_date'], tx.get('amount', 0), tx.get('cashflow', tx.get('amount', 0)),
                       tx.get('units', 0), tx.get('nav'), tx.get('charges', 0), tx.get('folio') or folio,
                       tx.get('source', source), tx.get('note'), tx.get('raw_type_text'), now))
        return holding_id

    def seed_fund(self, payload, history):
        folios = [self.raw_folio(f) for f in (payload.get('folios') or []) if self.raw_folio(f)]
        if len(folios) <= 1:
            with self._lock, self.conn() as c:
                return self._insert_holding(c, payload, history, 'seed')
        groups = {self.normalize_folio(f): [] for f in folios}
        for tx in history or []:
            groups.setdefault(self.normalize_folio(tx.get('folio')), []).append(tx)
        first_id = None
        for folio in folios:
            child = dict(payload)
            child['folios'] = [folio]
            child['transactions'] = groups.get(self.normalize_folio(folio), [])
            with self._lock, self.conn() as c:
                hid = self._insert_holding(c, child, child['transactions'], 'seed')
            if first_id is None:
                first_id = hid
        return first_id

    def insert_fund(self, payload):
        with self._lock, self.conn() as c:
            holding_id = self._insert_holding(c, payload, [], 'manual')
            if payload.get('invested', 0) > 0 or payload.get('units', 0) > 0:
                now = iso_now()
                init_date = payload.get('initial_date') or date.today().isoformat()
                folio = self._holding_folio(payload)
                c.execute('''INSERT INTO transactions(holding_id,txn_type,txn_date,amount,cashflow,units,nav,charges,folio,source,note,raw_type_text,created_at)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (holding_id, 'initial', init_date, payload['invested'], -payload['invested'], payload['units'], None, 0, folio,
                           'manual', 'Initial holding', None, now))
            return holding_id

    def update_fund(self, holding_id, payload):
        now = iso_now()
        with self._lock, self.conn() as c:
            scheme_id = self.resolve_or_create_scheme(payload.get('scheme_code'), payload.get('scheme_name'), payload.get('isin'), True)
            folio = self._holding_folio(payload)
            normalized_folio = self.normalize_folio(folio)
            c.execute('''UPDATE holdings SET scheme_id=?,scheme_code=?,scheme_name=?,isin=?,folio=?,normalized_folio=?,
                         units=?,invested=?,sip_enabled=?,sip_amount=?,sip_day=?,initial_date=?,updated_at=? WHERE id=?''',
                      (scheme_id, payload.get('scheme_code') or '', payload['scheme_name'], payload.get('isin'), folio,
                       normalized_folio, payload['units'], payload['invested'],
                       1 if payload.get('sip_enabled') else 0, payload.get('sip_amount', 0), payload.get('sip_day'), payload.get('initial_date'), now, holding_id))
            self._sync_sip_schedule(c, holding_id, payload.get('sip_amount'), payload.get('sip_day'), bool(payload.get('sip_enabled')), payload.get('initial_date'), 'manual')

    def rebuild_sip_schedule(self, holding_id, new_amount, new_day, replace_from, generated):
        now = iso_now()
        with self._lock, self.conn() as c:
            fund = c.execute('SELECT * FROM holdings WHERE id=?', (holding_id,)).fetchone()
            if not fund:
                raise ValueError('fund not found')
            old_rows = c.execute("SELECT amount,units FROM transactions WHERE holding_id=? AND txn_type='sip' AND txn_date>=? ORDER BY txn_date,id", (holding_id, replace_from.isoformat())).fetchall()
            removed_amount = sum(float(r['amount']) for r in old_rows)
            removed_units = sum(float(r['units']) for r in old_rows)
            added_amount = sum(float(t.get('amount', 0)) for t in generated)
            added_units = sum(float(t.get('units', 0)) for t in generated)
            c.execute("DELETE FROM transactions WHERE holding_id=? AND txn_type='sip' AND txn_date>=?", (holding_id, replace_from.isoformat()))
            for tx in generated:
                c.execute('''INSERT INTO transactions(holding_id,txn_type,txn_date,amount,cashflow,units,nav,charges,folio,source,note,raw_type_text,created_at)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (holding_id, 'sip', tx['txn_date'], tx.get('amount', 0), tx.get('cashflow', -(float(tx.get('amount', 0)) + float(tx.get('charges', 0)))),
                           tx.get('units', 0), tx.get('nav'), tx.get('charges', 0), tx.get('folio'), 'sip_edit', tx.get('note'), None, now))
            new_units = float(fund['units'] or 0) - removed_units + added_units
            new_invested = float(fund['invested'] or 0) - removed_amount + added_amount
            if new_units < -1e-9 or new_invested < -1e-9:
                raise ValueError('SIP edit would make current holding values negative')
            c.execute('UPDATE holdings SET units=?,invested=?,sip_enabled=1,sip_amount=?,sip_day=?,last_sip_cycle=NULL,last_sip_nav_date=NULL,updated_at=? WHERE id=?',
                      (new_units, new_invested, float(new_amount), int(new_day), now, holding_id))
            self._sync_sip_schedule(c, holding_id, new_amount, new_day, True, replace_from.isoformat(), 'user_confirmed_change')

    def update_nav(self, holding_id, values):
        with self._lock, self.conn() as c:
            c.execute('''UPDATE holdings SET last_nav=?,last_nav_date=?,previous_nav=?,previous_nav_date=?,month_nav=?,month_nav_date=?,year_nav=?,year_nav_date=?,last_refresh=?,last_error=?,updated_at=? WHERE id=?''',
                      (values.get('last_nav'), values.get('last_nav_date'), values.get('previous_nav'), values.get('previous_nav_date'),
                       values.get('month_nav'), values.get('month_nav_date'), values.get('year_nav'), values.get('year_nav_date'),
                       values.get('last_refresh'), values.get('last_error'), iso_now(), holding_id))

    def nav_reference(self, scheme_code):
        code = str(scheme_code or '').strip()
        if not code:
            return None
        with self.conn() as c:
            return c.execute('SELECT * FROM nav_reference WHERE scheme_code=?', (code,)).fetchone()

    def upsert_nav_reference(self, values):
        code = str(values.get('scheme_code') or '').strip()
        if not code:
            raise ValueError('scheme_code is required')
        now = values.get('updated_at') or iso_now()
        with self._lock, self.conn() as c:
            c.execute('''INSERT INTO nav_reference(
                scheme_code,latest_nav_date,latest_nav,previous_nav_date,previous_nav,
                month_nav_date,month_nav,year_nav_date,year_nav,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(scheme_code) DO UPDATE SET
                latest_nav_date=excluded.latest_nav_date, latest_nav=excluded.latest_nav,
                previous_nav_date=excluded.previous_nav_date, previous_nav=excluded.previous_nav,
                month_nav_date=excluded.month_nav_date, month_nav=excluded.month_nav,
                year_nav_date=excluded.year_nav_date, year_nav=excluded.year_nav,
                updated_at=excluded.updated_at''', (
                code, values.get('latest_nav_date'), values.get('latest_nav'),
                values.get('previous_nav_date'), values.get('previous_nav'),
                values.get('month_nav_date'), values.get('month_nav'),
                values.get('year_nav_date'), values.get('year_nav'), now))

    def upsert_nav_references_atomic(self, records):
        """Atomically persist complete reference records and sync owned holdings."""
        if not records:
            return
        now = iso_now()
        with self._lock, self.conn() as c:
            for values in records:
                code = str(values.get('scheme_code') or '').strip()
                if not code:
                    raise ValueError('scheme_code is required')
                c.execute('''INSERT INTO nav_reference(
                    scheme_code,latest_nav_date,latest_nav,previous_nav_date,previous_nav,
                    month_nav_date,month_nav,year_nav_date,year_nav,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(scheme_code) DO UPDATE SET
                    latest_nav_date=excluded.latest_nav_date, latest_nav=excluded.latest_nav,
                    previous_nav_date=excluded.previous_nav_date, previous_nav=excluded.previous_nav,
                    month_nav_date=excluded.month_nav_date, month_nav=excluded.month_nav,
                    year_nav_date=excluded.year_nav_date, year_nav=excluded.year_nav,
                    updated_at=excluded.updated_at''', (
                    code, values.get('latest_nav_date'), values.get('latest_nav'),
                    values.get('previous_nav_date'), values.get('previous_nav'),
                    values.get('month_nav_date'), values.get('month_nav'),
                    values.get('year_nav_date'), values.get('year_nav'),
                    values.get('updated_at') or now))

                c.execute('''UPDATE holdings
                             SET last_nav=?, last_nav_date=?, previous_nav=?, previous_nav_date=?,
                                 month_nav=?, month_nav_date=?, year_nav=?, year_nav_date=?, updated_at=?
                             WHERE scheme_code=?
                               AND (last_nav_date IS NULL OR last_nav_date <= ?)''', (
                    values.get('latest_nav'), values.get('latest_nav_date'),
                    values.get('previous_nav'), values.get('previous_nav_date'),
                    values.get('month_nav'), values.get('month_nav_date'),
                    values.get('year_nav'), values.get('year_nav_date'), now,
                    code, values.get('latest_nav_date')))

    def all_nav_references(self):
        with self.conn() as c:
            return c.execute('SELECT * FROM nav_reference ORDER BY scheme_code').fetchall()

    def set_sip_executed(self, holding_id, cycle, nav_date):
        with self._lock, self.conn() as c:
            c.execute('UPDATE holdings SET last_sip_cycle=?,last_sip_nav_date=?,updated_at=? WHERE id=?', (cycle, nav_date, iso_now(), holding_id))

    def record_sip_execution(self, fund, sip_date, nav_date, amount, units, nav, executed_at=None):
        executed_at = executed_at or iso_now()
        with self._lock, self.conn() as c:
            c.execute('''INSERT INTO sip_executions(holding_id,profile_id,fund_name,sip_date,nav_date,amount,units,nav,executed_at)
                         VALUES(?,?,?,?,?,?,?,?,?)''',
                      (fund['id'], fund['profile_id'], fund['scheme_name'], sip_date, nav_date, float(amount), float(units),
                       float(nav) if nav is not None else None, executed_at))

    def record_live_sip_execution(self, fund, sip_date, nav_date, amount, units, nav, executed_at=None, execution_date=None):
        """Persist the dedicated live SIP execution record used by SIP Executed Today."""
        executed_at = executed_at or iso_now()
        execution_date = str(execution_date or executed_at[:10])
        with self._lock, self.conn() as c:
            c.execute('''INSERT OR IGNORE INTO live_sip_executions
                (holding_id,profile_id,fund_name,sip_date,nav_date,amount,units,nav,execution_date,executed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (fund['id'], fund['profile_id'], fund['scheme_name'], sip_date, nav_date, float(amount), float(units),
                 float(nav) if nav is not None else None, execution_date, executed_at))
            row = c.execute('''SELECT * FROM live_sip_executions
                               WHERE holding_id=? AND sip_date=?''', (fund['id'], sip_date)).fetchone()
            if not row:
                raise RuntimeError('Live SIP execution record could not be persisted')
            return row

    def live_sip_execution_for_cycle(self, holding_id, sip_date):
        with self.conn() as c:
            return c.execute(
                'SELECT * FROM live_sip_executions WHERE holding_id=? AND sip_date=? ORDER BY id LIMIT 1',
                (holding_id, sip_date)
            ).fetchone()

    def sip_execution_for_cycle(self, holding_id, sip_date):
        with self.conn() as c:
            return c.execute(
                'SELECT * FROM sip_executions WHERE holding_id=? AND sip_date=? ORDER BY id LIMIT 1',
                (holding_id, sip_date)
            ).fetchone()

    def live_sip_transaction_for_cycle(self, holding_id, sip_date):
        """Find the live SIP transaction using its stable scheduled-cycle note marker."""
        pattern = f"Scheduled SIP for {sip_date} (%"
        with self.conn() as c:
            return c.execute(
                """SELECT * FROM transactions
                   WHERE holding_id=? AND txn_type='sip' AND source='sip_execution'
                     AND note LIKE ?
                   ORDER BY id LIMIT 1""",
                (holding_id, pattern)
            ).fetchone()

    def live_sip_executions(self, profile_id=None, execution_date=None):
        with self.conn() as c:
            if profile_id is None and execution_date is None:
                return c.execute('SELECT * FROM live_sip_executions ORDER BY execution_date,executed_at,id').fetchall()
            if profile_id is None:
                return c.execute('SELECT * FROM live_sip_executions WHERE execution_date=? ORDER BY executed_at,id', (execution_date,)).fetchall()
            if execution_date is None:
                return c.execute('SELECT * FROM live_sip_executions WHERE profile_id=? ORDER BY execution_date,executed_at,id', (profile_id,)).fetchall()
            return c.execute('''SELECT * FROM live_sip_executions
                                WHERE profile_id=? AND execution_date=?
                                ORDER BY executed_at,id''', (profile_id, execution_date)).fetchall()

    def sip_executions(self, profile_id=None):
        with self.conn() as c:
            if profile_id is None:
                return c.execute('SELECT * FROM sip_executions ORDER BY executed_at,id').fetchall()
            return c.execute('SELECT * FROM sip_executions WHERE profile_id=? ORDER BY executed_at,id', (profile_id,)).fetchall()

    def sip_transactions(self, profile_id=None):
        with self.conn() as c:
            if profile_id is None:
                return c.execute('''SELECT t.* FROM transactions t JOIN holdings h ON h.id=t.holding_id
                                    WHERE t.txn_type='sip' ORDER BY t.txn_date,t.id''').fetchall()
            return c.execute('''SELECT t.* FROM transactions t JOIN holdings h ON h.id=t.holding_id
                                WHERE t.txn_type='sip' AND h.profile_id=? ORDER BY t.txn_date,t.id''', (profile_id,)).fetchall()

    def sip_executions_for_holding(self, holding_id, unreconciled=False):
        with self.conn() as c:
            if unreconciled:
                return c.execute('SELECT * FROM sip_executions WHERE holding_id=? AND reconciled_with_cams=0 ORDER BY executed_at,id', (holding_id,)).fetchall()
            return c.execute('SELECT * FROM sip_executions WHERE holding_id=? ORDER BY executed_at,id', (holding_id,)).fetchall()

    def set_sip_cycle_accounted(self, holding_id, cycle):
        with self._lock, self.conn() as c:
            c.execute('UPDATE holdings SET last_sip_cycle=?,last_sip_nav_date=NULL,updated_at=? WHERE id=?', (cycle, iso_now(), holding_id))

    @staticmethod
    def _remaining_invested_after_outflow(current_units, current_invested, outgoing_units):
        """Reduce invested cost in proportion to the units leaving the holding."""
        current_units = float(current_units or 0.0)
        current_invested = float(current_invested or 0.0)
        outgoing_units = float(outgoing_units or 0.0)
        if outgoing_units <= 0.0:
            return current_invested
        if current_units <= 1e-12 or outgoing_units >= current_units - 1e-12:
            return 0.0
        remaining_units = current_units - outgoing_units
        return max(0.0, current_invested * (remaining_units / current_units))

    @staticmethod
    def _transaction_value_state_before(transactions, transaction_id):
        """Reconstruct units/cost immediately before one transaction from ledger history."""
        units, invested = 0.0, 0.0
        for tx in transactions:
            if int(tx['id']) == int(transaction_id):
                break
            t = str(tx['txn_type'] or '')
            u = float(tx['units'] or 0.0)
            a = abs(float(tx['amount'] or 0.0))
            if t in ('sip', 'lumpsum', 'buy', 'initial'):
                units += u; invested += a
            elif t in ('sell', 'switch_out'):
                outgoing = abs(u)
                invested = Database._remaining_invested_after_outflow(units, invested, outgoing)
                units += u
            elif t == 'switch_in':
                units += u
            elif t == 'adjustment':
                units += u; invested += float(tx['amount'] or 0.0)
            elif t == 'fee':
                invested += a
        return units, invested

    @staticmethod
    def _reverse_transaction_state_after_target(transactions, transaction_id, current_units, current_invested):
        """Reverse transactions after the target using the authoritative current holding state."""
        rows = list(transactions)
        target_index = next((i for i, tx in enumerate(rows) if int(tx['id']) == int(transaction_id)), None)
        if target_index is None:
            return None
        units = float(current_units or 0.0)
        invested = float(current_invested or 0.0)
        for tx in reversed(rows[target_index + 1:]):
            t = str(tx['txn_type'] or '')
            u = float(tx['units'] or 0.0)
            a = abs(float(tx['amount'] or 0.0))
            if t in ('sip', 'lumpsum', 'buy', 'initial'):
                units -= u; invested -= a
            elif t in ('sell', 'switch_out'):
                outgoing = abs(u)
                if outgoing <= 1e-12:
                    continue
                if units <= 1e-12:
                    # A later full exit reset the holding; anything before it cannot change
                    # today's holding state after that reset.
                    return units, invested, True
                pre_units = units + outgoing
                invested = invested * (pre_units / units)
                units = pre_units
            elif t == 'switch_in':
                units -= u
            elif t == 'adjustment':
                units -= u; invested -= float(tx['amount'] or 0.0)
            elif t == 'fee':
                invested -= a
            if units < -1e-8 or invested < -1e-8:
                return None
        return max(0.0, units), max(0.0, invested), False

    @staticmethod
    def _replay_without_transaction(transactions, target_id, start_units=0.0, start_invested=0.0):
        """Replay transaction accounting while omitting one transaction."""
        units = float(start_units or 0.0)
        invested = float(start_invested or 0.0)
        for tx in transactions:
            if int(tx['id']) == int(target_id):
                continue
            t = str(tx['txn_type'] or '')
            u = float(tx['units'] or 0.0)
            a = abs(float(tx['amount'] or 0.0))
            if t in ('sip', 'lumpsum', 'buy', 'initial'):
                units += u; invested += a
            elif t in ('sell', 'switch_out'):
                outgoing = abs(u)
                invested = Database._remaining_invested_after_outflow(units, invested, outgoing)
                units += u
            elif t == 'switch_in':
                units += u
            elif t == 'adjustment':
                units += u; invested += float(tx['amount'] or 0.0)
            elif t == 'fee':
                invested += a
            if units < -1e-8 or invested < -1e-8:
                raise ValueError('transaction history is inconsistent; cannot safely rebuild holding')
        return max(0.0, units), max(0.0, invested)

    def add_transaction(self, holding_id, txn_type, txn_date, amount, units, nav, note, charges=0.0, folio=None, source='manual'):
        now = iso_now()
        with self._lock, self.conn() as c:
            row = c.execute('SELECT units,invested,folio FROM holdings WHERE id=?', (holding_id,)).fetchone()
            if not row:
                raise ValueError('fund not found')
            current_units = float(row['units']); current_invested = float(row['invested']); amount = float(amount); units = float(units); charges = float(charges or 0)
            if txn_type in ('sip','buy','lumpsum'):
                new_units=current_units+units; new_invested=current_invested+amount; cashflow=-(amount+charges); unit_delta=units
            elif txn_type == 'sell':
                new_units=current_units-units; new_invested=self._remaining_invested_after_outflow(current_units,current_invested,units); cashflow=amount-charges; unit_delta=-units
                if new_units < -1e-9:
                    raise ValueError('selling more units than currently held')
            elif txn_type == 'switch_in':
                new_units=current_units+units; new_invested=current_invested; cashflow=0.0; unit_delta=units
            elif txn_type == 'switch_out':
                new_units=current_units-units; new_invested=self._remaining_invested_after_outflow(current_units,current_invested,units); cashflow=0.0; unit_delta=-units
                if new_units < -1e-9:
                    raise ValueError('switching out more units than currently held')
            elif txn_type == 'adjustment':
                new_units=current_units+units; new_invested=current_invested+amount; cashflow=-amount; unit_delta=units
            elif txn_type == 'fee':
                new_units=current_units; new_invested=current_invested+amount; cashflow=-amount; unit_delta=0.0
            else:
                raise ValueError('invalid transaction type')
            if new_units < -1e-9 or new_invested < -1e-9:
                raise ValueError('transaction would make holdings negative')
            c.execute('''INSERT INTO transactions(holding_id,txn_type,txn_date,amount,cashflow,units,nav,charges,folio,source,note,raw_type_text,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (holding_id,txn_type,txn_date,amount,cashflow,unit_delta,nav,charges,folio or row['folio'],source,note,None,now))
            c.execute('UPDATE holdings SET units=?,invested=?,updated_at=? WHERE id=?',(new_units,new_invested,now,holding_id))

    def delete_transaction(self, holding_id, transaction_id):
        'Delete one transaction and reverse its effect on the holding.'
        with self._lock, self.conn() as c:
            tx = c.execute('SELECT * FROM transactions WHERE id=? AND holding_id=?', (transaction_id, holding_id)).fetchone()
            if not tx:
                raise ValueError('transaction not found')
            hold = c.execute('SELECT units,invested FROM holdings WHERE id=?', (holding_id,)).fetchone()
            if not hold:
                raise ValueError('fund not found')

            txn_type = str(tx['txn_type'] or '')
            tx_units = float(tx['units'] or 0)
            tx_amount = abs(float(tx['amount'] or 0))
            current_units = float(hold['units'] or 0)
            current_invested = float(hold['invested'] or 0)

            if txn_type in ('sip', 'lumpsum', 'buy', 'initial'):
                new_units = current_units - tx_units
                new_invested = current_invested - tx_amount
            elif txn_type in ('sell', 'switch_out'):
                tx_rows = c.execute('SELECT * FROM transactions WHERE holding_id=? ORDER BY txn_date,id', (holding_id,)).fetchall()
                outgoing_units = abs(tx_units)
                reverse_state = self._reverse_transaction_state_after_target(tx_rows, transaction_id, current_units, current_invested)
                if reverse_state and reverse_state[2]:
                    new_units, new_invested = current_units, current_invested
                else:
                    pre_units, pre_invested = self._transaction_value_state_before(tx_rows, transaction_id)
                    if reverse_state is not None:
                        target_after_units, target_after_invested = reverse_state[0], reverse_state[1]
                    else:
                        target_after_units, target_after_invested = current_units, current_invested
                    if outgoing_units <= 1e-12:
                        new_units, new_invested = current_units, current_invested
                    elif target_after_units > 1e-12:
                        target_pre_units = target_after_units + outgoing_units
                        target_pre_invested = target_after_invested * (target_pre_units / target_after_units)
                        target_index = next(i for i, row2 in enumerate(tx_rows) if int(row2['id']) == int(transaction_id))
                        try:
                            new_units, new_invested = self._replay_without_transaction(
                                tx_rows[target_index:], transaction_id, target_pre_units, target_pre_invested
                            )
                        except ValueError:
                            raise
                    elif pre_units > 1e-12 and pre_invested > 0.0:
                        target_index = next(i for i, row2 in enumerate(tx_rows) if int(row2['id']) == int(transaction_id))
                        new_units, new_invested = self._replay_without_transaction(
                            tx_rows[:target_index] + tx_rows[target_index + 1:], transaction_id, 0.0, 0.0
                        )
                    else:
                        raise ValueError('Cannot safely delete this full sell/switch-out because its pre-transaction cost basis is not reconstructible.')
            elif txn_type == 'adjustment':
                new_units = current_units - tx_units
                new_invested = current_invested - float(tx['amount'] or 0)
            elif txn_type == 'fee':
                new_units = current_units
                new_invested = current_invested - tx_amount
            else:
                raise ValueError('unsupported transaction type')

            if new_units < -1e-8 or new_invested < -1e-8:
                raise ValueError('deleting this transaction would make the holding negative')

            is_live_sip = str(tx['source'] or '') == 'sip_execution' and txn_type == 'sip'

            # A deleted live SIP is converted into a zero-value SIP marker. The
            # marker remains in the ledger so the SIP scheduler sees this cycle as
            # accounted, but it has no financial effect. The dedicated execution
            # rows are removed so the live 'Executed Today' status turns off.
            if is_live_sip:
                c.execute(
                    'DELETE FROM sip_executions WHERE holding_id=? AND nav_date=? '
                    'AND ABS(amount-?) <= ? AND ABS(units-?) <= ?',
                    (holding_id, tx['txn_date'], tx_amount,
                     max(0.02, tx_amount * 0.001), abs(tx_units),
                     max(0.0002, abs(tx_units) * 0.001))
                )
                c.execute(
                    'DELETE FROM live_sip_executions WHERE holding_id=? AND nav_date=? '
                    'AND ABS(amount-?) <= ? AND ABS(units-?) <= ?',
                    (holding_id, tx['txn_date'], tx_amount,
                     max(0.02, tx_amount * 0.001), abs(tx_units),
                     max(0.0002, abs(tx_units) * 0.001))
                )

                c.execute(
                    '''UPDATE transactions
                       SET amount=0.0, cashflow=0.0, units=0.0, nav=NULL, charges=0.0,
                           note=?
                       WHERE id=? AND holding_id=?''',
                    (f'SIP skipped/deleted for {tx["txn_date"]}', transaction_id, holding_id)
                )
                updated = c.execute('SELECT * FROM transactions WHERE id=? AND holding_id=?', (transaction_id, holding_id)).fetchone()
            else:
                c.execute('DELETE FROM transactions WHERE id=? AND holding_id=?', (transaction_id, holding_id))
                updated = None

            c.execute(
                'UPDATE holdings SET units=?, invested=?, updated_at=? WHERE id=?',
                (max(0.0, new_units), max(0.0, new_invested), iso_now(), holding_id)
            )
            return dict(updated or tx)

    def import_fund(self,payload,transactions):
        with self._lock, self.conn() as c:
            return self._insert_holding(c, payload, transactions, 'import')

    @staticmethod
    def raw_folio(value):
        if value is None:
            return None
        v = str(value).strip()
        return v or None

    @staticmethod
    def normalize_folio(value):
        """Normalize folio strings for comparison without losing the raw value."""
        if value is None:
            return None
        v = str(value).strip().replace(' ', '')
        if not v:
            return None
        # Only /0 is equivalent to the bare folio. Other sub-account suffixes remain distinct.
        if v.endswith('/0'):
            v = v[:-2]
        return v or None

    @staticmethod
    def normalize_scheme_name_for_match(value):
        v = str(value or '').strip().lower()
        v = re.sub(r'[^a-z0-9]+', ' ', v)
        v = re.sub(r'\s+', ' ', v).strip()
        return v

    @staticmethod
    def _amount_close(a, b):
        try:
            aa, bb = float(a or 0), float(b or 0)
        except Exception:
            return False
        return abs(aa - bb) <= max(0.01, 0.0001 * max(abs(aa), abs(bb)))

    @staticmethod
    def _units_close(a, b):
        try:
            return abs(float(a or 0) - float(b or 0)) <= 0.001
        except Exception:
            return False

    def transaction_is_duplicate(self, incoming, existing_rows):
        """Determine whether an incoming transaction is already represented.

        Exact date/type plus tolerant amount/units is the default. NAV/charges are
        deliberately not identity fields because statement formatting can vary.
        """
        itype = str(incoming.get('txn_type') or '')
        idate = str(incoming.get('txn_date') or '')
        if not itype or not idate:
            return False
        if itype == 'sip':
            for row in existing_rows:
                if str(row['txn_type'] or '') != 'sip' or str(row['txn_date'] or '') != idate:
                    continue
                if self._amount_close(incoming.get('amount'), row['amount']) and self._units_close(incoming.get('units'), row['units']):
                    if self.normalize_folio(incoming.get('folio')) == self.normalize_folio(row['folio']):
                        return True
            return False
        for row in existing_rows:
            if str(row['txn_type'] or '') != itype or str(row['txn_date'] or '') != idate:
                continue
            if self.normalize_folio(incoming.get('folio')) != self.normalize_folio(row['folio']):
                continue
            if self._amount_close(incoming.get('amount'), row['amount']) and self._units_close(incoming.get('units'), row['units']):
                return True
        return False

    def _scheme_identity_assessment(self, candidate, incoming):
        """Assess scheme identity and confidence for an existing holding candidate.

        Returns (verdict, confidence):
          MATCH / HIGH, MATCH / MEDIUM, MATCH / LOW
          MISMATCH / HIGH, MISMATCH / LOW
          INCONSISTENT / HIGH  (same scheme code, conflicting ISIN)
          INDETERMINATE / LOW

        ISIN is authoritative when present. Scheme code is a strong corroborating
        identifier but must never override an ISIN conflict. Name-only matches are
        intentionally weak and require review when they are the only evidence.
        """
        if not isinstance(candidate, dict):
            candidate = dict(candidate)
        incoming_isin = str(incoming.get('isin') or '').strip().upper()
        candidate_isin = str(candidate.get('canonical_isin') or candidate.get('isin') or '').strip().upper()
        incoming_code = str(incoming.get('scheme_code') or '').strip()
        candidate_code = str(candidate.get('canonical_scheme_code') or candidate.get('scheme_code') or '').strip()

        # Strongest rule: both sides have ISIN. Same ISIN means exact same scheme.
        if incoming_isin and candidate_isin:
            if incoming_isin == candidate_isin:
                return 'MATCH', 'HIGH'
            # An ISIN conflict under the same verified scheme code is internally
            # inconsistent and must never be auto-split.
            if incoming_code and candidate_code and incoming_code == candidate_code:
                return 'INCONSISTENT', 'HIGH'
            # Both ISINs are present and different, and therefore strongly identify
            # two distinct schemes. The caller may safely create a separate holding.
            return 'MISMATCH', 'HIGH'

        # One or both ISINs are missing. A matching verified scheme code is useful
        # corroboration, but it is weaker than an ISIN-level match.
        if incoming_code and candidate_code:
            if incoming_code == candidate_code:
                return 'MATCH', 'MEDIUM'
            return 'MISMATCH', 'LOW'

        # With only one side's ISIN present, do not manufacture a conflict from a
        # missing value. A strong existing scheme plus a name/code-consistent incoming
        # record is still a candidate for review rather than silent splitting.
        incoming_name = self.normalize_scheme_name_for_match(incoming.get('scheme_name'))
        candidate_name = self.normalize_scheme_name_for_match(candidate.get('canonical_scheme_name') or candidate.get('scheme_name'))
        if incoming_name and candidate_name:
            ratio = difflib.SequenceMatcher(None, incoming_name, candidate_name).ratio()
            if ratio >= 0.97:
                return 'MATCH', 'LOW'
            if ratio >= 0.92:
                return 'MATCH', 'LOW'

        return 'INDETERMINATE', 'LOW'

    def _verify_scheme_identity(self, candidate, incoming):
        """Backward-compatible compact scheme identity verdict."""
        verdict, _confidence = self._scheme_identity_assessment(candidate, incoming)
        if verdict == 'INCONSISTENT':
            return 'INCONSISTENT'
        return verdict

    def find_import_fund(self, profile_id, incoming):
        """Resolve an incoming holding using PAN/profile + folio + verified scheme identity.

        Folio numbers may legitimately be reused across schemes. The decision therefore
        depends on how confidently the two scheme identities are established.
        """
        if not profile_id:
            return None, 'NEW_INVESTMENT', 'No resolved investor profile.'
        incoming_folios = [self.normalize_folio(f) for f in (incoming.get('folios') or []) if self.normalize_folio(f)]
        if len(incoming_folios) > 1:
            return None, 'REVIEW_REQUIRED', 'Each import holding must resolve to exactly one folio.'
        holdings = list(self.all_funds(profile_id))

        if incoming_folios:
            nf = incoming_folios[0]
            folio_matches = [h for h in holdings if self.normalize_folio(h['normalized_folio'] or h['folio']) == nf]

            if folio_matches:
                matches = []
                weak_matches = []
                strong_mismatches = []
                weak_mismatches = []
                inconsistent = []
                for candidate in folio_matches:
                    verdict, confidence = self._scheme_identity_assessment(candidate, incoming)
                    if verdict == 'MATCH':
                        matches.append((candidate, confidence))
                    elif verdict == 'INCONSISTENT':
                        inconsistent.append(candidate)
                    elif verdict == 'MISMATCH' and confidence == 'HIGH':
                        strong_mismatches.append(candidate)
                    elif verdict == 'MISMATCH':
                        weak_mismatches.append(candidate)
                    else:
                        weak_matches.append(candidate)

                # Exact/strong scheme match wins. Multiple exact candidates mean
                # database corruption/duplicate holdings and must be reviewed.
                high_matches = [c for c, conf in matches if conf == 'HIGH']
                if len(high_matches) == 1:
                    return high_matches[0], 'EXISTING_INVESTMENT_MERGE', 'Matched by investor + normalized folio + ISIN.'
                if len(high_matches) > 1:
                    return None, 'REVIEW_REQUIRED', 'Multiple holdings match the same investor, folio and verified scheme identity.'

                # A matching scheme code with one or both ISINs missing is useful
                # evidence, but a name-only match remains too weak to silently merge
                # when multiple schemes share the same folio.
                medium_matches = [c for c, conf in matches if conf == 'MEDIUM']
                if len(medium_matches) == 1 and not inconsistent:
                    return medium_matches[0], 'EXISTING_INVESTMENT_MERGE', 'Matched by investor + folio + verified scheme code; ISIN was unavailable on one side.'
                if len(medium_matches) > 1:
                    return None, 'REVIEW_REQUIRED', 'Multiple holdings match the same investor, folio and scheme code.'

                # Same scheme code + conflicting ISIN is explicitly inconsistent.
                if inconsistent:
                    return inconsistent[0], 'REVIEW_REQUIRED', 'Same investor and folio have the same scheme code but conflicting ISIN values.'

                # Strong ISIN-to-ISIN disagreement on both sides is a legitimate
                # multi-scheme folio pattern. Create a separate holding.
                if strong_mismatches and not weak_mismatches and not weak_matches:
                    return None, 'NEW_FOLIO', 'Same folio is already used by another strongly verified scheme; creating a separate holding.'

                # Strong mismatch exists alongside weak/ambiguous evidence. Do not let
                # weak scheme resolution silently split what could be the same holding.
                if strong_mismatches:
                    return None, 'REVIEW_REQUIRED', 'Same folio exists, but scheme identities cannot be separated confidently because at least one side is weakly resolved.'

                # Name-only/weak scheme evidence is never enough to split a shared folio.
                if weak_matches or weak_mismatches:
                    return None, 'REVIEW_REQUIRED', 'Same folio exists, but scheme identity is only weakly or partially resolved.'

                return None, 'REVIEW_REQUIRED', 'Folio matches an existing holding, but the incoming scheme identity cannot be verified uniquely.'

            # No holding under this folio. Use the global scheme identity to determine
            # whether this is another folio of a scheme already held by the investor.
            incoming_isin = str(incoming.get('isin') or '').strip().upper()
            incoming_code = str(incoming.get('scheme_code') or '').strip()
            if incoming_isin:
                schemes = [h for h in holdings if str(h['canonical_isin'] or h['isin'] or '').strip().upper() == incoming_isin]
            elif incoming_code:
                schemes = [h for h in holdings if str(h['canonical_scheme_code'] or h['scheme_code'] or '').strip() == incoming_code]
            else:
                schemes = []

            # Multiple holdings can legitimately belong to the same scheme (same ISIN)
            # when the investor has multiple folios.  The incoming folio is new, so
            # this is still unambiguously a NEW_FOLIO as long as all matching holdings
            # resolve to the same canonical scheme identity.
            if schemes:
                scheme_ids = {h['canonical_scheme_id'] or h['scheme_id'] for h in schemes}
                if len(scheme_ids) == 1:
                    return schemes[0], 'NEW_FOLIO', 'Verified scheme matches an existing scheme, but the incoming folio is new.'
                return None, 'REVIEW_REQUIRED', 'The incoming scheme identity maps to multiple different scheme records for this investor.'
            # A strong structured scheme identifier (ISIN or verified scheme code)
            # must not be overridden by fuzzy name similarity. If the incoming
            # record has a verified ISIN/code and that scheme identity is not
            # already held by this investor, similar names on other schemes are
            # not evidence that the incoming record is the same scheme.
            # Name-only review is appropriate only when stronger identifiers are
            # absent.
            if not incoming_isin and not incoming_code:
                incoming_name = self.normalize_scheme_name_for_match(incoming.get('scheme_name'))
                if incoming_name:
                    named = [h for h in holdings if difflib.SequenceMatcher(None, incoming_name, self.normalize_scheme_name_for_match(h['canonical_scheme_name'] or h['scheme_name'])).ratio() >= 0.92]
                    if named:
                        return named[0], 'REVIEW_REQUIRED', 'Scheme matched only by name while the incoming folio is new; manual review is required.'
            return None, 'NEW_INVESTMENT', 'No existing folio or verified scheme match.'

        incoming_isin = str(incoming.get('isin') or '').strip().upper()
        if incoming_isin:
            candidates = [h for h in holdings if str(h['canonical_isin'] or h['isin'] or '').strip().upper() == incoming_isin]
            if len(candidates) == 1:
                return candidates[0], 'EXISTING_INVESTMENT_MERGE', 'Matched by investor + ISIN because the statement has no folio.'
            if len(candidates) > 1:
                return None, 'REVIEW_REQUIRED', 'Multiple existing holdings match the incoming ISIN and the statement has no folio.'
        incoming_code = str(incoming.get('scheme_code') or '').strip()
        if incoming_code:
            candidates = [h for h in holdings if str(h['canonical_scheme_code'] or h['scheme_code'] or '').strip() == incoming_code]
            if len(candidates) == 1:
                return candidates[0], 'EXISTING_INVESTMENT_MERGE', 'Matched by investor + scheme code because the statement has no folio.'
            if len(candidates) > 1:
                return None, 'REVIEW_REQUIRED', 'Multiple existing holdings match the incoming scheme code and the statement has no folio.'
        return None, 'NEW_INVESTMENT', 'No folio or scheme match.'

    @staticmethod
    def _transaction_value_deltas(transactions):
        """Derive per-holding current units and invested contribution from normalized transactions."""
        units = 0.0
        invested = 0.0
        for tx in transactions or []:
            t = str(tx.get('txn_type') or '')
            u = float(tx.get('units') or 0)
            a = abs(float(tx.get('amount') or 0))
            if t in ('sip', 'lumpsum', 'buy', 'initial'):
                units += u
                invested += a
            elif t == 'sell':
                invested = Database._remaining_invested_after_outflow(units, invested, abs(u))
                units += u  # sell units are already negative in normalized JSON
            elif t == 'switch_in':
                units += u
            elif t == 'switch_out':
                invested = Database._remaining_invested_after_outflow(units, invested, abs(u))
                units += u
            elif t == 'adjustment':
                units += u
                invested += float(tx.get('cashflow') or -a) * -1
            elif t == 'fee':
                invested += a
        return units, invested

    def _expand_preview_holdings(self, preview):
        """Expand each CAMS fund record into one internal holding record per folio.

        The JSON format intentionally remains scheme-oriented for compactness, but the
        database is folio-oriented. Multi-folio records are split before preview/save so
        a transaction can never belong ambiguously to more than one holding.
        """
        expanded = []
        for fund_index, fund in enumerate(preview.get('funds', [])):
            raw_folios = [self.raw_folio(f) for f in (fund.get('folios') or []) if self.raw_folio(f)]
            if len(raw_folios) <= 1:
                child = dict(fund)
                nf = self.normalize_folio(raw_folios[0]) if raw_folios else ''
                # Preserve an existing import reference so repeated calls to
                # _expand_preview_holdings() remain idempotent. This is important
                # for the import flow: the UI first receives an expanded preview,
                # then /api/import/changes validates and expands the same preview
                # again. Re-numbering refs on the second expansion can shift keys
                # after a multi-folio fund and make an otherwise valid SIP status
                # selection appear missing.
                child['_import_ref'] = str(fund.get('_import_ref') or '').strip() or f"fund:{fund_index}:folio:{nf or '-'}"
                expanded.append(child)
                continue
            groups = {self.normalize_folio(f): [] for f in raw_folios}
            unmatched = []
            for tx in fund.get('transactions') or []:
                tf = self.normalize_folio(tx.get('folio'))
                if tf in groups:
                    groups[tf].append(dict(tx))
                elif tf is None:
                    unmatched.append(tx)
                else:
                    raise ValueError(f'Multi-folio fund {fund.get("scheme_name")} contains a transaction for unknown folio {tx.get("folio")}.')
            if unmatched:
                raise ValueError(f'Multi-folio fund {fund.get("scheme_name")} has transactions without a folio; cannot safely allocate them to holdings.')
            for folio in raw_folios:
                nf = self.normalize_folio(folio)
                txs = groups.get(nf, [])
                if not txs:
                    raise ValueError(f'Multi-folio fund {fund.get("scheme_name")} has no transaction history for folio {folio}; aggregate closing values cannot safely be split.')
                units, invested = self._transaction_value_deltas(txs)
                child = dict(fund)
                child['folios'] = [folio]
                child['transactions'] = txs
                child['units'] = units
                child['invested'] = invested
                # SIP configuration is folio-scoped. A multi-folio scheme gets an active
                # schedule only on a folio that actually has SIP purchases in the statement.
                has_sip = any(str(t.get('txn_type') or '') == 'sip' for t in txs)
                child['sip_enabled'] = bool(fund.get('sip_enabled') and has_sip)
                child['_expanded_from_multi_folio'] = True
                child['_import_ref'] = f"fund:{fund_index}:folio:{nf}"
                expanded.append(child)
        result = dict(preview)
        result['funds'] = expanded
        result['counts'] = dict(preview.get('counts') or {})
        result['counts']['funds'] = len(expanded)
        result['counts']['transactions'] = sum(len(f.get('transactions') or []) for f in expanded)
        return result

    def _sip_reconciliation_candidates(self, holding_id, tx):
        tx_date = parse_optional_date(tx.get('txn_date'))
        if not tx_date:
            return []
        amount = tx.get('amount')
        candidates=[]
        for row in self.sip_executions_for_holding(holding_id, unreconciled=True):
            if not self._amount_close(amount, row['amount']):
                continue
            dates=[]
            for key in ('sip_date','nav_date'):
                if row[key]:
                    d=parse_optional_date(row[key])
                    if d: dates.append(d)
            try:
                dt=datetime.fromisoformat(str(row['executed_at']).replace('Z','+00:00'))
                dates.append(dt.date())
            except Exception:
                pass
            if any(abs((tx_date-d).days) <= 5 for d in dates):
                candidates.append(row)
        if not candidates:
            with self.conn() as c:
                legacy = c.execute("SELECT id,amount,units,txn_date,created_at FROM transactions WHERE holding_id=? AND txn_type='sip' AND source='sip_execution' ORDER BY created_at DESC,id DESC", (holding_id,)).fetchall()
            for row in legacy:
                if self._amount_close(tx.get('amount'), row['amount']):
                    d = parse_optional_date(row['txn_date'])
                    if d and abs((tx_date-d).days) <= 5:
                        candidates.append({'id': None, 'amount': row['amount'], 'units': row['units'], 'sip_date': row['txn_date'], 'nav_date': row['txn_date'], 'executed_at': row['created_at'], '_legacy_txn_id': row['id']})
        return candidates

    def _review_candidate_holdings(self, profile_id, incoming, limit=12):
        """Return safe, user-selectable holding candidates for a manual import decision."""
        if not profile_id:
            return []
        holdings = list(self.all_funds(profile_id))
        incoming_isin = str(incoming.get('isin') or '').strip().upper()
        incoming_code = str(incoming.get('scheme_code') or '').strip()
        incoming_folio = self.normalize_folio((incoming.get('folios') or [None])[0])
        incoming_name = self.normalize_scheme_name_for_match(incoming.get('scheme_name'))
        scored = []
        for h in holdings:
            score = 0
            h_isin = str(h['canonical_isin'] or h['isin'] or '').strip().upper()
            h_code = str(h['canonical_scheme_code'] or h['scheme_code'] or '').strip()
            h_folio = self.normalize_folio(h['normalized_folio'] or h['folio'])
            h_name = self.normalize_scheme_name_for_match(h['canonical_scheme_name'] or h['scheme_name'])
            if incoming_folio and h_folio == incoming_folio:
                score += 100
            if incoming_isin and h_isin == incoming_isin:
                score += 90
            if incoming_code and h_code and incoming_code == h_code:
                score += 70
            if incoming_name and h_name:
                ratio = difflib.SequenceMatcher(None, incoming_name, h_name).ratio()
                if ratio >= 0.97: score += 50
                elif ratio >= 0.92: score += 35
            if score <= 0:
                continue
            nav = float(h['last_nav'] or 0)
            units = float(h['units'] or 0)
            scored.append((score, h, nav * units))
        scored.sort(key=lambda x: (-x[0], str(x[1]['canonical_scheme_name'] or x[1]['scheme_name']), str(x[1]['folio'] or '')))
        result=[]
        seen=set()
        for score,h,value in scored:
            if h['id'] in seen:
                continue
            seen.add(h['id'])
            result.append({
                'id': h['id'],
                'scheme_name': h['canonical_scheme_name'] or h['scheme_name'],
                'scheme_code': h['canonical_scheme_code'] or h['scheme_code'],
                'isin': h['canonical_isin'] or h['isin'],
                'folio': h['folio'],
                'units': h['units'],
                'invested': h['invested'],
                'nav': h['last_nav'],
                'nav_date': h['last_nav_date'],
                'current_value': value,
                'match_score': score,
            })
            if len(result) >= limit:
                break
        return result

    def preview_import_changes(self, preview, profile_mapping=None, sip_status_mapping=None):
        """Return a pure, read-only ProposedChangeSet for the normalized holdings model."""
        preview=self._expand_preview_holdings(preview)
        profile_mapping=profile_mapping or {}
        sip_status_mapping=sip_status_mapping or {}
        resolved_profiles={}
        for inv in preview.get('investors',[]):
            ref=inv['ref']; choice=profile_mapping.get(ref)
            if choice is None:
                status=inv.get('identity_status'); match=inv.get('match')
                if status=='pan_match_same' and match: choice={'mode':'existing','profile_id':match['id']}
                elif status=='no_match': choice={'mode':'new'}
                else: raise ValueError(f'Investor identity for {inv.get("name") or "statement investor"} needs a merge/new-profile decision.')
            if isinstance(choice,dict) and choice.get('mode')=='existing':
                pid=int(choice.get('profile_id'))
                if not self.get_profile(pid): raise ValueError(f'Investor profile not found for {inv["name"]}')
                resolved_profiles[ref]=pid
            else: resolved_profiles[ref]=None
        changes=[]
        for fund in preview.get('funds',[]):
            profile_id=resolved_profiles.get(fund.get('investor_ref'))
            if profile_id is None and len(resolved_profiles)==1: profile_id=next(iter(resolved_profiles.values()))
            fund=dict(fund); fund['profile_id']=profile_id
            if fund.get('sip_confirmation_required'):
                # preview_import_changes() belongs to Database, so resolve the
                # holding-scoped import status directly here. New multi-folio
                # imports key by _import_ref; retain scheme-code fallback for
                # older clients. Explicit False must not be lost via `or`.
                import_key = str(fund.get('_import_ref') or '').strip()
                scheme_code = str(fund.get('scheme_code') or '').strip()
                if import_key and import_key in sip_status_mapping:
                    status_value = sip_status_mapping[import_key]
                elif scheme_code and scheme_code in sip_status_mapping:
                    status_value = sip_status_mapping[scheme_code]
                else:
                    status_value = None
                if status_value is None: raise ValueError(f'SIP active/inactive confirmation is required for {fund["scheme_name"]}')
                fund['sip_enabled']=bool(status_value)
            existing, action, reason = self.find_import_fund(profile_id,fund) if profile_id else (None,'NEW_INVESTMENT','No resolved investor profile.')
            folios=[self.raw_folio(f) for f in fund.get('folios',[]) if self.raw_folio(f)]
            txs=list(fund.get('transactions') or [])
            decision_key = fund.get('_import_ref') or f"{fund.get('investor_ref')}|{folios[0] if folios else ''}|{str(fund.get('isin') or '').strip().upper()}|{str(fund.get('scheme_code') or '').strip()}|{fund.get('scheme_name','')}"
            if action=='REVIEW_REQUIRED':
                candidates=self._review_candidate_holdings(profile_id,fund)
                changes.append({
                    'type':'review_required','decision_key':decision_key,
                    'scheme_name':fund['scheme_name'],'scheme_code':fund.get('scheme_code'),'isin':fund.get('isin'),
                    'folios':folios,'transactions':0,'reason':reason,
                    'automatic_action':'review_required',
                    'automatic_label':'Needs review',
                    'override_options':['skip','merge','new'],
                    'review_options':['skip','merge','new'],
                    'merge_candidates':candidates,
                }); continue
            if action=='NEW_INVESTMENT':
                candidates=self._review_candidate_holdings(profile_id,fund)
                changes.append({'type':'new_fund','decision_key':decision_key,'scheme_name':fund['scheme_name'],'scheme_code':fund.get('scheme_code'),'isin':fund.get('isin'),'folios':folios,'transactions':len(txs),'units':fund.get('units',0),'invested':fund.get('invested',0),'reason':reason,'automatic_action':'new','automatic_label':'Create as new holding','override_options':['skip','merge','new'],'merge_candidates':candidates}); continue
            if action=='NEW_FOLIO':
                candidates=self._review_candidate_holdings(profile_id,fund)
                # A new folio must remain a separate holding; only expose merge when a
                # candidate actually represents the same folio identity.
                nf=self.normalize_folio(folios[0]) if folios else ''
                safe_candidates=[c for c in candidates if self.normalize_folio(c.get('folio'))==nf] if nf else []
                changes.append({'type':'new_folio','decision_key':decision_key,'scheme_name':fund['scheme_name'],'scheme_code':fund.get('scheme_code'),'isin':fund.get('isin'),'folios':folios,'transactions':len(txs),'new_redemptions':sum(1 for t in txs if str(t.get('txn_type') or '')=='sell'),'units':fund.get('units',0),'invested':fund.get('invested',0),'reason':reason,'automatic_action':'new_folio','automatic_label':'Create new folio','override_options':['skip'] + (['merge'] if safe_candidates else []),'merge_candidates':safe_candidates,'transaction_details':[{'date':t.get('txn_date'),'type':t.get('txn_type'),'amount':t.get('amount',0),'units':t.get('units',0),'nav':t.get('nav'),'folio':t.get('folio')} for t in txs]}); continue
            rows=self.transactions(existing['id']); new_txs=[]; new_red=[]; review=None
            for raw in txs:
                tx=dict(raw); tx['folio']=tx.get('folio') or existing['folio']
                if self.transaction_is_duplicate(tx,rows): continue
                if tx.get('txn_type')=='sip':
                    cand=self._sip_reconciliation_candidates(existing['id'],tx)
                    if len(cand)==1: continue
                    if len(cand)>1:
                        review=f'Ambiguous SIP reconciliation: {len(cand)} internal executions could match the incoming CAMS SIP on {tx.get("txn_date")}.'; break
                new_txs.append(tx)
                if tx.get('txn_type')=='sell': new_red.append(tx)
            if review:
                candidates=self._review_candidate_holdings(profile_id,fund)
                changes.append({'type':'review_required','decision_key':decision_key,'scheme_name':fund['scheme_name'],'scheme_code':fund.get('scheme_code'),'isin':fund.get('isin'),'folios':folios,'transactions':0,'reason':review,'automatic_action':'review_required','automatic_label':'Needs review','override_options':['skip','merge','new'],'review_options':['skip','merge','new'],'merge_candidates':candidates})
            elif new_txs:
                candidates=self._review_candidate_holdings(profile_id,fund)
                changes.append({'type':'new_transactions','decision_key':decision_key,'scheme_name':fund['scheme_name'],'scheme_code':fund.get('scheme_code'),'isin':fund.get('isin'),'folios':folios,'transactions':len(new_txs),'new_redemptions':len(new_red),'automatic_action':'merge','automatic_label':'Add new transactions','override_options':['skip'],'merge_candidates':candidates,'transaction_details':[{'date':t.get('txn_date'),'type':t.get('txn_type'),'amount':t.get('amount',0),'units':t.get('units',0),'nav':t.get('nav'),'folio':t.get('folio')} for t in new_txs]})
            else:
                changes.append({'type':'no_change','decision_key':decision_key,'scheme_name':fund['scheme_name'],'scheme_code':fund.get('scheme_code'),'folios':folios,'transactions':0,'new_redemptions':0,'automatic_action':'no_change','automatic_label':'No changes','override_options':['skip']})
        return changes

    def merge_import_fund(self, payload, transactions, import_source=None, target_holding_id=None):
        """Apply a reviewed import to exactly one holding."""
        now=iso_now(); profile_id=payload.get('profile_id')
        with self._lock, self.conn() as c:
            if target_holding_id:
                existing = self.get_fund(int(target_holding_id))
                if not existing or int(existing['profile_id']) != int(profile_id):
                    raise ValueError('Selected merge target is not valid for this investor.')
                action = 'EXISTING_INVESTMENT_MERGE'
            else:
                existing, action, reason=self.find_import_fund(profile_id,payload)
                if action=='REVIEW_REQUIRED': raise ValueError(reason)
                if action in ('NEW_INVESTMENT','NEW_FOLIO'): raise ValueError('Fund must be created as a new holding, not merged.')
            holding_id=existing['id']
            rows=c.execute('SELECT * FROM transactions WHERE holding_id=? ORDER BY txn_date,id',(holding_id,)).fetchall()
            inserted=0
            for raw in transactions:
                tx=dict(raw); tx_folio=self.normalize_folio(tx.get('folio')) or self.normalize_folio(existing['normalized_folio'] or existing['folio']); tx['folio']=tx_folio
                if self.transaction_is_duplicate(tx,rows): continue
                if tx.get('txn_type')=='sip':
                    cand=self._sip_reconciliation_candidates(holding_id,tx)
                    if len(cand)>1:
                        raise ValueError(f'Ambiguous SIP reconciliation for {payload.get("scheme_name")} on {tx.get("txn_date")}; manual review is required.')
                    if len(cand)==1:
                        candidate=cand[0]
                        internal_tx = c.execute("SELECT id FROM transactions WHERE holding_id=? AND txn_type='sip' AND source='sip_execution' AND ABS(amount-?) <= ? AND ABS(units-?) <= ? ORDER BY created_at DESC, id DESC LIMIT 1",
                                               (holding_id, float(candidate['amount']), max(0.01, 0.0001*max(abs(float(candidate['amount'])), abs(float(tx.get('amount') or 0)))),
                                                float(candidate['units']), 0.001)).fetchone()
                        if internal_tx and ('id' in candidate.keys() if hasattr(candidate, 'keys') else bool(candidate.get('id'))):
                            c.execute('UPDATE sip_executions SET reconciled_with_cams=1,cams_transaction_id=?,cams_transaction_date=? WHERE id=?',
                                      (internal_tx['id'], tx['txn_date'], candidate['id']))
                        continue
                c.execute('''INSERT INTO transactions(holding_id,txn_type,txn_date,amount,cashflow,units,nav,charges,folio,source,note,raw_type_text,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (holding_id,tx['txn_type'],tx['txn_date'],tx.get('amount',0),tx.get('cashflow',tx.get('amount',0)),tx.get('units',0),tx.get('nav'),tx.get('charges',0),tx_folio,tx.get('source','import'),tx.get('note'),tx.get('raw_type_text'),now))
                inserted+=1
                rows.append({'id':c.execute('SELECT last_insert_rowid()').fetchone()[0],'txn_type':tx['txn_type'],'txn_date':tx['txn_date'],'amount':tx.get('amount',0),'units':tx.get('units',0),'nav':tx.get('nav'),'charges':tx.get('charges',0),'folio':tx_folio,'source':tx.get('source','import')})
            c.execute('''UPDATE holdings SET scheme_name=?,isin=COALESCE(?,isin),scheme_code=COALESCE(NULLIF(?,' '),scheme_code),units=?,invested=?,sip_enabled=?,sip_amount=?,sip_day=?,initial_date=?,updated_at=? WHERE id=?''',
                      (payload['scheme_name'],payload.get('isin'),payload.get('scheme_code') or ' ',payload.get('units',existing['units']),payload.get('invested',existing['invested']),1 if payload.get('sip_enabled') else 0,payload.get('sip_amount',0),payload.get('sip_day'),payload.get('initial_date') or existing['initial_date'],now,holding_id))
            self._sync_sip_schedule(c,holding_id,payload.get('sip_amount',0),payload.get('sip_day'),bool(payload.get('sip_enabled')),payload.get('initial_date'),'cams_statement')
            return holding_id, inserted

    def transactions(self, holding_id):
        with self.conn() as c:
            return c.execute('SELECT * FROM transactions WHERE holding_id=? ORDER BY txn_date, id', (holding_id,)).fetchall()

    def delete_fund(self, holding_id):
        with self._lock, self.conn() as c:
            c.execute('DELETE FROM holdings WHERE id=?', (holding_id,))


def xirr(cashflows: list[float], dates: list[date]) -> float | None:
    if len(cashflows) < 2 or len(cashflows) != len(dates):
        return None
    if not (any(v < 0 for v in cashflows) and any(v > 0 for v in cashflows)):
        return None
    base = dates[0]
    years = [(d - base).days / 365.0 for d in dates]

    def f(rate):
        if rate <= -0.999999999:
            return float('inf')
        try:
            return sum(cf / ((1.0 + rate) ** t) for cf, t in zip(cashflows, years))
        except (OverflowError, ZeroDivisionError):
            return float('inf')

    def df(rate):
        if rate <= -0.999999999:
            return float('inf')
        return sum(-t * cf / ((1.0 + rate) ** (t + 1)) for cf, t in zip(cashflows, years))

    # Newton tries common starting points.
    for guess in (0.1, 0.25, 0.5, 1.0, -0.25, -0.5):
        r = guess
        for _ in range(80):
            fv = f(r)
            dv = df(r)
            if not math.isfinite(fv) or not math.isfinite(dv) or abs(dv) < 1e-14:
                break
            nr = r - fv / dv
            if nr <= -0.999999:
                break
            if abs(nr - r) < 1e-8:
                return nr
            r = nr

    # Bracket search + bisection.
    points = [-0.9999, -0.9, -0.75, -0.5, -0.25, -0.1, 0.0, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100]
    prev_r = points[0]
    prev_f = f(prev_r)
    for r in points[1:]:
        fr = f(r)
        if math.isfinite(prev_f) and math.isfinite(fr) and prev_f == 0:
            return prev_r
        if math.isfinite(prev_f) and math.isfinite(fr) and prev_f * fr < 0:
            lo, hi = prev_r, r
            flo, fhi = prev_f, fr
            for _ in range(120):
                mid = (lo + hi) / 2
                fm = f(mid)
                if not math.isfinite(fm):
                    break
                if abs(fm) < 1e-9 or abs(hi - lo) < 1e-10:
                    return mid
                if flo * fm <= 0:
                    hi, fhi = mid, fm
                else:
                    lo, flo = mid, fm
        prev_r, prev_f = r, fr
    return None


def classify_scheme_metadata(scheme_name, scheme_category=None, scheme_type=None):
    name = re.sub(r'\s+', ' ', str(scheme_name or '').strip())
    low_name = name.lower()
    category = re.sub(r'\s+', ' ', str(scheme_category or '').strip())
    low_category = category.lower()
    type_text = re.sub(r'\s+', ' ', str(scheme_type or '').strip())
    low_type = type_text.lower()

    if re.search(r'\bdirect\b', low_name):
        plan_type = 'Direct'
    elif re.search(r'\bregular\b', low_name):
        plan_type = 'Regular'
    else:
        plan_type = 'Other'

    if re.search(r'\bidcw\b|\bdividend\b|income distribution', low_name):
        option_type = 'IDCW / Dividend'
    elif re.search(r'\bgrowth\b', low_name):
        option_type = 'Growth'
    else:
        option_type = 'Other'

    if 'close ended' in low_type or 'close-ended' in low_type:
        structure_type = 'Close-Ended'
    elif 'interval' in low_type:
        structure_type = 'Interval'
    elif 'open ended' in low_type or 'open-ended' in low_type:
        structure_type = 'Open-Ended'
    else:
        structure_type = 'Other'

    asset_class = 'Other'
    if low_category.startswith('equity scheme'):
        asset_class = 'Equity'
    elif low_category.startswith('debt scheme'):
        asset_class = 'Debt'
    elif low_category.startswith('hybrid scheme'):
        asset_class = 'Hybrid'
    elif any(k in low_category or k in low_name for k in ('gold', 'commodity', 'silver')):
        asset_class = 'Commodity / Gold'

    if ' - ' in category:
        category_detail = category.split(' - ', 1)[1].strip()
    else:
        category_detail = category or 'Other'
    category_detail = re.sub(r'\s*/\s*', ' / ', category_detail)

    passive_tokens = ('index', 'etf', 'passive', 'nifty', 'sensex', 's&p', 'bse', 'equal weight')
    management_style = 'Passive / Index / ETF' if any(tok in low_name or tok in low_category for tok in passive_tokens) else 'Active'

    if 'elss' in low_category or 'elss' in low_name or 'tax saver' in low_name:
        horizon_goal = 'ELSS (Tax Saving)'
    elif 'overnight' in low_category or 'liquid fund' in low_category or 'money market' in low_category:
        horizon_goal = 'Overnight / Liquid'
    elif 'solution oriented' in low_category or 'solution-oriented' in low_category:
        horizon_goal = 'Solution-Oriented'
    else:
        horizon_goal = 'Other'

    return {
        'plan_type': plan_type,
        'option_type': option_type,
        'structure_type': structure_type,
        'asset_class': asset_class,
        'scheme_category': category_detail,
        'management_style': management_style,
        'horizon_goal': horizon_goal,
    }


def build_portfolio(db: Database, profile_id=None):
    funds = db.all_funds(profile_id)
    profile = db.get_profile(profile_id) if profile_id else None
    rows = []
    total = {
        'value': 0.0, 'invested': 0.0, 'profit': 0.0,
        'day_change': 0.0, 'month_change': 0.0, 'year_change': 0.0
    }
    portfolio_cash = []
    portfolio_dates = []
    today = date.today()
    for fund in funds:
        units = float(fund['units'] or 0)
        invested = float(fund['invested'] or 0)
        nav = fund['last_nav']
        prev = fund['previous_nav']
        month_nav = fund['month_nav']
        year_nav = fund['year_nav']
        value = units * float(nav) if nav is not None else 0.0
        prev_value = units * float(prev) if prev is not None else 0.0
        month_value = units * float(month_nav) if month_nav is not None else 0.0
        year_value = units * float(year_nav) if year_nav is not None else 0.0
        day_change = value - prev_value if nav is not None and prev is not None else 0.0
        month_change = value - month_value if nav is not None and month_nav is not None else 0.0
        year_change = value - year_value if nav is not None and year_nav is not None else 0.0
        profit = value - invested
        day_pct = (day_change / prev_value * 100.0) if prev_value else None
        month_pct = (month_change / month_value * 100.0) if month_value else None
        total_profit_pct = (profit / invested * 100.0) if invested else None
        txns = db.transactions(fund['id'])
        cfs = []
        ds = []
        for tx in txns:
            if tx['txn_type'] == 'initial' and not fund['initial_date']:
                continue
            try:
                dt = datetime.strptime(tx['txn_date'], '%Y-%m-%d').date()
            except ValueError:
                continue
            cfs.append(float(tx['cashflow']))
            ds.append(dt)
            if abs(float(tx['cashflow'])) > 1e-12:
                portfolio_cash.append(float(tx['cashflow']))
                portfolio_dates.append(dt)
        fund_xirr = None
        if nav is not None and cfs and ds:
            fund_xirr = xirr(cfs + [value], ds + [today])
        total['value'] += value
        total['invested'] += invested
        total['profit'] += profit
        total['day_change'] += day_change
        total['month_change'] += month_change
        total['year_change'] += year_change
        classification = classify_scheme_metadata(fund['scheme_name'], fund['scheme_category'], fund['scheme_type'])
        rows.append({
            'id': fund['id'], 'profile_id': fund['profile_id'], 'fund_name': fund['scheme_name'], 'scheme_code': fund['scheme_code'], 'isin': fund['isin'],
            **classification,
            'sip_amount': fmt_num(fund['sip_amount']), 'sip_day': fund['sip_day'], 'sip_enabled': bool(fund['sip_enabled']),
            'sip_status': ('active' if fund['sip_enabled'] else ('inactive' if float(fund['sip_amount'] or 0) > 0 else 'none')),
            'units': fmt_num(units, 4), 'invested': fmt_num(invested), 'nav': fmt_num(nav, 4) if nav is not None else None,
            'nav_date': fund['last_nav_date'], 'previous_nav': fmt_num(prev, 4) if prev is not None else None,
            'value': fmt_num(value), 'day_change': fmt_num(day_change), 'day_pct': fmt_num(day_pct),
            'month_change': fmt_num(month_change), 'month_pct': fmt_num(month_pct), 'year_change': fmt_num(year_change),
            'profit': fmt_num(profit), 'profit_pct': fmt_num(total_profit_pct),
            'xirr': fmt_num(fund_xirr * 100 if fund_xirr is not None else None),
            'last_sip_cycle': fund['last_sip_cycle'], 'last_sip_nav_date': fund['last_sip_nav_date'],
            'error': fund['last_error']
        })
    total_pct = (total['profit'] / total['invested'] * 100.0) if total['invested'] else None
    portfolio_x = None
    if portfolio_cash and any(v < 0 for v in portfolio_cash) and total['value'] > 0:
        portfolio_x = xirr(portfolio_cash + [total['value']], portfolio_dates + [today])
    total.update({'profit_pct': fmt_num(total_pct), 'xirr': fmt_num(portfolio_x * 100 if portfolio_x is not None else None)})
    # Portfolio NAV date is the oldest valid NAV date among the included funds.
    # This deliberately shows the date to which ALL funds are updated, so a fund
    # lagging behind prevents the portfolio from appearing fully current.
    nav_dates = []
    for row in rows:
        try:
            if row['nav_date']:
                nav_dates.append(date.fromisoformat(str(row['nav_date'])))
        except (TypeError, ValueError):
            pass
    portfolio_nav_date = min(nav_dates).isoformat() if nav_dates else None
    return {'updated_at': iso_now(), 'profile_id': profile_id, 'profile': db.profile_dict(profile) if profile else None, 'rows': rows, 'nav_date': portfolio_nav_date, 'nse_holiday': TRACKER._holiday_status(datetime.now(NSE_TIMEZONE).date()) if 'TRACKER' in globals() else None, 'market_status': TRACKER._market_status_payload() if 'TRACKER' in globals() else None, 'total': {k: fmt_num(v) if isinstance(v, (int, float)) else v for k, v in total.items()}}


REFRESH_STATE = {
    'id': None, 'status': 'idle', 'stage': 'Idle', 'progress': 0,
    'message': 'Waiting for refresh', 'updated_at': iso_now(),
    'last_nav_refresh': None, 'error': None
}
REFRESH_STATE_LOCK = threading.Lock()

def _refresh_state(**updates):
    with REFRESH_STATE_LOCK:
        REFRESH_STATE.update(updates)
        REFRESH_STATE['updated_at'] = iso_now()
        return dict(REFRESH_STATE)

def get_refresh_state():
    with REFRESH_STATE_LOCK:
        return dict(REFRESH_STATE)

class Tracker:
    def __init__(self):
        self.db = Database(DB_PATH)
        self._amfi_catalog = None
        self._amfi_catalog_at = 0.0
        self._amfi_lock = threading.Lock()
        self._latest_nav_snapshot = None
        self._latest_nav_snapshot_at = 0.0
        self._latest_nav_snapshot_all_schemes = False
        self._latest_nav_snapshot_lock = threading.Lock()
        self._amfi_history_snapshot_cache = {}
        self._amfi_history_snapshot_lock = threading.Lock()
        self._nav_reference_lock = threading.Lock()
        self.lock = threading.Lock()
        self.timezone_name = 'UTC'
        self._get_ha_timezone()
        self._integration_state_lock = threading.Lock()
        # Export the current state immediately after startup. This is important
        # because startup migration can backfill the SIP execution log from
        # existing successful SIP transactions, and the HA custom integration
        # reads its data from integration_state.json.
        try:
            self._write_integration_state()
        except Exception:
            pass
        self._refresh_request_path = Path('/share/mutual_fund_tracker/refresh.request')
        self._export_request_path = Path('/share/mutual_fund_tracker/export.request')
        self._export_dir = Path('/share/mutual_fund_tracker/exports')
        self._export_lock = threading.Lock()
        self._nifty_lock = threading.Lock()
        self._nifty_quote = None
        self._nifty_last_error = None
        self._nifty_next_fetch_mono = None
        self._nifty_state_write_condition = threading.Condition()
        self._nifty_state_write_pending = False
        threading.Thread(target=self._refresh_request_loop, daemon=True).start()
        threading.Thread(target=self._integration_reload_ack_loop, daemon=True).start()
        threading.Thread(target=self._export_request_loop, daemon=True).start()
        threading.Thread(target=self._holiday_refresh_loop, daemon=True).start()
        threading.Thread(target=self._nifty_state_writer_loop, daemon=True).start()
        threading.Thread(target=self._market_status_loop, daemon=True).start()
        threading.Thread(target=self._prefetch_nav_reference_cache, daemon=True).start()

    def invalidate_graph_snapshots(self, holding_id, from_date=None):
        '''Invalidate derived graph snapshots after a financial transaction change.'''
        with self.db._lock, self.db.conn() as c:
            if from_date:
                c.execute('DELETE FROM daily_portfolio_snapshot WHERE holding_id=? AND snapshot_date>=?', (holding_id, str(from_date)))
            else:
                c.execute('DELETE FROM daily_portfolio_snapshot WHERE holding_id=?', (holding_id,))
            now=iso_now()
            c.execute('INSERT INTO graph_cache_meta(holding_id,generation,updated_at) VALUES(?,?,?) ON CONFLICT(holding_id) DO UPDATE SET generation=generation+1,updated_at=excluded.updated_at',(holding_id,1,now))

    def graph_nav_coverage(self, scheme_code):
        with self.db.conn() as c:
            row=c.execute('SELECT MIN(nav_date) min_date, MAX(nav_date) max_date FROM graph_nav_history WHERE scheme_code=?',(str(scheme_code),)).fetchone()
            return (row['min_date'],row['max_date']) if row and row['min_date'] else (None,None)

    def graph_nav_entries(self, scheme_code, start_date, end_date):
        with self.db.conn() as c:
            rows=c.execute('SELECT nav_date,nav FROM graph_nav_history WHERE scheme_code=? AND nav_date BETWEEN ? AND ? ORDER BY nav_date',(str(scheme_code),start_date.isoformat(),end_date.isoformat())).fetchall()
        return [(parse_optional_date(r['nav_date']),float(r['nav'])) for r in rows if r['nav'] is not None]

    def _fetch_graph_nav_range(self, scheme_code, start_date, end_date):
        if end_date < start_date:return 0
        payload=self.request_nav_json_with_retry(f'{MFAPI_BASE}/mf/{scheme_code}?startDate={start_date.isoformat()}&endDate={end_date.isoformat()}')
        rows=[];now=iso_now()
        for item in payload.get('data') or []:
            try:
                d=parse_nav_date(item['date']); n=float(item['nav'])
                if start_date<=d<=end_date and math.isfinite(n): rows.append((str(scheme_code),d.isoformat(),n,now))
            except Exception: continue
        if not rows:return 0
        with self.db._lock,self.db.conn() as c:
            c.executemany('INSERT OR REPLACE INTO graph_nav_history(scheme_code,nav_date,nav,fetched_at) VALUES(?,?,?,?)',rows)
        return len(rows)

    def ensure_graph_nav_history(self, scheme_code, start_date, end_date):
        code=str(scheme_code or '').strip()
        if not code or end_date<start_date:return
        min_d,max_d=self.graph_nav_coverage(code); ranges=[]
        if min_d is None:ranges=[(start_date,end_date)]
        else:
            lo=date.fromisoformat(min_d);hi=date.fromisoformat(max_d)
            if start_date<lo:ranges.append((start_date,lo-timedelta(days=1)))
            if end_date>hi:ranges.append((hi+timedelta(days=1),end_date))
        for a,b in ranges:
            if a<=b:self._fetch_graph_nav_range(code,a,b)

    @staticmethod
    def _snapshot_txn_transition(state, tx):
        '''Apply the same transaction semantics used by add_transaction().'''
        t=str(tx['txn_type'] or '');amount=abs(float(tx['amount'] or 0));units=float(tx['units'] or 0)
        if t in ('sip','buy','lumpsum','initial'):state['units']+=units;state['invested']+=amount
        elif t=='sell':
            state['invested']=Database._remaining_invested_after_outflow(state['units'],state['invested'],abs(units))
            state['units']+=units if units<0 else -units
        elif t=='switch_in':state['units']+=units
        elif t=='switch_out':
            state['invested']=Database._remaining_invested_after_outflow(state['units'],state['invested'],abs(units))
            state['units']+=units
        elif t=='adjustment':state['units']+=units;state['invested']+=amount
        elif t=='fee':state['invested']+=amount

    def _graph_snapshot_needs_build(self, holding_id,start_date,end_date):
        with self.db.conn() as c:
            row=c.execute('SELECT MIN(snapshot_date) min_date,MAX(snapshot_date) max_date,COUNT(*) n FROM daily_portfolio_snapshot WHERE holding_id=? AND snapshot_date BETWEEN ? AND ?',(holding_id,start_date.isoformat(),end_date.isoformat())).fetchone()
        return not row or not row['n'] or row['min_date']!=start_date.isoformat() or row['max_date']!=end_date.isoformat()

    def build_graph_snapshots(self, holding_id,start_date,end_date):
        holding=self.db.get_fund(holding_id)
        if not holding:raise ValueError('fund not found')
        self.ensure_graph_nav_history(holding['scheme_code'],start_date,end_date)
        nav_entries=self.graph_nav_entries(holding['scheme_code'],start_date,end_date)
        if not nav_entries:raise ValueError(f'No historical NAV data available for {holding["scheme_name"]}')
        grouped={}
        for tx in self.db.transactions(holding_id):
            td=parse_optional_date(tx['txn_date'])
            if td:grouped.setdefault(td,[]).append(tx)
        state={'units':0.0,'invested':0.0}
        for td in sorted(d for d in grouped if d<=start_date):
            for tx in grouped[td]:self._snapshot_txn_transition(state,tx)
        out=[];now=iso_now();idx=0;tds=sorted(grouped)
        for d,nav in nav_entries:
            while idx<len(tds) and tds[idx]<=d:
                td=tds[idx]
                if td>start_date:
                    for tx in grouped[td]:self._snapshot_txn_transition(state,tx)
                idx+=1
            out.append((holding_id,d.isoformat(),state['units'],max(0.0,state['invested']),state['units']*nav,now))
        with self.db._lock,self.db.conn() as c:
            c.execute('DELETE FROM daily_portfolio_snapshot WHERE holding_id=? AND snapshot_date BETWEEN ? AND ?',(holding_id,start_date.isoformat(),end_date.isoformat()))
            c.executemany('INSERT OR REPLACE INTO daily_portfolio_snapshot(holding_id,snapshot_date,units_held,invested_amount,value,calculated_at) VALUES(?,?,?,?,?,?)',out)

    def graph_snapshots(self, holding_id,start_date,end_date):
        with self.db.conn() as c:
            rows=c.execute('SELECT snapshot_date,units_held,invested_amount,value FROM daily_portfolio_snapshot WHERE holding_id=? AND snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date',(holding_id,start_date.isoformat(),end_date.isoformat())).fetchall()
        return [dict(r) for r in rows]

    def graph_period_range(self, holding_id,period):
        today=self.local_today();p=str(period or '1y').lower()
        if p=='1m':start=today.replace(day=1)
        elif p=='30d':start=today-timedelta(days=29)
        elif p=='3m':start=today-timedelta(days=92)
        elif p=='6m':start=today-timedelta(days=183)
        elif p=='1y':start=today-timedelta(days=364)
        elif p=='3y':start=today-timedelta(days=1094)
        elif p=='5y':start=today-timedelta(days=1824)
        elif p in ('inception','all'):
            ds=[parse_optional_date(r['txn_date']) for r in self.db.transactions(holding_id)];ds=[d for d in ds if d];start=min(ds) if ds else today
        else:raise ValueError('Unsupported graph period')
        return start,today

    @staticmethod
    def _lttb(points,max_points):
        if len(points)<=max_points or max_points<3:return points
        sampled=[points[0]];every=(len(points)-2)/(max_points-2);a=0
        for i in range(max_points-2):
            avg_start=int((i+1)*every)+1;avg_end=min(int((i+2)*every)+1,len(points));avg=points[avg_start:avg_end] or [points[-1]]
            ax=sum(x for x,_ in avg)/len(avg);ay=sum(y for _,y in avg)/len(avg);rs=int(i*every)+1;re=min(int((i+1)*every)+1,len(points)-1);px,py=points[a];best=rs;area_max=-1
            for j in range(rs,re):
                x,y=points[j];area=abs((px-ax)*(y-py)-(px-x)*(ay-py))*0.5
                if area>area_max:area_max=area;best=j
            sampled.append(points[best]);a=best
        sampled.append(points[-1]);return sampled

    def generate_graph_dataset(self,profile_id,holding_ids,parameters,period):
        selected=[]
        for raw in holding_ids:
            try:fid=int(raw)
            except Exception:continue
            f=self.db.get_fund(fid)
            if f and (profile_id is None or int(f['profile_id'])==int(profile_id)):selected.append(f)
        if not selected:raise ValueError('Select at least one fund')
        params=[p for p in parameters if p in {'total_value','total_invested','day_change','profit_pct'}]
        if not params:raise ValueError('Select at least one graph parameter')
        ranges=[];unique={};warnings=[]
        for f in selected:
            start,end=self.graph_period_range(f['id'],period);ranges.append((f,start,end));unique[(str(f['scheme_code']),start,end)]=(str(f['scheme_code']),start,end)
        with ThreadPoolExecutor(max_workers=GRAPH_NAV_CONCURRENCY) as pool:
            futures=[pool.submit(self.ensure_graph_nav_history,*r) for r in unique.values() if r[0]]
            for fut in futures:
                try:fut.result()
                except Exception as exc:warnings.append(f'Historical NAV fetch failed: {exc}')
        series=[]
        for f,start,end in ranges:
            try:
                if self._graph_snapshot_needs_build(f['id'],start,end):self.build_graph_snapshots(f['id'],start,end)
                snaps=self.graph_snapshots(f['id'],start,end)
            except Exception as exc:warnings.append(f'{f["scheme_name"]}: {exc}');continue
            prev=None;points=[]
            for r in snaps:
                value=float(r['value']);invested=float(r['invested_amount']);day=value-prev if prev is not None else None;pp=((value-invested)/invested*100.0) if invested>0 else None
                points.append({'date':r['snapshot_date'],'total_value':value,'total_invested':invested,'day_change':day,'profit_pct':pp});prev=value
            stats_by_param={}
            # Calculate single-fund statistics from the full-resolution dataset before any display downsampling.
            for param in params:
                raw_values=[float(p[param]) for p in points if p[param] is not None and math.isfinite(float(p[param]))]
                if len(raw_values):
                    ordered=sorted(raw_values)
                    n=len(ordered);mid=n//2
                    median=ordered[mid] if n%2 else (ordered[mid-1]+ordered[mid])/2.0
                    max_val=max(raw_values);min_val=min(raw_values)
                    max_date=next((p['date'] for p in points if p[param] is not None and float(p[param])==max_val),None)
                    min_date=next((p['date'] for p in points if p[param] is not None and float(p[param])==min_val),None)
                    stats_by_param[param]={'max':max_val,'max_date':max_date,'min':min_val,'min_date':min_date,'median':median,'count':n}
                raw=[(i,p[param]) for i,p in enumerate(points) if p[param] is not None]
                if len(raw)>GRAPH_MAX_POINTS:
                    keep={i for i,_ in self._lttb([(i,float(v)) for i,v in raw],GRAPH_MAX_POINTS)};plot=[p for i,p in enumerate(points) if i in keep]
                else:plot=points
                series.append({'holding_id':f['id'],'fund_name':f['scheme_name'],'parameter':param,'points':plot})
            single_stats=stats_by_param if len(selected)==1 else {}
            for series_item in series:
                if single_stats.get(series_item['parameter']):
                    series_item['statistics']=single_stats[series_item['parameter']]
        return {'period':period,'parameters':params,'series':series,'warnings':warnings,'generated_at':iso_now(),'holding_count':len(selected),'statistics':single_stats}

    def _get_ha_timezone(self):
        token = os.getenv('SUPERVISOR_TOKEN')
        if token:
            try:
                cfg = self.ha_request('GET', 'http://supervisor/core/api/config')
                tz = cfg.get('time_zone')
                if tz:
                    self.timezone_name = tz
                    return
            except Exception:
                pass
        # Fallbacks for environments where the Supervisor API is unavailable.
        candidate = os.getenv('TZ')
        if candidate:
            try:
                ZoneInfo(candidate)
                self.timezone_name = candidate
                return
            except Exception:
                pass
        try:
            localtime = os.path.realpath('/etc/localtime')
            marker = '/zoneinfo/'
            if marker in localtime:
                candidate = localtime.split(marker, 1)[1]
                ZoneInfo(candidate)
                self.timezone_name = candidate
                return
        except Exception:
            pass

    def local_now(self):
        try:
            return datetime.now(ZoneInfo(self.timezone_name))
        except Exception:
            return datetime.now()

    def local_today(self):
        try:
            return datetime.now(ZoneInfo(self.timezone_name)).date()
        except Exception:
            return date.today()

    def sip_reporting_date(self):
        """Return the logical SIP day (23:00 to 22:59:59 local time)."""
        today = self.local_today()
        if self.local_now().hour >= 23:
            return today
        return today - timedelta(days=1)

    def ha_request(self, method, url, payload=None):
        token = os.getenv('SUPERVISOR_TOKEN')
        if not token:
            raise RuntimeError('SUPERVISOR_TOKEN unavailable')
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(url, data=data, headers=headers, method=method)
        with urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw.decode()) if raw else None

    def _holiday_calendar_payload(self):
        """Return the current year's persisted NSE calendar, creating a fallback if needed."""
        year = self.local_today().year
        try:
            if NSE_HOLIDAY_JSON.exists():
                payload = json.loads(NSE_HOLIDAY_JSON.read_text(encoding='utf-8'))
                if int(payload.get('year', 0)) == year and isinstance(payload.get('dates'), dict):
                    return payload
        except Exception:
            pass
        return self._build_nse_calendar(year, None)

    def _fetch_nse_holiday_api(self, year):
        """Fetch holiday records for the current year from Upstox's market-holidays API."""
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        token = os.getenv('UPSTOX_ACCESS_TOKEN') or os.getenv('UPSTOX_API_TOKEN')
        if token:
            headers['Authorization'] = f'Bearer {token}'
        req = Request(UPSTOX_HOLIDAY_URL, headers=headers, method='GET')
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise ValueError('Invalid Upstox holiday response')
        return data['data']

    def _build_nse_calendar(self, year, api_rows):
        """Build a full-year effective NSE trading calendar including weekends and overrides."""
        seed = NSE_2026_SEED_HOLIDAYS if year == 2026 else {}
        special_seed = NSE_2026_SPECIAL_OPEN if year == 2026 else {}
        api_map = {}
        for item in api_rows or []:
            if not isinstance(item, dict):
                continue
            d = str(item.get('date') or '')
            try:
                date.fromisoformat(d)
            except ValueError:
                continue
            closed = item.get('closed_exchanges') or []
            open_exchanges = item.get('open_exchanges') or []
            nse_open_override = any((x.get('exchange') if isinstance(x, dict) else x) == 'NSE' for x in open_exchanges)
            nse_closed = 'NSE' in closed
            # An entry may be a special-timing record without closing NSE.
            api_map[d] = {
                'description': item.get('description'),
                'holiday_type': item.get('holiday_type'),
                'nse_closed': nse_closed,
                'nse_open_override': nse_open_override,
                'raw': item,
            }
        dates = {}
        d = date(year, 1, 1)
        while d.year == year:
            iso = d.isoformat()
            weekend = d.weekday() >= 5
            entry = api_map.get(iso)
            is_special_open = iso in special_seed
            if entry:
                if entry['nse_open_override']:
                    open_ = True
                    kind = 'SPECIAL_OPEN'
                elif entry['nse_closed']:
                    open_ = False
                    kind = 'NSE_HOLIDAY'
                else:
                    open_ = not weekend
                    kind = 'SPECIAL_TIMING' if entry.get('holiday_type') == 'SPECIAL_TIMING' else ('WEEKEND' if weekend else 'TRADING_DAY')
                description = entry.get('description')
                holiday_type = entry.get('holiday_type')
            elif is_special_open:
                open_ = True
                kind = 'SPECIAL_OPEN'
                description = special_seed[iso]
                holiday_type = 'SPECIAL_TRADING'
            elif iso in seed:
                open_ = False
                kind = 'NSE_HOLIDAY'
                description = seed[iso]
                holiday_type = 'TRADING_HOLIDAY'
            elif weekend:
                open_ = False
                kind = 'WEEKEND'
                description = 'Saturday' if d.weekday() == 5 else 'Sunday'
                holiday_type = 'WEEKEND'
            else:
                open_ = True
                kind = 'TRADING_DAY'
                description = None
                holiday_type = None
            # Exchange-open override wins even if the weekday is normally a weekend/holiday.
            if iso in special_seed:
                open_ = True
                kind = 'SPECIAL_OPEN'
                description = special_seed[iso]
                holiday_type = 'SPECIAL_TRADING'
            dates[iso] = {
                'nse_open': bool(open_),
                'type': kind,
                'description': description,
                'holiday_type': holiday_type,
            }
            d += timedelta(days=1)
        payload = {
            'year': year,
            'generated_at': iso_now(),
            'source': 'Upstox market holidays API' if api_rows else 'Built-in NSE 2026 seed + weekend rules',
            'dates': dates,
        }
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = NSE_HOLIDAY_JSON.with_suffix('.tmp')
            tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            tmp.replace(NSE_HOLIDAY_JSON)
        except Exception:
            pass
        return payload

    def refresh_nse_holiday_calendar(self, force=False):
        year = self.local_today().year
        try:
            rows = self._fetch_nse_holiday_api(year)
            return self._build_nse_calendar(year, rows)
        except Exception:
            # Keep a previously good calendar if available; otherwise create a deterministic fallback.
            try:
                if NSE_HOLIDAY_JSON.exists():
                    existing = json.loads(NSE_HOLIDAY_JSON.read_text(encoding='utf-8'))
                    if int(existing.get('year', 0)) == year and isinstance(existing.get('dates'), dict):
                        return existing
            except Exception:
                pass
            return self._build_nse_calendar(year, None)

    def _holiday_status(self, target: date | None = None):
        target = target or self.local_today()
        payload = self._holiday_calendar_payload()
        item = (payload.get('dates') or {}).get(target.isoformat()) or {
            'nse_open': target.weekday() < 5,
            'type': 'TRADING_DAY' if target.weekday() < 5 else 'WEEKEND',
            'description': None if target.weekday() < 5 else ('Saturday' if target.weekday() == 5 else 'Sunday'),
            'holiday_type': None,
        }
        next_open = target
        for _ in range(370):
            if (payload.get('dates') or {}).get(next_open.isoformat(), {}).get('nse_open'):
                break
            next_open += timedelta(days=1)
        prev_open = target
        for _ in range(370):
            if (payload.get('dates') or {}).get(prev_open.isoformat(), {}).get('nse_open'):
                break
            prev_open -= timedelta(days=1)
        yesterday = target - timedelta(days=1)
        yesterday_item = (payload.get('dates') or {}).get(yesterday.isoformat()) or {
            'nse_open': yesterday.weekday() < 5,
            'type': 'TRADING_DAY' if yesterday.weekday() < 5 else 'WEEKEND',
            'description': None if yesterday.weekday() < 5 else ('Saturday' if yesterday.weekday() == 5 else 'Sunday'),
            'holiday_type': None,
        }
        tomorrow = target + timedelta(days=1)
        tomorrow_item = (payload.get('dates') or {}).get(tomorrow.isoformat()) or {
            'nse_open': tomorrow.weekday() < 5,
            'type': 'TRADING_DAY' if tomorrow.weekday() < 5 else 'WEEKEND',
            'description': None if tomorrow.weekday() < 5 else ('Saturday' if tomorrow.weekday() == 5 else 'Sunday'),
            'holiday_type': None,
        }
        return {
            'date': target.isoformat(),
            'status_checked_at': iso_now(),
            'nse_open': bool(item.get('nse_open')),
            'status': 'Open' if item.get('nse_open') else 'Holiday',
            'type': item.get('type'),
            'description': item.get('description'),
            'holiday_type': item.get('holiday_type'),
            'yesterday_date': yesterday.isoformat(),
            'yesterday_nse_open': bool(yesterday_item.get('nse_open')),
            'yesterday_type': yesterday_item.get('type'),
            'yesterday_description': yesterday_item.get('description'),
            'yesterday_holiday_type': yesterday_item.get('holiday_type'),
            'tomorrow_date': tomorrow.isoformat(),
            'tomorrow_nse_open': bool(tomorrow_item.get('nse_open')),
            'tomorrow_type': tomorrow_item.get('type'),
            'tomorrow_description': tomorrow_item.get('description'),
            'tomorrow_holiday_type': tomorrow_item.get('holiday_type'),
            'next_trading_date': next_open.isoformat(),
            'previous_trading_date': prev_open.isoformat(),
            'calendar_year': payload.get('year'),
            'calendar_generated_at': payload.get('generated_at'),
        }

    def _nse_market_snapshot(self, now=None):
        """Return the current NSE continuous-market state using NSE local time."""
        if now is None:
            now = datetime.now(NSE_TIMEZONE)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=NSE_TIMEZONE)
        else:
            now = now.astimezone(NSE_TIMEZONE)
        today = now.date()
        holiday = self._holiday_status(today)
        minute = now.hour * 60 + now.minute
        if not holiday.get('nse_open'):
            reason = holiday.get('description') or holiday.get('type') or 'Holiday'
            return {
                'market_open': False,
                'status': 'HOLIDAY' if holiday.get('type') != 'WEEKEND' else 'WEEKEND',
                'reason': reason,
                'date': today.isoformat(),
                'status_checked_at': iso_now(),
            }
        if minute < NSE_MARKET_OPEN_MINUTE:
            return {
                'market_open': False,
                'status': 'PRE_OPEN',
                'reason': 'Open @ 9:00am',
                'date': today.isoformat(),
                'status_checked_at': iso_now(),
            }
        if minute < NSE_MARKET_CLOSE_MINUTE:
            return {
                'market_open': True,
                'status': 'OPEN',
                'reason': 'Open',
                'date': today.isoformat(),
                'status_checked_at': iso_now(),
            }
        return {
            'market_open': False,
            'status': 'CLOSED',
            'reason': 'Closed',
            'date': today.isoformat(),
            'status_checked_at': iso_now(),
        }

    def _nifty_csv_request_url(self, base_url=None):
        """Return a cache-busting Google Sheets CSV URL.

        The primary source is the publicly published CSV endpoint, which is
        directly accessible and empirically observed to return changing NIFTY
        values. The Visualization endpoint is retained as a backup source.
        """
        base = base_url or NIFTY_CSV_URL
        separator = '&' if '?' in base else '?'
        return f'{base}{separator}mft_cache={time.time_ns()}'

    def _fetch_nifty50_quote(self):
        """Fetch and validate a NIFTY 50 quote from reliable live sources.

        Yahoo Finance is the primary source because it has been empirically
        verified from the same Ubuntu/HA host to return live NIFTY values.
        The published Google Sheets CSV is retained as a secondary fallback.
        The old Google Visualization endpoint is intentionally not used because
        it can return a server-cached value for minutes even while the primary
        feed is healthy.
        """
        headers = {
            'Accept': 'application/json,text/csv,text/plain,*/*',
            'User-Agent': NIFTY_USER_AGENT,
            'Cache-Control': 'no-cache, no-store, max-age=0',
            'Pragma': 'no-cache',
            'Expires': '0',
        }

        def to_float(value):
            text = str(value or '').strip().replace(',', '')
            if not text:
                return None
            try:
                return float(text)
            except (TypeError, ValueError):
                return None

        def parse_google_csv(raw):
            if not raw or not raw.strip():
                raise ValueError('NIFTY 50 CSV response was empty')
            rows = list(csv.reader(io.StringIO(raw)))
            for row in rows:
                if not row or len(row) < 2:
                    continue
                value = to_float(row[0])
                change = to_float(row[1])
                pct = to_float(row[2]) if len(row) >= 3 else None
                if value is not None and change is not None:
                    return value, change, pct
            raise ValueError('NIFTY 50 CSV did not contain numeric index and change values')

        def parse_yahoo(raw):
            try:
                data = json.loads(raw.decode('utf-8'))
            except Exception as exc:
                raise ValueError(f'Yahoo NIFTY JSON parse failed: {exc}') from exc
            result = ((data.get('chart') or {}).get('result') or [None])[0]
            meta = result.get('meta') if isinstance(result, dict) else None
            if not isinstance(meta, dict):
                raise ValueError('Yahoo NIFTY response did not contain chart metadata')
            value = to_float(meta.get('regularMarketPrice'))
            previous_close = to_float(meta.get('previousClose'))
            if previous_close is None:
                previous_close = to_float(meta.get('chartPreviousClose'))
            if value is None or previous_close is None:
                raise ValueError('Yahoo NIFTY response missing current or previous-close value')
            change = value - previous_close
            pct = (change / previous_close * 100.0) if previous_close else None
            return value, change, pct

        errors = []
        # Source 1: independently verified live Yahoo Finance endpoint.
        try:
            req = Request(NIFTY_YAHOO_URL, headers=headers, method='GET')
            with urlopen(req, timeout=15) as resp:
                raw = resp.read()
            value, change, pct = parse_yahoo(raw)
            return {
                'index': 'NIFTY 50',
                'value': value,
                'change': change,
                'percent_change': pct,
                'timestamp': iso_now(),
                'source': 'Yahoo Finance NIFTY 50 live endpoint',
            }
        except Exception as exc:
            errors.append(f'Yahoo Finance NIFTY 50 live endpoint: {exc}')

        # Source 2: published Google Sheets CSV, with a unique cache-buster.
        try:
            req = Request(self._nifty_csv_request_url(NIFTY_CSV_URL), headers=headers, method='GET')
            with urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8-sig', errors='replace')
            value, change, pct = parse_google_csv(raw)
            return {
                'index': 'NIFTY 50',
                'value': value,
                'change': change,
                'percent_change': pct,
                'timestamp': iso_now(),
                'source': 'Google Sheets GOOGLEFINANCE CSV proxy (published CSV endpoint)',
            }
        except Exception as exc:
            errors.append(f'Google Sheets published CSV: {exc}')

        raise RuntimeError('Unable to fetch a valid NIFTY 50 quote from configured live sources: ' + '; '.join(errors))

    def _market_status_payload(self):
        snapshot = self._nse_market_snapshot()
        with self._nifty_lock:
            quote = dict(self._nifty_quote) if isinstance(self._nifty_quote, dict) else None
            error = self._nifty_last_error
            next_fetch_mono = getattr(self, '_nifty_next_fetch_mono', None)
        payload = dict(snapshot)
        payload['nifty'] = quote
        payload['nifty_available'] = quote is not None
        payload['nifty_last_error'] = error
        payload['nifty_last_update'] = quote.get('timestamp') if quote else None
        db = getattr(self, 'db', None)
        interval = db.get_nifty_poll_interval() if db is not None else DEFAULT_NIFTY_POLL_INTERVAL_SECONDS
        if snapshot.get('market_open') and interval > 0 and next_fetch_mono is not None:
            remaining = max(0.0, next_fetch_mono - time.monotonic())
            payload['nifty_next_update_in_seconds'] = remaining
            payload['nifty_next_update_at'] = (datetime.now(timezone.utc) + timedelta(seconds=remaining)).isoformat().replace('+00:00', 'Z')
        else:
            payload['nifty_next_update_in_seconds'] = None
            payload['nifty_next_update_at'] = None
        payload['nifty_poll_interval_seconds'] = interval
        return payload

    def _request_nifty_integration_state_write(self):
        """Queue a NIFTY state publication without blocking the poller."""
        with self._nifty_state_write_condition:
            self._nifty_state_write_pending = True
            self._nifty_state_write_condition.notify()

    def _nifty_state_writer_loop(self):
        """Serialize NIFTY integration-state writes on a dedicated thread."""
        while True:
            with self._nifty_state_write_condition:
                while not self._nifty_state_write_pending:
                    self._nifty_state_write_condition.wait()
                self._nifty_state_write_pending = False
            try:
                self._write_nifty_integration_state()
            except Exception as exc:
                _LOGGER.exception('Unable to publish queued NIFTY integration state: %s', exc)

    def _market_status_loop(self):
        """Keep NSE status responsive and poll NIFTY only during market hours.

        The scheduler uses a monotonic clock so wall-clock changes cannot
        stretch/shorten the configured NIFTY interval. Market-open detection is
        checked every few seconds, and entering market hours triggers an
        immediate quote fetch. The integration export is written only when
        meaningful market state or quote data changes; the volatile
        ``status_checked_at`` field is deliberately excluded from the state
        signature so a full portfolio export is not rebuilt every few seconds.
        """
        last_status_signature = None
        next_nifty_fetch_mono = None
        last_interval = None
        while True:
            try:
                snapshot = self._nse_market_snapshot()
                # Ignore status_checked_at when deciding whether the integration
                # snapshot needs a full rewrite; that timestamp changes every
                # loop and must not force expensive portfolio reconstruction.
                snapshot_signature = json.dumps({
                    'market_open': bool(snapshot.get('market_open')),
                    'status': snapshot.get('status'),
                    'reason': snapshot.get('reason'),
                    'date': snapshot.get('date'),
                }, sort_keys=True)
                state_changed = snapshot_signature != last_status_signature
                quote_changed = False
                nifty_fetch_attempted = False
                interval = max(0, int(self.db.get_nifty_poll_interval() or 0))
                now_mono = time.monotonic()
                market_open = bool(snapshot.get('market_open'))

                # Changing the interval should take effect immediately during
                # market hours rather than waiting for the old deadline.
                if interval != last_interval:
                    last_interval = interval
                    next_nifty_fetch_mono = now_mono if market_open and interval > 0 else None

                if not market_open or interval <= 0:
                    next_nifty_fetch_mono = None
                elif next_nifty_fetch_mono is None or now_mono >= next_nifty_fetch_mono:
                    nifty_fetch_attempted = True
                    # Measure the cadence from the fetch START, not completion.
                    # Network latency must not silently stretch a configured
                    # 60-second interval to 65/70/75+ seconds.
                    fetch_start_mono = time.monotonic()
                    try:
                        quote = self._fetch_nifty50_quote()
                        new_core = (
                            float(quote.get('value')) if quote.get('value') is not None else None,
                            float(quote.get('change')) if quote.get('change') is not None else None,
                            float(quote.get('percent_change')) if quote.get('percent_change') is not None else None,
                        )
                        with self._nifty_lock:
                            previous = dict(self._nifty_quote) if isinstance(self._nifty_quote, dict) else None
                            previous_core = (
                                float(previous.get('value')) if previous and previous.get('value') is not None else None,
                                float(previous.get('change')) if previous and previous.get('change') is not None else None,
                                float(previous.get('percent_change')) if previous and previous.get('percent_change') is not None else None,
                            )

                            # Accept a validated quote from either live source.
                            # Yahoo is primary; the published CSV is a secondary
                            # fallback. Never use the old cached Visualization feed.
                            if new_core != previous_core or previous is None:
                                quote['data_changed_at'] = quote.get('timestamp') or iso_now()
                            else:
                                quote['data_changed_at'] = previous.get('data_changed_at') or previous.get('timestamp') or quote.get('timestamp')
                            self._nifty_quote = quote
                            self._nifty_last_error = None if 'Yahoo Finance' in str(quote.get('source') or '') else 'Yahoo Finance primary failed; using published Google Sheets CSV fallback'

                        # A successful fetch still needs an integration-state
                        # write so the existing HA countdown/next-update behavior
                        # remains accurate. Numeric quote changes are tracked
                        # separately from the fetch timestamp.
                        quote_changed = new_core != previous_core
                    except Exception as exc:
                        with self._nifty_lock:
                            previous_error = self._nifty_last_error
                            self._nifty_last_error = str(exc)
                        quote_changed = previous_error != self._nifty_last_error
                    # Anchor the next deadline to the beginning of this fetch.
                    # If the request itself overruns the interval, the next
                    # 5-second scheduler tick will immediately perform the
                    # overdue fetch rather than adding another full interval.
                    next_nifty_fetch_mono = fetch_start_mono + interval

                with self._nifty_lock:
                    self._nifty_next_fetch_mono = next_nifty_fetch_mono

                if state_changed or quote_changed or nifty_fetch_attempted:
                    # Keep NIFTY/market-status publishing independent from the
                    # expensive full portfolio export. The NIFTY scheduler must
                    # never wait for build_portfolio()/XIRR/SIP calculations.
                    self._request_nifty_integration_state_write()
                    last_status_signature = snapshot_signature
            except Exception as exc:
                _LOGGER.exception('NSE/market status loop failed: %s', exc)
            time.sleep(MARKET_STATUS_CHECK_INTERVAL_SECONDS)

    def _holiday_refresh_loop(self):
        while True:
            try:
                self.refresh_nse_holiday_calendar()
                try:
                    self._write_integration_state()
                except Exception:
                    pass
            except Exception:
                pass
            now = datetime.now(self._tzinfo())
            next_run = now.replace(hour=HOLIDAY_REFRESH_HOUR, minute=HOLIDAY_REFRESH_MINUTE, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            time.sleep(max(60, (next_run - now).total_seconds()))

    def _tzinfo(self):
        try:
            return ZoneInfo(self.timezone_name)
        except Exception:
            return timezone.utc

    def _sip_transaction_exists_for_cycle(self, fund, cycle_date, effective_date=None):
        """Return True when a SIP transaction/execution record represents a cycle."""
        try:
            hid = int(fund['id'])
        except (KeyError, TypeError, ValueError):
            return False
        cycle_iso = cycle_date.isoformat()
        try:
            if self.db.live_sip_execution_for_cycle(hid, cycle_iso):
                return True
        except Exception:
            pass
        try:
            if self.db.sip_execution_for_cycle(hid, cycle_iso):
                return True
        except Exception:
            pass
        try:
            if self.db.live_sip_transaction_for_cycle(hid, cycle_iso):
                return True
        except Exception:
            pass
        effective_dates = {cycle_date}
        if effective_date:
            effective_dates.add(effective_date)
        try:
            rows = self.db.transactions(hid)
        except Exception:
            rows = []
        for row in rows:
            try:
                if str(row['txn_type'] or '') != 'sip':
                    continue
                txn_date = parse_optional_date(row['txn_date'])
                if txn_date in effective_dates:
                    return True
                note = str(row['note'] or '')
                if cycle_iso in note and ('SIP' in note or 'sip' in note):
                    return True
            except Exception:
                continue
        return False

    def _sip_execution_index(self, profile_id):
        """Build separate accounted-cycle and live-execution indexes for SIP status."""
        accounted_cycle_keys = set()
        executed_cycle_keys = set()
        txn_dates = {}
        try:
            rows = self.db.sip_executions(profile_id)
        except Exception:
            rows = []
        for row in rows:
            try:
                accounted_cycle_keys.add((int(row['holding_id']), str(row['sip_date'])))
            except Exception:
                continue
        try:
            rows = self.db.live_sip_executions(profile_id)
        except Exception:
            rows = []
        for row in rows:
            try:
                key = (int(row['holding_id']), str(row['sip_date']))
                accounted_cycle_keys.add(key)
                executed_cycle_keys.add(key)
            except Exception:
                continue
        try:
            rows = self.db.sip_transactions(profile_id)
        except Exception:
            rows = []
        import re as _re
        for row in rows:
            try:
                hid = int(row['holding_id'])
                txn_date = str(row['txn_date'])
                txn_dates.setdefault(hid, set()).add(txn_date)
                note = str(row['note'] or '')
                scheduled_matches = _re.findall(r'(?:Scheduled|Historical) SIP for (\d{4}-\d{2}-\d{2})', note, flags=_re.IGNORECASE)
                deleted_matches = _re.findall(r'SIP skipped/deleted for (\d{4}-\d{2}-\d{2})', note, flags=_re.IGNORECASE)
                for match in scheduled_matches:
                    accounted_cycle_keys.add((hid, match))
                for match in deleted_matches:
                    accounted_cycle_keys.add((hid, match))
                try:
                    has_source = 'source' in row.keys()
                except Exception:
                    has_source = False
                source = str(row['source'] or '') if has_source else ''
                if scheduled_matches and not deleted_matches and ((not has_source) or source == 'sip_execution'):
                    # A non-zero live-SIP ledger row can confirm execution when the dedicated live row
                    # is temporarily absent (for example during crash recovery). A deleted zero marker
                    # uses a different note and therefore never enters this executed index.
                    for match in scheduled_matches:
                        executed_cycle_keys.add((hid, match))
            except Exception:
                continue
        return accounted_cycle_keys, executed_cycle_keys, txn_dates

    @staticmethod
    def _month_start(value):
        return date(value.year, value.month, 1)

    @staticmethod
    def _next_month(value):
        if value.month == 12:
            return date(value.year + 1, 1, 1)
        return date(value.year, value.month + 1, 1)

    def _sip_cycle_date_for_month(self, month_start, sip_day):
        import calendar
        return date(month_start.year, month_start.month, min(int(sip_day), calendar.monthrange(month_start.year, month_start.month)[1]))

    def _next_nse_open_after(self, value):
        """First NSE-open date strictly after value; weekdays are the safe fallback for missing calendar dates."""
        for offset in range(1, 370):
            candidate = value + timedelta(days=offset)
            status = self._holiday_status(candidate)
            if bool(status.get('nse_open')):
                return candidate
        return None

    def _next_expected_sip_schedule(self, funds, today):
        """Project the next SIP date from active SIP schedules only.

        This intentionally does not consult transaction history, execution logs,
        last_sip_cycle, NAV readiness, or SIP execution state. Those concerns
        belong to the execution/status sensors. Next Expected SIP is a schedule
        projection: for each fund, keep only SIP dates strictly after that fund's
        own latest NAV date, select the earliest remaining SIP date, adjust only
        when that scheduled date itself is not an NSE working day, and expect the
        NAV on the following calendar day.
        """
        active = []
        for fund in funds:
            try:
                enabled = bool(fund['sip_enabled'])
                amount = float(fund['sip_amount'] or 0)
                day = int(fund['sip_day'])
            except (KeyError, TypeError, ValueError):
                continue
            if not enabled or amount <= 0 or day <= 0:
                continue
            initial_date = None
            raw_initial = fund['initial_date'] if 'initial_date' in fund.keys() else None
            if raw_initial:
                initial_date = parse_optional_date(raw_initial)
            active.append((fund, day, amount, initial_date))

        if not active:
            return {
                'selected': [],
                'expected_date': None,
                'working_sip_date': None,
                'reason': None,
            }

        def entries_for_month(month_start):
            entries = []
            for fund, day, amount, initial_date in active:
                cycle_date = self._sip_cycle_date_for_month(month_start, day)
                if initial_date and cycle_date < initial_date:
                    continue
                entries.append({
                    'fund': fund,
                    'cycle_date': cycle_date,
                    'amount': amount,
                })
            entries.sort(key=lambda item: (item['cycle_date'], str(item['fund']['scheme_name']).lower(), int(item['fund']['id']) if 'id' in item['fund'].keys() else 0))
            return entries

        # A SIP date remains pending until NAV for that specific fund has
        # reached/passed the scheduled SIP date. This is intentionally
        # fund-specific; do not use today's date or a global/latest portfolio NAV.
        def is_pending(item):
            raw_latest_nav = item['fund']['last_nav_date'] if 'last_nav_date' in item['fund'].keys() else None
            latest_nav = parse_optional_date(raw_latest_nav) if raw_latest_nav else None
            return latest_nav is None or item['cycle_date'] > latest_nav

        current_entries = [
            item for item in entries_for_month(self._month_start(today))
            if is_pending(item)
        ]

        # If the current month has no SIP dates left, continue with the next month.
        if not current_entries:
            current_entries = [
                item for item in entries_for_month(self._next_month(self._month_start(today)))
                if is_pending(item)
            ]
            if not current_entries:
                # Keep searching future months rather than returning a false empty
                # sensor when all current/next-month schedules are temporarily absent.
                month = self._next_month(self._next_month(self._month_start(today)))
                for _ in range(35):
                    current_entries = [item for item in entries_for_month(month) if is_pending(item)]
                    if current_entries:
                        break
                    month = self._next_month(month)

        if not current_entries:
            return {
                'selected': [],
                'expected_date': None,
                'working_sip_date': None,
                'reason': None,
            }

        first_sip_date = current_entries[0]['cycle_date']
        working_sip_date = first_sip_date
        while not bool(self._holiday_status(working_sip_date).get('nse_open')):
            working_sip_date += timedelta(days=1)

        expected_date = working_sip_date + timedelta(days=1)

        if working_sip_date == first_sip_date:
            selected = [item for item in current_entries if item['cycle_date'] == first_sip_date]
        else:
            selected = [item for item in current_entries if item['cycle_date'] <= working_sip_date]

        return {
            'selected': selected,
            'expected_date': expected_date,
            'working_sip_date': working_sip_date,
            'reason': 'schedule_projection',
        }

    def _delayed_sip_status_for_fund(self, fund, today):
        """Return NAV-delay details for an active SIP fund.

        A fund is considered delayed when today is on or after the
        date obtained by taking the next NSE working day after the latest
        NAV date and adding two calendar days.
        """
        # NAV freshness is independent of SIP configuration. Every holding
        # needs an up-to-date NAV for portfolio valuation, even when it has
        # no active SIP.
        latest_nav_date = parse_optional_date(fund['last_nav_date'])
        if latest_nav_date is None:
            return None
        next_working_nav_date = self._next_nse_open_after(latest_nav_date)
        if next_working_nav_date is None:
            return None
        delayed_after = next_working_nav_date + timedelta(days=2)
        if today < delayed_after:
            return None
        return {
            'fund_name': fund['scheme_name'],
            'holding_id': int(fund['id']) if 'id' in fund.keys() else 0,
            'latest_nav_date': latest_nav_date.isoformat(),
            'next_working_nav_date': next_working_nav_date.isoformat(),
            'delayed_after': delayed_after.isoformat(),
            'days_since_latest_nav': (today - latest_nav_date).days,
        }

    def _sip_status_for_fund(self, fund, today, execution_index=None):
        """Return the next unexecuted SIP cycle plus current-month execution status."""
        if not fund['sip_enabled'] or not fund['sip_amount'] or not fund['sip_day']:
            return None

        day = int(fund['sip_day'])
        current_month = self._month_start(today)
        raw_initial_date = fund['initial_date'] if 'initial_date' in fund.keys() else None
        initial_date = parse_optional_date(raw_initial_date) if raw_initial_date else None

        current_cycle = self._sip_cycle_date_for_month(current_month, day)
        current_effective = self._effective_sip_trading_date(current_cycle)
        last_cycle = str(fund['last_sip_cycle'] or '').strip()

        # Transaction history is used only as a targeted verification for the
        # current candidate, not as the primary source for building the SIP list.
        current_cycle_iso = current_cycle.isoformat()
        hid = int(fund['id']) if 'id' in fund.keys() else 0
        current_accounted = False
        current_executed = False
        if execution_index is not None:
            accounted_cycle_keys, executed_cycle_keys, _txn_dates = execution_index
            current_accounted = (hid, current_cycle_iso) in accounted_cycle_keys
            current_executed = (hid, current_cycle_iso) in executed_cycle_keys
        else:
            current_accounted = self._sip_transaction_exists_for_cycle(fund, current_cycle, current_effective)
            try:
                current_executed = bool(self.db.live_sip_execution_for_cycle(hid, current_cycle_iso))
            except Exception:
                current_executed = False

        start_month = current_month
        if initial_date:
            start_month = max(start_month, self._month_start(initial_date))
        if re.fullmatch(r'\d{4}-\d{2}', last_cycle):
            try:
                y, m = (int(x) for x in last_cycle.split('-', 1))
                start_month = max(start_month, self._next_month(date(y, m, 1)))
            except Exception:
                pass
        # If the current month's cycle is already executed, the next pending
        # cycle must be the following month regardless of the cached marker.
        if current_accounted:
            start_month = max(start_month, self._next_month(current_month))

        candidate = start_month
        cycle_date = None
        effective_date = None
        for _ in range(36):
            cycle_date = self._sip_cycle_date_for_month(candidate, day)
            effective_date = self._effective_sip_trading_date(cycle_date)
            if effective_date is not None:
                break
            candidate = self._next_month(candidate)
        else:
            return None

        latest_nav_date = parse_optional_date(fund['last_nav_date'])
        nav_ready = bool(latest_nav_date and effective_date and latest_nav_date >= effective_date)
        due_or_delayed = bool(effective_date and effective_date <= today)
        delayed_nav = bool(due_or_delayed and not nav_ready)
        ready_now = bool(due_or_delayed and nav_ready)

        return {
            'fund': fund,
            'cycle_date': cycle_date,
            'effective_date': effective_date,
            'latest_nav_date': latest_nav_date,
            'nav_ready': nav_ready,
            'due_or_delayed': due_or_delayed,
            'delayed_nav': delayed_nav,
            'ready_now': ready_now,
            'current_executed': current_executed,
            'current_cycle_date': current_cycle,
            'current_effective_date': current_effective,
        }

    def _build_sip_status(self, profile_id):
        """Build one authoritative SIP classification used by all SIP sensors."""
        today = self.local_today()
        funds = self.db.all_funds(profile_id)
        execution_index = self._sip_execution_index(profile_id)
        statuses = []
        delayed_sips = []
        for fund in funds:
            delayed = self._delayed_sip_status_for_fund(fund, today)
            if delayed:
                delayed_sips.append(delayed)
            status = self._sip_status_for_fund(fund, today, execution_index=execution_index)
            if not status:
                continue
            status['delayed_sip'] = bool(delayed)
            statuses.append(status)

        delayed_sips.sort(key=lambda x: (x['latest_nav_date'], x['fund_name'].lower(), x['holding_id']))

        statuses.sort(key=lambda item: (item['cycle_date'], str(item['fund']['scheme_name']).lower(), int(item['fund']['id']) if 'id' in item['fund'].keys() else 0))
        groups = []
        grouped = {}
        for item in statuses:
            key = item['cycle_date'].isoformat()
            if key not in grouped:
                grouped[key] = {
                    'cycle_date': key,
                    'sip_date': item['cycle_date'].day,
                    'funds': [],
                    'effective_dates': set(),
                }
                groups.append(grouped[key])
            grouped[key]['funds'].append({
                'fund_name': item['fund']['scheme_name'],
                'holding_id': int(item['fund']['id']) if 'id' in item['fund'].keys() else 0,
                'amount': float(item['fund']['sip_amount'] or 0),
                'effective_date': item['effective_date'].isoformat() if item['effective_date'] else None,
                'latest_nav_date': item['latest_nav_date'].isoformat() if item['latest_nav_date'] else None,
                'nav_ready': item['nav_ready'],
                'delayed_nav': item['delayed_nav'],
                'due_or_delayed': item['due_or_delayed'],
                'ready_now': item['ready_now'],
                'delayed_sip': item.get('delayed_sip', False),
            })
            if item['effective_date']:
                grouped[key]['effective_dates'].add(item['effective_date'].isoformat())

        for group in groups:
            group['funds'].sort(key=lambda x: (x['fund_name'].lower(), x['holding_id']))
            group['effective_dates'] = sorted(group['effective_dates'])

        # Current-month Upcoming/Executed SIP sensors and table filters use a
        # deliberately simple schedule classification. Build the current-month
        # SIP schedule from active funds, then compare EACH fund's scheduled SIP
        # date with THAT SAME FUND's latest NAV date. No transaction history,
        # execution logs, cycle markers, or global NAV date is consulted here.
        monthly_sip_schedule = {}
        current_month = self._month_start(today)
        for fund in funds:
            try:
                enabled = bool(fund['sip_enabled'])
                amount = float(fund['sip_amount'] or 0)
                sip_day = int(fund['sip_day'])
            except (KeyError, TypeError, ValueError):
                continue
            if not enabled or amount <= 0 or sip_day <= 0:
                continue

            raw_initial = fund['initial_date'] if 'initial_date' in fund.keys() else None
            initial_date = parse_optional_date(raw_initial) if raw_initial else None
            cycle_date = self._sip_cycle_date_for_month(current_month, sip_day)
            if initial_date and cycle_date < initial_date:
                continue

            monthly_sip_schedule.setdefault(cycle_date.day, []).append({
                'fund': fund,
                'cycle_date': cycle_date,
                'amount': amount,
            })

        monthly_executed = []
        monthly_upcoming = []
        for sip_day in sorted(monthly_sip_schedule):
            for entry in monthly_sip_schedule[sip_day]:
                fund = entry['fund']
                cycle_date = entry['cycle_date']
                latest_nav_date = parse_optional_date(fund['last_nav_date'])
                hid = int(fund['id']) if 'id' in fund.keys() else 0
                effective_date = self._effective_sip_trading_date(cycle_date)
                nav_ready = bool(latest_nav_date and effective_date and latest_nav_date >= effective_date)
                delayed_nav = bool(cycle_date <= today and not nav_ready)
                next_open_after_effective = self._next_nse_open_after(effective_date) if effective_date else None
                detail = {
                    'fund_name': fund['scheme_name'],
                    'holding_id': hid,
                    'amount': float(fund['sip_amount'] or 0),
                    'cycle_date': cycle_date.isoformat(),
                    'sip_date': cycle_date.day,
                    'effective_date': effective_date.isoformat() if effective_date else None,
                    'latest_nav_date': latest_nav_date.isoformat() if latest_nav_date else None,
                    'delayed_nav': delayed_nav,
                    'nav_ready': nav_ready,
                    'expected_execution_date': (
                        today.isoformat() if cycle_date <= today else
                        next_open_after_effective.isoformat() if next_open_after_effective else None
                    ),
                }

                # Fund-specific comparison is the complete classification rule.
                # No transaction/execution state is consulted.
                if latest_nav_date is not None and cycle_date <= latest_nav_date:
                    monthly_executed.append(detail)
                else:
                    monthly_upcoming.append(detail)

        monthly_executed.sort(key=lambda x: x['fund_name'].lower())
        monthly_upcoming.sort(key=lambda x: (x['cycle_date'], x['fund_name'].lower()))
        monthly_executed.sort(key=lambda x: x['fund_name'].lower())
        monthly_upcoming.sort(key=lambda x: (x['cycle_date'], x['fund_name'].lower()))

        # Next Expected SIP is intentionally schedule-only. It does not depend on
        # execution bookkeeping, transaction notes, NAV readiness, or cached SIP-cycle markers.
        next_schedule = self._next_expected_sip_schedule(funds, today)
        selected = next_schedule['selected']
        next_expected_date = next_schedule['expected_date']
        expected_reason = next_schedule['reason']
        working_sip_date = next_schedule['working_sip_date']

        next_funds = sorted({item['fund']['scheme_name'] for item in selected})
        next_amounts = {}
        next_details = []
        for item in selected:
            name = item['fund']['scheme_name']
            amount = float(item['fund']['sip_amount'] or 0)
            next_amounts[name] = next_amounts.get(name, 0.0) + amount
            next_details.append({
                'fund_name': name,
                'holding_id': int(item['fund']['id']) if 'id' in item['fund'].keys() else 0,
                'amount': amount,
                'cycle_date': item['cycle_date'].isoformat(),
                'effective_date': self._effective_sip_trading_date(item['cycle_date']).isoformat() if self._effective_sip_trading_date(item['cycle_date']) else None,
                'latest_nav_date': ((parse_optional_date(item['fund']['last_nav_date']).isoformat() if ('last_nav_date' in item['fund'].keys() and parse_optional_date(item['fund']['last_nav_date'])) else None)),
                'nav_ready': bool(('last_nav_date' in item['fund'].keys()) and parse_optional_date(item['fund']['last_nav_date']) and working_sip_date and parse_optional_date(item['fund']['last_nav_date']) >= working_sip_date),
                'delayed_nav': False,
                'expected_execution_date': next_expected_date.isoformat() if next_expected_date else None,
            })
        next_details.sort(key=lambda x: (x['cycle_date'], x['fund_name'].lower(), x['holding_id']))


        return {
            'sip_date_fund_groups': groups,
            # Detailed records are the authoritative SIP status payloads.
            # Parallel funds/amounts/count/total fields are retained internally
            # for existing app/card calculations, but the HA integration exposes
            # only the relevant detail list for each SIP entity.
            'executed_sip_funds': [item['fund_name'] for item in monthly_executed],
            'executed_sip_count': len(monthly_executed),
            'executed_sip_amounts': {item['fund_name']: item['amount'] for item in monthly_executed},
            'executed_sip_total_amount': sum(item['amount'] for item in monthly_executed),
            'executed_sip_details': monthly_executed,
            'upcoming_sip_funds': [item['fund_name'] for item in monthly_upcoming],
            'upcoming_sip_count': len(monthly_upcoming),
            'upcoming_sip_amounts': {item['fund_name']: item['amount'] for item in monthly_upcoming},
            'upcoming_sip_total_amount': sum(item['amount'] for item in monthly_upcoming),
            'upcoming_sip_details': monthly_upcoming,
            'next_expected_sip_date': next_expected_date.isoformat() if next_expected_date else None,
            'next_expected_sip_funds': next_funds,
            'next_expected_sip_amounts': next_amounts,
            'next_expected_sip_total_amount': sum(next_amounts.values()),
            'next_expected_sip_count': len(selected),
            'next_expected_sip_last_nav_date': (
                min(
                    nav_date for nav_date in (parse_optional_date(item['fund']['last_nav_date']) for item in selected if 'last_nav_date' in item['fund'].keys() and item['fund']['last_nav_date'])
                    if nav_date
                ).isoformat()
                if any('last_nav_date' in item['fund'].keys() and item['fund']['last_nav_date'] for item in selected) else None
            ),
            'next_expected_sip_trading_date': working_sip_date.isoformat() if working_sip_date else None,
            'next_expected_sip_reason': expected_reason,
            'next_expected_sip_details': next_details,
            'delayed_nav_update_details': delayed_sips,
        }

    def _next_expected_sip_details(self, profile_id):
        """Backward-compatible detail view using the schedule-only projection."""
        today = self.local_today()
        funds = self.db.all_funds(profile_id)
        schedule = self._next_expected_sip_schedule(funds, today)
        selected = schedule['selected']
        next_date = schedule['expected_date']
        batch = [(item['cycle_date'], item['fund']) for item in selected]
        latest_dates = [parse_optional_date(item['fund']['last_nav_date']) for item in selected if 'last_nav_date' in item['fund'].keys() and item['fund']['last_nav_date']]
        latest_dates = [value for value in latest_dates if value]
        return next_date, batch, (min(latest_dates).isoformat() if latest_dates else None), (schedule['working_sip_date'].isoformat() if schedule['working_sip_date'] else None)

    def _next_expected_sip(self, profile_id):
        status = self._build_sip_status(profile_id)
        next_date = parse_optional_date(status.get('next_expected_sip_date'))
        return next_date, status.get('next_expected_sip_funds') or [], status.get('next_expected_sip_last_nav_date'), status.get('next_expected_sip_trading_date')

    def _sip_summary(self, profile_id):
        """Return one authoritative SIP classification plus today's execution details."""
        today_iso = self.sip_reporting_date().isoformat()
        status = self._build_sip_status(profile_id)
        executed_details = []
        try:
            report_date = self.sip_reporting_date()
            calendar_today = self.local_today()
            for row in self.db.live_sip_executions(profile_id):
                stored_execution_date = parse_optional_date(row['execution_date'])
                include = bool(stored_execution_date and stored_execution_date.isoformat() == today_iso)

                # Compatibility for live rows created before v1.0.0. Those rows
                # could store the calendar execution date (for example 26-Aug) even
                # though a delayed 25-Aug SIP executed before 23:00 on 26-Aug belongs
                # to the current logical 25-Aug reporting window. Keep such an existing
                # row visible without rewriting the financial execution history.
                if (not include and report_date != calendar_today
                        and stored_execution_date == calendar_today
                        and parse_optional_date(row['sip_date']) == report_date):
                    include = True

                if not include:
                    continue
                executed_details.append({
                    'fund_name': row['fund_name'],
                    'amount': float(row['amount'] or 0),
                    'units': float(row['units'] or 0),
                    'nav': float(row['nav']) if row['nav'] is not None else None,
                    'sip_date': row['sip_date'],
                    'nav_date': row['nav_date'],
                    'execution_date': row['execution_date'],
                    'executed_at': str(row['executed_at'] or ''),
                })
        except Exception:
            executed_details = []
        executed_details.sort(key=lambda x: (x['fund_name'].lower(), x.get('executed_at') or ''))
        executed = [item['fund_name'] for item in executed_details]
        executed_amounts = {item['fund_name']: item['amount'] for item in executed_details}
        status.update({
            'today_executed_sip_date': today_iso if executed_details else None,
            'today_executed_sip_funds': executed,
            'today_executed_sip_count': len(executed),
            'today_executed_sip_details': executed_details,
            'today_executed_sip_amounts': executed_amounts,
            'today_executed_sip_total_amount': sum(executed_amounts.values()),
            'nse_holiday': self._holiday_status(self.local_today()),
        })
        return status

    def _next_integration_state_revision(self):
        """Atomically advance the shared HA integration-state revision."""
        with self.db._lock, self.db.conn() as c:
            row = c.execute(
                'SELECT value FROM settings WHERE key=?',
                ('integration_state_revision',),
            ).fetchone()
            try:
                current = int(row['value'] if row else 0)
            except (TypeError, ValueError):
                current = 0
            revision = current + 1
            c.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                ('integration_state_revision', str(revision), iso_now()),
            )
            return revision

    def _integration_state_dirs(self):
        override = os.getenv('MFT_INTEGRATION_STATE_DIRS', '').strip()
        return (
            [Path(item.strip()) for item in override.split(os.pathsep) if item.strip()]
            if override
            else [Path('/share/mutual_fund_tracker'), Path('/config/mutual_fund_tracker')]
        )

    def _write_nifty_integration_state(self):
        """Publish only market/NIFTY state without rebuilding portfolio data."""
        state_dirs = self._integration_state_dirs()
        # Serialize the complete read/merge/revision/write operation with full
        # state exports so a slow portfolio export can never overwrite a newer
        # NIFTY-only snapshot with an older state_revision.
        with self._integration_state_lock:
            candidates = []
            for state_dir in state_dirs:
                target = state_dir / 'integration_state.json'
                try:
                    data = json.loads(target.read_text(encoding='utf-8'))
                    if not isinstance(data, dict) or int(data.get('version', 0) or 0) < 1:
                        continue
                    try:
                        revision = int(data.get('state_revision', 0) or 0)
                    except (TypeError, ValueError):
                        revision = 0
                    candidates.append((revision, str(data.get('updated_at') or ''), data))
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
            if not candidates:
                _LOGGER.warning('Unable to publish NIFTY state: no existing integration snapshot found')
                return
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            payload = dict(candidates[0][2])
            payload['state_revision'] = self._next_integration_state_revision()
            payload['updated_at'] = iso_now()
            payload['market_status'] = self._market_status_payload()
            encoded = json.dumps(payload, ensure_ascii=False, indent=2)
            written = 0
            failures = []
            for state_dir in state_dirs:
                try:
                    state_dir.mkdir(parents=True, exist_ok=True)
                    tmp = state_dir / 'integration_state.json.tmp'
                    target = state_dir / 'integration_state.json'
                    tmp.write_text(encoded, encoding='utf-8')
                    tmp.replace(target)
                    written += 1
                except Exception as exc:
                    failures.append(f'{state_dir}: {exc}')
                    _LOGGER.exception('Unable to write lightweight NIFTY integration state to %s', state_dir)
            if not written:
                raise RuntimeError('Unable to write Mutual Fund Tracker NIFTY integration state: ' + '; '.join(failures))

    def _write_integration_state(self):
        # Export to the HA host's shared /share mount. The custom integration
        # runs outside the add-on container and cannot read the add-on's
        # private addon_config mount. Keep /config export for compatibility.
        state_dirs = self._integration_state_dirs()
        for state_dir in state_dirs:
            state_dir.mkdir(parents=True, exist_ok=True)
        profiles = []
        for row in self.db.all_profiles():
            pid = int(row['id'])
            portfolio = build_portfolio(self.db, pid)
            profiles.append({
                'id': pid,
                'name': portfolio.get('profile', {}).get('name') if portfolio.get('profile') else row['name'],
                'pan': portfolio.get('profile', {}).get('pan') if portfolio.get('profile') else row['pan'],
                'fund_count': len(portfolio.get('rows', [])),
                'total': portfolio.get('total', {}),
                'funds': portfolio.get('rows', []),
                'nav_date': portfolio.get('nav_date'),
                'updated_at': portfolio.get('updated_at'),
                **self._sip_summary(pid),
            })
        all_portfolio = build_portfolio(self.db, None)
        reload_requested = str(self.db.get_setting('integration_reload_requested', '0')) == '1'
        reload_token = str(self.db.get_setting('integration_reload_token', '') or '')
        payload = {
            'version': 3,
            'state_revision': None,
            'updated_at': None,
            'last_nav_refresh': self.db.last_refresh_time(None),
            'nav_refresh_error': self.db.nav_refresh_error(),
            'integration_reload_requested': reload_requested,
            'integration_reload_token': reload_token,
            'profiles': profiles,
            'nse_holiday': self._holiday_status(datetime.now(NSE_TIMEZONE).date()),
            'market_status': self._market_status_payload(),
            'all_investors': {
                'name': 'All Investors',
                'total': all_portfolio.get('total', {}),
                'fund_count': len(all_portfolio.get('rows', [])),
                'funds': all_portfolio.get('rows', []),
                'nav_date': all_portfolio.get('nav_date'),
                'updated_at': all_portfolio.get('updated_at'),
                **self._sip_summary(None),
            },
        }
        written = 0
        failures = []
        with self._integration_state_lock:
            payload['state_revision'] = self._next_integration_state_revision()
            payload['updated_at'] = iso_now()
            encoded = json.dumps(payload, ensure_ascii=False, indent=2)
            for state_dir in state_dirs:
                try:
                    state_dir.mkdir(parents=True, exist_ok=True)
                    tmp = state_dir / 'integration_state.json.tmp'
                    target = state_dir / 'integration_state.json'
                    tmp.write_text(encoded, encoding='utf-8')
                    tmp.replace(target)
                    written += 1
                except Exception as exc:
                    failures.append(f'{state_dir}: {exc}')
                    _LOGGER.exception('Unable to write Mutual Fund Tracker integration state to %s', state_dir)
        if not written:
            raise RuntimeError('Unable to write Mutual Fund Tracker integration state: ' + '; '.join(failures))

    def update_ha_states(self, portfolio):
        try:
            self._write_integration_state()
        except Exception:
            pass
        token = os.getenv('SUPERVISOR_TOKEN')
        if not token:
            return
        total = portfolio['total']
        states = {
            'sensor.mutual_fund_total_value': (total['value'], 'Indian Rupee'),
            'sensor.mutual_fund_total_invested': (total['invested'], 'Indian Rupee'),
            'sensor.mutual_fund_total_profit': (total['profit'], 'Indian Rupee'),
            'sensor.mutual_fund_total_profit_percentage': (total['profit_pct'], '%'),
            'sensor.mutual_fund_daily_change': (total['day_change'], 'Indian Rupee'),
            'sensor.mutual_fund_monthly_change': (total['month_change'], 'Indian Rupee'),
            'sensor.mutual_fund_yearly_change': (total['year_change'], 'Indian Rupee'),
            'sensor.mutual_fund_portfolio_xirr': (total['xirr'], '%'),
        }
        def _push(item):
            entity_id, (state, unit) = item
            try:
                attrs = {'friendly_name': entity_id.replace('sensor.', '').replace('_', ' ').title(), 'unit_of_measurement': unit}
                self.ha_request('POST', f'http://supervisor/core/api/states/{entity_id}', {'state': state if state is not None else 'unknown', 'attributes': attrs})
            except Exception:
                pass
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(_push, states.items()))

    @staticmethod
    def _norm_scheme_name(value: str) -> str:
        s = str(value or '').upper()
        s = re.sub(r'\(.*?\)', ' ', s)
        s = s.replace('&', ' AND ')
        s = re.sub(r'\b(FORMERLY|EARLIER KNOWN AS)\b.*$', ' ', s)
        s = re.sub(r'\bOPTION\b', ' ', s)
        s = re.sub(r'[^A-Z0-9]+', ' ', s)
        stop = {'FUND','PLAN','DIRECT','REGULAR','GROWTH','GROWTHOPTION','DIRECTPLAN'}
        tokens = [t for t in s.split() if t not in stop]
        return ' '.join(tokens)

    def _load_amfi_catalog(self, force=False):
        now = time.time()
        if not force and self._amfi_catalog is not None and (now - self._amfi_catalog_at) < 6 * 3600:
            return self._amfi_catalog
        with self._amfi_lock:
            now = time.time()
            if not force and self._amfi_catalog is not None and (now - self._amfi_catalog_at) < 6 * 3600:
                return self._amfi_catalog
            last_exc = None
            raw = None
            for url in AMFI_NAV_URLS:
                try:
                    raw = request_bytes(url, timeout=30)
                    break
                except Exception as exc:
                    last_exc = exc
            if raw is None:
                raise RuntimeError(f'Unable to download AMFI scheme catalogue: {last_exc}')
            text = raw.decode('utf-8-sig', errors='replace')
            by_code = {}
            by_isin = {}
            rows = []
            for line in text.splitlines():
                parts = [p.strip() for p in line.split(';')]
                if len(parts) < 6 or not parts[0].isdigit():
                    continue
                code, isin_growth, isin_reinv, scheme_name, nav, nav_date = parts[:6]
                row = {
                    'scheme_code': code,
                    'isin_growth': isin_growth.upper(),
                    'isin_reinv': isin_reinv.upper(),
                    'scheme_name': scheme_name,
                    'nav': nav,
                    'date': nav_date,
                    'norm_name': self._norm_scheme_name(scheme_name),
                }
                by_code[code] = row
                for isin in (isin_growth.upper(), isin_reinv.upper()):
                    if isin and isin != '-':
                        by_isin.setdefault(isin, []).append(row)
                rows.append(row)
            if not rows:
                raise RuntimeError('AMFI scheme catalogue contained no usable scheme rows')
            self._amfi_catalog = {'by_code': by_code, 'by_isin': by_isin, 'rows': rows}
            self._amfi_catalog_at = time.time()
            return self._amfi_catalog

    def resolve_scheme_identity(self, code: str | None, name: str, isin: str | None):
        """Resolve an import fund to the correct AMFI/MFAPI scheme code.

        AI-generated scheme codes are treated as hints only. ISIN is the strongest
        identifier, followed by an exact/strong scheme-name match, then a verified
        code lookup. This prevents a bad AI code from silently selecting another fund.
        """
        code = str(code or '').strip()
        name = str(name or '').strip()
        isin = str(isin or '').strip().upper()
        warnings = []
        try:
            catalog = self._load_amfi_catalog()
        except Exception as catalog_exc:
            # Network fallback: still verify a supplied code/name through MFAPI rather
            # than failing the entire import because the AMFI catalogue is temporarily
            # unreachable. This also handles occasional AMFI TLS/availability issues.
            if code and code.isdigit():
                try:
                    data = request_json(f'{MFAPI_BASE}/mf/{code}')
                    meta = data.get('meta') or {} if isinstance(data, dict) else {}
                    canonical_code = str(meta.get('scheme_code') or code).strip()
                    canonical_name = str(meta.get('scheme_name') or '').strip()
                    if canonical_name and (not name or difflib.SequenceMatcher(None, self._norm_scheme_name(name), self._norm_scheme_name(canonical_name)).ratio() >= 0.68):
                        if canonical_code != code:
                            warnings.append(f'scheme code normalized from {code} to {canonical_code} using MFAPI')
                        warnings.append(f'AMFI catalogue unavailable during import; scheme verified through MFAPI ({catalog_exc})')
                        return canonical_code, canonical_name, warnings
                except Exception:
                    pass
            if name:
                try:
                    results = request_json(f'{MFAPI_BASE}/mf/search?q={urlquote(name)}')
                    if isinstance(results, list) and results:
                        norm = self._norm_scheme_name(name)
                        ranked = sorted(results, key=lambda r: difflib.SequenceMatcher(None, norm, self._norm_scheme_name(r.get('schemeName') or r.get('scheme_name') or '')).ratio(), reverse=True)
                        best = ranked[0]
                        best_name = str(best.get('schemeName') or best.get('scheme_name') or '').strip()
                        best_code = str(best.get('schemeCode') or best.get('scheme_code') or '').strip()
                        if best_code and best_name and difflib.SequenceMatcher(None, norm, self._norm_scheme_name(best_name)).ratio() >= 0.72:
                            warnings.append(f'AMFI catalogue unavailable; scheme resolved through MFAPI search ({catalog_exc})')
                            return best_code, best_name, warnings
                except Exception:
                    pass
            raise ValueError(f'Unable to verify scheme against AMFI or MFAPI: {catalog_exc}')

        # 1) Exact ISIN match.
        if isin:
            matches = catalog['by_isin'].get(isin, [])
            if len(matches) == 1:
                row = matches[0]
                if code and code != row['scheme_code']:
                    warnings.append(f'scheme code corrected from {code} to {row["scheme_code"]} using ISIN {isin}')
                return row['scheme_code'], row['scheme_name'], warnings
            if len(matches) > 1:
                # Prefer a growth/direct name match when multiple rows share an ISIN.
                norm = self._norm_scheme_name(name)
                scored = sorted(matches, key=lambda r: difflib.SequenceMatcher(None, norm, r['norm_name']).ratio(), reverse=True)
                row = scored[0]
                if code and code != row['scheme_code']:
                    warnings.append(f'scheme code corrected from {code} to {row["scheme_code"]} using ISIN {isin}')
                return row['scheme_code'], row['scheme_name'], warnings

        norm = self._norm_scheme_name(name)

        # 2) Exact/near-exact name match.
        if norm:
            exact = [r for r in catalog['rows'] if r['norm_name'] == norm]
            if len(exact) == 1:
                row = exact[0]
                if code and code != row['scheme_code']:
                    warnings.append(f'scheme code corrected from {code} to {row["scheme_code"]} using scheme name')
                return row['scheme_code'], row['scheme_name'], warnings
            if exact:
                scored = sorted(exact, key=lambda r: difflib.SequenceMatcher(None, norm, r['norm_name']).ratio(), reverse=True)
                row = scored[0]
                if code and code != row['scheme_code']:
                    warnings.append(f'scheme code corrected from {code} to {row["scheme_code"]} using scheme name')
                return row['scheme_code'], row['scheme_name'], warnings

            # Search a limited candidate set containing the same major fund tokens.
            candidates = []
            name_tokens = set(norm.split())
            for row in catalog['rows']:
                row_tokens = set(row['norm_name'].split())
                overlap = len(name_tokens & row_tokens)
                if overlap >= max(2, min(4, len(name_tokens))):
                    score = difflib.SequenceMatcher(None, norm, row['norm_name']).ratio()
                    if score >= 0.72:
                        candidates.append((score, overlap, row))
            if candidates:
                candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                best = candidates[0][2]
                if len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.03:
                    if code and code != best['scheme_code']:
                        warnings.append(f'scheme code corrected from {code} to {best["scheme_code"]} using scheme name similarity')
                    return best['scheme_code'], best['scheme_name'], warnings

        # 3) Verified provided code as last resort.
        if code and code in catalog['by_code']:
            row = catalog['by_code'][code]
            # If a name was supplied, require a reasonable match; otherwise accept verified code.
            if not norm or difflib.SequenceMatcher(None, norm, row['norm_name']).ratio() >= 0.72:
                return code, row['scheme_name'], warnings

        raise ValueError(f'Could not resolve scheme to a verified AMFI code (name={name!r}, ISIN={isin!r}, supplied_code={code!r}). The statement JSON should contain the correct ISIN and fund name.')

    def search(self, q: str, mode: str = 'name'):
        q = (q or '').strip()
        if len(q) < 2:
            return []

        # Exact scheme-code lookup is useful because the MFAPI text search does not
        # necessarily return every scheme name. A direct /mf/<scheme_code> lookup
        # gives us the canonical scheme name from the fund metadata.
        if mode == 'code':
            if not q.isdigit():
                return []
            try:
                data = request_json(f'{MFAPI_BASE}/mf/{q}')
            except Exception:
                return []
            meta = data.get('meta') or {} if isinstance(data, dict) else {}
            code = str(meta.get('scheme_code') or q).strip()
            name = str(meta.get('scheme_name') or '').strip()
            if not name:
                return []
            return [{'schemeCode': code, 'schemeName': name}]

        data = request_json(f'{MFAPI_BASE}/mf/search?q={urlquote(q)}')
        return data[:50] if isinstance(data, list) else []

    def validate_import_document(self, data, progress_callback=None):
        if not isinstance(data, dict):
            raise ValueError('Import JSON must be an object')
        try:
            import_version = int(data.get('format_version', 0))
        except Exception:
            import_version = 0
        if import_version not in SUPPORTED_IMPORT_FORMATS:
            raise ValueError(f'Unsupported import format_version. Expected one of: {sorted(SUPPORTED_IMPORT_FORMATS)}')
        funds = data.get('funds')
        source = data.get('source') if isinstance(data.get('source'), dict) else {}
        statement_to = parse_optional_date(source.get('statement_to') or source.get('to')) if (source.get('statement_to') or source.get('to')) else None
        generated_raw = source.get('statement_generated_date') or source.get('generated_date') or source.get('statement_to') or source.get('to')
        generated_date = parse_optional_date(generated_raw) if generated_raw else None
        if generated_date:
            age_days = (self.local_today() - generated_date).days
            if age_days > 10:
                raise ValueError(f'This consolidated statement is {age_days} days old. Imports are limited to statements generated within the last 10 days; please upload a newer statement.')
            if age_days < 0:
                age_warning = f'Statement generated date {generated_date.isoformat()} is in the future relative to the application date.'
            else:
                age_warning = None
        else:
            age_warning = None
        raw_investors = data.get('investors')
        if raw_investors is None and isinstance(data.get('investor'), dict):
            raw_investors = [dict(data.get('investor'), ref='investor_1')]
        if raw_investors is None and isinstance(source.get('investor'), dict):
            raw_investors = [dict(source.get('investor'), ref='investor_1')]
        if raw_investors is None:
            raw_investors = []
        if not isinstance(raw_investors, list):
            raise ValueError('investors must be an array when provided')
        detected_investors=[]
        for idx, inv in enumerate(raw_investors, 1):
            try:
                norm=normalize_profile(inv)
            except Exception as exc:
                raise ValueError(f'Invalid investor {idx}: {exc}')
            ref=str(inv.get('ref') or f'investor_{idx}')
            matches=self.db.find_profile_matches(norm)
            exact_same = next((m for m in matches if m.get('match_type')=='pan' and not m.get('details_differ')), None)
            detected_investors.append({
                'ref':ref, **norm,
                'match':exact_same,
                'matches':matches,
                'identity_status': ('pan_match_same' if exact_same else ('pan_match_different' if any(m.get('match_type')=='pan' for m in matches) else ('fallback_match' if matches else 'no_match')))
            })
        is_template = bool(data.get('template')) or source.get('type') == 'MUTUAL_FUND_TRACKER_TEMPLATE'
        # Recognize the example template shipped in v0.1.6 so uploading that file does not
        # accidentally trigger real NAV reconstruction for the fake example scheme.
        if not is_template and isinstance(funds, list) and len(funds) == 1 and isinstance(funds[0], dict):
            sample_code = str(funds[0].get('scheme_code') or '').strip()
            sample_name = str(funds[0].get('scheme_name') or '').strip().lower()
            sample_isin = str(funds[0].get('isin') or '').strip().upper()
            if sample_code == '123456' and sample_name.startswith('example mutual fund') and sample_isin == 'INF000000000':
                is_template = True
        if is_template:
            return {
                'template': True,
                'errors': [],
                'warnings': ['This is a blank/example template. Fill in the fund data from your statement, then upload the completed JSON. Nothing will be imported from this template.'],
                'funds': [],
                'counts': {'funds': 0, 'transactions': 0}
            }
        if not isinstance(funds, list) or not funds:
            raise ValueError('Import JSON must contain a non-empty funds array')
        if len(funds) > MAX_IMPORT_FUNDS:
            raise ValueError(f'Maximum {MAX_IMPORT_FUNDS} funds per import')

        errors, warnings, normalized = [], [], []
        if age_warning:
            warnings.append(age_warning)
        tx_total = 0
        # A verified AMFI scheme_code identifies a scheme, not a unique holding.
        # Multiple folios for the same scheme are valid in one import. Track the
        # folios seen for each verified scheme_code so only a true duplicate
        # (same scheme + overlapping folio, or no folio information to distinguish
        # two records) is rejected.
        seen_codes = {}
        nav_cache = {}
        if progress_callback:
            progress_callback('Analysing funds', 10, f'Analysing {len(funds)} funds…')

        for i, fund in enumerate(funds, 1):
            if progress_callback:
                pct = 10 + int((i - 1) / max(1, len(funds)) * 68)
                progress_callback('Analysing funds', pct, f'Analysing fund {i} of {len(funds)}…')
            prefix = f'Fund {i}'
            if not isinstance(fund, dict):
                errors.append(f'{prefix}: must be an object')
                continue
            raw_code = str(fund.get('scheme_code') or '').strip()
            name = str(fund.get('scheme_name') or '').strip()
            isin = str(fund.get('isin') or '').strip().upper() or None
            if not name and not isin and not raw_code:
                errors.append(f'{prefix}: scheme_name or ISIN or scheme_code is required')
                continue
            try:
                code, canonical_name, resolve_warnings = self.resolve_scheme_identity(raw_code, name, isin)
                warnings.extend([f'{prefix}: {w}' for w in resolve_warnings])
                name = canonical_name or name
            except Exception as exc:
                errors.append(f'{prefix}: scheme could not be resolved: {exc}')
                continue
            incoming_folios_for_check = [
                self.db.normalize_folio(f)
                for f in (fund.get('folios') or [])
                if self.db.normalize_folio(f)
            ]
            code_seen_folios = seen_codes.get(code)
            if code_seen_folios is not None:
                incoming_folio_set = set(incoming_folios_for_check)
                # A verified scheme code identifies the scheme, not the holding.
                # Same scheme + same folio appearing twice in one JSON is therefore
                # an input duplicate, but it should not make the entire import fail:
                # the second occurrence can safely merge into the first holding and
                # its transactions can still be reconciled.
                if code_seen_folios and incoming_folio_set:
                    overlapping = code_seen_folios.intersection(incoming_folio_set)
                    if overlapping:
                        warnings.append(
                            f'{prefix}: duplicate holding record in import for verified scheme_code {code} '
                            f'and folio {sorted(overlapping)[0]}; records will be merged'
                        )
                    code_seen_folios.update(incoming_folio_set)
                else:
                    # Without a folio there is no safe way to distinguish two holdings
                    # for the same scheme, so retain the blocking duplicate protection.
                    errors.append(f'{prefix}: duplicate verified scheme_code {code} in import without distinct folio')
                    continue
            else:
                seen_codes[code] = set(incoming_folios_for_check)
            existing = self.db.get_fund_by_code(code)
            # Same scheme does not mean same holding: folio is the primary
            # identity for an investment. Warn about an existing holding only
            # when an incoming folio actually matches an existing folio for the
            # matched investor. A different folio must remain importable.
            if existing:
                # incoming_folios_for_check was normalized above for duplicate-scheme validation.
                matched_profile_id = None
                for _inv in detected_investors:
                    if _inv.get('ref') == fund.get('investor_ref') and _inv.get('match'):
                        matched_profile_id = _inv['match'].get('id')
                        break
                same_folio = False
                if matched_profile_id is not None:
                    existing_for_profile = self.db.get_fund_by_code(code, matched_profile_id)
                    if existing_for_profile:
                        existing_folio_value = existing_for_profile['normalized_folio'] or existing_for_profile['folio']
                        existing_folio_set_for_check = {str(existing_folio_value).strip().replace(' ', '')} if str(existing_folio_value or '').strip() else set()
                        same_folio = bool(existing_folio_set_for_check.intersection(incoming_folios_for_check))
                elif not incoming_folios_for_check:
                    same_folio = True
                if same_folio:
                    warnings.append(f'{name or code}: existing folio found; new transactions will be checked')

            cb = fund.get('closing_balance') or {}
            try:
                units = parse_float(cb.get('units', fund.get('units', 0)))
                invested = parse_money(cb.get('cost_value', fund.get('invested', 0)))
            except ValueError as exc:
                errors.append(f'{prefix}: {exc}')
                continue

            # Zero-holding funds are not portfolio positions. Skip them from import even
            # when the AI included historical transactions for a fully redeemed/closed fund.
            if units <= 1e-12 and invested <= 1e-12:
                warnings.append(f'{name or raw_code or "Fund"}: closing units and invested value are zero; excluded from import')
                continue

            normalized_txs = []
            import_nav_entries = None
            import_nav_snapshot = None

            # Fetch one NAV history per fund and reuse it for all compact SIP blocks.
            history = fund.get('history') if import_version >= 2 else None
            blocks_preview = (history.get('sip_blocks') or []) if isinstance(history, dict) else []
            block_starts=[]
            for _b in blocks_preview if isinstance(blocks_preview,list) else []:
                if isinstance(_b,dict) and _b.get('start_date'):
                    try: block_starts.append(parse_optional_date(_b.get('start_date')))
                    except Exception: pass
            try:
                # Latest/current NAV for an import is always taken from the AMFI
                # all-schemes snapshot. MFAPI remains strictly the historical NAV
                # provider for SIP reconstruction and other date-specific lookups.
                if 'import_latest_nav_snapshot' not in locals():
                    import_latest_nav_snapshot = self.request_latest_nav_snapshot(force=True, all_schemes=True)
                latest_record = import_latest_nav_snapshot.get(str(code).strip())
                if latest_record is None:
                    raise RuntimeError(f'Scheme {code} was not present in latest AMFI NAV snapshot')

                ref_date = self.local_today()
                # Import uses the same AMFI reference-NAV record as a normal refresh so the
                # freshly imported portfolio immediately has correct day/month/year
                # calculations. MFAPI is reserved here for targeted historical transaction
                # and SIP reconstruction below.
                latest_d = parse_optional_date(latest_record[0])
                self.update_nav_reference_table({code: (latest_d, float(latest_record[1]))}, scheme_codes=[code])
                ref = self._nav_reference(code) or self._compute_nav_reference(code, latest_record)
                import_nav_snapshot = {
                    'last_nav': float(latest_record[1]),
                    'last_nav_date': latest_d.isoformat(),
                    'previous_nav': ref['previous_nav'], 'previous_nav_date': ref['previous_nav_date'],
                    'month_nav': ref['month_nav'], 'month_nav_date': ref['month_nav_date'],
                    'year_nav': ref['year_nav'], 'year_nav_date': ref['year_nav_date'],
                    'last_refresh': iso_now(), 'last_error': None,
                }
                # MFAPI remains deliberately available here only for specific
                # historical transaction/SIP reconstruction. It is not used for
                # previous/month/year reference NAVs.
                if block_starts:
                    nav_start = min(block_starts) - timedelta(days=10)
                    import_nav_entries = self._nav_entries_for_range(code, nav_start, nav_cache)
                if progress_callback:
                    progress_callback('Fetching NAV history', 16 + int((i - 1) / max(1, len(funds)) * 10), f'Fetched NAV history for {name or code}')
            except Exception as exc:
                if block_starts:
                    errors.append(f'{prefix}: could not fetch NAV history: {exc}')

            # v2 compact history: recurring SIP blocks + exact irregular transactions.
            if isinstance(history, dict):
                # Exact SIP transactions take precedence over reconstructed compact SIP rows.
                # A well-formed statement may contain both a sip_block covering a month and
                # an exact transaction for that same month (for example, an exception or the
                # latest SIP). Reconstructing that month as well would create two economic
                # events with slightly different NAV/unit values. Track the exact SIP months
                # up front so reconstruction only fills genuinely missing months.
                exact = history.get('transactions') or []
                if not isinstance(exact, list):
                    errors.append(f'{prefix}: history.transactions must be an array')
                    exact = []
                explicit_sip_months = set()
                explicit_sip_months_no_folio = set()
                for _tx in exact:
                    if not isinstance(_tx, dict) or normalize_txn_type(str(_tx.get('type') or '')) != 'sip':
                        continue
                    _d = str(_tx.get('date') or '').strip()
                    try:
                        _dt = parse_optional_date(_d)
                    except Exception:
                        continue
                    _month = (_dt.year, _dt.month)
                    _folio = self.db.normalize_folio(_tx.get('folio'))
                    if _folio:
                        explicit_sip_months.add((_month, _folio))
                    else:
                        explicit_sip_months_no_folio.add(_month)

                blocks = history.get('sip_blocks') or []
                if not isinstance(blocks, list):
                    errors.append(f'{prefix}: history.sip_blocks must be an array')
                    blocks = []
                for bidx, block in enumerate(blocks, 1):
                    if not isinstance(block, dict):
                        errors.append(f'{prefix} SIP block {bidx}: must be an object')
                        continue
                    try:
                        amount = parse_money(block.get('amount', 0))
                        charges = parse_money(block.get('charges', 0))
                        count = int(block.get('count', 0))
                        start_date = parse_optional_date(block.get('start_date'))
                        end_date = parse_optional_date(block.get('end_date'))
                        sip_day = int(block.get('sip_day')) if block.get('sip_day') not in (None, '') else (start_date.day if start_date else None)
                    except (ValueError, TypeError) as exc:
                        errors.append(f'{prefix} SIP block {bidx}: {exc}')
                        continue
                    if amount <= 0 or count < 1 or not start_date:
                        errors.append(f'{prefix} SIP block {bidx}: amount, count and start_date are required')
                        continue
                    if count > 600:
                        errors.append(f'{prefix} SIP block {bidx}: maximum 600 recurring SIPs per block')
                        continue
                    if not sip_day or not (1 <= sip_day <= 31):
                        errors.append(f'{prefix} SIP block {bidx}: sip_day must be 1-31')
                        continue
                    if end_date and end_date < start_date:
                        errors.append(f'{prefix} SIP block {bidx}: end_date cannot be before start_date')
                        continue
                    try:
                        if progress_callback:
                            progress_callback('Reconstructing SIP history', 18 + int((i - 1) / max(1, len(funds)) * 55), f'Reconstructing SIP history for {name or code}…')
                        generated = self.historical_sip_transactions_from_entries(amount, sip_day, start_date, count, import_nav_entries) if import_nav_entries is not None else self.historical_sip_transactions(code, amount, sip_day, start_date, count)
                        # Never let a compact historical SIP block reconstruct transactions
                        # beyond the statement coverage date. Those recent missing occurrences
                        # are handled separately by the explicit SIP reconciliation workflow.
                        if statement_to:
                            generated = [tx for tx in generated if (parse_optional_date(tx.get('txn_date')) or statement_to) <= statement_to]
                    except Exception as exc:
                        errors.append(f'{prefix} SIP block {bidx}: could not reconstruct NAVs: {exc}')
                        continue
                    block_folio = self.db.normalize_folio(block.get('folio'))
                    filtered_generated = []
                    suppressed = []
                    for tx in generated:
                        try:
                            tx_date = parse_optional_date(tx.get('txn_date'))
                        except Exception:
                            tx_date = None
                        month_key = (tx_date.year, tx_date.month) if tx_date else None
                        # If the statement provides an exact SIP transaction for this month
                        # (and folio), keep the exact row and do not reconstruct another one.
                        # If the exact row has no folio, a single-folio block is still safely
                        # covered by the month-level fallback.
                        if month_key and (month_key in explicit_sip_months_no_folio or
                                          (block_folio and (month_key, block_folio) in explicit_sip_months)):
                            suppressed.append(tx_date.isoformat())
                            continue
                        tx['charges'] = charges
                        tx['cashflow'] = -(float(tx['amount']) + charges)
                        tx['folio'] = str(block.get('folio') or '').strip() or None
                        tx['source'] = 'compressed_sip'
                        tx['note'] = f'Compressed historical SIP block; scheduled day {sip_day}'
                        filtered_generated.append(tx)
                    if suppressed:
                        warnings.append(f'{prefix} SIP block {bidx}: {len(suppressed)} reconstructed SIP month(s) replaced by exact statement transaction(s)')
                    generated = filtered_generated
                    if end_date:
                        actual_end = parse_optional_date(generated[-1]['txn_date']) if generated else None
                        if actual_end and actual_end != end_date:
                            warnings.append(f'{prefix} SIP block {bidx}: reconstructed last transaction is {actual_end.isoformat()}, statement block end_date is {end_date.isoformat()}')
                    normalized_txs.extend(generated)
                    if sip_day != start_date.day:
                        warnings.append(f'{prefix} SIP block {bidx}: SIP schedule day {sip_day} differs from first observed transaction date {start_date.day}; using explicit sip_day')

            else:
                # v1 compatibility: exact transaction arrays are still accepted.
                exact = fund.get('transactions') or []
                if not isinstance(exact, list):
                    errors.append(f'{prefix}: transactions must be an array')
                    exact = []

            for j, tx in enumerate(exact, 1):
                if not isinstance(tx, dict):
                    errors.append(f'{prefix} transaction {j}: must be an object')
                    continue
                td = str(tx.get('date') or '').strip()
                try:
                    parse_optional_date(td)
                except ValueError:
                    errors.append(f'{prefix} transaction {j}: invalid date {td!r}')
                    continue
                raw_tx_type = str(tx.get('type') or '')
                ttype = normalize_txn_type(raw_tx_type)
                # AI importers sometimes label a statement row as 'lumpsum' even though
                # the description clearly says SIP/Systematic Investment. Never convert a
                # stopped/inactive SIP history into a lumpsum; it remains a SIP transaction.
                note_text = ' '.join([raw_tx_type, str(tx.get('note') or '')]).lower()
                if ttype == 'lumpsum' and ('sip' in note_text or 'systematic investment' in note_text or 'instalment' in note_text or 'installment' in note_text):
                    ttype = 'sip'
                allowed = {'sip','lumpsum','buy','sell','switch_in','switch_out','adjustment','fee'}
                if ttype not in allowed:
                    warnings.append(f'{prefix} transaction {j}: unknown type {tx.get("type")!r}; imported as adjustment')
                    ttype = 'adjustment'
                try:
                    amount = parse_signed_float(tx.get('amount', 0), 'amount')
                    units_delta = parse_signed_float(tx.get('units', 0), 'units')
                    charges = parse_money(tx.get('charges', 0))
                    nav_raw = tx.get('nav')
                    nav = float(nav_raw) if nav_raw not in (None,'') else None
                except (ValueError, TypeError) as exc:
                    errors.append(f'{prefix} transaction {j}: {exc}')
                    continue
                cashflow = tx.get('cashflow')
                if cashflow in (None, ''):
                    if ttype in ('sip','lumpsum','buy'):
                        cashflow = -(abs(amount) + charges)
                    elif ttype == 'sell':
                        cashflow = abs(amount) - charges
                    elif ttype == 'fee':
                        cashflow = -abs(amount)
                    else:
                        cashflow = 0.0
                else:
                    try:
                        cashflow = parse_signed_float(cashflow, 'cashflow')
                    except ValueError as exc:
                        errors.append(f'{prefix} transaction {j}: {exc}')
                        continue
                if ttype in ('sip','lumpsum','buy') and units_delta < 0:
                    units_delta = abs(units_delta)
                if ttype in ('sell','switch_out') and units_delta > 0:
                    units_delta = -units_delta
                normalized_txs.append({
                    'txn_type': ttype, 'txn_date': td, 'amount': abs(amount) if ttype in ('sip','lumpsum','buy','sell') else amount,
                    'cashflow': float(cashflow), 'units': units_delta, 'nav': nav, 'charges': charges,
                    'folio': str(tx.get('folio') or '').strip() or None, 'source': 'import', 'note': str(tx.get('note') or '').strip() or None
                })

            # De-duplicate only when a compact generated SIP overlaps an explicit row.
            # Do NOT collapse two identical statement rows: a fund can legitimately have two
            # SIP purchases on the same day (e.g. two folios/instalments).
            seen_exact = {}
            filtered = []
            for tx in normalized_txs:
                key = (tx['txn_type'], tx['txn_date'], round(float(tx['amount']), 8), round(float(tx['units']), 8), tx.get('folio'))
                previous = seen_exact.get(key)
                if previous is None:
                    seen_exact[key] = tx
                    filtered.append(tx)
                    continue
                prev_source = previous.get('source')
                cur_source = tx.get('source')
                if {prev_source, cur_source} == {'compressed_sip', 'import'}:
                    # Prefer the compact reconstructed row because it has the fetched historical NAV.
                    if cur_source == 'compressed_sip':
                        filtered[filtered.index(previous)] = tx
                        seen_exact[key] = tx
                    warnings.append(f'{prefix}: explicit transaction overlapped a compressed SIP block and was de-duplicated')
                else:
                    # Preserve genuine duplicate statement transactions.
                    filtered.append(tx)
            normalized_txs = sorted(filtered, key=lambda t: (t['txn_date'], t['txn_type'], t.get('source') or ''))
            tx_total += len(normalized_txs)

            if len(normalized_txs) == 0:
                warnings.append(f'{name}: no transactions found')
            if units == 0 and invested == 0:
                warnings.append(f'{name}: closing units and cost value are both zero')

            initial_date = min((t['txn_date'] for t in normalized_txs), default=None)
            sip = fund.get('sip') or {}
            sip_blocks = (history or {}).get('sip_blocks') if isinstance(history, dict) else None
            if not sip and sip_blocks:
                latest_block = sip_blocks[-1] if isinstance(sip_blocks[-1], dict) else {}
                sip = {'enabled': True, 'amount': latest_block.get('amount', 0), 'day': latest_block.get('sip_day') or (parse_optional_date(latest_block.get('start_date')).day if latest_block.get('start_date') else None)}
            sip_enabled = bool(sip.get('enabled', False))
            try:
                # Preserve the latest SIP instruction even when it is inactive/stopped.
                # sip_enabled controls whether future automated SIPs run; the amount/day
                # remain visible as the last known SIP configuration.
                sip_amount = parse_money(sip.get('amount', 0))
            except ValueError as exc:
                errors.append(f'{prefix}: invalid SIP amount: {exc}')
                sip_amount = 0.0
            sip_day = int(sip.get('day')) if sip.get('day') not in (None,'') else None
            if sip_day is not None and not (1 <= sip_day <= 31):
                errors.append(f'{prefix}: SIP day must be 1-31')
            sip_status = 'active' if sip_enabled else ('inactive' if sip_amount > 0 or sip_blocks else 'none')

            # Flag mismatches between reconstructed history and closing units as warnings, not hard failures.
            reconstructed_units = sum(float(t['units']) for t in normalized_txs if t['txn_type'] in ('sip','lumpsum','buy','switch_in','adjustment','sell','switch_out'))
            if normalized_txs and abs(reconstructed_units - units) > 0.01:
                warnings.append(f'{name}: imported transaction units ({reconstructed_units:.4f}) do not exactly match closing units ({units:.4f}); this can happen with switches, redemptions or incomplete statement history')

            # Guarantee that every imported holding carries the comprehensive AMFI
            # reference snapshot even when its compact history has no SIP blocks.
            if import_nav_snapshot is None:
                latest_snapshot = self.request_latest_nav_snapshot(force=True, all_schemes=True)
                latest_record = latest_snapshot.get(str(code).strip())
                if latest_record is not None:
                    latest_d = parse_optional_date(latest_record[0])
                    self.update_nav_reference_table({code: (latest_d, float(latest_record[1]))}, scheme_codes=[code])
                    ref = self._nav_reference(code) or self._compute_nav_reference(code, latest_record)
                    import_nav_snapshot = {
                        'last_nav': float(latest_record[1]), 'last_nav_date': latest_d.isoformat(),
                        'previous_nav': ref['previous_nav'], 'previous_nav_date': ref['previous_nav_date'],
                        'month_nav': ref['month_nav'], 'month_nav_date': ref['month_nav_date'],
                        'year_nav': ref['year_nav'], 'year_nav_date': ref['year_nav_date'],
                        'last_refresh': iso_now(), 'last_error': None,
                    }
            statement_to = parse_optional_date(source.get('statement_to') or source.get('to'))
            reference_date = statement_to or self.local_today()
            import calendar
            prev_year, prev_month = reference_date.year, reference_date.month-1
            if prev_month == 0:
                prev_year, prev_month = prev_year-1, 12
            prev_start=date(prev_year,prev_month,1)
            prev_end=date(prev_year,prev_month,calendar.monthrange(prev_year,prev_month)[1])
            sip_dates=[parse_optional_date(t['txn_date']) for t in normalized_txs if t['txn_type']=='sip']
            last_sip_date=max(sip_dates) if sip_dates else None
            days_since_sip=(reference_date-last_sip_date).days if last_sip_date else None
            # Auto-classify recent SIPs as active, old/stale SIPs as inactive, and ask
            # the user only when the last SIP falls into the ambiguous 30-60 day window.
            if sip_amount > 0 and last_sip_date:
                if days_since_sip < 30:
                    sip_enabled = True
                elif 30 <= days_since_sip <= 60:
                    sip_enabled = bool(sip_enabled)
                else:
                    sip_enabled = False
            else:
                # When the statement contains SIP configuration/blocks but no in-window
                # SIP transaction remains after the statement cutoff, preserve the current
                # SIP enabled flag from the statement instead of treating it as inactive.
                sip_enabled = bool(sip.get('enabled')) if (sip_amount > 0 or sip_blocks) else False
            sip_status = 'active' if sip_enabled else ('inactive' if sip_amount > 0 or sip_blocks else 'none')
            sip_confirmation_required=bool(last_sip_date and 30 <= days_since_sip <= 60)
            if sip_confirmation_required:
                warnings.append(f'{name}: last SIP was {days_since_sip} days before the statement date; confirm whether this SIP is still active')

            normalized.append({
                'scheme_code': code, 'scheme_name': name, 'isin': isin,
                'investor_ref': str(fund.get('investor_ref') or (detected_investors[0]['ref'] if len(detected_investors)==1 else '')).strip() or None,
                'folios': fund.get('folios') if isinstance(fund.get('folios'), list) else [],
                'units': units, 'invested': invested, 'sip_enabled': sip_enabled, 'sip_amount': sip_amount,
                'sip_day': sip_day, 'sip_status': sip_status, 'sip_confirmation_required': sip_confirmation_required,
                'last_sip_transaction_date': max(sip_dates).isoformat() if sip_dates else None,
                'initial_date': initial_date, 'transactions': normalized_txs, 'closing_balance': cb, 'nav_snapshot': import_nav_snapshot, 'import_nav_entries': import_nav_entries or [],
                'import_sip_reconciliations': self._build_import_sip_reconciliations({
                    'investor_ref': str(fund.get('investor_ref') or (detected_investors[0]['ref'] if len(detected_investors)==1 else '')).strip() or None,
                    'scheme_code': code, 'scheme_name': name, 'folios': fund.get('folios') if isinstance(fund.get('folios'), list) else [],
                    'sip_enabled': sip_enabled, 'sip_amount': sip_amount, 'sip_day': sip_day,
                    'transactions': normalized_txs, 'import_nav_entries': import_nav_entries or []
                }, statement_to)
            })

        if tx_total > MAX_IMPORT_TRANSACTIONS:
            errors.append(f'Maximum {MAX_IMPORT_TRANSACTIONS} expanded transactions per import')

        # Expand multi-folio schemes before the preview reaches the UI. This makes
        # SIP status, reconciliation and merge decisions holding/folio-specific while
        # preserving the compact scheme-oriented input JSON. Recompute SIP recency on
        # each expanded holding because a multi-folio scheme can contain one active
        # folio and one historical/stopped folio with different last SIP dates.
        preview_for_expand = {
            'funds': normalized,
            'source': data.get('source') or {},
            'investors': detected_investors,
        }
        expanded = self.db._expand_preview_holdings(preview_for_expand)
        expanded_funds = expanded.get('funds') or []
        statement_to_date = parse_optional_date(source.get('statement_to') or source.get('to')) if (source.get('statement_to') or source.get('to')) else self.local_today()
        import calendar
        for child in expanded_funds:
            sip_txs = [t for t in (child.get('transactions') or []) if str(t.get('txn_type') or '') == 'sip']
            sip_dates = [parse_optional_date(t.get('txn_date')) for t in sip_txs]
            sip_dates = [d for d in sip_dates if d]
            last_sip_date = max(sip_dates) if sip_dates else None
            days_since_sip = (statement_to_date - last_sip_date).days if last_sip_date else None
            sip_amount = parse_money(child.get('sip_amount', 0) or 0)
            sip_day = child.get('sip_day')
            current_enabled = bool(child.get('sip_enabled'))
            if sip_amount > 0 and last_sip_date:
                if days_since_sip < 30:
                    current_enabled = True
                elif 30 <= days_since_sip <= 60:
                    current_enabled = bool(current_enabled)
                else:
                    current_enabled = False
            elif not (sip_amount > 0 or child.get('history', {}).get('sip_blocks')):
                current_enabled = False
            child['sip_enabled'] = current_enabled
            child['sip_status'] = 'active' if current_enabled else ('inactive' if sip_amount > 0 or sip_txs else 'none')
            child['last_sip_transaction_date'] = last_sip_date.isoformat() if last_sip_date else None
            child['sip_confirmation_required'] = bool(last_sip_date and 30 <= days_since_sip <= 60)
            # Rebuild recent SIP reconciliation against this folio's own transaction history.
            child['import_sip_reconciliations'] = self._build_import_sip_reconciliations({
                'investor_ref': child.get('investor_ref'),
                'scheme_code': child.get('scheme_code'),
                'scheme_name': child.get('scheme_name'),
                'folios': child.get('folios') or [],
                'sip_enabled': current_enabled,
                'sip_amount': sip_amount,
                'sip_day': sip_day,
                'transactions': child.get('transactions') or [],
                'import_nav_entries': child.get('import_nav_entries') or [],
            }, statement_to_date)
        expanded['errors'] = errors
        expanded['warnings'] = warnings
        expanded['format_version'] = IMPORT_FORMAT_VERSION
        expanded['input_format_version'] = import_version
        expanded['counts'] = {
            'funds': len(expanded_funds),
            'transactions': sum(len(f.get('transactions') or []) for f in expanded_funds),
        }
        return expanded

    def _build_import_sip_reconciliations(self, fund, statement_to):
        """Identify the one recent SIP occurrence that falls at/after statement coverage.

        If the applicable NAV is already this fund's latest available NAV, it is a
        current/latest-NAV import and will be auto-recorded as a live execution. If
        the applicable NAV is older than the latest available NAV, the user must
        explicitly choose Executed or Skipped.
        """
        if not fund.get('sip_enabled') or not fund.get('sip_day') or not statement_to:
            return []
        try:
            statement_to = parse_optional_date(statement_to)
            day = int(fund.get('sip_day'))
        except Exception:
            return []
        if not statement_to or not (1 <= day <= 31):
            return []
        import calendar
        year, month = statement_to.year, statement_to.month
        target = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        if target < statement_to:
            month += 1
            if month == 13:
                month, year = 1, year + 1
            target = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        entries = []
        for d, n in (fund.get('import_nav_entries') or []):
            try:
                dd = parse_optional_date(d)
                if dd:
                    entries.append((dd, float(n)))
            except Exception:
                continue
        entries.sort(key=lambda x: x[0])
        if not entries:
            return []
        effective = self._effective_sip_trading_date(target) or target
        applicable = next(((d, n) for d, n in entries if d >= effective), None)
        if not applicable:
            return []
        latest_nav_date, _latest_nav = entries[-1]
        nav_date, nav = applicable
        if nav_date > latest_nav_date or nav_date < statement_to:
            return []
        # If the statement already contains the actual SIP transaction, nothing is missing.
        for tx in fund.get('transactions') or []:
            if str(tx.get('txn_type') or '') == 'sip':
                td = parse_optional_date(tx.get('txn_date')) if tx.get('txn_date') else None
                if td and td == target:
                    return []
        amount = float(fund.get('sip_amount') or 0)
        if amount <= 0 or nav <= 0:
            return []
        key = f"{fund.get('investor_ref') or ''}|{fund.get('scheme_code') or ''}|{(fund.get('folios') or [None])[0] or ''}|{target.isoformat()}"
        return [{
            'key': key,
            'import_ref': fund.get('_import_ref'),
            'scheme_code': fund.get('scheme_code'),
            'scheme_name': fund.get('scheme_name'),
            'sip_date': target.isoformat(),
            'effective_sip_date': effective.isoformat(),
            'nav_date': nav_date.isoformat(),
            'nav': nav,
            'latest_nav_date': latest_nav_date.isoformat(),
            'amount': amount,
            'units': amount / nav,
            'auto_execute': nav_date == latest_nav_date,
        }]

    def _apply_import_sip_cutoff(self, holding_id, fund, source):
        """Apply the existing import-time SIP cutoff/execution logic to one holding."""
        if not fund.get('sip_enabled') or not fund.get('sip_day'):
            return
        statement_to = parse_optional_date((source or {}).get('statement_to'))
        if not statement_to:
            return
        import calendar
        day = int(fund['sip_day'])
        target = date(statement_to.year, statement_to.month, min(day, calendar.monthrange(statement_to.year, statement_to.month)[1]))
        cycle = f'{target.year:04d}-{target.month:02d}'
        if target < statement_to:
            self.db.set_sip_cycle_accounted(holding_id, cycle)
            return
        if target != statement_to:
            return
        eligible_import_navs = [
            (d, n) for d, n in (fund.get('import_nav_entries') or [])
            if target <= d <= statement_to
        ]
        if not eligible_import_navs:
            return
        nav_date, _nav = min(eligible_import_navs, key=lambda item: item[0])
        sip_tx_dates = [
            parse_optional_date(t.get('txn_date'))
            for t in (fund.get('transactions') or [])
            if t.get('txn_type') == 'sip' and t.get('txn_date')
        ]
        already_executed = any(target <= d <= nav_date for d in sip_tx_dates)
        if already_executed:
            self.db.set_sip_cycle_accounted(holding_id, cycle)
        else:
            imported_holding = self.db.get_fund(holding_id)
            self.maybe_execute_sip(imported_holding, eligible_import_navs, nav_date, source='import', statement_to=statement_to)

    def _validate_manual_merge_target(self, profile_id, fund, target_id):
        target=self.db.get_fund(int(target_id)) if target_id else None
        if not target or int(target['profile_id']) != int(profile_id):
            raise ValueError('Selected merge target is not valid for this investor.')
        incoming_folio=self.db.normalize_folio((fund.get('folios') or [None])[0])
        target_folio=self.db.normalize_folio(target['normalized_folio'] or target['folio'])
        if incoming_folio and target_folio and incoming_folio != target_folio:
            raise ValueError('Selected merge target has a different folio. A new folio must remain a separate holding.')
        incoming_isin=str(fund.get('isin') or '').strip().upper()
        target_isin=str(target['canonical_isin'] or target['isin'] or '').strip().upper()
        if incoming_isin and target_isin and incoming_isin != target_isin:
            raise ValueError('Selected merge target has a different verified ISIN.')
        return target

    @staticmethod
    def _import_sip_status_key(fund):
        return str(fund.get('_import_ref') or '').strip() or str(fund.get('scheme_code') or '').strip()

    @classmethod
    def _sip_status_value_for_fund(cls, fund, mapping):
        key = cls._import_sip_status_key(fund)
        if key in mapping:
            return mapping[key]
        # Backward compatibility for old client payloads that keyed SIP status by
        # scheme code. New multi-folio imports use _import_ref so each holding can
        # have an independent active/inactive decision.
        code = str(fund.get('scheme_code') or '').strip()
        return mapping.get(code) if code else None

    @staticmethod
    def _normalized_import_sip_status_mapping(mapping):
        out = {}
        for k, v in (mapping or {}).items():
            key = str(k or '').strip()
            if key:
                out[key] = bool(v)
        return out

    def import_validated_preview(self, preview, profile_mapping=None, sip_status_mapping=None, fund_decisions=None, sip_reconciliation_mapping=None, progress_callback=None):
        preview = self.db._expand_preview_holdings(preview)
        imported, skipped = [], []
        affected_sip_holds = []
        profile_mapping = profile_mapping or {}
        sip_status_mapping = self._normalized_import_sip_status_mapping(sip_status_mapping)
        fund_decisions = fund_decisions or {}
        sip_reconciliation_mapping = sip_reconciliation_mapping or {}
        # Validate all required historical SIP choices before saving anything. Current/latest-NAV
        # auto cases do not require a user choice. A fund explicitly skipped in change review
        # does not need a SIP reconciliation decision because it will not be imported.
        for _fund in preview.get('funds', []):
            _decision_key = _fund.get('_import_ref') or f"{_fund.get('investor_ref')}|{(_fund.get('folios') or [None])[0]}|{str(_fund.get('isin') or '').strip().upper()}|{str(_fund.get('scheme_code') or '').strip()}|{_fund.get('scheme_name','')}"
            _decision = fund_decisions.get(_decision_key) or {}
            if isinstance(_decision, dict) and _decision.get('mode') == 'skip':
                continue
            # An explicit Stopped / inactive SIP-status choice is authoritative for
            # every later reconciliation stage. It must suppress both auto-execution
            # and the requirement for an Executed/Skipped reconciliation choice.
            _fund_status_value = self._sip_status_value_for_fund(_fund, sip_status_mapping)
            if _fund_status_value is False:
                continue
            for _rec in _fund.get('import_sip_reconciliations') or []:
                if _rec.get('auto_execute'):
                    continue
                _choice = str(sip_reconciliation_mapping.get(_rec.get('key')) or '').strip().lower()
                if _choice not in ('executed', 'skipped'):
                    raise ValueError(f"SIP decision is required for {_fund.get('scheme_name') or 'fund'} on {_rec.get('sip_date')}.")
        # Resolve detected investors to existing or newly-created profiles.
        resolved_profiles = {}
        for inv in preview.get('investors', []):
            ref = inv['ref']
            choice = profile_mapping.get(ref)
            if choice is None:
                status = inv.get('identity_status')
                match = inv.get('match')
                if status == 'pan_match_same' and match:
                    choice = {'mode':'existing','profile_id':match['id']}
                elif status == 'no_match':
                    choice = {'mode':'new'}
                else:
                    raise ValueError(f'Investor identity for {inv.get("name") or "statement investor"} needs a merge/new-profile decision because the PAN matches an existing profile but the details differ.')
            mode = choice.get('mode') if isinstance(choice, dict) else 'existing'
            if mode == 'existing':
                pid = int(choice.get('profile_id'))
                if not self.db.get_profile(pid):
                    raise ValueError(f'Investor profile not found for {inv["name"]}')
                # Preserve additional email addresses from the statement when consolidating.
                for email in inv.get('emails') or []:
                    self.db.add_profile_email(pid, email)
                resolved_profiles[ref] = pid
            else:
                payload = {k: inv.get(k) for k in ('name','emails','phone','address','pan')}
                pid = self.db.claim_empty_primary_profile(payload)
                if pid is None:
                    pid = self.db.create_profile(payload)
                resolved_profiles[ref] = pid
        total = max(1, len(preview.get('funds', [])))
        for idx, fund in enumerate(preview.get('funds', []), 1):
            ref = fund.get('investor_ref')
            profile_id = resolved_profiles.get(ref)
            if profile_id is None:
                if len(resolved_profiles) == 1:
                    profile_id = next(iter(resolved_profiles.values()))
                else:
                    # Fall back to the default profile only when the JSON has no investor information.
                    profiles = self.db.all_profiles()
                    profile_id = profiles[0]['id'] if profiles else self.db._ensure_default_profile()
            fund = dict(fund); fund['profile_id'] = profile_id
            _status_value = self._sip_status_value_for_fund(fund, sip_status_mapping)
            if _status_value is not None:
                # An explicit user choice must always override the imported/current SIP flag.
                # This is intentionally applied even when confirmation_required is false so the
                # decision remains authoritative through every later import stage.
                fund['sip_enabled'] = bool(_status_value)
                if fund['sip_enabled'] is False:
                    # A stopped/inactive choice is terminal for this import. Do not even carry
                    # its recent-SIP reconciliation records into the post-import execution stage.
                    fund['import_sip_reconciliations'] = []
            elif fund.get('sip_confirmation_required'):
                raise ValueError(f'SIP active/inactive confirmation is required for {fund["scheme_name"]}')
            existing_fund, match_action, match_reason = self.db.find_import_fund(profile_id, fund)
            decision_key = fund.get('_import_ref') or f"{ref}|{(fund.get('folios') or [None])[0]}|{str(fund.get('isin') or '').strip().upper()}|{str(fund.get('scheme_code') or '').strip()}|{fund.get('scheme_name','')}"
            decision = fund_decisions.get(decision_key) or {}
            decision_mode = decision.get('mode') if isinstance(decision, dict) else None
            if decision_mode == 'skip':
                skipped.append({'scheme_code':fund.get('scheme_code'),'scheme_name':fund.get('scheme_name'),'profile_id':profile_id,'reason':'Skipped by user during change review'})
                continue
            if decision_mode == 'merge':
                target_id=int(decision.get('holding_id') or 0)
                self._validate_manual_merge_target(profile_id, fund, target_id)
                fid, new_tx_count = self.db.merge_import_fund(fund, fund.get('transactions') or [], preview.get('source') or {}, target_holding_id=target_id)
                skipped.append({'scheme_code':fund.get('scheme_code'),'scheme_name':fund.get('scheme_name'),'profile_id':profile_id,'reason':'merged into the holding selected during review','new_transactions':new_tx_count,'new_folio':False})
                if fund.get('nav_snapshot'): self.db.update_nav(fid, fund['nav_snapshot'])
                affected_sip_holds.append({'holding_id':fid,'import_ref':fund.get('_import_ref'),'scheme_code':str(fund.get('scheme_code') or '').strip(),'scheme_name':fund.get('scheme_name'),'sip_enabled':fund.get('sip_enabled'),'import_sip_status': (self._sip_status_value_for_fund(fund, sip_status_mapping) if self._sip_status_value_for_fund(fund, sip_status_mapping) is not None else None),'sip_day':fund.get('sip_day'),'import_nav_entries':fund.get('import_nav_entries') or [],'nav_snapshot':fund.get('nav_snapshot') or {}, 'import_sip_reconciliations':fund.get('import_sip_reconciliations') or []})
                continue
            if match_action == 'REVIEW_REQUIRED':
                if decision_mode == 'skip':
                    skipped.append({'scheme_code':fund.get('scheme_code'),'scheme_name':fund.get('scheme_name'),'profile_id':profile_id,'reason':'Skipped by user during review'})
                    continue
                if decision_mode == 'new':
                    match_action = 'NEW_INVESTMENT'
                elif decision_mode == 'merge':
                    target_id = int(decision.get('holding_id') or 0)
                    candidate_ids = {c['id'] for c in self.db._review_candidate_holdings(profile_id, fund)}
                    if not target_id or target_id not in candidate_ids:
                        raise ValueError(f'Invalid merge target selected for {fund.get("scheme_name")}')
                    fid, new_tx_count = self.db.merge_import_fund(fund, fund['transactions'], preview.get('source') or {}, target_holding_id=target_id)
                    skipped.append({'scheme_code': fund.get('scheme_code'), 'scheme_name': fund.get('scheme_name'), 'profile_id': profile_id, 'reason': 'merged into the holding selected during review', 'new_transactions': new_tx_count, 'new_folio': False})
                    if fund.get('nav_snapshot'):
                        self.db.update_nav(fid, fund['nav_snapshot'])
                    affected_sip_holds.append({'holding_id': fid, 'import_ref': fund.get('_import_ref'), 'scheme_code': str(fund.get('scheme_code') or '').strip(), 'scheme_name': fund.get('scheme_name'), 'sip_enabled': fund.get('sip_enabled'), 'import_sip_status': (self._sip_status_value_for_fund(fund, sip_status_mapping) if self._sip_status_value_for_fund(fund, sip_status_mapping) is not None else None), 'sip_day': fund.get('sip_day'), 'import_nav_entries': fund.get('import_nav_entries') or [], 'nav_snapshot': fund.get('nav_snapshot') or {}, 'import_sip_reconciliations': fund.get('import_sip_reconciliations') or []})
                    continue
                else:
                    raise ValueError(match_reason)
            if decision_mode == 'new' and match_action == 'EXISTING_INVESTMENT_MERGE':
                match_action = 'NEW_INVESTMENT'
            if match_action == 'EXISTING_INVESTMENT_MERGE':
                fid, new_tx_count = self.db.merge_import_fund(fund, fund['transactions'], preview.get('source') or {})
                new_folio_present = False
                skipped.append({'scheme_code': fund['scheme_code'], 'scheme_name': fund['scheme_name'], 'profile_id': profile_id,
                                'reason': ('merged existing holding with new transactions' if new_tx_count else 'already imported; no new transactions'),
                                'new_transactions': new_tx_count, 'new_folio': new_folio_present})
                if fund.get('nav_snapshot'):
                    self.db.update_nav(fid, fund['nav_snapshot'])
                affected_sip_holds.append({'holding_id': fid, 'import_ref': fund.get('_import_ref'), 'scheme_code': str(fund.get('scheme_code') or '').strip(), 'scheme_name': fund.get('scheme_name'), 'sip_enabled': fund.get('sip_enabled'), 'import_sip_status': (self._sip_status_value_for_fund(fund, sip_status_mapping) if self._sip_status_value_for_fund(fund, sip_status_mapping) is not None else None), 'sip_day': fund.get('sip_day'), 'import_nav_entries': fund.get('import_nav_entries') or [], 'nav_snapshot': fund.get('nav_snapshot') or {}, 'import_sip_reconciliations': fund.get('import_sip_reconciliations') or []})
                continue
            if match_action == 'NEW_FOLIO':
                fid = self.db.import_fund(fund, fund['transactions'])
                if fund.get('nav_snapshot'):
                    self.db.update_nav(fid, fund['nav_snapshot'])
                affected_sip_holds.append({'holding_id': fid, 'import_ref': fund.get('_import_ref'), 'scheme_code': str(fund.get('scheme_code') or '').strip(), 'scheme_name': fund.get('scheme_name'), 'sip_enabled': fund.get('sip_enabled'), 'import_sip_status': (self._sip_status_value_for_fund(fund, sip_status_mapping) if self._sip_status_value_for_fund(fund, sip_status_mapping) is not None else None), 'sip_day': fund.get('sip_day'), 'import_nav_entries': fund.get('import_nav_entries') or [], 'nav_snapshot': fund.get('nav_snapshot') or {}, 'import_sip_reconciliations': fund.get('import_sip_reconciliations') or []})
                imported.append({'id': fid, 'scheme_code': fund['scheme_code'], 'scheme_name': fund['scheme_name'], 'profile_id': profile_id, 'folio': (fund.get('folios') or [None])[0], 'action': 'NEW_FOLIO'})
                if progress_callback:
                    pct = 80 + int(idx / total * 10)
                    progress_callback('Saving portfolio', pct, f'Saved new folio {idx} of {total}: {fund["scheme_name"]}')
                continue
            fid = self.db.import_fund(fund, fund['transactions'])
            if fund.get('nav_snapshot'):
                self.db.update_nav(fid, fund['nav_snapshot'])
            affected_sip_holds.append({'holding_id': fid, 'import_ref': fund.get('_import_ref'), 'scheme_code': str(fund.get('scheme_code') or '').strip(), 'scheme_name': fund.get('scheme_name'), 'sip_enabled': fund.get('sip_enabled'), 'import_sip_status': (self._sip_status_value_for_fund(fund, sip_status_mapping) if self._sip_status_value_for_fund(fund, sip_status_mapping) is not None else None), 'sip_day': fund.get('sip_day'), 'import_nav_entries': fund.get('import_nav_entries') or [], 'nav_snapshot': fund.get('nav_snapshot') or {}, 'import_sip_reconciliations': fund.get('import_sip_reconciliations') or []})
            imported.append({'id': fid, 'scheme_code': fund['scheme_code'], 'scheme_name': fund['scheme_name'], 'profile_id': profile_id})
            if progress_callback:
                pct = 80 + int(idx / total * 10)
                progress_callback('Saving portfolio', pct, f'Saved fund {idx} of {total}: {fund["scheme_name"]}')
        removed_placeholders=self.db.cleanup_empty_primary_profiles()
        if removed_placeholders:
            skipped.extend({'profile_id': pid, 'reason': 'removed empty Primary Investor placeholder'} for pid in removed_placeholders)
        statement_to = (preview.get('source') or {}).get('statement_to')
        for item in affected_sip_holds:
            item['statement_to'] = statement_to
        return {'imported': imported, 'skipped': skipped, 'warnings': preview.get('warnings', []), 'profiles': resolved_profiles, 'removed_placeholders': removed_placeholders, 'affected_sip_holds': affected_sip_holds}

    def execute_post_import_sips(self, affected_sip_holds, sip_reconciliation_mapping=None, sip_status_mapping=None, progress_callback=None):
        """Reconcile SIP occurrences identified during import validation.

        A SIP whose applicable NAV is the holding's latest available NAV is treated
        as a current/live execution and is written to live_sip_executions. Older
        occurrences require an explicit Executed/Skipped choice. Historical import
        reconciliations never enter the live execution table.
        """
        mapping = sip_reconciliation_mapping or {}
        status_mapping = self._normalized_import_sip_status_mapping(sip_status_mapping)
        executed, auto_executed, skipped = [], [], []
        records = []
        for item in affected_sip_holds or []:
            # The user's explicit SIP status decision is authoritative even if the
            # imported holding state or a legacy preview still says it is enabled.
            _code = str(item.get('scheme_code') or '').strip()
            _explicit_status = item.get('import_sip_status', None)
            if _explicit_status is False:
                continue
            _status_key = str(item.get('import_ref') or '').strip()
            if _status_key and _status_key in status_mapping and status_mapping[_status_key] is False:
                continue
            if _code in status_mapping and status_mapping[_code] is False:
                continue
            # A fund explicitly marked Stopped / inactive during import must never
            # enter the post-import SIP execution queue, even when the preview was
            # generated before the user's status decision and therefore still contains
            # an auto_execute reconciliation record.
            if item.get('sip_enabled') is False:
                continue
            for rec in item.get('import_sip_reconciliations') or []:
                records.append((int(item['holding_id']), rec))
        # Preserve v0.1.145's established latest-NAV import behavior for affected
        # holdings that have no new reconciliation record. Newer reconciliation
        # records handle the statement-to-latest-NAV gap explicitly.
        if not records:
            legacy_executed = []
            unique = {}
            for item in affected_sip_holds or []:
                try:
                    unique[int(item.get('holding_id'))] = item
                except Exception:
                    continue
            for hid, item in unique.items():
                _code = str(item.get('scheme_code') or '').strip()
                if _code in status_mapping and not bool(status_mapping.get(_code)):
                    continue
                fund = self.db.get_fund(hid)
                if not fund or not fund['sip_enabled'] or not fund['sip_day'] or not fund['sip_amount']:
                    continue
                latest_nav_date = parse_optional_date(fund['last_nav_date'])
                latest_nav = fund['last_nav']
                if latest_nav_date is None or latest_nav is None:
                    continue
                import calendar
                day = int(fund['sip_day'])
                target = date(latest_nav_date.year, latest_nav_date.month, min(day, calendar.monthrange(latest_nav_date.year, latest_nav_date.month)[1]))
                if target != latest_nav_date:
                    continue
                rows = self.db.transactions(hid)
                if any(str(r['txn_type'] or '') == 'sip' and parse_optional_date(r['txn_date']) == target for r in rows):
                    cycle = f'{target.year:04d}-{target.month:02d}'
                    if fund['last_sip_cycle'] != cycle:
                        self.db.set_sip_executed(hid, cycle, target.isoformat())
                    continue
                before = len(rows)
                self.maybe_execute_sip(fund, [(latest_nav_date, float(latest_nav))], latest_nav_date, source='import', statement_to=item.get('statement_to'))
                after_rows = self.db.transactions(hid)
                if len(after_rows) > before:
                    legacy_executed.append({'holding_id': hid, 'scheme_name': fund['scheme_name'], 'txn_date': latest_nav_date.isoformat()})
            return {'executed': legacy_executed, 'auto_executed': [], 'skipped': [], 'checked': len(unique)}

        total = len(records)
        for idx, (hid, rec) in enumerate(records, 1):
            fund = self.db.get_fund(hid)
            if not fund:
                continue
            if int(fund['sip_enabled'] or 0) == 0:
                # Persisted holding state is the final safety gate: an explicitly inactive
                # SIP can never reach the reconciliation decision/auto-execution branch.
                continue
            _code = str(fund['scheme_code'] or '').strip()
            if _code in status_mapping and status_mapping[_code] is False:
                continue
            key = str(rec.get('key') or '')
            is_auto = bool(rec.get('auto_execute'))
            choice = 'executed' if is_auto else str(mapping.get(key) or '').strip().lower()
            if choice not in ('executed', 'skipped'):
                raise ValueError(f'SIP decision is required for {rec.get("scheme_name") or "fund"} on {rec.get("sip_date")}.')

            sip_date = parse_optional_date(rec.get('sip_date'))
            nav_date = parse_optional_date(rec.get('nav_date'))
            if not sip_date or not nav_date:
                raise ValueError(f'Invalid SIP reconciliation dates for {fund["scheme_name"]}.')
            amount = float(rec.get('amount') or 0)
            nav = float(rec.get('nav') or 0)
            if amount <= 0 or nav <= 0:
                raise ValueError(f'Invalid SIP amount/NAV for {fund["scheme_name"]}.')
            units = amount / nav
            cycle = f'{sip_date.year:04d}-{sip_date.month:02d}'
            rows = self.db.transactions(hid)
            # A statement SIP can be stored on the actual NAV/transaction date.
            # Treat the scheduled and actual NAV dates as one monthly SIP cycle so
            # the import path cannot create a duplicate for the same installment.
            existing_sip = next((
                r for r in rows
                if str(r['txn_type'] or '') == 'sip'
                and parse_optional_date(r['txn_date']) in {sip_date, nav_date}
                and (float(r['amount'] or 0) > 0 or float(r['units'] or 0) > 0)
            ), None)
            if existing_sip is not None:
                if fund['last_sip_cycle'] != cycle:
                    existing_date = parse_optional_date(existing_sip['txn_date']) or nav_date
                    self.db.set_sip_executed(hid, cycle, existing_date.isoformat())
                continue
            if fund['last_sip_cycle'] == cycle:
                continue

            folio = (fund['normalized_folio'] if 'normalized_folio' in fund.keys() and fund['normalized_folio'] else fund['folio'] if 'folio' in fund.keys() else None)
            if choice == 'skipped':
                # Mark the historical cycle as accounted so the normal scheduler cannot
                # resurrect a SIP the user explicitly said was skipped.
                self.db.set_sip_cycle_accounted(hid, cycle)
                skipped.append({'holding_id': hid, 'scheme_name': fund['scheme_name'], 'sip_date': sip_date.isoformat(), 'nav_date': nav_date.isoformat()})
            elif is_auto:
                # This is the special current/latest-NAV import case. Preserve the live
                # execution semantics because the user wants it reflected by today's sensor.
                self.db.add_transaction(
                    hid, 'sip', nav_date.isoformat(), amount, units, nav,
                    f'Scheduled SIP for {sip_date.isoformat()} (NAV date {nav_date.isoformat()}) — reconciled from latest statement import',
                    folio=folio, source='sip_execution'
                )
                self.db.set_sip_executed(hid, cycle, nav_date.isoformat())
                executed_at = iso_now()
                try:
                    exec_dt = datetime.fromisoformat(executed_at.replace('Z', '+00:00'))
                    if exec_dt.tzinfo is None:
                        exec_dt = exec_dt.replace(tzinfo=timezone.utc)
                    execution_date = exec_dt.astimezone(ZoneInfo(self.timezone_name)).date().isoformat()
                except Exception:
                    execution_date = self.local_today().isoformat()
                self.db.record_live_sip_execution(fund, sip_date.isoformat(), nav_date.isoformat(), amount, units, nav, executed_at=executed_at, execution_date=execution_date)
                try:
                    self.db.record_sip_execution(fund, sip_date.isoformat(), nav_date.isoformat(), amount, units, nav, executed_at=executed_at)
                except Exception:
                    pass
                auto_executed.append({'holding_id': hid, 'scheme_name': fund['scheme_name'], 'sip_date': sip_date.isoformat(), 'nav_date': nav_date.isoformat()})
            else:
                # Historical reconciliation: financially real, but deliberately not a
                # live execution so it cannot appear in SIP Executed Today.
                self.db.add_transaction(
                    hid, 'sip', nav_date.isoformat(), amount, units, nav,
                    f'Historical SIP for {sip_date.isoformat()} (NAV date {nav_date.isoformat()}) — reconciled during statement import',
                    folio=folio, source='import_reconciled_sip'
                )
                self.db.set_sip_executed(hid, cycle, nav_date.isoformat())
                executed.append({'holding_id': hid, 'scheme_name': fund['scheme_name'], 'sip_date': sip_date.isoformat(), 'nav_date': nav_date.isoformat()})

            if progress_callback:
                pct = 90 + int(idx / max(1, total) * 6)
                progress_callback('Reconciling SIP', pct, f'Reconciled SIP {idx} of {total}: {fund["scheme_name"]}')

        return {'executed': executed, 'auto_executed': auto_executed, 'skipped': skipped, 'checked': total}

    def import_document(self, data, profile_mapping=None, sip_status_mapping=None, fund_decisions=None, sip_reconciliation_mapping=None):
        preview = self.validate_import_document(data)
        if preview['errors']:
            raise ValueError('; '.join(preview['errors'][:8]))
        if preview.get('template'):
            raise ValueError('This is a blank/example template. Fill it with fund data before importing.')
        return self.import_validated_preview(preview, profile_mapping=profile_mapping, sip_status_mapping=sip_status_mapping, fund_decisions=fund_decisions, sip_reconciliation_mapping=sip_reconciliation_mapping)

    def _nav_entries_for_range(self, scheme_code: str, start_date: date, cache: dict | None = None):
        key = str(scheme_code)
        if cache is not None and key in cache:
            return cache[key]
        payload = self.request_nav_json_with_retry(f'{MFAPI_BASE}/mf/{key}?startDate={start_date.isoformat()}')
        entries=[]
        for item in payload.get('data') or []:
            try:
                entries.append((parse_nav_date(item['date']), float(item['nav'])))
            except Exception:
                continue
        entries.sort(key=lambda x:x[0])
        if not entries:
            raise RuntimeError('No NAV history returned')
        if cache is not None:
            cache[key]=entries
        return entries

    @staticmethod
    def _snapshot_from_nav_entries(entries, today: date):
        desc=sorted(entries,key=lambda x:x[0],reverse=True)
        latest_d,latest_nav=desc[0]
        prev_d,prev_nav=desc[1] if len(desc)>1 else (None,None)
        month_first=date(today.year,today.month,1)
        year_first=date(today.year,1,1)
        month_ref=next(((d,n) for d,n in desc if d<=month_first),None)
        year_ref=next(((d,n) for d,n in desc if d<=year_first),None)
        return {'last_nav':latest_nav,'last_nav_date':latest_d.isoformat(),'previous_nav':prev_nav,'previous_nav_date':iso_date(prev_d),'month_nav':month_ref[1] if month_ref else None,'month_nav_date':iso_date(month_ref[0]) if month_ref else None,'year_nav':year_ref[1] if year_ref else None,'year_nav_date':iso_date(year_ref[0]) if year_ref else None,'last_refresh':iso_now(),'last_error':None}

    def historical_sip_transactions(self, scheme_code: str, sip_amount: float, sip_day: int, first_sip_date: date, count: int):
        if count <= 0:
            return []
        if count > 240:
            raise ValueError('SIP count is too large (maximum 240)')
        today = self.local_today()
        # The first transaction date in a compressed block is authoritative. It may be
        # an initial purchase or the first available NAV after the scheduled date. From
        # the following month onward, use the recurring scheduled SIP day. This avoids
        # rejecting valid histories where the first transaction date differs from the
        # recurring SIP day (e.g. an initial purchase on the 5th followed by monthly SIPs
        # on the 12th).
        if first_sip_date > today:
            raise ValueError('first SIP date cannot be in the future')
        scheduled = [first_sip_date]
        import calendar
        for i in range(1, count):
            base_index = (first_sip_date.month - 1) + i
            yy = first_sip_date.year + base_index // 12
            mm = base_index % 12 + 1
            dd = min(sip_day, calendar.monthrange(yy, mm)[1])
            d = date(yy, mm, dd)
            if d > today:
                raise ValueError('SIP count extends into the future; reduce the number of SIPs or correct the first SIP date')
            scheduled.append(d)
        start = min(scheduled) - timedelta(days=10)
        entries = self._nav_entries_for_range(scheme_code, start)
        txs = []
        for target in scheduled:
            actual = next(((d, nav) for d, nav in entries if d >= target), None)
            if actual is None:
                raise RuntimeError(f'No NAV available on or after {target.isoformat()}')
            actual_date, nav = actual
            units = sip_amount / nav
            txs.append({
                'txn_type': 'sip',
                'txn_date': actual_date.isoformat(),
                'amount': sip_amount,
                'cashflow': -sip_amount,
                'units': units,
                'nav': nav,
                'note': f'Historical SIP for scheduled date {target.isoformat()}'
            })
        return txs

    def historical_sip_transactions_from_entries(self, sip_amount: float, sip_day: int, first_sip_date: date, count: int, entries):
        if count <= 0:
            return []
        if count > 240:
            raise ValueError('SIP count is too large (maximum 240)')
        today=self.local_today()
        if first_sip_date > today:
            raise ValueError('first SIP date cannot be in the future')
        scheduled=[first_sip_date]
        import calendar
        for i in range(1,count):
            base_index=(first_sip_date.month-1)+i
            yy=first_sip_date.year+base_index//12
            mm=base_index%12+1
            dd=min(sip_day,calendar.monthrange(yy,mm)[1])
            d=date(yy,mm,dd)
            if d>today:
                raise ValueError('SIP count extends into the future; reduce the number of SIPs or correct the first SIP date')
            scheduled.append(d)
        txs=[]
        for target in scheduled:
            actual=next(((d,nav) for d,nav in entries if d>=target),None)
            if actual is None:
                raise RuntimeError(f'No NAV available on or after {target.isoformat()}')
            actual_date,nav=actual
            txs.append({'txn_type':'sip','txn_date':actual_date.isoformat(),'amount':sip_amount,'cashflow':-sip_amount,'units':sip_amount/nav,'nav':nav,'note':f'Historical SIP for scheduled date {target.isoformat()}'})
        return txs

    def _prefetch_nav_reference_cache(self):
        """Warm AMFI latest/reference history without creating rows for the AMFI universe."""
        try:
            # Fetch the complete latest snapshot once so startup can establish the
            # current AMFI date and warm the small set of reference-date files.
            latest_snapshot = self.request_latest_nav_snapshot(force=True, all_schemes=True)
            latest_dates = [record[0] for record in latest_snapshot.values() if record and record[0]]
            if latest_dates:
                global_latest = max(latest_dates)
                previous_candidate = self._previous_nse_open_before(global_latest)
                month_candidate = self._first_nse_open_on_or_after(
                    date(global_latest.year, global_latest.month, 1), limit_date=global_latest
                )
                year_candidate = self._first_nse_open_on_or_after(
                    date(global_latest.year, 1, 1), limit_date=global_latest
                )
                warm_dates = {d for d in (previous_candidate, month_candidate, year_candidate) if d}
                for ref_date in sorted(warm_dates):
                    try:
                        self._amfi_history_snapshot_for_date(ref_date)
                    except Exception as exc:
                        # Startup warming is opportunistic. A fund-specific import/reference
                        # calculation can retry the missing date later without blocking startup.
                        _LOGGER.warning('AMFI reference history warm-up failed for %s: %s', ref_date.isoformat(), exc)

            # Only populate the database for funds that actually exist. A brand-new install
            # must not create thousands of reference rows for unrelated AMFI schemes.
            held_codes = sorted({
                str(f['scheme_code'] or '').strip()
                for f in self.db.all_funds()
                if str(f['scheme_code'] or '').strip()
            })
            if held_codes:
                self.update_nav_reference_table(latest_snapshot, scheme_codes=held_codes)
        except Exception as exc:
            _LOGGER.warning('Initial AMFI NAV/reference prefetch failed: %s', exc)

    @staticmethod
    def _parse_amfi_history_text(raw: str):
        """Parse one AMFI historical-date report into {scheme_code: (date, nav)}.

        The historical report is semicolon-delimited but its column order differs
        from NAVAll.txt, so the header names are used when available.
        """
        if not isinstance(raw, str):
            raise ValueError('AMFI historical NAV payload is not text')
        rows = list(csv.reader(io.StringIO(raw), delimiter=';'))
        header = None
        header_idx = None
        for idx, row in enumerate(rows):
            normalized = [str(x).strip().lower() for x in row]
            if 'scheme code' in normalized and 'net asset value' in normalized and 'date' in normalized:
                header = normalized
                header_idx = idx
                break
        if header is None:
            # Fallback to the known AMFI history layout.
            header = ['scheme code','nav name','plan','option','isin div payout/isin growth','isin div reinvestment','net asset value','date']
            header_idx = -1
        def col(name):
            try:
                return header.index(name)
            except ValueError:
                return None
        code_i = col('scheme code')
        nav_i = col('net asset value')
        date_i = col('date')
        if code_i is None or nav_i is None or date_i is None:
            raise ValueError('AMFI historical NAV header missing required columns')
        snapshot = {}
        for row in rows[header_idx + 1:]:
            if max(code_i, nav_i, date_i) >= len(row):
                continue
            code = str(row[code_i]).strip()
            if not code.isdigit():
                continue
            try:
                nav = float(str(row[nav_i]).strip())
                nav_date = datetime.strptime(str(row[date_i]).strip(), '%d-%b-%Y').date()
            except (TypeError, ValueError):
                continue
            if not math.isfinite(nav) or nav <= 0:
                continue
            snapshot[code] = (nav_date, nav)
        return snapshot

    def _amfi_history_snapshot_for_date(self, target_date):
        """Return/cache the AMFI historical report for one date."""
        if not hasattr(self, '_amfi_history_snapshot_lock'):
            self._amfi_history_snapshot_lock = threading.Lock()
            self._amfi_history_snapshot_cache = {}
        target = parse_optional_date(target_date)
        if not target:
            raise ValueError('Historical AMFI date is required')
        key = target.isoformat()
        with self._amfi_history_snapshot_lock:
            cached = self._amfi_history_snapshot_cache.get(key)
            if cached is not None:
                return dict(cached)
        cache_dir = DATA_DIR / 'amfi_nav_history_v2'
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f'{key}.txt'
        raw = None
        if cache_path.exists():
            try:
                raw = cache_path.read_text(encoding='utf-8-sig')
            except Exception:
                raw = None
        if not raw or len(raw.strip()) < 50:
            url = f'https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt={target.strftime("%d-%b-%Y")}'
            try:
                raw = request_bytes(url, timeout=HTTP_TIMEOUT, retries=HTTP_RETRIES).decode('utf-8-sig', errors='replace')
            except Exception as exc:
                raise RuntimeError(f'AMFI historical NAV fetch failed for {target.isoformat()}: {exc}')
            if not raw.strip():
                raise RuntimeError(f'AMFI historical NAV file is empty for {target.isoformat()}')
            try:
                cache_path.write_text(raw, encoding='utf-8')
            except Exception:
                pass
        snapshot = self._parse_amfi_history_text(raw)
        if not snapshot:
            raise RuntimeError(f'AMFI historical NAV file contains no usable rows for {target.isoformat()}')
        with self._amfi_history_snapshot_lock:
            self._amfi_history_snapshot_cache[key] = dict(snapshot)
        return dict(snapshot)

    def _previous_nse_open_before(self, value):
        for offset in range(1, 370):
            candidate = value - timedelta(days=offset)
            if self._holiday_status(candidate).get('nse_open'):
                return candidate
        return None

    def _first_nse_open_on_or_after(self, value, limit_date=None):
        for offset in range(0, 370):
            candidate = value + timedelta(days=offset)
            if limit_date and candidate > limit_date:
                return None
            if self._holiday_status(candidate).get('nse_open'):
                return candidate
        return None

    def _find_amfi_reference(self, scheme_code, start_date, *, direction='forward', stop_date=None):
        """Find a scheme NAV on the first eligible AMFI date in a bounded direction."""
        code = str(scheme_code).strip()
        candidate = parse_optional_date(start_date)
        if not code or not candidate:
            return (None, None)
        eligible_checks = 0
        max_business_days = 30
        step = 1 if direction == 'forward' else -1
        for _ in range(370):
            if stop_date and ((direction == 'forward' and candidate > stop_date) or (direction == 'backward' and candidate < stop_date)):
                return (None, None)
            if self._holiday_status(candidate).get('nse_open'):
                eligible_checks += 1
                try:
                    snap = self._amfi_history_snapshot_for_date(candidate)
                except Exception:
                    raise
                record = snap.get(code)
                if record and record[0] == candidate:
                    return candidate, float(record[1])
                if eligible_checks >= max_business_days:
                    return (None, None)
            candidate = candidate + timedelta(days=step)
        return (None, None)

    def _compute_nav_reference(self, scheme_code, latest_record):
        latest_d, latest_nav = latest_record
        latest_d = parse_optional_date(latest_d)
        latest_nav = float(latest_nav)
        if not latest_d or not math.isfinite(latest_nav):
            raise ValueError('Invalid latest NAV record')
        previous_candidate = self._previous_nse_open_before(latest_d)
        prev_d, prev_nav = self._find_amfi_reference(
            scheme_code, previous_candidate, direction='backward'
        ) if previous_candidate else (None, None)
        month_start = date(latest_d.year, latest_d.month, 1)
        month_candidate = self._first_nse_open_on_or_after(month_start, limit_date=latest_d)
        month_d, month_nav = self._find_amfi_reference(
            scheme_code, month_candidate, direction='forward', stop_date=latest_d
        ) if month_candidate else (None, None)
        year_start = date(latest_d.year, 1, 1)
        year_candidate = self._first_nse_open_on_or_after(year_start, limit_date=latest_d)
        year_d, year_nav = self._find_amfi_reference(
            scheme_code, year_candidate, direction='forward', stop_date=latest_d
        ) if year_candidate else (None, None)
        return {
            'scheme_code': str(scheme_code).strip(),
            'latest_nav_date': latest_d.isoformat(), 'latest_nav': latest_nav,
            'previous_nav_date': iso_date(prev_d), 'previous_nav': prev_nav,
            'month_nav_date': iso_date(month_d), 'month_nav': month_nav,
            'year_nav_date': iso_date(year_d), 'year_nav': year_nav,
            'updated_at': iso_now(),
        }

    def _nav_reference(self, scheme_code):
        return self.db.nav_reference(scheme_code)

    def update_nav_reference_table(self, latest_snapshot, scheme_codes=None):
        """Atomically update complete reference records for owned schemes only."""
        if not hasattr(self, '_nav_reference_lock'):
            self._nav_reference_lock = threading.Lock()
        codes = {str(c).strip() for c in (scheme_codes or latest_snapshot.keys()) if str(c).strip()}
        if scheme_codes is None:
            held = {str(f['scheme_code']).strip() for f in self.db.all_funds() if str(f['scheme_code'] or '').strip()}
            codes = codes & held

        updates = []
        with self._nav_reference_lock:
            for code in sorted(codes):
                latest_record = latest_snapshot.get(code)
                if latest_record is None:
                    continue
                latest_date = parse_optional_date(latest_record[0])
                if latest_date is None:
                    continue
                current = self.db.nav_reference(code)
                if current:
                    current_date = parse_optional_date(current['latest_nav_date'])
                    month_date = parse_optional_date(current['month_nav_date'])
                    year_date = parse_optional_date(current['year_nav_date'])
                    latest_value_matches = False
                    try:
                        latest_value_matches = math.isclose(float(current['latest_nav']), float(latest_record[1]), rel_tol=0.0, abs_tol=1e-12)
                    except (TypeError, ValueError):
                        latest_value_matches = False
                    complete = (
                        current_date == latest_date and latest_value_matches and
                        current['previous_nav_date'] and current['previous_nav'] is not None and
                        current['month_nav_date'] and current['month_nav'] is not None and
                        current['year_nav_date'] and current['year_nav'] is not None and
                        month_date is not None and month_date <= latest_date and month_date.year == latest_date.year and month_date.month == latest_date.month and
                        year_date is not None and year_date <= latest_date and year_date.year == latest_date.year and
                        parse_optional_date(current['previous_nav_date']) is not None and
                        parse_optional_date(current['previous_nav_date']) < latest_date
                    )
                    if current_date and current_date > latest_date:
                        continue
                    if complete:
                        continue

                # Safe migration path for an existing installation upgrading to this schema.
                if current is None:
                    existing = next((f for f in self.db.all_funds() if str(f['scheme_code'] or '').strip() == code), None)
                    if existing and str(existing['last_nav_date'] or '') == latest_date.isoformat() and \
                       existing['previous_nav'] is not None and existing['previous_nav_date'] and \
                       existing['month_nav'] is not None and existing['month_nav_date'] and \
                       existing['year_nav'] is not None and existing['year_nav_date']:
                        updates.append({
                            'scheme_code': code,
                            'latest_nav_date': latest_date.isoformat(), 'latest_nav': float(existing['last_nav']),
                            'previous_nav_date': existing['previous_nav_date'], 'previous_nav': float(existing['previous_nav']),
                            'month_nav_date': existing['month_nav_date'], 'month_nav': float(existing['month_nav']),
                            'year_nav_date': existing['year_nav_date'], 'year_nav': float(existing['year_nav']),
                            'updated_at': iso_now(),
                        })
                        continue

                # Compute every pending record before writing anything. If a required AMFI
                # history download fails, nothing from this update cycle is committed.
                record = self._compute_nav_reference(code, latest_record)
                for field in ('previous_nav_date', 'month_nav_date', 'year_nav_date'):
                    if record.get(field) is None or record.get(field.replace('_date', '')) is None:
                        raise RuntimeError(f'AMFI reference NAV unavailable for scheme {code}: {field}')
                updates.append(record)

            if updates:
                self.db.upsert_nav_references_atomic(updates)
        return updates

    @staticmethod
    def _parse_amfi_latest_nav_text(raw: str, held_codes: set[str] | None = None):
        """Parse AMFI NAVAll.txt into {scheme_code: (latest_date, nav)}.

        NAVAll.txt is a semicolon-delimited text export. It contains section/header
        lines and occasional historical rows, so only rows with a numeric scheme code
        and a valid positive NAV/date are considered.
        """
        if not isinstance(raw, str):
            raise ValueError('AMFI latest NAV payload is not text')
        snapshot = {}
        wanted = {str(code).strip() for code in (held_codes or set()) if str(code).strip()}
        for row in csv.reader(io.StringIO(raw), delimiter=';'):
            if len(row) < 8:
                continue
            code = str(row[0]).strip()
            if not code.isdigit():
                continue
            if wanted and code not in wanted:
                continue
            try:
                nav = float(str(row[6]).strip())
                nav_date = datetime.strptime(str(row[7]).strip(), '%d-%b-%Y').date()
                if not math.isfinite(nav) or nav <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            previous = snapshot.get(code)
            if previous is None or nav_date >= previous[0]:
                snapshot[code] = (nav_date, nav)
        return snapshot

    def request_latest_nav_snapshot(self, force=False, all_schemes=False):
        """Fetch and cache the latest NAV snapshot from AMFI NAVAll.txt.

        Historical NAV lookups continue to use the existing MFAPI endpoints. The
        latest snapshot cache is intentionally limited to two hours. ``all_schemes``
        is used by import so a brand-new holding that is not yet in the database is
        still resolved from the same AMFI latest-NAV snapshot.
        """
        now = time.monotonic()
        with self._latest_nav_snapshot_lock:
            cached = self._latest_nav_snapshot
            cached_at = self._latest_nav_snapshot_at
            cache_is_full = bool(getattr(self, '_latest_nav_snapshot_all_schemes', False))
            cache_usable = cached is not None and (not all_schemes or cache_is_full)
            if not force and cache_usable and (now - cached_at) < NAV_SNAPSHOT_CACHE_SECONDS:
                return dict(cached)

        held_codes = None if all_schemes else {str(f['scheme_code'] or '').strip() for f in self.db.all_funds() if str(f['scheme_code'] or '').strip()}
        last_exc = None
        raw = None
        for url in AMFI_NAV_URLS:
            try:
                raw = request_bytes(url, timeout=HTTP_TIMEOUT, retries=HTTP_RETRIES).decode('utf-8-sig', errors='replace')
                if raw.strip():
                    break
            except Exception as exc:
                last_exc = exc
                _LOGGER.warning('AMFI latest NAV fetch failed from %s: %s', url, exc)

        if raw is None:
            raise RuntimeError(f'AMFI latest NAV fetch failed: {last_exc}')

        snapshot = self._parse_amfi_latest_nav_text(raw, held_codes=held_codes)
        if not snapshot:
            raise RuntimeError('AMFI latest NAV endpoint returned no usable scheme records')
        with self._latest_nav_snapshot_lock:
            self._latest_nav_snapshot = dict(snapshot)
            self._latest_nav_snapshot_at = time.monotonic()
            self._latest_nav_snapshot_all_schemes = bool(all_schemes)
        return dict(snapshot)

    def nav_refresh_is_recent(self, max_age_seconds=NAV_SNAPSHOT_CACHE_SECONDS):
        """Return True when the last successful full NAV refresh is younger than max_age_seconds."""
        stamp = self.db.last_refresh_time(None)
        if not stamp:
            return False
        try:
            raw = str(stamp).replace('Z', '+00:00')
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
            return 0 <= age < float(max_age_seconds)
        except Exception:
            return False

    def refresh_all_if_stale(self, max_age_seconds=NAV_SNAPSHOT_CACHE_SECONDS):
        """Run a full NAV refresh only when the last one is older than max_age_seconds."""
        if self.nav_refresh_is_recent(max_age_seconds):
            _refresh_state(status='completed', stage='Skipped', progress=100,
                           message='NAV refresh skipped — last successful refresh is less than 2 hours old.',
                           last_nav_refresh=self.db.last_refresh_time(None), error=None)
            return False
        return self.refresh_all()

    def request_nav_json_with_retry(self, url: str):
        last_exc = None
        for attempt in range(1, NAV_RETRY_ATTEMPTS + 1):
            try:
                return request_json(url, retries=1)
            except HTTPError as exc:
                last_exc = exc
                if exc.code in (400, 401, 403, 404) or attempt >= NAV_RETRY_ATTEMPTS:
                    raise
                time.sleep(NAV_RETRY_DELAY_SECONDS)
            except Exception as exc:
                last_exc = exc
                if attempt < NAV_RETRY_ATTEMPTS:
                    time.sleep(NAV_RETRY_DELAY_SECONDS)
        raise last_exc or RuntimeError('NAV request failed')

    def nav_on_date(self, scheme_code, target_date):
        d=parse_optional_date(target_date) if isinstance(target_date,str) else target_date
        if not d: raise ValueError('NAV date is required')
        start=(d-timedelta(days=10)).isoformat()
        payload=self.request_nav_json_with_retry(f'{MFAPI_BASE}/mf/{scheme_code}?startDate={start}')
        for item in payload.get('data') or []:
            try:
                idate=parse_nav_date(item['date'])
                if idate == d:
                    return float(item['nav']), idate
            except Exception:
                continue
        raise ValueError(f'No NAV is available for {d.isoformat()}')

    def nav_on_or_after_date(self, scheme_code, start_date, end_date):
        """Return the earliest available NAV on or after start_date through end_date.

        This is used only when a live NAV event arrives on a different date from the
        pending SIP's effective NAV date. It deliberately prevents substitution with
        an older NAV and chooses the NAV closest to the SIP date from the forward
        range.
        """
        start_date = parse_optional_date(start_date)
        end_date = parse_optional_date(end_date)
        if not start_date or not end_date or end_date < start_date:
            return None
        payload = self.request_nav_json_with_retry(
            f'{MFAPI_BASE}/mf/{scheme_code}?startDate={start_date.isoformat()}&endDate={end_date.isoformat()}'
        )
        candidates = []
        for item in payload.get('data') or []:
            try:
                nav_date = parse_nav_date(item['date'])
                nav = float(item['nav'])
                if start_date <= nav_date <= end_date and math.isfinite(nav):
                    candidates.append((nav_date, nav))
            except Exception:
                continue
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[0])

    def refresh_fund_from_latest_snapshot(self, fund, latest_record):
        """Refresh a fund using AMFI latest + cached AMFI reference NAVs."""
        holding_id = fund['id']
        try:
            latest_d, latest_nav = latest_record
            latest_d = parse_optional_date(latest_d)
            latest_nav = float(latest_nav)
            if not latest_d or not math.isfinite(latest_nav):
                raise ValueError('Invalid latest NAV record')
            self.update_nav_reference_table({str(fund['scheme_code']).strip(): (latest_d, latest_nav)}, scheme_codes=[str(fund['scheme_code']).strip()])
            ref = self._nav_reference(fund['scheme_code'])
            if not ref:
                raise RuntimeError(f'NAV reference data unavailable for scheme {fund["scheme_code"]}')
            stored_last_d = parse_optional_date(fund['last_nav_date'])
            if stored_last_d and latest_d < stored_last_d:
                raise RuntimeError(f'Latest NAV date {latest_d.isoformat()} is older than stored NAV date {stored_last_d.isoformat()}')
            prev_nav = ref['previous_nav']
            prev_d = parse_optional_date(ref['previous_nav_date'])
            vals = {
                'last_nav': latest_nav, 'last_nav_date': latest_d.isoformat(),
                'previous_nav': prev_nav, 'previous_nav_date': iso_date(prev_d),
                'month_nav': ref['month_nav'], 'month_nav_date': ref['month_nav_date'],
                'year_nav': ref['year_nav'], 'year_nav_date': ref['year_nav_date'],
                'last_refresh': iso_now(), 'last_error': None,
            }
            self.db.update_nav(holding_id, vals)
            try:
                self.maybe_execute_sip(self.db.get_fund(holding_id), [(latest_d, latest_nav)], latest_d, source='live', previous_nav_date=stored_last_d)
            except Exception as sip_exc:
                self.db.update_nav(holding_id, {**vals, 'last_error': f'SIP execution failed: {sip_exc}'})
            return True
        except Exception as exc:
            self.db.update_nav(holding_id, {
                'last_nav': fund['last_nav'], 'last_nav_date': fund['last_nav_date'],
                'previous_nav': fund['previous_nav'], 'previous_nav_date': fund['previous_nav_date'],
                'month_nav': fund['month_nav'], 'month_nav_date': fund['month_nav_date'],
                'year_nav': fund['year_nav'], 'year_nav_date': fund['year_nav_date'],
                'last_refresh': fund['last_refresh'], 'last_error': str(exc)
            })
            return False

    def refresh_fund(self, fund, entries=None, fetch_error=None, latest_record=None):
        """Refresh one fund from AMFI; MFAPI remains for targeted historical SIP work."""
        holding_id = fund['id']
        code = str(fund['scheme_code'] or '').strip()
        if fetch_error:
            self.db.update_nav(holding_id, {
                'last_nav': fund['last_nav'], 'last_nav_date': fund['last_nav_date'],
                'previous_nav': fund['previous_nav'], 'previous_nav_date': fund['previous_nav_date'],
                'month_nav': fund['month_nav'], 'month_nav_date': fund['month_nav_date'],
                'year_nav': fund['year_nav'], 'year_nav_date': fund['year_nav_date'],
                'last_refresh': fund['last_refresh'], 'last_error': fetch_error
            })
            return None
        try:
            if latest_record is None:
                latest_snapshot = self.request_latest_nav_snapshot()
                latest_record = latest_snapshot.get(code)
            if latest_record is None:
                raise RuntimeError(f'Scheme {code} was not present in latest AMFI NAV snapshot')
            latest_d, latest_nav = latest_record
            latest_d = parse_optional_date(latest_d)
            latest_nav = float(latest_nav)
            if not latest_d or not math.isfinite(latest_nav):
                raise ValueError('Invalid latest NAV record')
            stored_last_d = parse_optional_date(fund['last_nav_date'])
            if stored_last_d and latest_d < stored_last_d:
                raise RuntimeError(f'Latest NAV date {latest_d.isoformat()} is older than stored NAV date {stored_last_d.isoformat()}')
            self.update_nav_reference_table({code: (latest_d, latest_nav)}, scheme_codes=[code])
            ref = self._nav_reference(code)
            if not ref:
                raise RuntimeError(f'NAV reference data unavailable for scheme {code}')
            prev_nav = ref['previous_nav']; prev_d = parse_optional_date(ref['previous_nav_date'])
            vals = {
                'last_nav': latest_nav, 'last_nav_date': latest_d.isoformat(),
                'previous_nav': prev_nav, 'previous_nav_date': iso_date(prev_d),
                'month_nav': ref['month_nav'], 'month_nav_date': ref['month_nav_date'],
                'year_nav': ref['year_nav'], 'year_nav_date': ref['year_nav_date'],
                'last_refresh': iso_now(), 'last_error': None,
            }
            previous_nav_date = fund['last_nav_date']
            self.db.update_nav(holding_id, vals)
            try:
                self.maybe_execute_sip(self.db.get_fund(holding_id), [(latest_d, latest_nav)], latest_d, source='live', previous_nav_date=previous_nav_date)
            except Exception as sip_exc:
                self.db.update_nav(holding_id, {**vals, 'last_error': f'SIP execution failed: {sip_exc}'})
            return entries if entries is not None else [(latest_d, latest_nav)]
        except Exception as exc:
            self.db.update_nav(holding_id, {
                'last_nav': fund['last_nav'], 'last_nav_date': fund['last_nav_date'],
                'previous_nav': fund['previous_nav'], 'previous_nav_date': fund['previous_nav_date'],
                'month_nav': fund['month_nav'], 'month_nav_date': fund['month_nav_date'],
                'year_nav': fund['year_nav'], 'year_nav_date': fund['year_nav_date'],
                'last_refresh': fund['last_refresh'], 'last_error': str(exc)
            })
            return None

    def first_scheduled_on_or_after(self, effective_date: date, sip_day: int):
        import calendar
        y,m=effective_date.year,effective_date.month
        candidate=date(y,m,min(int(sip_day),calendar.monthrange(y,m)[1]))
        if candidate < effective_date:
            if m==12:
                y,m=y+1,1
            else:
                m+=1
            candidate=date(y,m,min(int(sip_day),calendar.monthrange(y,m)[1]))
        return candidate

    def rebuild_sip_history_for_edit(self, holding_id, new_amount, new_day, effective_date):
        fund=self.db.get_fund(holding_id)
        if not fund:
            raise ValueError('fund not found')
        existing=[r for r in self.db.transactions(holding_id) if r['txn_type']=='sip']
        last_existing=max((parse_optional_date(r['txn_date']) for r in existing), default=None)
        first_sched=self.first_scheduled_on_or_after(effective_date,int(new_day))
        generated=[]
        if last_existing and first_sched <= last_existing:
            months=(last_existing.year-first_sched.year)*12+(last_existing.month-first_sched.month)+1
            generated=self.historical_sip_transactions(fund['scheme_code'],float(new_amount),int(new_day),first_sched,months)
        self.db.rebuild_sip_schedule(holding_id,float(new_amount),int(new_day),effective_date,generated)

    def _effective_sip_trading_date(self, sip_date: date):
        """Return the first NSE-open trading date on/after the scheduled SIP date."""
        cal = self._holiday_calendar_payload().get('dates') or {}
        cur = sip_date
        for _ in range(370):
            if bool(cal.get(cur.isoformat(), {}).get('nse_open')):
                return cur
            cur += timedelta(days=1)
        return None

    def _sip_cycle_candidate_dates(self, target, effective_date=None):
        """Return transaction dates that can represent the scheduled SIP cycle.

        The scheduled SIP date and the actual NAV/transaction date are distinct.
        The effective date is the first available NSE-open date on/after the scheduled
        date, so it remains evidence for the same SIP cycle even when it crosses a
        calendar-month boundary.
        """
        dates = {target}
        if effective_date is not None and effective_date >= target:
            dates.add(effective_date)
        return dates

    def _recover_sip_cycle(self, fund, target, cycle, *, repair_live_record=False):
        """Return True when this SIP cycle already has durable execution history.

        Imported statement SIPs may be stored on the effective NAV/transaction
        date rather than the scheduled SIP date. They satisfy the same monthly
        cycle, but must never be promoted to a live execution record merely
        because the normal scheduler recognized them.
        """
        sip_date = target.isoformat()
        live_record = self.db.live_sip_execution_for_cycle(fund['id'], sip_date)
        legacy_record = self.db.sip_execution_for_cycle(fund['id'], sip_date)
        live_tx = self.db.live_sip_transaction_for_cycle(fund['id'], sip_date)
        imported_tx = None

        if not (live_record or legacy_record or live_tx):
            effective_date = self._effective_sip_trading_date(target)
            candidate_dates = self._sip_cycle_candidate_dates(target, effective_date)
            for row in self.db.transactions(fund['id']):
                try:
                    if str(row['txn_type'] or '') != 'sip':
                        continue
                    row_date = parse_optional_date(row['txn_date'])
                    if row_date in candidate_dates and (
                        float(row['amount'] or 0) > 0 or float(row['units'] or 0) > 0
                    ):
                        imported_tx = row
                        break
                except Exception:
                    continue
            if imported_tx is None:
                return False

        record = live_record or legacy_record
        if record is not None:
            repair_nav_date = parse_optional_date(record['nav_date'])
            repair_at = record['executed_at']
            amount = float(record['amount'] or 0)
            units = float(record['units'] or 0)
            nav = float(record['nav']) if record['nav'] is not None else None
        elif live_tx is not None:
            repair_nav_date = parse_optional_date(live_tx['txn_date'])
            repair_at = live_tx['created_at']
            amount = float(live_tx['amount'] or 0)
            units = float(live_tx['units'] or 0)
            nav = float(live_tx['nav']) if live_tx['nav'] is not None else None
        else:
            repair_nav_date = parse_optional_date(imported_tx['txn_date'])
            repair_at = imported_tx['created_at']
            amount = float(imported_tx['amount'] or 0)
            units = float(imported_tx['units'] or 0)
            nav = float(imported_tx['nav']) if imported_tx['nav'] is not None else None

        if repair_nav_date is None:
            return True

        self.db.set_sip_executed(fund['id'], cycle, repair_nav_date.isoformat())

        # Imported statement transactions are financial evidence only. Never turn
        # them into today's live-execution evidence during normal refresh.
        if repair_live_record and imported_tx is None and live_record is None and nav is not None:
            # Repaired live records must use the same logical reporting date as
            # normal live executions; this path is also subject to the 23:00 window.
            execution_date = self.sip_reporting_date().isoformat()
            self.db.record_live_sip_execution(
                fund, sip_date, repair_nav_date.isoformat(), amount, units, nav,
                executed_at=repair_at, execution_date=execution_date
            )

        if repair_live_record and imported_tx is None and legacy_record is None and nav is not None:
            try:
                self.db.record_sip_execution(
                    fund, sip_date, repair_nav_date.isoformat(), amount, units, nav,
                    executed_at=repair_at
                )
            except Exception:
                pass
        return True

    def maybe_execute_sip(self, fund, entries, nav_date: date, *, source='live', statement_to=None, previous_nav_date=None):
        """Process one due SIP with one common execution path.

        NAV selection branches only for same-day versus later NAV dates. Everything
        after NAV selection uses the same duplicate check and transaction path.
        """
        if not fund['sip_enabled'] or not fund['sip_amount'] or not fund['sip_day']:
            return False
        if source not in ('live', 'import'):
            raise ValueError('Invalid SIP execution source')
        if source == 'import' and not statement_to:
            return False

        import calendar
        nav_date = parse_optional_date(nav_date)
        if not nav_date:
            return False

        day = int(fund['sip_day'])
        if source == 'live':
            last_cycle = str(fund['last_sip_cycle'] or '').strip()
            target = None
            if re.fullmatch(r'\d{4}-\d{2}', last_cycle):
                try:
                    ly, lm = (int(x) for x in last_cycle.split('-', 1))
                    if lm == 12:
                        ly, lm = ly + 1, 1
                    else:
                        lm += 1
                    target = date(ly, lm, min(day, calendar.monthrange(ly, lm)[1]))
                except Exception:
                    target = None
            if target is None:
                target = date(nav_date.year, nav_date.month, min(day, calendar.monthrange(nav_date.year, nav_date.month)[1]))
        else:
            target = date(nav_date.year, nav_date.month, min(day, calendar.monthrange(nav_date.year, nav_date.month)[1]))

        effective_date = self._effective_sip_trading_date(target)
        cycle = f'{target.year:04d}-{target.month:02d}'

        # Execution history is authoritative for a completed cycle. Recover any
        # missing cycle/log marker before using last_sip_cycle as the cheap gate.
        if self._recover_sip_cycle(fund, target, cycle, repair_live_record=(source == 'live')):
            return False
        if fund['last_sip_cycle'] == cycle:
            return False

        if source == 'import':
            statement_to = parse_optional_date(statement_to)
            if not statement_to or target < statement_to:
                return False

        # Select the NAV. Same effective-date NAV uses the triggering event directly;
        # a later NAV causes one targeted historical lookup over the forward range.
        execution_nav_date = nav_date
        execution_nav = None
        if source == 'live':
            if effective_date is None or nav_date < effective_date:
                return False
            if nav_date == effective_date:
                for d, n in entries or []:
                    if parse_optional_date(d) == execution_nav_date:
                        execution_nav = float(n)
                        break
            else:
                delayed_nav = self.nav_on_or_after_date(fund['scheme_code'], effective_date, nav_date)
                if delayed_nav is None:
                    return False
                execution_nav_date, execution_nav = delayed_nav
        else:
            for d, n in entries or []:
                if parse_optional_date(d) == execution_nav_date:
                    execution_nav = float(n)
                    break

        if execution_nav is None or not math.isfinite(float(execution_nav)):
            return False

        nav = float(execution_nav)
        nav_date = execution_nav_date
        amount = float(fund['sip_amount'])
        units = amount / nav
        folio = (fund['normalized_folio'] if 'normalized_folio' in fund.keys() and fund['normalized_folio']
                 else fund['folio'] if 'folio' in fund.keys() else None)

        # Final duplicate check immediately before the financial mutation. If a
        # concurrent/previous path completed the cycle while NAV was being fetched,
        # recover its marker instead of creating another transaction.
        if self._recover_sip_cycle(fund, target, cycle, repair_live_record=(source == 'live')):
            return False

        # Final transaction-level guard: a real imported SIP may be recorded on
        # the effective NAV date rather than the scheduled SIP date. Treat both
        # dates as the same monthly cycle, while preserving the existing skipped
        # marker behavior on the scheduled date.
        duplicate_dates = self._sip_cycle_candidate_dates(target, effective_date)
        existing_cycle_tx = next((
            r for r in self.db.transactions(fund['id'])
            if str(r['txn_type'] or '') in ('sip', 'sip_skipped')
            and (d := parse_optional_date(r['txn_date'])) is not None
            and d in duplicate_dates
        ), None)
        if existing_cycle_tx is not None:
            existing_date = parse_optional_date(existing_cycle_tx['txn_date']) or target
            self.db.set_sip_executed(fund['id'], cycle, existing_date.isoformat())
            return False

        # Common final execution path for both normal and delayed NAV cases.
        self.db.add_transaction(
            fund['id'], 'sip', nav_date.isoformat(), amount, units, nav,
            f'Scheduled SIP for {target.isoformat()} (NAV date {nav_date.isoformat()})',
            folio=folio, source='sip_execution'
        )
        self.db.set_sip_executed(fund['id'], cycle, nav_date.isoformat())
        if source == 'live':
            executed_at = iso_now()
            # SIP Executed Today uses the established 23:00–22:59:59 logical
            # reporting window. A delayed NAV arriving at 09:00 on 26-Aug for the
            # 25-Aug SIP therefore belongs to the 25-Aug reporting day.
            execution_date = self.sip_reporting_date().isoformat()
            self.db.record_live_sip_execution(
                fund, target.isoformat(), nav_date.isoformat(), amount, units, nav,
                executed_at=executed_at, execution_date=execution_date
            )
            try:
                self.db.record_sip_execution(
                    fund, target.isoformat(), nav_date.isoformat(), amount, units, nav,
                    executed_at=executed_at
                )
            except Exception:
                pass
        try:
            self._write_integration_state()
        except Exception:
            pass
        return True

    def refresh_all(self):
        # Only one full refresh may run at a time. A second trigger simply
        # leaves the existing refresh running rather than stacking NAV requests.
        if not self.lock.acquire(blocking=False):
            return False
        refresh_id = uuid.uuid4().hex
        _refresh_state(id=refresh_id, status='running', stage='Starting', progress=0,
                       message='Preparing NAV refresh…', error=None)
        try:
            self._get_ha_timezone()
            funds = self.db.all_funds()
            if not funds:
                completed_at = iso_now()
                self.db.clear_nav_refresh_error()
                self.db.set_setting('last_successful_nav_refresh', completed_at)
                self.db.set_setting('nav_refresh_retry_count', '0')
                self.db.set_setting('nav_refresh_retry_next', '')
                try:
                    self._write_integration_state()
                except Exception:
                    pass
                _refresh_state(status='completed', stage='Completed', progress=100,
                               message='No funds to refresh.', last_nav_refresh=completed_at)
                return True

            # Current and reference NAVs are resolved from the AMFI bulk snapshot/cache.
            # MFAPI remains reserved for targeted historical operations such as SIP NAV
            # selection and transaction/history lookups.
            grouped_funds = {}
            for fund in funds:
                grouped_funds.setdefault(str(fund['scheme_code']).strip(), []).append(fund)

            batch_ready = all(
                fund['last_nav_date'] and
                fund['month_nav'] is not None and fund['month_nav_date'] and
                fund['year_nav'] is not None and fund['year_nav_date']
                for fund in funds
            )

            total_unique = len(grouped_funds)
            processed_unique = 0
            failed_schemes = []

            if batch_ready:
                _refresh_state(stage='Downloading latest NAV snapshot', progress=5,
                               message='Fetching the latest NAV snapshot for all schemes…')
                latest_snapshot = self.request_latest_nav_snapshot(force=True)
                self.update_nav_reference_table(latest_snapshot, scheme_codes=grouped_funds.keys())

                for scheme_code, scheme_funds in grouped_funds.items():
                    first_fund = scheme_funds[0]
                    pct_before = int((processed_unique / total_unique) * 90)
                    _refresh_state(stage='Refreshing NAV', progress=max(10, pct_before),
                                   message=f'Refreshing NAV {processed_unique + 1} of {total_unique}: {first_fund["scheme_name"]}')

                    latest_record = latest_snapshot.get(scheme_code)
                    if latest_record is None:
                        error = f'Scheme {scheme_code} was not present in latest NAV snapshot'
                        failed_schemes.append(f'{first_fund["scheme_name"]}: {error}')
                        for fund in scheme_funds:
                            self.refresh_fund(fund, fetch_error=error)
                    else:
                        for fund in scheme_funds:
                            if not self.refresh_fund_from_latest_snapshot(fund, latest_record):
                                updated = self.db.get_fund(fund['id'])
                                err = updated['last_error'] if updated else 'NAV refresh failed'
                                failed_schemes.append(f'{fund["scheme_name"]}: {err}')

                    processed_unique += 1
                    pct_after = int((processed_unique / total_unique) * 90)
                    _refresh_state(stage='Refreshing NAV', progress=min(90, pct_after),
                                   message=f'Updated {processed_unique} of {total_unique} unique funds ({len(scheme_funds)} investor holdings): {first_fund["scheme_name"]}')
            else:
                # Initialization/import-safe path: current and previous/month/year
                # reference NAVs all come from the AMFI snapshot/reference cache.
                # MFAPI remains reserved for targeted historical operations.
                _refresh_state(stage='Downloading latest NAV snapshot', progress=5,
                               message='Fetching the latest NAV snapshot for all schemes…')
                latest_snapshot = self.request_latest_nav_snapshot(force=True)
                self.update_nav_reference_table(latest_snapshot, scheme_codes=grouped_funds.keys())
                for scheme_code, scheme_funds in grouped_funds.items():
                    first_fund = scheme_funds[0]
                    pct_before = int((processed_unique / total_unique) * 90)
                    _refresh_state(stage='Refreshing NAV', progress=max(1, pct_before),
                                   message=f'Refreshing NAV {processed_unique + 1} of {total_unique}: {first_fund["scheme_name"]}')

                    latest_record = latest_snapshot.get(scheme_code)
                    if latest_record is None:
                        shared_entries = None
                        shared_error = f'Scheme {scheme_code} was not present in latest AMFI NAV snapshot'
                    else:
                        shared_entries = self.refresh_fund(first_fund, latest_record=latest_record)
                        shared_error = None
                    if shared_entries is None:
                        updated_first = self.db.get_fund(first_fund['id'])
                        shared_error = updated_first['last_error'] if updated_first else 'NAV refresh failed'
                        failed_schemes.append(f'{first_fund["scheme_name"]}: {shared_error}')

                    for fund in scheme_funds[1:]:
                        result = self.refresh_fund(fund, entries=shared_entries, fetch_error=shared_error, latest_record=latest_record)
                        if result is None and shared_entries is not None:
                            updated = self.db.get_fund(fund['id'])
                            err = updated['last_error'] if updated else 'NAV refresh failed'
                            failed_schemes.append(f'{fund["scheme_name"]}: {err}')

                    processed_unique += 1
                    pct_after = int((processed_unique / total_unique) * 90)
                    _refresh_state(stage='Refreshing NAV', progress=min(90, pct_after),
                                   message=f'Updated {processed_unique} of {total_unique} unique funds ({len(scheme_funds)} investor holdings): {first_fund["scheme_name"]}')
                    if processed_unique < total_unique:
                        time.sleep(NAV_REQUEST_SPACING_SECONDS)

            _refresh_state(stage='Updating portfolio', progress=94, message='Recalculating portfolio totals and SIP status…')
            self.update_ha_states(build_portfolio(self.db))

            if failed_schemes:
                raise RuntimeError('NAV refresh failed for: ' + '; '.join(failed_schemes))

            completed_at = iso_now()
            self.db.clear_nav_refresh_error()
            self.db.set_setting('last_successful_nav_refresh', completed_at)
            self.db.set_setting('nav_refresh_retry_count', '0')
            self.db.set_setting('nav_refresh_retry_next', '')
            try:
                self._write_integration_state()
            except Exception:
                pass
            _refresh_state(status='completed', stage='Completed', progress=100,
                           message='NAV refresh complete.', last_nav_refresh=completed_at, error=None)
            return True
        except Exception as exc:
            error_text = str(exc) or exc.__class__.__name__
            # Keep last_successful_nav_refresh unchanged. The error is persisted
            # separately so HA can expose it without cluttering the web UI.
            self.db.set_nav_refresh_error(error_text, 'NAV refresh', 0, None)
            try:
                self._write_integration_state()
            except Exception:
                pass
            _refresh_state(status='error', stage='Failed', progress=100, message=error_text, error=error_text,
                           last_nav_refresh=self.db.last_refresh_time(None))
            raise
        finally:
            self.lock.release()

    def _font(self, size, bold=False):
        if ImageFont is None:
            return None
        candidates = [
            '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/TTF/DejaVuSans.ttf',
        ]
        for path in candidates:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    @staticmethod
    def _export_indian_number(value, decimals=0):
        """Format numbers using Indian digit grouping (e.g. 45,00,000)."""
        value = float(value)
        sign = '-' if value < 0 else ''
        formatted = f'{abs(value):.{decimals}f}'
        integer, _, fraction = formatted.partition('.')
        if len(integer) <= 3:
            grouped = integer
        else:
            last_three = integer[-3:]
            remaining = integer[:-3]
            groups = []
            while remaining:
                groups.append(remaining[-2:])
                remaining = remaining[:-2]
            grouped = ','.join(reversed(groups)) + ',' + last_three
        return sign + grouped + ('.' + fraction if decimals else '')

    @classmethod
    def _export_money(cls, value):
        if value is None:
            return '—'
        return '₹' + cls._export_indian_number(value, 0)

    @classmethod
    def _export_num(cls, value, decimals=2):
        if value is None:
            return '—'
        return cls._export_indian_number(value, decimals)

    @classmethod
    def _export_pct(cls, value):
        return '—' if value is None else f'{cls._export_indian_number(value, 2)}%'

    def export_portfolio_png(self, profile_id, output_name):
        """Render a clean, report-style PNG export for Home Assistant."""
        if Image is None:
            raise RuntimeError('PNG export requires Pillow in the add-on image')
        try:
            pid = None if profile_id in (None, '', 'all', 0, '0') else int(profile_id)
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid investor profile') from exc

        portfolio = build_portfolio(self.db, pid)
        rows = list(portfolio.get('rows') or [])
        profile = portfolio.get('profile') or {}
        total = portfolio.get('total') or {}

        def sort_key(row):
            day = row.get('sip_day')
            try:
                day_num = int(day)
            except (TypeError, ValueError):
                day_num = 999
            return (day_num, str(row.get('fund_name') or '').lower())
        rows.sort(key=sort_key)

        safe_name = re.sub(r'[^a-zA-Z0-9._-]+', '-', str(output_name or 'portfolio.png')).strip('-') or 'portfolio.png'
        if not safe_name.lower().endswith('.png'):
            safe_name += '.png'
        self._export_dir.mkdir(parents=True, exist_ok=True)
        target = self._export_dir / safe_name
        tmp = target.with_suffix('.tmp.png')

        # Match the compact report-style layout used by the reference export:
        # fixed-width table, centered title, dark grid lines, larger body text,
        # and minimal unused horizontal space.
        cols = [
            ('Fund Name', 300), ('SIP Amount', 112), ('SIP Date', 82),
            ('Day Change', 112), ('Day Change %', 118),
            ('Month Change', 112), ('Month Change %', 122),
            ('Total Value', 128), ('Year Returns', 124),
            ('Invested', 128), ('Total Profit', 128),
            ('Total Profit %', 112), ('XIRR%', 92),
        ]
        margin = 8
        top = 70
        header_h = 45
        row_h = 38
        bottom = 28
        width = margin * 2 + sum(w for _, w in cols)
        height = top + header_h + row_h * (len(rows) + 1) + bottom

        img = Image.new('RGB', (width, height), 'white')
        draw = ImageDraw.Draw(img)

        grid = '#4B4B4B'
        header_fill = '#F0F0F0'

        def rect(x0, y0, x1, y1, fill):
            draw.rectangle((x0, y0, x1, y1), fill=fill, outline=grid, width=1)

        def text(x, y, value, font, fill='#111111', anchor='la'):
            draw.text((x, y), str(value), font=font, fill=fill, anchor=anchor)

        def clip(value, max_chars=34):
            s = str(value or '')
            return s if len(s) <= max_chars else s[:max_chars - 1] + '…'

        f_title = self._font(22, True)
        f_profile = self._font(16, True)
        f_head = self._font(17, True)
        f_cell = self._font(18)
        f_cell_b = self._font(18, True)
        f_total = self._font(16, True)
        f_footer = self._font(12)

        # The export title should reflect the newest NAV date represented by
        # any exported fund, not the portfolio's oldest/overall as-of date.
        nav_dates = []
        for row in rows:
            try:
                if row.get('nav_date'):
                    nav_dates.append(date.fromisoformat(str(row['nav_date'])[:10]))
            except (TypeError, ValueError):
                pass
        latest_nav_date = max(nav_dates) if nav_dates else None
        report_date = (
            f'{latest_nav_date.day}/{latest_nav_date.month}/{latest_nav_date.year}'
            if latest_nav_date else
            datetime.now(NSE_TIMEZONE).strftime('%-d/%-m/%Y')
        )

        report_title = f'MUTUAL FUND TRACKER OF {report_date}'
        text(width / 2, 24, report_title, f_title, '#111111', 'ma')

        if profile.get('name'):
            text(width / 2, 49, str(profile['name']), f_profile, '#222222', 'ma')

        positive_columns = {3, 4, 5, 6, 8, 10, 11, 12}

        def tone_bg(value):
            try:
                v = float(value)
            except (TypeError, ValueError):
                return '#FFFFFF'
            if v > 0:
                return '#087F0E'
            if v < 0:
                return '#E3143A'
            return '#FFFFFF'

        def tone_color(value):
            try:
                return '#FFFFFF' if float(value) != 0 else '#222222'
            except (TypeError, ValueError):
                return '#222222'

        def value_text(row, idx):
            vals = [
                row.get('fund_name') or '',
                self._export_money(row.get('sip_amount')) if row.get('sip_enabled') else '0',
                row.get('sip_day') if row.get('sip_enabled') and row.get('sip_day') else '-',
                self._export_money(row.get('day_change')),
                self._export_pct(row.get('day_pct')),
                self._export_money(row.get('month_change')),
                self._export_pct(row.get('month_pct')),
                self._export_money(row.get('value')),
                self._export_money(row.get('year_change')),
                self._export_money(row.get('invested')),
                self._export_money(row.get('profit')),
                self._export_pct(row.get('profit_pct')),
                self._export_pct(row.get('xirr')),
            ]
            return vals[idx]

        raw_keys = [
            'fund_name', 'sip_amount', 'sip_day', 'day_change', 'day_pct',
            'month_change', 'month_pct', 'value', 'year_change', 'invested',
            'profit', 'profit_pct', 'xirr'
        ]

        header_lines = {
            'Fund Name': ['Fund Name'],
            'SIP Amount': ['SIP', 'Amount'],
            'SIP Date': ['SIP', 'Date'],
            'Day Change': ['Day', 'Change'],
            'Day Change %': ['Day Change', '%'],
            'Month Change': ['Month', 'Change'],
            'Month Change %': ['Month Change', '%'],
            'Total Value': ['Total', 'Value'],
            'Year Returns': ['Year', 'Returns'],
            'Invested': ['Invested'],
            'Total Profit': ['Total', 'Profit'],
            'Total Profit %': ['Total Profit', '%'],
            'XIRR%': ['XIRR%'],
        }

        x = margin
        y = top
        for label, w in cols:
            rect(x, y, x + w, y + header_h, header_fill)
            lines = header_lines.get(label, [label])
            if len(lines) == 1:
                text(x + w / 2, y + header_h / 2, lines[0], f_head, '#111111', 'mm')
            else:
                text(x + w / 2, y + header_h * 0.36, lines[0], f_head, '#111111', 'mm')
                text(x + w / 2, y + header_h * 0.70, lines[1], f_head, '#111111', 'mm')
            x += w

        for row in rows:
            y += header_h if y == top else row_h
            x = margin
            for ci, (_, w) in enumerate(cols):
                raw = row.get(raw_keys[ci])
                fill = tone_bg(raw) if ci in positive_columns else '#FFFFFF'
                color = tone_color(raw) if ci in positive_columns else '#111111'
                rect(x, y, x + w, y + row_h, fill)

                if ci == 0:
                    value = clip(value_text(row, ci), 35)
                    text(x + 7, y + row_h / 2, value, f_cell_b, color, 'lm')
                else:
                    text(x + w / 2, y + row_h / 2,
                         clip(value_text(row, ci), 16),
                         f_cell, color, 'mm')
                x += w

        y = top + header_h + row_h * len(rows)
        x = margin
        totals = [
            'Totals', '', '', self._export_money(total.get('day_change')), '',
            self._export_money(total.get('month_change')), '',
            self._export_money(total.get('value')), self._export_money(total.get('year_change')),
            self._export_money(total.get('invested')), self._export_money(total.get('profit')),
            self._export_pct(total.get('profit_pct')), self._export_pct(total.get('xirr')),
        ]
        total_raw = [
            None, None, None, total.get('day_change'), None, total.get('month_change'),
            None, total.get('value'), total.get('year_change'), total.get('invested'),
            total.get('profit'), total.get('profit_pct'), total.get('xirr')
        ]
        total_colored = {3, 5, 7, 8, 10, 11, 12}

        for ci, (_, w) in enumerate(cols):
            raw = total_raw[ci]
            fill = tone_bg(raw) if ci in total_colored else header_fill
            color = tone_color(raw) if ci in total_colored else '#111111'
            rect(x, y, x + w, y + row_h, fill)
            text(x + 7 if ci == 0 else x + w / 2, y + row_h / 2,
                 totals[ci], f_total, color, 'lm' if ci == 0 else 'mm')
            x += w

        export_now = datetime.now(NSE_TIMEZONE)
        text(margin, height - 10,
             f"Exported {export_now.strftime('%-d/%-m/%Y, %-I:%M:%S %p')} · {len(rows)} funds",
             f_footer, '#667085', 'la')

        img.save(tmp, format='PNG', optimize=True)
        tmp.replace(target)
        return str(target)

    def _export_request_loop(self):
        while True:
            try:
                if self._export_request_path.exists():
                    try:
                        request = json.loads(self._export_request_path.read_text(encoding='utf-8'))
                    finally:
                        try:
                            self._export_request_path.unlink()
                        except FileNotFoundError:
                            pass
                    request_id = str(request.get('request_id') or uuid.uuid4().hex)
                    result_path = self._export_dir / f'export_result_{request_id}.json'
                    try:
                        with self._export_lock:
                            output = self.export_portfolio_png(request.get('profile_id'), request.get('output_name'))
                        result = {'status': 'completed', 'request_id': request_id, 'path': output, 'updated_at': iso_now()}
                    except Exception as exc:
                        result = {'status': 'error', 'request_id': request_id, 'error': str(exc), 'updated_at': iso_now()}
                    self._export_dir.mkdir(parents=True, exist_ok=True)
                    result_path.write_text(json.dumps(result), encoding='utf-8')
            except Exception:
                pass
            time.sleep(0.5)

    def _integration_reload_ack_loop(self):
        """Reset the persisted reload request after HA acknowledges it."""
        while True:
            try:
                if INTEGRATION_RELOAD_ACK_PATH.exists():
                    try:
                        token = INTEGRATION_RELOAD_ACK_PATH.read_text(encoding='utf-8').strip()
                    except OSError:
                        token = ''
                    try:
                        INTEGRATION_RELOAD_ACK_PATH.unlink()
                    except FileNotFoundError:
                        pass
                    if token:
                        if self.db.acknowledge_integration_reload(token):
                            try:
                                self._write_integration_state()
                            except Exception:
                                pass
            except Exception:
                pass
            time.sleep(0.5)

    def _refresh_request_loop(self):
        # Lightweight local command channel used by the HA integration button.
        # It polls only the shared filesystem and therefore creates no NAV load.
        while True:
            try:
                if self._refresh_request_path.exists():
                    try:
                        self._refresh_request_path.unlink()
                    except FileNotFoundError:
                        pass
                    if get_refresh_state().get('status') != 'running':
                        threading.Thread(target=self.refresh_all, daemon=True).start()
            except Exception:
                pass
            time.sleep(1)

    def _last_successful_full_nav_refresh(self):
        """Return the timestamp of the last completed full NAV refresh.

        The scheduler should use the dedicated global success marker rather than
        per-holding ``last_refresh`` timestamps.  Per-holding timestamps can move
        while a refresh is still in progress and can therefore accidentally reset
        the scheduler countdown.
        """
        stamp = self.db.get_setting('last_successful_nav_refresh', '')
        return stamp or self.db.last_refresh_time(None)

    @staticmethod
    def _refresh_age_seconds(stamp):
        if not stamp:
            return None
        try:
            raw = str(stamp).replace('Z', '+00:00')
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
        except Exception:
            return None

    def automatic_nav_refresh_due(self, interval_seconds=None):
        """Return True when the normal automatic NAV refresh is due.

        This uses only the persisted *successful full refresh* marker.  It deliberately
        ignores per-holding timestamps because those can change while a refresh is in
        progress and must not postpone the scheduler.
        """
        interval = float(interval_seconds if interval_seconds is not None else self.db.get_refresh_interval())
        age = self._refresh_age_seconds(self._last_successful_full_nav_refresh())
        return age is None or age >= interval

    def background_loop(self):
        """Run scheduled NAV refreshes reliably using the persisted success timestamp."""
        time.sleep(2)
        retry_count = 0
        next_retry_at = None
        suppress_until = None
        last_seen_success = self._last_successful_full_nav_refresh()

        while True:
            try:
                interval = self.db.get_refresh_interval()
                now = time.monotonic()
                current_success = self._last_successful_full_nav_refresh()

                # A successful manual/automatic refresh resets the retry burst and
                # re-arms the normal interval countdown.  This is based only on the
                # persisted global success marker, never on per-fund timestamps.
                if current_success and current_success != last_seen_success:
                    last_seen_success = current_success
                    retry_count = 0
                    next_retry_at = None
                    suppress_until = None
                    self.db.set_setting('nav_refresh_retry_count', '0')
                    self.db.set_setting('nav_refresh_retry_next', '')

                age = self._refresh_age_seconds(current_success)
                due_by_age = age is None or age >= float(interval)
                retry_due = next_retry_at is not None and now >= next_retry_at
                retry_suppressed = suppress_until is not None and now < suppress_until
                due = retry_due or (next_retry_at is None and not retry_suppressed and due_by_age)

                if due:
                    try:
                        result = self.refresh_all()
                    except Exception as exc:
                        retry_count += 1
                        failure_mono = time.monotonic()
                        if retry_count <= AUTO_REFRESH_RETRY_COUNT:
                            next_retry_at = failure_mono + AUTO_REFRESH_RETRY_DELAY_SECONDS
                            retry_dt = (datetime.now(timezone.utc) + timedelta(seconds=AUTO_REFRESH_RETRY_DELAY_SECONDS)).isoformat()
                            self.db.set_setting('nav_refresh_retry_count', str(retry_count))
                            self.db.set_setting('nav_refresh_retry_next', retry_dt)
                            self.db.set_nav_refresh_error(str(exc), 'Automatic NAV refresh', retry_count, retry_dt)
                            try:
                                self._write_integration_state()
                            except Exception:
                                pass
                        else:
                            # Stop the short retry burst.  The next automatic attempt
                            # is scheduled from now using the configured normal interval.
                            next_retry_at = None
                            suppress_until = failure_mono + interval
                            retry_count = 0
                            self.db.set_setting('nav_refresh_retry_count', '0')
                            self.db.set_setting('nav_refresh_retry_next', '')
                            self.db.set_nav_refresh_error(
                                str(exc),
                                'Automatic NAV refresh (retry limit reached)',
                                AUTO_REFRESH_RETRY_COUNT,
                                None,
                            )
                            try:
                                self._write_integration_state()
                            except Exception:
                                pass
                    else:
                        if result is True:
                            # refresh_all() has already persisted the global success
                            # timestamp.  The next automatic refresh is based on that
                            # timestamp, so changing the interval in Settings also takes
                            # effect naturally without a restart.
                            retry_count = 0
                            next_retry_at = None
                            suppress_until = None
                            last_seen_success = self._last_successful_full_nav_refresh()
                        else:
                            # Another refresh is already running (typically a manual
                            # refresh).  Do not consume the retry budget; revisit soon.
                            next_retry_at = time.monotonic() + 5.0

            except Exception:
                # The scheduler must never die because of a transient settings,
                # database, or runtime error.  The next loop iteration retries it.
                pass

            try:
                interval = self.db.get_refresh_interval()
            except Exception:
                interval = REFRESH_SECONDS

            now = time.monotonic()
            if next_retry_at is not None:
                sleep_for = max(1.0, min(5.0, next_retry_at - now))
            elif suppress_until is not None and now < suppress_until:
                sleep_for = max(1.0, min(5.0, suppress_until - now))
            else:
                # Poll frequently enough to notice a settings change, but keep the
                # actual NAV refresh decision based on the persisted success age.
                sleep_for = min(5.0, max(1.0, float(interval)))
            time.sleep(sleep_for)


# Python's urllib has no quote imported under a shorter name; keep it local for the API call.
from urllib.parse import quote as urlquote

IMPORT_JOBS = {}
IMPORT_JOBS_LOCK = threading.Lock()

ADD_FUND_JOBS = {}
ADD_FUND_JOBS_LOCK = threading.Lock()

SIP_PREVIEW_JOBS = {}
SIP_PREVIEW_JOBS_LOCK = threading.Lock()
SIP_PREVIEWS = {}
SIP_PREVIEWS_LOCK = threading.Lock()
GRAPH_EXECUTOR = ThreadPoolExecutor(max_workers=2)
GRAPH_JOBS = globals().get('GRAPH_JOBS', {})
GRAPH_JOBS_LOCK = globals().get('GRAPH_JOBS_LOCK', threading.Lock())

def _start_graph_job(data):
    job_id=uuid.uuid4().hex
    job={'id':job_id,'status':'queued','progress':0,'stage':'Queued','message':'Preparing historical graph data…','result':None,'error':None,'created_at':iso_now(),'updated_at':iso_now()}
    with GRAPH_JOBS_LOCK: GRAPH_JOBS[job_id]=job
    GRAPH_EXECUTOR.submit(_run_graph_job,job_id,data)
    return job

def _update_graph_job(job_id, **updates):
    with GRAPH_JOBS_LOCK:
        job=GRAPH_JOBS.get(job_id)
        if not job:return
        job.update(updates); job['updated_at']=iso_now()

def _run_graph_job(job_id,data):
    try:
        _update_graph_job(job_id,status='running',stage='Fetching history',progress=10,message='Preparing historical NAV data…')
        profile_raw=data.get('profile_id')
        profile_id=None if profile_raw in (None,'','all','0') else int(profile_raw)
        holding_ids=data.get('holding_ids') or []
        parameters=data.get('parameters') or []
        period=str(data.get('period') or '1y')
        _update_graph_job(job_id,progress=35,message='Calculating historical portfolio values…')
        result=TRACKER.generate_graph_dataset(profile_id,holding_ids,parameters,period)
        _update_graph_job(job_id,status='completed',stage='Ready',progress=100,message='Graph data ready.',result=result,error=None)
    except Exception as exc:
        _LOGGER.exception('Graph job failed')
        _update_graph_job(job_id,status='failed',stage='Failed',progress=100,message='Unable to prepare graph data.',error=str(exc),result=None)


def _new_sip_preview_job(data):
    job_id = uuid.uuid4().hex
    job = {'id': job_id, 'status': 'queued', 'stage': 'Queued', 'progress': 0, 'message': 'Waiting to start…', 'sub': '', 'result': None, 'error': None, 'created_at': iso_now(), 'updated_at': iso_now()}
    with SIP_PREVIEW_JOBS_LOCK:
        SIP_PREVIEW_JOBS[job_id] = job
    threading.Thread(target=_run_sip_preview_job, args=(job_id, data), daemon=True).start()
    return job

def _update_sip_preview_job(job_id, **updates):
    with SIP_PREVIEW_JOBS_LOCK:
        job = SIP_PREVIEW_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job['updated_at'] = iso_now()

def _prepare_sip_preview(data, progress_cb=None):
    scheme_code = str(data.get('scheme_code') or '').strip()
    name = str(data.get('scheme_name') or '').strip()
    if not scheme_code or not name:
        raise ValueError('Fund scheme is required')
    profile_id = int(data.get('profile_id') or 0)
    if not TRACKER.db.get_profile(profile_id):
        raise ValueError('Investor profile not found')
    if TRACKER.db.get_fund_by_code(scheme_code, profile_id):
        raise ValueError('Fund is already in this investor profile')
    sip_amount = parse_money(data.get('sip_amount'))
    if sip_amount <= 0:
        raise ValueError('SIP amount must be greater than zero')
    sip_day = int(data.get('sip_day') or 0)
    if not 1 <= sip_day <= 31:
        raise ValueError('SIP day must be between 1 and 31')
    first_sip = parse_optional_date(data.get('first_sip_date'))
    if not first_sip:
        raise ValueError('First SIP date is required')
    sip_count = int(data.get('sip_count') or 0)
    if not 1 <= sip_count <= 240:
        raise ValueError('Number of SIPs already made must be between 1 and 240')
    current_units = parse_float(data.get('units', 0))
    current_invested = parse_money(data.get('invested', 0))
    if current_units < 0:
        raise ValueError('Current units held cannot be negative')
    if current_invested < 0:
        raise ValueError('Total amount invested cannot be negative')
    if progress_cb:
        progress_cb('Validating SIP setup', 8, 'Checking SIP dates and investor details.', '')
    history = TRACKER.historical_sip_transactions(scheme_code, sip_amount, sip_day, first_sip, sip_count)
    if progress_cb:
        progress_cb('Calculating SIP units', 76, f'Calculated {len(history)} historical SIP transactions.', '')
    reconstructed_units = sum(float(t['units']) for t in history)
    expected_invested = sip_amount * sip_count
    adjustment_units = current_units - reconstructed_units
    preview_id = uuid.uuid4().hex
    preview = {
        'preview_id': preview_id,
        'profile_id': profile_id,
        'scheme_code': scheme_code,
        'scheme_name': name,
        'acquisition_type': 'sip',
        'sip_amount': sip_amount,
        'sip_day': sip_day,
        'first_sip_date': first_sip.isoformat(),
        'sip_count': sip_count,
        'current_units': current_units,
        'current_invested': current_invested,
        'expected_invested': expected_invested,
        'reconstructed_units': reconstructed_units,
        'adjustment_units': adjustment_units,
        'history': history,
        'initial_date': first_sip.isoformat(),
        'sip_enabled': True,
    }
    with SIP_PREVIEWS_LOCK:
        SIP_PREVIEWS[preview_id] = {'data': preview, 'created_at': time.time()}
    return preview

def _run_sip_preview_job(job_id, data):
    try:
        _update_sip_preview_job(job_id, status='running', stage='Fetching NAV history', progress=12, message='Fetching NAV history for the SIP dates…', sub='The add-on will calculate the units for each historical SIP.')
        def progress(stage, pct, message, sub=''):
            _update_sip_preview_job(job_id, stage=stage, progress=max(0, min(100, int(pct))), message=message, sub=sub)
        preview = _prepare_sip_preview(data, progress_cb=progress)
        total_invested = float(preview['current_invested'] if preview['current_invested'] > 0 else preview['expected_invested'])
        preview_result = {
            'preview_id': preview['preview_id'],
            'scheme_code': preview['scheme_code'],
            'scheme_name': preview['scheme_name'],
            'sip_amount': preview['sip_amount'],
            'sip_day': preview['sip_day'],
            'first_sip_date': preview['first_sip_date'],
            'sip_count': preview['sip_count'],
            'current_units': preview['current_units'],
            'current_invested': preview['current_invested'],
            'expected_invested': preview['expected_invested'],
            'total_invested': total_invested,
            'reconstructed_units': preview['reconstructed_units'],
            'adjustment_units': preview['adjustment_units'],
            'first_transaction_date': preview['history'][0]['txn_date'] if preview['history'] else None,
            'last_transaction_date': preview['history'][-1]['txn_date'] if preview['history'] else None,
        }
        _update_sip_preview_job(job_id, status='completed', stage='Preview ready', progress=100, message='NAV lookup complete. Review the SIP preview before adding the fund.', sub='The historical SIP transactions are ready for confirmation.', result=preview_result)
    except Exception as exc:
        text = str(exc) or exc.__class__.__name__
        _update_sip_preview_job(job_id, status='error', stage='Failed', progress=100, message=text, error=text)

def _new_add_fund_job(data):
    job_id = uuid.uuid4().hex
    job = {'id': job_id, 'status': 'queued', 'stage': 'Queued', 'progress': 0, 'message': 'Waiting to start…', 'sub': '', 'result': None, 'error': None, 'added': False, 'created_at': iso_now(), 'updated_at': iso_now()}
    with ADD_FUND_JOBS_LOCK:
        ADD_FUND_JOBS[job_id] = job
    threading.Thread(target=_run_add_fund_job, args=(job_id, data), daemon=True).start()
    return job

def _update_add_fund_job(job_id, **updates):
    with ADD_FUND_JOBS_LOCK:
        job = ADD_FUND_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job['updated_at'] = iso_now()

def _prepare_lumpsum_payload(data, require_nav=True):
    scheme_code = str(data.get('scheme_code') or '').strip()
    name = str(data.get('scheme_name') or '').strip()
    if not scheme_code or not name:
        raise ValueError('Fund scheme is required')
    profile_id = int(data.get('profile_id') or 0)
    if not TRACKER.db.get_profile(profile_id):
        raise ValueError('Investor profile not found')
    if TRACKER.db.get_fund_by_code(scheme_code, profile_id):
        raise ValueError('Fund is already in this investor profile')
    lumps = data.get('lumpsum_transactions') or []
    if not lumps:
        raise ValueError('Add at least one historical lumpsum transaction')
    history = []
    for idx, item in enumerate(lumps, 1):
        td = str(item.get('nav_date') or item.get('date') or '').strip()
        if not td:
            raise ValueError(f'Lumpsum {idx}: NAV date is required')
        parse_optional_date(td)
        amount_present = item.get('amount') not in (None, '')
        units_present = item.get('units') not in (None, '')
        if not amount_present and not units_present:
            raise ValueError(f'Lumpsum {idx}: enter either amount or units')
        amount = parse_money(item.get('amount')) if amount_present else None
        units = parse_float(item.get('units')) if units_present else None
        if amount is not None and amount <= 0:
            raise ValueError(f'Lumpsum {idx}: amount must be greater than zero')
        if units is not None and units <= 0:
            raise ValueError(f'Lumpsum {idx}: units must be greater than zero')
        nav = float(item['nav']) if item.get('nav') not in (None, '') else None
        if require_nav and nav is None:
            raise ValueError(f'Lumpsum {idx}: NAV must be confirmed before recording')
        if nav is not None and nav <= 0:
            raise ValueError(f'Lumpsum {idx}: NAV must be greater than zero')
        if amount is not None and units is not None and nav is not None:
            expected = units * nav
            if abs(expected - amount) > max(0.02, abs(amount) * 0.001):
                raise ValueError(f'Lumpsum {idx}: amount and units do not match NAV {nav:.6f}; expected amount about ₹{expected:.2f}')
        if amount is None:
            amount = units * nav
        if units is None:
            units = amount / nav
        history.append({'txn_type':'lumpsum','txn_date':td,'amount':-float(amount),'units':float(units),'nav':nav,'note':'Historical lumpsum'})
    total_amount = sum(abs(float(t['amount'])) for t in history)
    total_units = sum(float(t['units']) for t in history)
    initial_date = min(t['txn_date'] for t in history)
    payload = {
        'profile_id': profile_id, 'scheme_code': scheme_code, 'scheme_name': name,
        'units': total_units, 'invested': total_amount, 'sip_enabled': False,
        'sip_amount': 0, 'sip_day': None, 'initial_date': initial_date,
    }
    return payload, history

def _run_add_fund_job(job_id, data):
    try:
        acquisition = str(data.get('acquisition_type') or 'lumpsum').lower().strip()
        if acquisition == 'sip':
            preview_id = str(data.get('preview_id') or '').strip()
            with SIP_PREVIEWS_LOCK:
                stored = dict(SIP_PREVIEWS.get(preview_id) or {})
            preview = stored.get('data')
            if not preview:
                raise ValueError('SIP preview has expired. Start the preview again.')
            payload = {
                'profile_id': preview['profile_id'], 'scheme_code': preview['scheme_code'], 'scheme_name': preview['scheme_name'],
                'units': preview['current_units'], 'invested': preview['current_invested'] if preview['current_invested'] > 0 else preview['expected_invested'],
                'sip_amount': preview['sip_amount'], 'sip_enabled': True, 'sip_day': preview['sip_day'], 'initial_date': preview['initial_date'],
            }
            history = list(preview['history'])
            _update_add_fund_job(job_id, status='running', stage='Saving fund', progress=18, message='Adding the mutual fund and historical SIP transactions…', sub=f"Saving {len(history)} historical SIP transactions.")
        else:
            _update_add_fund_job(job_id, status='running', stage='Validating', progress=5, message='Checking the confirmed lumpsum values…')
            payload, history = _prepare_lumpsum_payload(data, require_nav=True)
            _update_add_fund_job(job_id, stage='Saving fund', progress=18, message='Adding the mutual fund and historical transactions…', sub=f'Saving {len(history)} historical lumpsum transactions.')

        fid = TRACKER.db.seed_fund(payload, history)
        if acquisition == 'sip':
            reconstructed = sum(float(t['units']) for t in history)
            delta_units = float(payload.get('units') or 0) - reconstructed
            if abs(delta_units) > 0.000001:
                TRACKER.db.add_transaction(fid, 'adjustment', payload.get('initial_date') or history[-1]['txn_date'], 0.0, delta_units, None, 'Opening unit adjustment to match current holding')
            first_sip = parse_optional_date(payload.get('initial_date'))
            sip_count = len(history)
            if first_sip and sip_count:
                last_y = first_sip.year + (first_sip.month - 1 + sip_count - 1) // 12
                last_m = (first_sip.month - 1 + sip_count - 1) % 12 + 1
                last_cycle = f'{last_y:04d}-{last_m:02d}'
                TRACKER.db.set_sip_executed(fid, last_cycle, history[-1]['txn_date'])

        _update_add_fund_job(job_id, stage='Fund added', progress=28, message='Fund added successfully.', sub='Updating the table before fetching the new fund’s current NAV.', added=True, result={'id': fid})

        # Add Mutual Fund deliberately refreshes only the newly added holding.
        # The normal manual/automatic full NAV refresh remains unchanged.  If a full
        # refresh is already running, wait for it to finish to avoid concurrent writes
        # to the same SQLite-backed holding; then refresh just this new fund.
        while get_refresh_state().get('status') == 'running':
            _update_add_fund_job(job_id, stage='Waiting for refresh', progress=32, message='Waiting for the current NAV refresh to finish…', sub='Avoiding a concurrent NAV write before updating the new fund.')
            time.sleep(0.4)

        new_fund = TRACKER.db.get_fund(fid)
        if not new_fund:
            raise RuntimeError('The fund was added, but the new holding could not be read back from the database.')

        _update_add_fund_job(job_id, stage='Refreshing new fund NAV', progress=40, message='Fetching the latest NAV for the new fund…', sub=f'Updating {new_fund["scheme_name"]} only.')
        refreshed_entries = TRACKER.refresh_fund(new_fund)
        if refreshed_entries is None:
            updated_fund = TRACKER.db.get_fund(fid)
            error = updated_fund['last_error'] if updated_fund else None
            raise RuntimeError('The fund was added, but its NAV refresh failed: ' + str(error or 'Unknown NAV error'))

        _update_add_fund_job(job_id, stage='Updating portfolio', progress=82, message='Recalculating the portfolio…', sub='Updating Home Assistant and the displayed totals using the new fund’s current NAV.')
        portfolio = build_portfolio(TRACKER.db)
        TRACKER.update_ha_states(portfolio)
        # update_ha_states intentionally preserves its existing Home Assistant behavior;
        # explicitly writing once more here makes the add-job completion dependent on a
        # successful integration-state export rather than silently accepting stale state.
        TRACKER._write_integration_state()

        _update_add_fund_job(job_id, status='completed', stage='Completed', progress=100, message='Fund added and current NAV updated successfully.', sub='Portfolio table, totals and Home Assistant state are up to date.', result={'id': fid})
        if acquisition == 'sip':
            with SIP_PREVIEWS_LOCK:
                SIP_PREVIEWS.pop(preview_id, None)
    except Exception as exc:
        text = str(exc) or exc.__class__.__name__
        _update_add_fund_job(job_id, status='error', stage='Failed', progress=100, message=text, error=text)



def _new_import_job(data):
    job_id = uuid.uuid4().hex
    job = {'id': job_id, 'status': 'queued', 'stage': 'Queued', 'progress': 0, 'message': 'Waiting to start…', 'result': None, 'error': None, 'created_at': iso_now(), 'updated_at': iso_now()}
    with IMPORT_JOBS_LOCK:
        IMPORT_JOBS[job_id] = job
    threading.Thread(target=_run_import_job, args=(job_id, data), daemon=True).start()
    return job

def _update_import_job(job_id, **updates):
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job['updated_at'] = iso_now()

def _run_import_job(job_id, data):
    try:
        _update_import_job(job_id, status='running', stage='Analysing JSON', progress=3, message='Reading and validating the imported portfolio…')
        def progress(stage, progress, message):
            _update_import_job(job_id, stage=stage, progress=max(0, min(100, int(progress))), message=message)

        if not data.get('confirm_changes'):
            raise ValueError('Import changes must be reviewed and explicitly confirmed before saving.')
        preview = TRACKER.validate_import_document(data, progress_callback=progress)
        if preview.get('template'):
            raise ValueError('This is a blank/example template. Fill it with fund data before importing.')
        if preview.get('errors'):
            raise ValueError(preview['errors'][0])
        progress('Saving portfolio', 84, f"Saving {preview['counts']['funds']} funds and {preview['counts']['transactions']} transactions…")
        result = TRACKER.import_validated_preview(preview, profile_mapping=data.get('profile_mapping') or {}, sip_status_mapping=data.get('sip_status_mapping') or {}, fund_decisions=data.get('fund_decisions') or {}, sip_reconciliation_mapping=data.get('sip_reconciliation_mapping') or {}, progress_callback=progress)
        progress('Executing SIP', 91, "Portfolio saved. Checking today's SIP transactions before execution…")
        sip_result = TRACKER.execute_post_import_sips(result.get('affected_sip_holds') or [], sip_reconciliation_mapping=data.get('sip_reconciliation_mapping') or {}, sip_status_mapping=data.get('sip_status_mapping') or {}, progress_callback=progress)
        result['sip_execution'] = sip_result
        progress('Updating Home Assistant', 97, 'Updating Home Assistant sensors from the imported portfolio…')
        TRACKER.update_ha_states(build_portfolio(TRACKER.db))
        TRACKER.db.request_integration_reload()
        TRACKER._write_integration_state()
        progress('Reloading Home Assistant', 98, 'Requesting a refresh of Mutual Fund Tracker entities…')
        progress('Finalising', 99, 'Rebuilding the completed portfolio table…')
        time.sleep(0.05)
        _update_import_job(job_id, status='completed', stage='Completed', progress=100, message=f"Imported {len(result.get('imported', []))} funds.", result=result)
    except Exception as exc:
        _update_import_job(job_id, status='error', stage='Failed', progress=100, message=str(exc), error=str(exc))

IMPORT_SCHEMA = {
    'format_version': IMPORT_FORMAT_VERSION,
    'description': 'Compact Mutual Fund Tracker portfolio import. Includes investor identity plus compressed recurring SIP blocks and exact irregular transactions.',
    'investor_fields': {
        'ref': 'Stable JSON-local identifier such as investor_1',
        'name': 'Full investor name from the statement',
        'emails': ['All email IDs associated with the statement', 'Use an array so multiple email IDs can later be consolidated to one profile'],
        'phone': 'Phone/mobile if shown',
        'pan': 'PAN if shown',
        'address': {'line1':'', 'line2':'', 'city':'', 'state':'', 'postal_code':'', 'country':''}
    },
    'fund_fields': {
        'investor_ref': 'References the investor in the investors array',
        'scheme_code': 'AMFI/MFAPI scheme code if known; optional and will be verified/corrected from ISIN and scheme name',
        'scheme_name': 'Canonical scheme name (required)',
        'isin': 'ISIN, if available',
        'folios': ['Folio numbers associated with this scheme'],
        'sip': {'enabled': True, 'amount': 2500, 'day': 20},
        'sip_status_note': 'If no SIP transaction appears in the previous completed month of the statement, the add-on will ask the user to confirm whether the SIP is still active.',
        'closing_balance': {'valuation_date': 'YYYY-MM-DD', 'units': 0, 'cost_value': 0, 'nav': 0, 'market_value': 0},
        'history': {
            'sip_blocks': [{'amount':2500,'charges':0.12,'count':36,'start_date':'YYYY-MM-DD','end_date':'YYYY-MM-DD','sip_day':20,'folio':'123'}],
            'transactions': [{'date':'YYYY-MM-DD','type':'lumpsum|buy|sell|switch_in|switch_out|adjustment|fee|sip','amount':0,'units':0,'nav':0,'charges':0,'cashflow':0,'folio':'','note':''}]
        }
    }
}


IMPORT_PROMPT = r"""
Convert the attached CAMS/KFintech Consolidated Account Statement into the COMPACT Mutual Fund Tracker JSON format below.

The goal is to keep the JSON VERY SMALL. Do NOT output one JSON transaction object for every monthly SIP. Instead compress continuous monthly SIPs into sip_blocks.

IMPORTANT RULES:
1. Return the complete JSON only inside ONE fenced Markdown code block labeled `json`. Do not add explanations, commentary, or any text before or after the code block. The code block is the PRIMARY and REQUIRED deliverable. The JSON inside the code block must be valid JSON only, with no Markdown or prose inside it. Use the code block's download/save control to make the JSON downloadable; do not rely on a separate file attachment.
2. Extract investor identity from the consolidated statement cover page. Put one or more investors in a top-level investors array. If the statement has one investor, use one object with ref="investor_1". If names/contact details differ and the statement clearly represents multiple investors, create separate investor objects.
3. Preserve full investor name, all email IDs visible on the statement, phone/mobile, PAN when shown, and postal address. Do not invent missing details.
4. One fund object per scheme_code per investor_ref. If the same scheme has multiple folios, consolidate them into one fund object for that investor and list all folios.
5. Preserve the statement's scheme name, ISIN, folios, closing unit balance, Total Cost Value, valuation date, NAV and market value.
6. For recurring monthly SIP series, use history.sip_blocks. Each block represents ONE SIP per month, for count consecutive months, from start_date to end_date.
7. A sip_block MUST be split whenever the SIP amount changes, charges change materially, SIP schedule day changes, or another condition makes the series non-contiguous.
8. Be VERY CAREFUL about SIP amount changes mid-scheme. For example, if a fund was ₹4,999.75 per month and later became ₹2,499.88, output TWO sip_blocks, not one block with an average amount.
9. If the statement shows a skipped, rejected, cancelled, duplicate, extra or otherwise irregular SIP, do not hide that inside a normal block. Break the block around it and put the irregular transaction in history.transactions.
10. If there are two SIP purchases in the same month, do not represent them as one monthly block. Use exact history.transactions for the exceptional month(s).
11. sip_day is the SCHEDULED SIP day, not necessarily the actual transaction date. Infer it from the recurring pattern in the statement. If a transaction is on the next working day because the scheduled day was a holiday, keep sip_day as the scheduled day and use the actual recurring transaction start/end dates for validation.
12. start_date and end_date in a sip_block are the FIRST and LAST ACTUAL TRANSACTION DATES shown in the statement for that block. count is the number of SIP transactions in that block.
13. The add-on will reconstruct each SIP's actual NAV/allotment date by finding the first available NAV on or after the scheduled SIP date. Therefore, do not fabricate per-transaction NAVs for compressed SIP blocks.
14. For a SIP block, amount is the principal invested in each transaction, excluding stamp duty/charges. charges is the per-transaction charge if it is constant throughout that block. If the charge changes, split the block.
15. For irregular purchases/lumpsums, redemptions, switch-ins, switch-outs, fees, rejected/cancelled items and other non-recurring financial rows, use exact history.transactions rows.
16. For purchases/SIPs, exact transaction amount should be principal; charges go in charges; cashflow is the actual investor cash outflow and is normally -(amount + charges).
17. For redemptions/sells, use positive amount, negative units, and positive cashflow equal to proceeds net of charges/TDS only when explicitly available.
18. Switch-in and switch-out are non-cash portfolio movements: cashflow 0; switch-in positive units; switch-out negative units.
19. Rejected/cancelled transactions should not double-count an investment. Use an exact adjustment/reversal only when needed to reproduce the statement's unit balance.
20. Ignore purely administrative rows such as address updates, nominee updates, KYC updates and similar non-financial events.
21. Put the current SIP configuration in the fund's sip object using the latest active SIP amount and scheduled day. If the SIP is no longer active, set enabled to false.
22. Preserve folio numbers at both fund and transaction/block level.
23. scheme_code is OPTIONAL input metadata. NEVER guess it. The add-on will verify/correct the code from ISIN and scheme name against AMFI. Prefer supplying the ISIN and exact scheme name; if you are not certain of the scheme_code, set it to an empty string. Put unresolved identification issues in unresolved.
24. closing_balance.cost_value comes from Total Cost Value in the statement. Do not replace it with an arithmetic sum if the statement gives a closing value.
25. closing_balance.units comes from Closing Unit Balance.
26. Do not use today's NAV in place of the statement's valuation NAV.
27. The add-on expands sip_blocks back into individual transactions before calculating XIRR.
28. statement_generated_date is required in source and should be the actual date the consolidated statement was generated. The add-on only accepts statements generated within the last 10 days.
29. The add-on may ask the user to reconcile a SIP that falls after statement_to and before the latest available NAV. Preserve the SIP schedule day and current SIP amount so that the add-on can determine the applicable NAV and ask Executed/Skipped when necessary.
30. If the statement's final coverage date is itself the latest NAV date for a scheduled SIP, the add-on may automatically reconcile that current SIP during import; do not fabricate an extra historical transaction for earlier missing dates.
31. If the statement is consolidated using an email address and the cover page shows that email, include it in investors[].emails. If multiple statement emails are being merged for the same person, list all of them in that one investor's emails array when the source provides them.

COMPACT JSON FORMAT:
{
  "format_version": 2,
  "source": {"type":"CAMS_CONSOLIDATED_STATEMENT","statement_from":"YYYY-MM-DD","statement_to":"YYYY-MM-DD","statement_generated_date":"YYYY-MM-DD"},
  "investors": [
    {"ref":"investor_1","name":"Full name","emails":["email@example.com"],"phone":"+91...","pan":"...","address":{"line1":"","line2":"","city":"","state":"","postal_code":"","country":"India"}}
  ],
  "unresolved": [],
  "funds": [
    {
      "investor_ref": "investor_1",
      "scheme_code": "",
      "scheme_name": "Canonical scheme name",
      "isin": "INF...",
      "folios": ["folio1"],
      "sip": {"enabled": true, "amount": 2500, "day": 20},
      "closing_balance": {"valuation_date":"2026-08-12","units":1000.123,"cost_value":125000,"nav":125.0,"market_value":130000},
      "history": {
        "sip_blocks": [
          {"amount":4999.75,"charges":0.25,"count":34,"start_date":"2021-03-10","end_date":"2023-12-11","sip_day":10,"folio":"folio1"},
          {"amount":2499.88,"charges":0.12,"count":30,"start_date":"2024-01-10","end_date":"2026-06-10","sip_day":10,"folio":"folio1"}
        ],
        "transactions": [
          {"date":"2026-02-05","type":"lumpsum","amount":50000,"units":350.123,"nav":142.80,"charges":0,"cashflow":-50000,"folio":"folio1","note":"Lumpsum purchase"}
        ]
      }
    }
  ]
}

Before returning, verify:
- JSON is valid.
- No scheme is duplicated.
- Every continuous SIP amount period is represented by its own block.
- SIP amount changes are split into separate blocks.
- SIP day changes are split into separate blocks.
- Charge/stamp-duty changes that materially affect cashflow are split into separate blocks.
- Skipped/rejected/cancelled/duplicate monthly transactions are represented by block boundaries and/or exact transactions.
- count matches the number of SIP transactions in the block.
- start_date and end_date are the actual first/last transaction dates in the statement for that block.
- If scheme_code is provided, it is only a hint; the importer will verify/correct it using AMFI + ISIN/name.
- ISIN and scheme_name must identify the intended Direct/Growth variant exactly.
- closing units/cost value match the statement.
"""

TRACKER = Tracker()


def json_response(handler, obj, status=200):
    body = json.dumps(obj, separators=(',', ':'), ensure_ascii=False, default=lambda v: v.isoformat() if isinstance(v, (date, datetime)) else str(v)).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get('Content-Length', '0'))
    raw = handler.rfile.read(length) if length else b'{}'
    return json.loads(raw.decode('utf-8'))


class Handler(BaseHTTPRequestHandler):
    server_version = 'MFT/1.0.0'

    def log_message(self, fmt, *args):
        print(f'[{iso_now()}] {fmt % args}', flush=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        try:
            if path == '/api/profiles':
                return json_response(self, [TRACKER.db.profile_dict(r) for r in TRACKER.db.all_profiles()])
            if path == '/api/settings':
                return json_response(self, {
                    'refresh_interval_seconds': TRACKER.db.get_refresh_interval(),
                    'nifty_poll_interval_seconds': TRACKER.db.get_nifty_poll_interval(),
                    'last_nav_refresh': TRACKER.db.last_refresh_time(),
                    'nav_date': (build_portfolio(TRACKER.db, None).get('nav_date')),
                })
            if path == '/api/refresh/status':
                return json_response(self, get_refresh_state())
            if path == '/api/portfolio':
                raw = parse_qs(parsed.query).get('profile_id', [''])[0]
                profile_id = None if raw in ('','all','0') else int(raw)
                portfolio = build_portfolio(TRACKER.db, profile_id)
                sip_summary = TRACKER._sip_summary(profile_id)
                portfolio.update(sip_summary)
                executed_hids = {int(d.get('holding_id')) for d in sip_summary.get('executed_sip_details', []) if d.get('holding_id') is not None}
                for row in portfolio.get('rows', []):
                    hid = int(row.get('id') or 0)
                    if row.get('sip_enabled'):
                        row['sip_execution_status'] = 'executed' if hid in executed_hids else 'not_executed'
                    else:
                        row['sip_execution_status'] = 'not_applicable'
                return json_response(self, portfolio)
            if path == '/api/funds':
                raw = parse_qs(parsed.query).get('profile_id', [''])[0]
                profile_id = None if raw in ('','all','0') else int(raw)
                return json_response(self, build_portfolio(TRACKER.db, profile_id)['rows'])
            if path == '/api/search':
                q = parse_qs(parsed.query).get('q', [''])[0]
                mode = parse_qs(parsed.query).get('mode', ['name'])[0]
                return json_response(self, TRACKER.search(q, mode))
            if path.startswith('/api/funds/') and path.endswith('/transactions'):
                fid = int(path.split('/')[3])
                return json_response(self, [dict(r) for r in TRACKER.db.transactions(fid) if not (str(r['txn_type'] or '') == 'sip' and abs(float(r['amount'] or 0)) <= 1e-12 and abs(float(r['units'] or 0)) <= 1e-12 and abs(float(r['cashflow'] or 0)) <= 1e-12)])
            if path == '/api/import/schema':
                return json_response(self, IMPORT_SCHEMA)
            if path == '/api/import/prompt':
                self.send_response(200)
                body = IMPORT_PROMPT.encode('utf-8')
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith('/api/import/status/'):
                job_id = path.rsplit('/', 1)[-1]
                with IMPORT_JOBS_LOCK:
                    job = dict(IMPORT_JOBS.get(job_id) or {})
                if not job:
                    return json_response(self, {'error': 'Import job not found'}, 404)
                return json_response(self, job)
            if path.startswith('/api/funds/sip/preview/status/'):
                job_id = path.rsplit('/', 1)[-1]
                with SIP_PREVIEW_JOBS_LOCK:
                    job = dict(SIP_PREVIEW_JOBS.get(job_id) or {})
                if not job:
                    return json_response(self, {'error': 'SIP preview job not found'}, 404)
                return json_response(self, job)
            if path.startswith('/api/funds/sip/status/'):
                job_id = path.rsplit('/', 1)[-1]
                with ADD_FUND_JOBS_LOCK:
                    job = dict(ADD_FUND_JOBS.get(job_id) or {})
                if not job:
                    return json_response(self, {'error': 'SIP add job not found'}, 404)
                return json_response(self, job)
            if path.startswith('/api/funds/lumpsum/status/'):
                job_id = path.rsplit('/', 1)[-1]
                with ADD_FUND_JOBS_LOCK:
                    job = dict(ADD_FUND_JOBS.get(job_id) or {})
                if not job:
                    return json_response(self, {'error': 'Lumpsum add job not found'}, 404)
                return json_response(self, job)
            if path.startswith('/api/graphs/status/'):
                job_id = path.rsplit('/', 1)[-1]
                with GRAPH_JOBS_LOCK:
                    job = dict(GRAPH_JOBS.get(job_id) or {})
                if not job:
                    return json_response(self, {'error': 'Graph job not found'}, 404)
                return json_response(self, job)
            if path == '/api/health':
                return json_response(self, {'ok': True, 'time': iso_now()})
            return self.serve_file(path)
        except ValueError as exc:
            return json_response(self, {'error': str(exc)}, 400)
        except Exception as exc:
            return json_response(self, {'error': str(exc)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        try:
            if path == '/api/profiles':
                data = read_json(self)
                profile = normalize_profile(data)
                pid = TRACKER.db.create_profile(profile)
                # Refresh immediately so newly added profiles are reflected consistently.
                TRACKER.refresh_all_if_stale()
                return json_response(self, {'id': pid, 'profile': TRACKER.db.profile_dict(TRACKER.db.get_profile(pid))}, 201)
            if path == '/api/graphs/start':
                data = read_json(self)
                if not isinstance(data, dict): raise ValueError('Graph request must be an object')
                job = _start_graph_job(data)
                return json_response(self, {'job_id': job['id'], 'status': job['status']}, 202)
            if path == '/api/import/start':
                data = read_json(self)
                # Quick structural check so malformed JSON is rejected immediately.
                if not isinstance(data, dict):
                    raise ValueError('Import JSON must be an object')
                job = _new_import_job(data)
                return json_response(self, {'job_id': job['id'], 'status': job['status']}, 202)
            if path == '/api/import/changes':
                data = read_json(self)
                preview = TRACKER.validate_import_document(data)
                if preview.get('errors'):
                    return json_response(self, {'errors': preview['errors'], 'changes': []})
                changes = TRACKER.db.preview_import_changes(preview, profile_mapping=data.get('profile_mapping') or {}, sip_status_mapping=data.get('sip_status_mapping') or {})
                return json_response(self, {'changes': changes})
            if path == '/api/import/validate':
                data = read_json(self)
                return json_response(self, TRACKER.validate_import_document(data))
            if path == '/api/import':
                data = read_json(self)
                preview = TRACKER.validate_import_document(data)
                if preview.get('template'):
                    raise ValueError('This is a blank/example template. Fill it with fund data before importing.')
                if preview.get('errors'):
                    raise ValueError(preview['errors'][0])
                result = TRACKER.import_document(data, profile_mapping=data.get('profile_mapping') or {}, sip_status_mapping=data.get('sip_status_mapping') or {}, fund_decisions=data.get('fund_decisions') or {}, sip_reconciliation_mapping=data.get('sip_reconciliation_mapping') or {})
                TRACKER.refresh_all_if_stale()
                return json_response(self, result, 201)
            if path == '/api/funds/sip/preview/start':
                data = read_json(self)
                if str(data.get('scheme_code') or '').strip() == '':
                    raise ValueError('Fund scheme is required')
                job = _new_sip_preview_job(data)
                return json_response(self, {'job_id': job['id'], 'status': job['status']}, 202)
            if path == '/api/funds/sip/start':
                data = read_json(self)
                preview_id = str(data.get('preview_id') or '').strip()
                with SIP_PREVIEWS_LOCK:
                    stored = dict(SIP_PREVIEWS.get(preview_id) or {})
                if not stored.get('data'):
                    raise ValueError('SIP preview has expired. Start the preview again.')
                job = _new_add_fund_job({'acquisition_type':'sip', 'preview_id':preview_id})
                return json_response(self, {'job_id': job['id'], 'status': job['status']}, 202)
            if path == '/api/funds/lumpsum/start':
                data = read_json(self)
                # Validate synchronously before creating the background job so the UI
                # gets immediate feedback for malformed or conflicting confirmation data.
                _prepare_lumpsum_payload(data, require_nav=True)
                job = _new_add_fund_job(data)
                return json_response(self, {'job_id': job['id'], 'status': job['status']}, 202)
            if path == '/api/funds/lumpsum-quote':
                data = read_json(self)
                scheme_code = str(data.get('scheme_code') or '').strip()
                if not scheme_code:
                    raise ValueError('Fund scheme is required')
                target = parse_optional_date(str(data.get('nav_date') or '').strip())
                if not target:
                    raise ValueError('NAV date is required')
                amount_raw = data.get('amount')
                units_raw = data.get('units')
                has_amount = amount_raw not in (None, '')
                has_units = units_raw not in (None, '')
                if has_amount == has_units:
                    raise ValueError('Enter either amount or units, not both')
                amount = parse_money(amount_raw) if has_amount else None
                units = parse_float(units_raw) if has_units else None
                if amount is not None and amount <= 0:
                    raise ValueError('Amount must be greater than zero')
                if units is not None and units <= 0:
                    raise ValueError('Units must be greater than zero')
                nav, actual = TRACKER.nav_on_date(scheme_code, target)
                if amount is not None:
                    units = amount / nav
                else:
                    amount = units * nav
                return json_response(self, {'nav_date': actual.isoformat(), 'nav': nav, 'amount': amount, 'units': units})
            if path == '/api/funds':
                data = read_json(self)
                scheme_code = str(data['scheme_code']).strip()
                name = str(data['scheme_name']).strip()
                acquisition = str(data.get('acquisition_type') or '').lower().strip()
                if acquisition not in ('lumpsum', 'sip'):
                    raise ValueError('Choose whether the existing holding was built by Lumpsum or SIP')
                units = parse_float(data.get('units', 0))
                invested = parse_money(data.get('invested', 0))
                sip_amount = parse_money(data.get('sip_amount', 0))
                sip_enabled = bool(data.get('sip_enabled', False))
                sip_day = int(data['sip_day']) if sip_enabled and data.get('sip_day') not in (None, '') else None
                if sip_enabled and not (1 <= sip_day <= 31):
                    raise ValueError('SIP day must be between 1 and 31')
                initial_date = data.get('initial_date') or None
                if initial_date:
                    parse_optional_date(initial_date)
                profile_id = int(data.get('profile_id') or 1)
                if not TRACKER.db.get_profile(profile_id):
                    raise ValueError('Investor profile not found')
                if TRACKER.db.get_fund_by_code(scheme_code, profile_id):
                    raise ValueError('Fund is already in this investor profile')
                history = []
                if acquisition == 'lumpsum':
                    lumps = data.get('lumpsum_transactions') or []
                    if not lumps:
                        raise ValueError('Add at least one historical lumpsum transaction')
                    total_lump_amount = 0.0
                    for item in lumps:
                        td = str(item.get('date') or '').strip()
                        if not td:
                            raise ValueError('Each lumpsum entry needs an investment date')
                        parse_optional_date(td)
                        amt = parse_money(item.get('amount', 0))
                        u = parse_float(item.get('units', 0))
                        nav = float(item['nav']) if item.get('nav') not in (None, '') else None
                        if amt <= 0 or u <= 0:
                            raise ValueError('Each lumpsum entry needs a positive amount and units')
                        history.append({'txn_type':'lumpsum','txn_date':td,'amount':-amt,'units':u,'nav':nav,'note':'Historical lumpsum'})
                        total_lump_amount += amt
                    if invested <= 0:
                        invested = total_lump_amount
                else:
                    if sip_amount <= 0:
                        raise ValueError('SIP amount must be greater than zero')
                    if not (1 <= (sip_day or 0) <= 31):
                        raise ValueError('SIP day must be between 1 and 31')
                    first_sip = parse_optional_date(data.get('first_sip_date'))
                    if not first_sip:
                        raise ValueError('First SIP date is required')
                    sip_count = int(data.get('sip_count') or 0)
                    if sip_count < 1:
                        raise ValueError('Number of SIPs already made must be at least 1')
                    history = TRACKER.historical_sip_transactions(scheme_code, sip_amount, sip_day, first_sip, sip_count)
                    if invested <= 0:
                        invested = sip_amount * sip_count
                    initial_date = first_sip.isoformat()
                fid = TRACKER.db.seed_fund({
                    'profile_id': profile_id, 'scheme_code': scheme_code, 'scheme_name': name, 'units': units,
                    'invested': invested, 'sip_amount': sip_amount, 'sip_enabled': sip_enabled,
                    'sip_day': sip_day, 'initial_date': initial_date
                }, history)
                # For SIP imports, reconcile the user's current units with reconstructed historical units
                if acquisition == 'sip':
                    reconstructed = sum(float(t['units']) for t in history)
                    delta_units = units - reconstructed
                    if abs(delta_units) > 0.000001:
                        TRACKER.db.add_transaction(fid, 'adjustment', initial_date or TRACKER.local_today().isoformat(), 0.0, delta_units, None, 'Opening unit adjustment to match current holding')
                    # The user said the completed SIP count is already up to date, so
                    # do not immediately execute the current month's SIP again.
                    first_sip = parse_optional_date(data.get('first_sip_date'))
                    sip_count = int(data.get('sip_count') or 0)
                    if first_sip and sip_count:
                        last_y = first_sip.year + (first_sip.month - 1 + sip_count - 1) // 12
                        last_m = (first_sip.month - 1 + sip_count - 1) % 12 + 1
                        last_cycle = f'{last_y:04d}-{last_m:02d}'
                        TRACKER.db.set_sip_executed(fid, last_cycle, history[-1]['txn_date'])
                # Refresh immediately so the new fund has NAVs before the table reloads.
                TRACKER.refresh_all_if_stale()
                return json_response(self, {'id': fid}, 201)
            if path == '/api/settings':
                data = read_json(self)
                mins = int(data.get('refresh_interval_minutes'))
                if mins < 1 or mins > 1440:
                    raise ValueError('Refresh interval must be between 1 and 1440 minutes')
                nifty_raw = data.get('nifty_poll_interval_minutes', None)
                if nifty_raw is not None:
                    nifty_mins = int(nifty_raw)
                    if nifty_mins not in (0, 1, 5, 10, 15, 30, 60):
                        raise ValueError('NIFTY update interval must be Off, 1, 5, 10, 15, 30 or 60 minutes')
                    TRACKER.db.set_setting('nifty_poll_interval_seconds', nifty_mins * 60)
                TRACKER.db.set_setting('refresh_interval_seconds', mins * 60)
                return json_response(self, {
                    'refresh_interval_seconds': mins * 60,
                    'nifty_poll_interval_seconds': TRACKER.db.get_nifty_poll_interval(),
                    'last_nav_refresh': TRACKER.db.last_refresh_time(),
                })
            if path == '/api/refresh':
                state = get_refresh_state()
                if state.get('status') != 'running':
                    threading.Thread(target=TRACKER.refresh_all, daemon=True).start()
                return json_response(self, {'started': True, 'job': get_refresh_state()})
            if path.startswith('/api/funds/') and path.endswith('/transaction-quote'):
                fid=int(path.split('/')[3])
                fund=TRACKER.db.get_fund(fid)
                if not fund: raise ValueError('fund not found')
                data=read_json(self)
                txn_type=str(data.get('txn_type') or '').lower()
                if txn_type not in ('buy','sell'):
                    raise ValueError('Transaction type must be Purchase or Sell')
                nav_date=str(data.get('nav_date') or '').strip()
                target=parse_optional_date(nav_date)
                if not target: raise ValueError('NAV date is required')
                amount_raw=data.get('amount')
                units_raw=data.get('units')
                has_amount=amount_raw not in (None,'')
                has_units=units_raw not in (None,'')
                if not has_amount and not has_units:
                    raise ValueError('Enter either amount or units')
                if has_amount and has_units:
                    raise ValueError('Enter either amount or units, not both')
                amount=parse_money(amount_raw) if has_amount else None
                units=parse_float(units_raw) if has_units else None
                if amount is not None and amount <= 0: raise ValueError('Amount must be greater than zero')
                if units is not None and units <= 0: raise ValueError('Units must be greater than zero')
                nav,actual=TRACKER.nav_on_date(fund['scheme_code'],target)
                if amount is not None and units is not None:
                    expected=units*nav
                    if abs(expected-amount)>max(0.02,abs(amount)*0.001):
                        raise ValueError(f'Amount and units do not match NAV {nav:.6f}; expected amount about ₹{expected:.2f}')
                elif amount is not None:
                    units=amount/nav
                else:
                    amount=units*nav
                return json_response(self, {'holding_id':fid,'txn_type':txn_type,'nav_date':actual.isoformat(),'nav':nav,'amount':amount,'units':units})
            if path.startswith('/api/funds/') and path.endswith('/transactions'):
                fid = int(path.split('/')[3])
                data = read_json(self)
                txn_type = data['txn_type']
                if txn_type not in ('buy', 'sell'):
                    raise ValueError('Manual transactions are Purchase or Sell')
                has_amount = data.get('amount') not in (None, '')
                has_units = data.get('units') not in (None, '')
                if has_amount and has_units and data.get('input_mode') != 'calculated':
                    raise ValueError('Enter either amount or units, not both')
                if not has_amount and not has_units:
                    raise ValueError('Enter either amount or units')
                amount = parse_money(data.get('amount', 0))
                units = parse_float(data.get('units', 0))
                txn_date = str(data.get('txn_date') or '').strip()
                if not txn_date: raise ValueError('NAV date is required')
                parse_optional_date(txn_date)
                nav = float(data['nav']) if data.get('nav') not in (None, '') else None
                if nav is None: raise ValueError('NAV is required')
                TRACKER.db.add_transaction(fid, txn_type, txn_date, amount, units, nav, str(data.get('note') or ''), parse_money(data.get('charges',0)), str(data.get('folio') or '') or None)
                # A manual purchase/sale already has its transaction NAV. Do not
                # trigger a full portfolio NAV refresh here; recalculate immediately
                # from the current cached NAVs so the table and HA entities update
                # as soon as the transaction is recorded.
                TRACKER.update_ha_states(build_portfolio(TRACKER.db))
                return json_response(self, {'ok': True}, 201)
            return json_response(self, {'error': 'not found'}, 404)
        except (KeyError, ValueError, TypeError) as exc:
            return json_response(self, {'error': str(exc)}, 400)
        except Exception as exc:
            return json_response(self, {'error': str(exc)}, 500)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        try:
            if path.startswith('/api/profiles/'):
                pid=int(path.split('/')[3])
                if not TRACKER.db.get_profile(pid): return json_response(self, {'error':'profile not found'},404)
                data=read_json(self)
                TRACKER.db.update_profile(pid, data)
                return json_response(self, {'ok':True})
            if path.startswith('/api/funds/'):
                fid = int(path.split('/')[3])
                existing=TRACKER.db.get_fund(fid)
                if not existing:
                    return json_response(self, {'error': 'fund not found'}, 404)
                data=read_json(self)
                name=str(data.get('scheme_name') or existing['scheme_name']).strip()
                units=parse_float(data.get('units', existing['units']))
                invested=parse_money(data.get('invested', existing['invested']))
                sip_enabled=bool(data.get('sip_enabled', False))
                sip_amount=parse_money(data.get('sip_amount', existing['sip_amount']))
                sip_day=int(data['sip_day']) if sip_enabled and data.get('sip_day') not in (None,'') else None
                if sip_enabled and not (1 <= sip_day <= 31):
                    raise ValueError('SIP day must be between 1 and 31')
                initial_date=data.get('initial_date') or existing['initial_date']
                if initial_date:
                    parse_optional_date(initial_date)
                sip_changed=sip_enabled and (abs(float(existing['sip_amount'] or 0)-sip_amount)>1e-9 or int(existing['sip_day'] or 0)!=int(sip_day or 0))
                effective = None
                if sip_changed:
                    effective=parse_optional_date(data.get('sip_effective_date')) if data.get('sip_effective_date') else None
                    if not effective:
                        raise ValueError('Please enter the first date from which the new SIP amount/date should apply')
                    TRACKER.rebuild_sip_history_for_edit(fid,sip_amount,sip_day,effective)
                else:
                    TRACKER.db.update_fund(fid, {'scheme_name':name,'units':units,'invested':invested,'sip_enabled':sip_enabled,'sip_amount':sip_amount,'sip_day':sip_day,'initial_date':initial_date})

                # If a SIP instruction is changed to take effect today (or an earlier
                # date), do not wait for the normal NAV interval. Refresh ONLY this
                # fund so the existing NAV/SIP engine can immediately acquire the
                # current NAV and execute the eligible monthly SIP. This is a targeted
                # request, not a full-portfolio refresh, so normal NAV load limits are
                # unchanged. Future-dated SIP edits do not trigger a NAV request.
                if sip_changed and effective and effective <= TRACKER.local_today():
                    TRACKER.refresh_fund(TRACKER.db.get_fund(fid))

                # Rebuild the portfolio immediately from the updated holdings,
                # transaction history and (when applicable) newly refreshed NAV.
                TRACKER.update_ha_states(build_portfolio(TRACKER.db))
                return json_response(self, {'ok':True})
            return json_response(self, {'error': 'not found'}, 404)
        except (KeyError, ValueError, TypeError) as exc:
            return json_response(self, {'error': str(exc)}, 400)
        except Exception as exc:
            return json_response(self, {'error': str(exc)}, 500)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        try:
            if path.startswith('/api/profiles/'):
                pid=int(path.split('/')[3])
                TRACKER.db.delete_profile(pid)
                if not TRACKER.db.all_profiles():
                    TRACKER.db._ensure_default_profile()
                # Immediately publish the post-deletion portfolio so the Home
                # Assistant integration can detect removed investors/devices.
                TRACKER.update_ha_states(build_portfolio(TRACKER.db))
                return json_response(self, {'ok':True})
            if path.startswith('/api/funds/') and path.endswith('/transactions'):
                fid = int(path.split('/')[3])
                if not TRACKER.db.get_fund(fid):
                    return json_response(self, {'error': 'fund not found'}, 404)
                data = read_json(self)
                txid = int(data.get('transaction_id'))
                removed = TRACKER.db.delete_transaction(fid, txid)
                TRACKER.update_ha_states(build_portfolio(TRACKER.db))
                return json_response(self, {'ok': True, 'transaction': removed})
            if path.startswith('/api/funds/'):
                fid = int(path.split('/')[3])
                if not TRACKER.db.get_fund(fid):
                    return json_response(self, {'error': 'fund not found'}, 404)
                TRACKER.db.delete_fund(fid)
                return json_response(self, {'ok': True})
            return json_response(self, {'error': 'not found'}, 404)
        except Exception as exc:
            return json_response(self, {'error': str(exc)}, 500)

    def serve_file(self, path):
        if path == '/':
            file_path = WEB_DIR / 'index.html'
        else:
            rel = path.lstrip('/').replace('..', '')
            file_path = WEB_DIR / rel
        if not file_path.exists() or not file_path.is_file():
            file_path = WEB_DIR / 'index.html'
        data = file_path.read_bytes()
        ctype = 'text/html; charset=utf-8'
        if file_path.suffix == '.js':
            ctype = 'text/javascript; charset=utf-8'
        elif file_path.suffix == '.css':
            ctype = 'text/css; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    try:
        TRACKER._write_integration_state()
    except Exception:
        pass
    print(f'Mutual Fund Tracker starting on {HOST}:{PORT}', flush=True)
    print(f'Database: {DB_PATH}', flush=True)
    threading.Thread(target=TRACKER.background_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == '__main__':
    main()
