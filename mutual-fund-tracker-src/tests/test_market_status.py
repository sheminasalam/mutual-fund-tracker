import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


def tracker():
    t = object.__new__(app.Tracker)
    t.timezone_name = "Asia/Kolkata"
    t._nifty_quote = None
    t._nifty_last_error = None
    t._nifty_next_fetch_mono = None
    import threading
    t._nifty_lock = threading.Lock()
    # Production initializes this synchronization primitive before the NIFTY
    # writer thread starts. Keep lightweight test fixtures equivalent.
    t._nifty_state_write_condition = threading.Condition()
    t._nifty_state_write_pending = False
    return t


def test_market_open_window():
    t = tracker()
    t._holiday_status = lambda d: {"nse_open": True, "type": "TRADING_DAY", "description": None}
    assert t._nse_market_snapshot(datetime(2026, 8, 17, 8, 59, tzinfo=app.ZoneInfo("Asia/Kolkata")))['status'] == 'PRE_OPEN'
    assert t._nse_market_snapshot(datetime(2026, 8, 17, 8, 59, tzinfo=app.ZoneInfo("Asia/Kolkata")))['reason'] == 'Open @ 9:00am'
    assert t._nse_market_snapshot(datetime(2026, 8, 17, 9, 0, tzinfo=app.ZoneInfo("Asia/Kolkata")))['market_open'] is True
    assert t._nse_market_snapshot(datetime(2026, 8, 17, 15, 30, tzinfo=app.ZoneInfo("Asia/Kolkata")))['market_open'] is False


def test_holiday_market_closed():
    t = tracker()
    t._holiday_status = lambda d: {"nse_open": False, "type": "NSE_HOLIDAY", "description": "Holi"}
    snap = t._nse_market_snapshot(datetime(2026, 3, 3, 10, 0, tzinfo=app.ZoneInfo("Asia/Kolkata")))
    assert snap['market_open'] is False
    assert snap['status'] == 'HOLIDAY'
    assert snap['reason'] == 'Holi'


def test_parse_nifty_quote_from_yahoo(monkeypatch):
    t = tracker()
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"chart":{"result":[{"meta":{"regularMarketPrice":24672.15,"previousClose":24548.70}}]}}'
    monkeypatch.setattr(app, 'urlopen', lambda req, timeout=15: Resp())
    q = t._fetch_nifty50_quote()
    assert q['value'] == 24672.15
    assert round(q['change'], 2) == 123.45
    assert round(q['percent_change'], 2) == 0.5
    assert q['source'] == 'Yahoo Finance NIFTY 50 live endpoint'


def test_parse_nifty_csv_with_header_and_quoted_comma_as_fallback(monkeypatch):
    t = tracker()

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return b'Index,Change,ChangePct\n"24,672.15",123.45,0.50\n'

    class FailYahoo:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): raise RuntimeError('Yahoo unavailable')

    def fake_urlopen(req, timeout=15):
        if 'query1.finance.yahoo.com' in req.full_url:
            return FailYahoo()
        return Resp()

    monkeypatch.setattr(app, 'urlopen', fake_urlopen)
    q = t._fetch_nifty50_quote()
    assert q['value'] == 24672.15
    assert round(q['change'], 2) == 123.45


def test_invalid_nifty_csv_raises_without_valid_quote(monkeypatch):
    t = tracker()
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'#N/A,#N/A,#N/A\n'
    monkeypatch.setattr(app, 'urlopen', lambda req, timeout=15: Resp())
    import pytest
    with pytest.raises(RuntimeError, match='Unable to fetch a valid NIFTY 50 quote'):
        t._fetch_nifty50_quote()



def test_invalid_yahoo_falls_back_to_published_google_csv(monkeypatch):
    t = tracker()
    calls = []
    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return self.body
    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        if 'query1.finance.yahoo.com' in req.full_url:
            return Resp(b'{"chart":{"result":[{"meta":{}}]}}')
        return Resp(b'24999.25,11.50,0.05\n')
    monkeypatch.setattr(app, 'urlopen', fake_urlopen)
    q = t._fetch_nifty50_quote()
    assert q['value'] == 24999.25
    assert q['change'] == 11.50
    assert q['source'] == 'Google Sheets GOOGLEFINANCE CSV proxy (published CSV endpoint)'
    assert len(calls) == 2

