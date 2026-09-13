from pathlib import Path
import sys
from datetime import datetime
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


def _tracker():
    t = object.__new__(app.Tracker)
    t.timezone_name = "Asia/Kolkata"
    t._nifty_quote = None
    t._nifty_last_error = None
    import threading
    t._integration_state_lock = threading.Lock()
    t._nifty_lock = threading.Lock()
    t._nifty_state_write_condition = threading.Condition()
    t._nifty_state_write_pending = False
    return t


def test_nifty_setting_defaults_to_one_minute():
    with tempfile.TemporaryDirectory() as td:
        db = app.Database(Path(td) / 'test.db')
        assert db.get_nifty_poll_interval() == 60


def test_nifty_setting_accepts_off_and_supported_intervals():
    with tempfile.TemporaryDirectory() as td:
        db = app.Database(Path(td) / 'test.db')
        for value, expected in [("0",0),("60",60),("300",300),("600",600),("900",900),("1800",1800),("3600",3600)]:
            db.set_setting('nifty_poll_interval_seconds', value)
            assert db.get_nifty_poll_interval() == expected


def test_nifty_setting_rejects_unsupported_value_and_falls_back():
    with tempfile.TemporaryDirectory() as td:
        db = app.Database(Path(td) / 'test.db')
        db.set_setting('nifty_poll_interval_seconds', '120')
        assert db.get_nifty_poll_interval() == 60



def test_nifty_fetch_uses_cache_buster_and_no_cache_headers(monkeypatch):
    t = _tracker()
    seen = {}
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b"25000,10,0.04\n"
    def fake_urlopen(req, timeout=0):
        seen['url'] = req.full_url
        seen['cache_control'] = req.get_header('Cache-control')
        seen['pragma'] = req.get_header('Pragma')
        seen['expires'] = req.get_header('Expires')
        return Resp()
    monkeypatch.setattr(app, 'urlopen', fake_urlopen)
    q = t._fetch_nifty50_quote()
    assert q['value'] == 25000.0
    assert 'mft_cache=' in seen['url']
    assert 'no-cache' in seen['cache_control'].lower()
    assert seen['pragma'] == 'no-cache'
    assert seen['expires'] == '0'


