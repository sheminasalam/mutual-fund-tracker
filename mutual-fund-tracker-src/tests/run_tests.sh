#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile app.py
node --check www/app.js
python3 tests/test_custom_card.py
python3 tests/test_dark_mode.py
find . -type d -name '__pycache__' -prune -exec rm -rf {} +
find . -type f -name '*.pyc' -delete
python3 - <<'PY'
from pathlib import Path
import zipfile
root = Path('.')
assert (root / 'app.py').exists()
assert not list(root.rglob('__pycache__'))
assert not list(root.rglob('*.pyc'))
for required in ('config.yaml', 'manifest.json', 'www/manifest.json', 'tests/test_regression.py'):
    assert (root / required).exists(), required
print('PASS: package source checks')
PY
echo 'ALL SMOKE TESTS PASSED'
node --check www/mutual-fund-tracker-card.js
python3 - <<'PY'
from pathlib import Path
p=Path('www/mutual-fund-tracker-card.js').read_text()
for token in ('getConfigElement', 'window.customCards', 'mutual-fund-tracker-card-editor', 'Refresh NAV', 'Latest NAV', "callService('button', 'press'", 'next_expected_sip_date', 'mft-market-strip', 'sipExecutedFunds'):
    assert token in p, token
print('PASS: custom card static checks')
from pathlib import Path as _Path
bin_src=_Path('../custom_components/mutual_fund_tracker/binary_sensor.py').read_text()
assert '_attr_name = "NSE Today"' in bin_src
assert '_attr_name = "NSE Trading"' in bin_src
assert '_attr_name = "NSE Yesterday Status"' in bin_src
assert 'f"{DOMAIN}_nse_today"' in bin_src
assert '_attr_name = "NSE Tomorrow"' in bin_src
assert 'f"{DOMAIN}_nse_tomorrow"' in bin_src
assert 'f"{DOMAIN}_nse_trading"' in bin_src
assert 'f"{DOMAIN}_nse_yesterday_status"' in bin_src
print('PASS: NSE sensor semantics checks')
PY