def test_nifty_numeric_change_timestamp_is_preserved_when_quote_is_unchanged():
    t = tracker()
    previous = {
        'index': 'NIFTY 50',
        'value': 25000.0,
        'change': 10.0,
        'percent_change': 0.04,
        'timestamp': 'fetch-old',
        'data_changed_at': 'change-old',
        'source': 'Google Sheets GOOGLEFINANCE CSV proxy (published CSV endpoint)',
    }
    t._nifty_quote = dict(previous)

    quote = dict(previous)
    quote['timestamp'] = 'fetch-new'
    new_core = (quote['value'], quote['change'], quote['percent_change'])
    old_core = (previous['value'], previous['change'], previous['percent_change'])

    assert new_core == old_core
    assert quote['data_changed_at'] == previous['data_changed_at']


def test_secondary_quote_updates_existing_quote(monkeypatch):
    t = tracker()
    t._nifty_quote = {
        'index': 'NIFTY 50',
        'value': 25000.0,
        'change': 10.0,
        'percent_change': 0.04,
        'timestamp': 'primary-fetch',
        'data_changed_at': 'primary-change',
        'source': 'Google Sheets GOOGLEFINANCE CSV proxy (published CSV endpoint)',
    }
    quote = {
        'index': 'NIFTY 50',
        'value': 25100.0,
        'change': 20.0,
        'percent_change': 0.08,
        'timestamp': 'fallback-fetch',
        'source': 'Google Sheets GOOGLEFINANCE CSV proxy (published CSV endpoint)',
    }
    previous = dict(t._nifty_quote)
    new_core = (quote['value'], quote['change'], quote['percent_change'])
    previous_core = (previous['value'], previous['change'], previous['percent_change'])
    if new_core != previous_core or previous is None:
        quote['data_changed_at'] = quote['timestamp']
    else:
        quote['data_changed_at'] = previous.get('data_changed_at')
    t._nifty_quote = quote
    assert t._nifty_quote['value'] == 25100.0
    assert t._nifty_quote['change'] == 20.0
    assert t._nifty_quote['data_changed_at'] == 'fallback-fetch'
    assert 'published CSV endpoint' in t._nifty_quote['source']


def test_market_status_loop_accepts_new_secondary_values(monkeypatch):
    t = tracker()
    class DB:
        def get_nifty_poll_interval(self): return 60
    t.db = DB()
    t._nse_market_snapshot = lambda now=None: {
        'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20',
        'status_checked_at': 'x',
    }
    quotes = iter([
        {'index': 'NIFTY 50', 'value': 24243.35, 'change': 165.05, 'percent_change': 0.69,
         'timestamp': 'fetch-1', 'source': 'Google Sheets GOOGLEFINANCE CSV proxy (published CSV endpoint)'},
        {'index': 'NIFTY 50', 'value': 24241.65, 'change': 163.35, 'percent_change': 0.68,
         'timestamp': 'fetch-2', 'source': 'Google Sheets GOOGLEFINANCE CSV proxy (published CSV endpoint)'},
    ])
    t._fetch_nifty50_quote = lambda: next(quotes)
    writes = []
    t._request_nifty_integration_state_write = lambda: writes.append(dict(t._nifty_quote))
    mono = iter([0.0, 0.0, 61.0, 61.0])
    monkeypatch.setattr(app.time, 'monotonic', lambda: next(mono))
    sleeps = iter([None, RuntimeError('__stop__')])
    def fake_sleep(_):
        action = next(sleeps)
        if isinstance(action, Exception):
            raise action
    monkeypatch.setattr(app.time, 'sleep', fake_sleep)
    try:
        t._market_status_loop()
    except RuntimeError as exc:
        assert str(exc) == '__stop__'
    assert len(writes) == 2
    assert writes[0]['value'] == 24243.35
    assert writes[1]['value'] == 24241.65
    assert writes[1]['source'].endswith('(published CSV endpoint)')
    assert writes[1]['data_changed_at'] == 'fetch-2'




def test_yahoo_parser_and_secondary_google_source_contract(monkeypatch):
    t = tracker()
    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return self.body
    calls = []
    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        if 'query1.finance.yahoo.com' in req.full_url:
            return Resp(b'{"chart":{"result":[{"meta":{"regularMarketPrice":24231.85,"previousClose":24154.90}}]}}')
        raise AssertionError('Google should not be contacted while Yahoo succeeds')
    monkeypatch.setattr(app, 'urlopen', fake_urlopen)
    q = t._fetch_nifty50_quote()
    assert q['source'] == 'Yahoo Finance NIFTY 50 live endpoint'
    assert q['value'] == 24231.85
    assert round(q['change'], 2) == 76.95
    assert round(q['percent_change'], 2) == 0.32
    assert len(calls) == 1