def test_market_status_payload_exposes_next_nifty_update_deadline(monkeypatch):
    t = _tracker()
    class DB:
        def get_nifty_poll_interval(self): return 300
    t.db = DB()
    t._nse_market_snapshot = lambda now=None: {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'}
    mono=[100.0]
    monkeypatch.setattr(app.time, 'monotonic', lambda: mono[0])
    t._nifty_next_fetch_mono = 400.0
    payload=t._market_status_payload()
    assert 299.9 <= payload['nifty_next_update_in_seconds'] <= 300.1
    assert payload['nifty_next_update_at'].endswith('Z')
    assert payload['nifty_poll_interval_seconds'] == 300

def test_market_status_loop_uses_configured_nifty_interval_and_stops_when_closed(monkeypatch):
    t = _tracker()
    class DB:
        def get_nifty_poll_interval(self): return 300
    t.db = DB()
    states = [
        {'market_open': False, 'status':'CLOSED', 'date':'2026-08-18'},
        {'market_open': True, 'status':'OPEN', 'date':'2026-08-18'},
    ]
    calls=[]
    def snapshot():
        return states[0] if len(calls)==0 else states[1]
    t._nse_market_snapshot = snapshot
    t._fetch_nifty50_quote = lambda: calls.append('fetch') or {'value':1,'change':1,'percent_change':1,'timestamp':'2026-08-18T04:00:00Z'}
    t._request_nifty_integration_state_write = lambda: calls.append('write')
    sleep_calls=[]
    def sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(app.time, 'sleep', sleep)
    try:
        t._market_status_loop()
    except KeyboardInterrupt:
        pass
    assert 'fetch' in calls
    assert all(v == app.MARKET_STATUS_CHECK_INTERVAL_SECONDS for v in sleep_calls)
    assert app.MARKET_STATUS_CHECK_INTERVAL_SECONDS == 5


def test_web_settings_expose_nifty_interval_and_ui_has_off_option():
    js=(ROOT/'www'/'app.js').read_text()
    appsrc=(ROOT/'app.py').read_text()
    css=(ROOT/'www'/'style.css').read_text()
    assert 'nifty_poll_interval_seconds' in appsrc
    assert 'nifty_poll_interval_minutes' in appsrc
    assert 'id="niftyInterval"' in js
    assert '<option value="0">Off</option>' in js
    assert 'NIFTY 50 update interval' in js
    assert '.table-wrap{margin:0 16px 12px;border:1px solid #aeb3b9;background:#fff;overflow-y:visible;overflow-x:hidden}' in css


def test_header_uses_elapsed_update_text_and_stopped_when_closed():
    js=(ROOT/'www'/'app.js').read_text()
    assert 'Next update in ${secs}s' in js
    assert 'Next update in ${mins}m' in js
    assert 'nifty_next_update_at' in js
    assert "'Update stopped'" in js
    assert 'marketOpen&&niftyEnabled' in js


def test_market_status_loop_uses_monotonic_deadline_and_reacts_to_open(monkeypatch):
    t = _tracker()
    class DB:
        def __init__(self): self.calls = 0
        def get_nifty_poll_interval(self): return 60
    t.db = DB()
    states = [
        {'market_open': False, 'status': 'PRE_OPEN', 'reason': 'Open @ 9:00am', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
    ]
    calls=[]
    state_i={'i':0}
    t._nse_market_snapshot=lambda now=None: states[min(state_i['i'], len(states)-1)]
    t._fetch_nifty50_quote=lambda: calls.append('fetch') or {'value': 25000,'change':1,'percent_change':0.01,'timestamp':f'{len(calls)}'}
    t._request_nifty_integration_state_write=lambda: calls.append('write')
    mono=[0.0]
    monkeypatch.setattr(app.time, 'monotonic', lambda: mono[0])
    sleep_calls=[]
    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        mono[0] += 5.0
        state_i['i'] += 1
        if state_i['i'] >= 4:
            raise KeyboardInterrupt
    monkeypatch.setattr(app.time, 'sleep', fake_sleep)
    try:
        t._market_status_loop()
    except KeyboardInterrupt:
        pass
    # Opening the market triggers an immediate fetch; subsequent fetches follow
    # the configured one-minute cadence rather than the 5-second status poll.
    assert calls.count('fetch') == 1
    assert calls.count('write') >= 2  # PRE_OPEN -> OPEN plus quote update


def test_market_status_signature_ignores_check_timestamp(monkeypatch):
    t = _tracker()
    class DB:
        def get_nifty_poll_interval(self): return 0
    t.db = DB()
    snapshots = [
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20', 'status_checked_at':'A'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20', 'status_checked_at':'B'},
    ]
    idx={'i':0}
    t._nse_market_snapshot=lambda now=None: snapshots[idx['i']]
    writes=[]
    t._request_nifty_integration_state_write=lambda: writes.append('write')
    mono=[0.0]
    monkeypatch.setattr(app.time, 'monotonic', lambda: mono[0])
    def fake_sleep(seconds):
        idx['i'] += 1
        mono[0] += seconds
        if idx['i'] >= 2:
            raise KeyboardInterrupt
    monkeypatch.setattr(app.time, 'sleep', fake_sleep)
    try:
        t._market_status_loop()
    except KeyboardInterrupt:
        pass
    # Only the first loop should force a full integration export; a changing
    # status_checked_at alone must not rebuild the whole portfolio snapshot.
    assert writes == ['write']


def test_nifty_fetches_immediately_at_market_open_then_honors_one_minute_interval(monkeypatch):
    t = _tracker()
    class DB:
        def get_nifty_poll_interval(self): return 60
    t.db = DB()
    states = [
        {'market_open': False, 'status': 'PRE_OPEN', 'reason': 'Open @ 9:00am', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
    ]
    idx={'i':0}
    fetch_times=[]
    writes=[]
    t._nse_market_snapshot=lambda now=None: states[min(idx['i'], len(states)-1)]
    mono=[0.0]
    monkeypatch.setattr(app.time, 'monotonic', lambda: mono[0])
    def fetch():
        fetch_times.append(mono[0])
        return {'value':25000,'change':10,'percent_change':0.1,'timestamp':str(mono[0])}
    t._fetch_nifty50_quote=fetch
    t._request_nifty_integration_state_write=lambda: writes.append(mono[0])
    def sleep(seconds):
        # First loop is pre-open at t=0. The second is market-open at t=5.
        mono[0] += seconds
        idx['i'] += 1
        if mono[0] >= 70:
            raise KeyboardInterrupt
    monkeypatch.setattr(app.time, 'sleep', sleep)
    try:
        t._market_status_loop()
    except KeyboardInterrupt:
        pass
    assert fetch_times[0] == 5.0
    assert fetch_times[1] == 65.0
    assert len(fetch_times) == 2



def test_nifty_poll_interval_is_anchored_to_fetch_start_despite_network_latency(monkeypatch):
    t = _tracker()
    class DB:
        def get_nifty_poll_interval(self): return 60
    t.db = DB()

    states = [
        {'market_open': True, 'status': 'OPEN', 'reason': 'Open', 'date': '2026-08-20'},
    ] * 20
    idx = {'i': 0}
    fetch_times = []
    mono = [0.0]

    t._nse_market_snapshot = lambda now=None: states[min(idx['i'], len(states) - 1)]

    def fetch():
        fetch_times.append(mono[0])
        # Simulate a slow 10-second HTTP request.
        mono[0] += 10.0
        return {
            'value': 25000,
            'change': 10,
            'percent_change': 0.1,
            'timestamp': str(mono[0]),
        }

    t._fetch_nifty50_quote = fetch
    t._write_nifty_integration_state = lambda: None

    def sleep(seconds):
        mono[0] += seconds
        idx['i'] += 1
        if mono[0] >= 75:
            raise KeyboardInterrupt

    monkeypatch.setattr(app.time, 'monotonic', lambda: mono[0])
    monkeypatch.setattr(app.time, 'sleep', sleep)
    try:
        t._market_status_loop()
    except KeyboardInterrupt:
        pass

    # First fetch starts at t=0 and takes 10s; next should begin at t=60,
    # not t=70 (completion + interval).
    assert fetch_times[:2] == [0.0, 60.0]

def test_nifty_polling_stops_when_market_closes(monkeypatch):
    t = _tracker()
    class DB:
        def get_nifty_poll_interval(self): return 60
    t.db = DB()
    states=[
        {'market_open': True, 'status':'OPEN', 'reason':'Open', 'date':'2026-08-20'},
        {'market_open': False, 'status':'CLOSED', 'reason':'Closed', 'date':'2026-08-20'},
        {'market_open': False, 'status':'CLOSED', 'reason':'Closed', 'date':'2026-08-20'},
    ]
    idx={'i':0}; fetches=[]
    t._nse_market_snapshot=lambda now=None: states[min(idx['i'],len(states)-1)]
    t._fetch_nifty50_quote=lambda: fetches.append(1) or {'value':25000,'change':10,'percent_change':0.1,'timestamp':'x'}
    t._write_nifty_integration_state=lambda: None
    mono=[0.0]
    monkeypatch.setattr(app.time, 'monotonic', lambda: mono[0])
    def sleep(seconds):
        mono[0]+=seconds; idx['i']+=1
        if idx['i']>=3: raise KeyboardInterrupt
    monkeypatch.setattr(app.time, 'sleep', sleep)
    try:
        t._market_status_loop()
    except KeyboardInterrupt:
        pass
    assert len(fetches) == 1