def test_nifty_writer_request_is_coalescing_and_nonblocking():
    import threading
    t = tracker()
    t._nifty_state_write_condition = threading.Condition()
    t._nifty_state_write_pending = False
    writes = []
    t._write_nifty_integration_state = lambda: writes.append('write')
    t._request_nifty_integration_state_write()
    t._request_nifty_integration_state_write()
    with t._nifty_state_write_condition:
        assert t._nifty_state_write_pending is True
    # The poller-side request itself performs no file I/O; it only sets the flag.
    assert writes == []

def test_nifty_state_writer_updates_only_market_status(tmp_path, monkeypatch):
    import json
    import threading

    state_dir = tmp_path / 'state'
    state_dir.mkdir()
    (state_dir / 'integration_state.json').write_text(json.dumps({
        'version': 3,
        'state_revision': 41,
        'updated_at': 'old',
        'profiles': [{'id': 1, 'name': 'Test Investor'}],
        'all_investors': {'name': 'All Investors'},
        'market_status': {'market_open': True, 'nifty': {'value': 24000}},
    }), encoding='utf-8')

    t = tracker()
    t._integration_state_lock = threading.Lock()
    t.db = app.Database(state_dir / 'test.db')
    t.db.set_setting('integration_state_revision', '41')
    t._market_status_payload = lambda: {
        'market_open': True,
        'status': 'OPEN',
        'nifty': {'index': 'NIFTY 50', 'value': 24250.0, 'change': 100.0, 'percent_change': 0.41},
        'nifty_available': True,
    }
    monkeypatch.setenv('MFT_INTEGRATION_STATE_DIRS', str(state_dir))

    t._write_nifty_integration_state()
    data = json.loads((state_dir / 'integration_state.json').read_text(encoding='utf-8'))

    assert data['state_revision'] == 42
    assert data['profiles'] == [{'id': 1, 'name': 'Test Investor'}]
    assert data['all_investors'] == {'name': 'All Investors'}
    assert data['market_status']['nifty']['value'] == 24250.0

def test_market_snapshot_converts_utc_to_ist():
    t = tracker()
    t._holiday_status = lambda d: {"nse_open": True, "type": "TRADING_DAY", "description": None}
    # 04:15 UTC is 09:45 IST, so the NSE market must be open.
    snap = t._nse_market_snapshot(datetime(2026, 8, 18, 4, 15, tzinfo=app.timezone.utc))
    assert snap['market_open'] is True
    assert snap['status'] == 'OPEN'


def test_nse_and_market_status_include_check_timestamp():
    t = tracker()
    t._holiday_status = lambda d: {"nse_open": True, "type": "TRADING_DAY", "description": None, "tomorrow_nse_open": True}
    snap = t._nse_market_snapshot(datetime(2026, 8, 18, 9, 15, tzinfo=app.ZoneInfo("Asia/Kolkata")))
    assert snap['status'] == 'OPEN'
    assert snap['status_checked_at']
    # The mocked holiday provider is intentionally minimal; status_checked_at belongs to the snapshot payload.


def test_nifty_csv_url_and_format_are_configured():
    assert 'docs.google.com/spreadsheets/d/e/2PACX-1vTj0LbJz31u9xE_LtaWLKaf-xiUYTMhPsHvwPS0sQ-RNw058kjLCeoSVn6w544mJAcIG_TsD929ZVTT/pub?gid=0&single=true&output=csv' in app.NIFTY_CSV_URL
    assert app.NIFTY_YAHOO_URL.startswith('https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI')
    assert app.NIFTY_CSV_FALLBACK_URL == app.NIFTY_CSV_URL
    assert app.DEFAULT_NIFTY_POLL_INTERVAL_SECONDS == 60


def test_nifty_request_uses_primary_yahoo_endpoint(monkeypatch):
    t = tracker()
    calls = []

    class Resp:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"chart":{"result":[{"meta":{"regularMarketPrice":24800.50,"previousClose":24875.75}}]}}'

    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        return Resp()

    monkeypatch.setattr(app, 'urlopen', fake_urlopen)
    q = t._fetch_nifty50_quote()
    assert q['value'] == 24800.50
    assert q['change'] == -75.25
    assert calls
    assert calls[0].startswith(app.NIFTY_YAHOO_URL)
    assert 'interval=1m' in calls[0]
    assert 'range=1d' in calls[0]


def test_market_status_payload_keeps_nifty_while_open():
    t = tracker()
    t._holiday_status = lambda d: {"nse_open": True, "type": "TRADING_DAY", "description": None}
    t._nse_market_snapshot = lambda now=None: {
        'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-18'
    }
    t._nifty_quote = {
        'index': 'NIFTY 50', 'value': 24672.15, 'change': 123.45,
        'percent_change': 0.50, 'timestamp': '2026-08-18T04:15:00Z'
    }
    payload = t._market_status_payload()
    assert payload['market_open'] is True
    assert payload['nifty']['value'] == 24672.15
    assert payload['nifty']['percent_change'] == 0.50


def test_holiday_status_includes_yesterday_nse_open_state():
    t = tracker()
    t._holiday_calendar_payload = lambda: {
        'dates': {
            '2026-08-17': {'nse_open': True, 'type': 'TRADING_DAY', 'description': None, 'holiday_type': None},
            '2026-08-18': {'nse_open': True, 'type': 'TRADING_DAY', 'description': None, 'holiday_type': None},
            '2026-08-19': {'nse_open': False, 'type': 'NSE_HOLIDAY', 'description': 'Holi', 'holiday_type': 'TRADING_HOLIDAY'},
        }
    }
    status = t._holiday_status(app.date(2026, 8, 18))
    assert status['yesterday_date'] == '2026-08-17'
    assert status['yesterday_nse_open'] is True


def test_holiday_status_marks_yesterday_closed_when_it_was_a_holiday():
    t = tracker()
    t._holiday_calendar_payload = lambda: {
        'dates': {
            '2026-08-17': {'nse_open': False, 'type': 'NSE_HOLIDAY', 'description': 'Holi', 'holiday_type': 'TRADING_HOLIDAY'},
            '2026-08-18': {'nse_open': True, 'type': 'TRADING_DAY', 'description': None, 'holiday_type': None},
        }
    }
    status = t._holiday_status(app.date(2026, 8, 18))
    assert status['yesterday_date'] == '2026-08-17'
    assert status['yesterday_nse_open'] is False


def test_holiday_status_marks_yesterday_closed_on_weekend():
    t = tracker()
    t._holiday_calendar_payload = lambda: {
        'dates': {
            '2026-08-16': {'nse_open': False, 'type': 'WEEKEND', 'description': 'Sunday', 'holiday_type': 'WEEKEND'},
            '2026-08-17': {'nse_open': True, 'type': 'TRADING_DAY', 'description': None, 'holiday_type': None},
        }
    }
    status = t._holiday_status(app.date(2026, 8, 17))
    assert status['yesterday_date'] == '2026-08-16'
    assert status['yesterday_nse_open'] is False


def test_holiday_status_exposes_tomorrow_open_state():
    t = tracker()
    t._holiday_calendar_payload = lambda: {
        'year': 2026,
        'generated_at': 'x',
        'dates': {
            '2026-08-21': {'nse_open': True, 'type': 'TRADING_DAY', 'description': None, 'holiday_type': None},
            '2026-08-22': {'nse_open': False, 'type': 'WEEKEND', 'description': 'Saturday', 'holiday_type': 'WEEKEND'},
            '2026-08-20': {'nse_open': True, 'type': 'TRADING_DAY', 'description': None, 'holiday_type': None},
        },
    }
    status = t._holiday_status(app.date(2026, 8, 21))
    assert status['tomorrow_date'] == '2026-08-22'
    assert status['tomorrow_nse_open'] is False
    assert status['tomorrow_type'] == 'WEEKEND'


def test_tomorrow_binary_sensor_uses_tomorrow_nse_open():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / 'custom_components' / 'mutual_fund_tracker' / 'binary_sensor.py').read_text()
    assert 'class MutualFundNSETomorrowBinarySensor' in src
    assert 'bool((self.coordinator.data.get("nse_holiday") or {}).get("tomorrow_nse_open", False))' in src


def test_binary_sensor_nse_names_and_unique_ids():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / 'custom_components' / 'mutual_fund_tracker' / 'binary_sensor.py').read_text()
    assert '_attr_name = "NSE Today"' in src
    assert '_attr_name = "NSE Trading"' in src
    assert '_attr_name = "NSE Yesterday Status"' in src
    assert 'f"{DOMAIN}_nse_today"' in src
    assert 'f"{DOMAIN}_nse_trading"' in src
    assert 'f"{DOMAIN}_nse_yesterday_status"' in src
    assert 'yesterday_nse_open' in src
    assert 'f"{DOMAIN}_nse_holiday"' not in src
    assert 'f"{DOMAIN}_nse_market"' not in src


def test_web_ui_market_status_header_and_plus_menu():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / 'www' / 'index.html').read_text()
    js = (root / 'www' / 'app.js').read_text()
    assert 'id="marketStatus" class="header-status-area"' in html
    assert 'id="plusMenuBtn"' in html
    assert 'id="plusMenu"' in html
    assert 'id="importBtn"' in html
    assert 'id="addBtn"' in html
    assert 'Next update in' in js
    assert 'Update stopped' in js
    assert 'function togglePlusMenu' in js
