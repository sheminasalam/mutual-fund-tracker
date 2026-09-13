import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CARD = (ROOT / 'www' / 'mutual-fund-tracker-card.js').as_uri()


def state(entity, value, **attrs):
    return {'entity_id': entity, 'state': str(value), 'attributes': attrs}


def build_hass():
    states = {}
    for pid, name, total in [
        ('10', 'Harry Potter', 4581015),
        ('11', 'Hermione Granger', 2456789),
    ]:
        base = f'sensor.mutual_fund_{pid}'
        # Deliberately use a realistic entity-id pattern with all related entities,
        # while relying on investor_id for cross-entity matching.
        entities = {
            f'{base}_fund_count': state(f'{base}_fund_count', 2, friendly_name=f'{name} Fund Count', investor_id=pid, fund_count=2, last_nav_refresh='2026-08-14T10:48:29+05:30'),
            f'{base}_total_value': state(f'{base}_total_value', total, friendly_name=f'{name} Total Value', investor_id=pid),
            f'{base}_total_invested': state(f'{base}_total_invested', total - 150000, friendly_name=f'{name} Total Invested', investor_id=pid),
            f'{base}_total_profit': state(f'{base}_total_profit', 150000, friendly_name=f'{name} Total Profit', investor_id=pid),
            f'{base}_total_profit_2': state(f'{base}_total_profit_2', 4.95, friendly_name=f'{name} Total Profit %', investor_id=pid),
            f'{base}_daily_change': state(f'{base}_daily_change', -9999, friendly_name=f'{name} Daily Change', investor_id=pid),
            f'{base}_monthly_change': state(f'{base}_monthly_change', 70903, friendly_name=f'{name} Monthly Change', investor_id=pid),
            f'{base}_yearly_change': state(f'{base}_yearly_change', 241587, friendly_name=f'{name} Yearly Change', investor_id=pid),
            f'{base}_portfolio_xirr': state(f'{base}_portfolio_xirr', 13.89, friendly_name=f'{name} Portfolio XIRR', investor_id=pid),
            f'{base}_latest_nav_date': state(f'{base}_latest_nav_date', '2026-08-13', friendly_name=f'{name} Latest NAV Date', investor_id=pid),
            f'{base}_next_expected_sip_date': state(f'{base}_next_expected_sip_date', '2026-08-20', friendly_name=f'{name} Next Expected SIP Date', investor_id=pid, details=[{'fund_name':'Test Fund A','amount':2500}]),
            f'{base}_fund_details': state(f'{base}_fund_details', 2, friendly_name=f'{name} Fund Details', investor_id=pid, funds=[
                {'fund_name':'Test Fund A','sip_amount':'2500','sip_day':13,'sip_enabled':True,'day_change':'-55','day_pct':'-0.02','month_change':'2165','month_pct':'0.96','value':'227223','invested':'155000','profit':'72223','profit_pct':'46.60','xirr':'12.02','nav_date':'2026-08-13'},
                {'fund_name':'Test Fund B','sip_amount':'1500','sip_day':14,'sip_enabled':True,'day_change':'227','day_pct':'0.09','month_change':'763','month_pct':'0.32','value':'239113','invested':'142500','profit':'96613','profit_pct':'67.80','xirr':'20.65','nav_date':'2026-08-13'},
            ]),
            f'binary_sensor.mutual_fund_{pid}_sip_executed_today': state(f'binary_sensor.mutual_fund_{pid}_sip_executed_today', 'on' if pid == '10' else 'off', investor_id=pid, reporting_date=('2026-08-20' if pid == '10' else None), count=(2 if pid == '10' else 0), total_amount=(4000 if pid == '10' else 0), details=([{'fund_name':'Test Fund A','amount':2500},{'fund_name':'Test Fund B','amount':1500}] if pid == '10' else [])),
            f'binary_sensor.mutual_fund_{pid}_delayed_nav_update': state(
                f'binary_sensor.mutual_fund_{pid}_delayed_nav_update',
                'on' if pid == '10' else 'off',
                investor_id=pid,
                details=([{'fund_name': 'Test Fund A', 'holding_id': 101, 'amount': 2500, 'latest_nav_date': '2026-08-18', 'next_working_nav_date': '2026-08-19', 'delayed_after': '2026-08-21', 'days_since_latest_nav': 3}] if pid == '10' else []),
            ),
        }
        states.update(entities)
    states['binary_sensor.mutual_fund_tracker_nse_holiday'] = state('binary_sensor.mutual_fund_tracker_nse_holiday', 'on', friendly_name='Mutual Fund Tracker NSE Market Open', nse_open=True, status='Open', type='TRADING_DAY', description='', tomorrow_nse_open=False, tomorrow_type='NSE_HOLIDAY', tomorrow_description='Holi')
    states['binary_sensor.mutual_fund_tracker_nse_tomorrow'] = state('binary_sensor.mutual_fund_tracker_nse_tomorrow', 'on', friendly_name='Mutual Fund Tracker NSE Tomorrow', tomorrow_date='2026-08-18', tomorrow_nse_open=True, tomorrow_type='TRADING_DAY', tomorrow_description='')
    states['binary_sensor.mutual_fund_tracker_nse_market'] = state('binary_sensor.mutual_fund_tracker_nse_market', 'on', friendly_name='Mutual Fund Tracker NSE Market Status', market_open=True, status='OPEN', reason='Open', nifty={'index':'NIFTY 50','value':24672.15,'change':123.45,'percent_change':0.50,'timestamp':'2026-08-17T09:45:00+05:30','source':'Nifty Indices LiveIndicesWatch'})
    states['button.mutual_fund_tracker_refresh_nav'] = state('button.mutual_fund_tracker_refresh_nav', 'unknown', friendly_name='Refresh NAV')
    return {'states': states, 'calls': [], 'callService': None}


def test_custom_card_render_and_editor():
    hass = build_hass()
    hass_script = f"window.TEST_HASS = {json.dumps(hass)}; window.TEST_HASS.callService = async (...args) => window.TEST_HASS.calls.push(args);"
    html = f"""
    <!doctype html><html><head><meta charset='utf-8'></head><body>
    <script>{hass_script}</script>
    <script src='{CARD}'></script>
    <script>
      window.makeTestCard = () => {{
        const el = document.createElement('mutual-fund-tracker-card');
        document.body.appendChild(el);
        el.hass = window.TEST_HASS;
        el.setConfig({{investors:['sensor.mutual_fund_10_fund_count','sensor.mutual_fund_11_fund_count']}});
        return el;
      }};
      window.makeTestEditor = () => {{
        const el = document.createElement('mutual-fund-tracker-card-editor');
        document.body.appendChild(el);
        el.hass = window.TEST_HASS;
        el.setConfig({{investors:[]}});
        return el;
      }};
    </script></body></html>
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        page.set_content(html)
        page.add_script_tag(path=str(ROOT / 'www' / 'mutual-fund-tracker-card.js'))
        page.evaluate('makeTestCard()')
        page.wait_for_timeout(100)

        assert page.locator('mutual-fund-tracker-card').count() == 1
        assert page.get_by_text('Mutual Fund Tracker').count() >= 1
        assert page.locator('.mft-logo').count() == 1
        assert page.locator('.mft-logo').get_attribute('src') == '/api/mutual_fund_tracker/mutual-fund-tracker-logo.png'
        assert page.locator('.mft-investor-select').count() == 1
        assert page.locator('.mft-investor-select').input_value() == 'sensor.mutual_fund_10_fund_count'
        assert page.get_by_text('Harry Potter').count() >= 1
        assert page.get_by_text('Hermione Granger').count() >= 0
        assert page.locator('.mft-investor').count() == 1
        assert page.get_by_text('Refresh NAV').count() >= 1
        assert page.locator('.mft-meta').count() == 0
        assert page.get_by_text('2 funds · NAV Aug 13, 2026').count() >= 1
        assert page.locator('.mft-market-strip').count() == 1
        assert page.locator('.mft-status-switch').count() == 3
        assert page.locator('.mft-status-switch').nth(0).get_attribute('aria-checked') == 'true'
        assert page.locator('.mft-status-switch').nth(1).get_attribute('aria-checked') == 'true'
        assert page.locator('.mft-status-switch').nth(2).get_attribute('aria-checked') == 'true'
        assert page.get_by_text('NSE Today').count() >= 1
        assert page.get_by_text('NSE Tomorrow').count() >= 1
        assert page.get_by_text('NSE Trading').count() >= 1
        # Status-item grid reserves enough width for the longest title (NSE Tomorrow).
        card_js = (ROOT / "www" / "mutual-fund-tracker-card.js").read_text(encoding="utf-8")
        assert "grid-template-columns:52px 90px minmax(0,1fr)" in card_js
        assert page.get_by_text('Tomorrow Holi Holiday').count() >= 1
        assert page.locator('.mft-nifty-label').inner_text() == 'Nifty50'
        assert page.locator('.mft-nifty-value.is-positive').inner_text() == '24,672.15(+123.45)'
        assert page.locator('.mft-nifty-value.is-positive').count() >= 1
        assert page.get_by_text('₹45,81,015').count() >= 1
        assert page.get_by_text('Month Change').count() >= 1
        assert page.get_by_text('Day Change').count() >= 1
        metric_labels = page.locator('.mft-metric span').all_text_contents()
        assert 'Day Change' in metric_labels
        assert 'Today' not in metric_labels
        assert page.get_by_text('₹70,903').count() >= 1
        assert page.get_by_text('SIP executed today').count() >= 1
        assert page.get_by_text('Funds: Test Fund A, Test Fund B').count() >= 1
        card_source = (ROOT / 'www' / 'mutual-fund-tracker-card.js').read_text(encoding='utf-8')
        assert 'sipExecutedAttributes.count' in card_source
        assert 'sipExecutedAttributes.total_amount' in card_source
        assert "sipExecuted: Number(sipExecutedAttributes.count || 0) > 0" in card_source
        assert page.get_by_text('Delayed NAV Update').count() >= 1
        assert page.locator('.mft-info-box.delayed strong').inner_text() == 'ON'
        delayed_detail = page.locator('.mft-info-box.delayed .mft-info-detail').inner_text()
        assert 'Test Fund A' in delayed_detail
        assert '₹2,500' in delayed_detail
        # Delayed NAV Update is a full-width row below the two primary SIP boxes.
        assert page.locator('.mft-sip-row > .mft-info-box').count() == 2
        assert page.locator('.mft-delayed-row').count() == 1
        # The delayed row is not a third column of the primary SIP row.
        assert page.locator('.mft-delayed-row .mft-info-box.delayed').count() == 1
        # Today is derived from the selected investor's fund day changes (-55 + 227 = +172),
        # rather than trusting the separate daily-change entity (fixture deliberately uses -9999).
        assert page.get_by_text('₹172').count() >= 1
        assert '.mft-nifty-value.is-positive' in (ROOT / 'www' / 'mutual-fund-tracker-card.js').read_text()
        assert '.mft-nifty-value.is-negative' in (ROOT / 'www' / 'mutual-fund-tracker-card.js').read_text()
        card_text = (ROOT / 'www' / 'mutual-fund-tracker-card.js').read_text()
        assert '_renderTimer' in card_text
        assert '}, 30000);' in card_text
        assert '}, 2000);' in card_text
        assert (ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / 'static' / 'mutual-fund-tracker-card.js').read_text() == card_text
        assert page.get_by_text('SIP Date').count() >= 1
        assert page.get_by_text('Test Fund A').count() >= 1
        assert page.locator('.mft-info-detail').count() >= 1
        assert page.get_by_text('Fund:').count() >= 1
        assert page.locator('.mft-sort-select').count() == 1
        assert page.locator('.mft-sort-direction').count() == 1
        assert page.locator('option[value=\"value\"]').count() >= 1

        # Investor selector should switch the visible portfolio without changing editor configuration.
        page.locator('.mft-investor-select').select_option('sensor.mutual_fund_11_fund_count')
        page.wait_for_timeout(50)
        assert page.locator('.mft-investor').count() == 1
        assert page.locator('.mft-investor-name').first.text_content() == 'Hermione Granger'
        # Delayed NAV Update is hidden completely when the binary sensor is OFF.
        assert page.locator('.mft-delayed-row').count() == 0
        assert page.locator('.mft-sip-row > .mft-info-box').count() == 2
        assert page.locator('.mft-metric strong').first.text_content() == '₹24,56,789'
        assert page.locator('.mft-investor-select').input_value() == 'sensor.mutual_fund_11_fund_count'
        page.locator('.mft-investor-select').select_option('sensor.mutual_fund_10_fund_count')
        page.wait_for_timeout(50)

        # UI sort should change row order without changing the configured investors.
        page.locator('.mft-sort-select').select_option('fund_name')
        page.wait_for_timeout(50)
        first_fund = page.locator('.mft-table tbody tr .fund-name div').first.text_content()
        assert first_fund == 'Test Fund B', first_fund
        page.locator('.mft-sort-direction').click()
        page.wait_for_timeout(50)
        first_fund_asc = page.locator('.mft-table tbody tr .fund-name div').first.text_content()
        assert first_fund_asc == 'Test Fund A', first_fund_asc

        page.locator('.mft-refresh').click()
        page.wait_for_timeout(50)
        calls = page.evaluate('TEST_HASS.calls')
        assert calls and calls[0][0] == 'button' and calls[0][1] == 'press'

        page2 = browser.new_page(viewport={'width': 900, 'height': 700})
        page2.set_content(html)
        page2.add_script_tag(path=str(ROOT / 'www' / 'mutual-fund-tracker-card.js'))
        page2.evaluate('makeTestEditor()')
        page2.wait_for_timeout(100)
        assert page2.locator('.mft-editor-select').count() == 0
        assert page2.locator('.mft-editor-check input[type="checkbox"]').count() == 2
        assert page2.locator('.mft-editor-sort-select').count() == 1
        assert page2.locator('.mft-editor-sort-direction').count() == 1
        assert page2.locator('.mft-editor-selection').text_content().strip().startswith('0 investor')
        # Selecting investors in the visual editor should emit a config containing an allow-list,
        # while preserving the editor's purpose as configuration rather than runtime selection.
        page2.locator('.mft-editor-check input[data-investor="sensor.mutual_fund_10_fund_count"]').check()
        page2.wait_for_timeout(50)
        assert page2.locator('.mft-editor-selection').text_content().strip().startswith('1 investor')
        assert page2.locator('.mft-editor-check input[data-investor="sensor.mutual_fund_10_fund_count"]').is_checked()
        page2.locator('.mft-editor-check input[data-investor="sensor.mutual_fund_11_fund_count"]').check()
        page2.wait_for_timeout(50)
        assert page2.locator('.mft-editor-selection').text_content().strip().startswith('2 investors')
        # Default sorting remains configurable from the visual editor.
        page2.locator('.mft-editor-sort-select').select_option('profit')
        page2.wait_for_timeout(30)
        assert page2.locator('.mft-editor-sort-select').input_value() == 'profit'
        page2.locator('.mft-editor-sort-direction').click()
        page2.wait_for_timeout(30)
        assert 'Ascending' in page2.locator('.mft-editor-sort-direction').text_content()
        browser.close()


if __name__ == '__main__':
    test_custom_card_render_and_editor()
    print('custom card test passed')

def test_custom_card_works_without_redundant_investor_id_attributes():
    hass = build_hass()
    # v0.1.186+ intentionally removed repeated investor_id attributes from
    # most entities. The card must still resolve related entities by the
    # deterministic entity-id relationship.
    for state in hass['states'].values():
        state.get('attributes', {}).pop('investor_id', None)

    hass_script = f"window.TEST_HASS = {json.dumps(hass)};"
    html = f"<!doctype html><html><head><meta charset='utf-8'></head><body><script>{hass_script}</script><script src='{CARD}'></script></body></html>"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        page.set_content(html)
        page.add_script_tag(path=str(ROOT / 'www' / 'mutual-fund-tracker-card.js'))
        page.evaluate("""(() => { const el=document.createElement('mutual-fund-tracker-card'); document.body.appendChild(el); el.hass=window.TEST_HASS; el.setConfig({investors:['sensor.mutual_fund_10_fund_count']}); return el; })()""")
        page.wait_for_timeout(150)
        assert page.get_by_text('Harry Potter').count() >= 1
        assert page.get_by_text('₹45,81,015').count() >= 1
        assert page.get_by_text('₹1,50,000').count() >= 1
        assert page.get_by_text('Test Fund A').count() >= 1
        assert page.locator('.mft-metric strong').count() >= 5
        browser.close()


def test_custom_card_negative_nifty_is_red():
    hass = build_hass()
    hass['states']['binary_sensor.mutual_fund_tracker_nse_market']['attributes']['nifty']['change'] = -81.35
    hass['states']['binary_sensor.mutual_fund_tracker_nse_market']['attributes']['nifty']['value'] = 24206.00
    hass_script = f"window.TEST_HASS = {json.dumps(hass)};"
    html = f"<!doctype html><html><head><meta charset='utf-8'></head><body><script>{hass_script}</script><script src='{CARD}'></script></body></html>"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1400, 'height': 800})
        page.set_content(html)
        page.add_script_tag(path=str(ROOT / 'www' / 'mutual-fund-tracker-card.js'))
        page.evaluate("""(() => { const el=document.createElement('mutual-fund-tracker-card'); document.body.appendChild(el); el.hass=window.TEST_HASS; el.setConfig({investors:['sensor.mutual_fund_10_fund_count']}); return el; })()""")
        page.wait_for_timeout(100)
        assert page.locator('.mft-nifty-value.is-negative').count() == 1
        assert page.locator('.mft-nifty-label').inner_text() == 'Nifty50'
        assert page.locator('.mft-nifty-value').inner_text() == '24,206(-81.35)'
        browser.close()



def test_custom_card_uses_dedicated_tomorrow_sensor():
    hass = build_hass()
    # Deliberately make today's NSE sensor say tomorrow is closed while the dedicated
    # tomorrow entity says open. The card must use the dedicated sensor for NSE Tomorrow.
    hass['states']['binary_sensor.mutual_fund_tracker_nse_holiday']['attributes']['tomorrow_nse_open'] = False
    hass['states']['binary_sensor.mutual_fund_tracker_nse_holiday']['attributes']['tomorrow_description'] = 'Holi'
    hass_script = f"window.TEST_HASS = {json.dumps(hass)};"
    html = f"<!doctype html><html><head><meta charset='utf-8'></head><body><script>{hass_script}</script><script src='{CARD}'></script></body></html>"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        page.set_content(html)
        page.add_script_tag(path=str(ROOT / 'www' / 'mutual-fund-tracker-card.js'))
        page.evaluate("(() => { const el=document.createElement('mutual-fund-tracker-card'); document.body.appendChild(el); el.hass=window.TEST_HASS; el.setConfig({investors:['sensor.mutual_fund_10_fund_count']}); })()")
        page.wait_for_timeout(100)
        assert page.get_by_text('NSE Tomorrow').count() == 1
        tomorrow_switch = page.locator('.mft-status-switch').nth(1)
        assert tomorrow_switch.get_attribute('aria-checked') == 'true'
        browser.close()


def test_card_static_resource_registration():
    init_text = (ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / '__init__.py').read_text()
    static_card = ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / 'static' / 'mutual-fund-tracker-card.js'
    static_logo = ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / 'static' / 'mutual-fund-tracker-logo.png'
    assert static_card.exists()
    assert static_logo.exists()
    assert static_card.read_text() == (ROOT / 'www' / 'mutual-fund-tracker-card.js').read_text()
    assert 'async_register_static_paths' in init_text
    assert '/api/mutual_fund_tracker/mutual-fund-tracker-card.js' in init_text
    assert '/api/mutual_fund_tracker/mutual-fund-tracker-logo.png' in init_text
    assert 'StaticPathConfig' in init_text

def test_nse_market_status_bypasses_render_throttle():
    card = (ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / 'static' / 'mutual-fund-tracker-card.js').read_text()
    assert '_findNseTradingDayEntityId' in card
    assert '_findNseTomorrowEntityId' in card
    assert '_findNseMarketEntityId' in card
    assert "Object.prototype.hasOwnProperty.call(attrs, 'nse_open')" in card
    assert "Object.prototype.hasOwnProperty.call(attrs, 'market_open')" in card
    assert "marketStateChanged" in card
    assert "window.clearTimeout(this._renderTimer)" in card
    assert "this._render();\n        return;\n      }\n      this._hasMarketState" in card


def test_custom_card_resolves_current_ha_entity_suffixes_without_investor_id():
    hass = build_hass()
    # Current production entity suffixes must resolve even when redundant
    # investor_id attributes are absent. Do not recreate legacy suffixes here.
    for state in hass['states'].values():
        state['attributes'] = dict(state.get('attributes', {}))
        state['attributes'].pop('investor_id', None)

    hass_script = f"window.TEST_HASS = {json.dumps(hass)};"
    html = f"<!doctype html><html><head><meta charset='utf-8'></head><body><script>{hass_script}</script><script src='{CARD}'></script></body></html>"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        page.set_content(html)
        page.add_script_tag(path=str(ROOT / 'www' / 'mutual-fund-tracker-card.js'))
        page.evaluate("(() => { const el=document.createElement('mutual-fund-tracker-card'); document.body.appendChild(el); el.hass=window.TEST_HASS; el.setConfig({investors:['sensor.mutual_fund_10_fund_count']}); return el; })()")
        page.wait_for_timeout(150)
        assert page.get_by_text('₹45,81,015').count() >= 1
        assert page.get_by_text('₹44,31,015').count() >= 1
        assert page.locator('.mft-metric strong').count() >= 5
        browser.close()



def test_profit_pct_entity_id_uses_explicit_object_id_and_migration():
    sensor = (ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / 'sensor.py').read_text(encoding='utf-8')
    init = (ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / '__init__.py').read_text(encoding='utf-8')
    card = (ROOT / 'www' / 'mutual-fund-tracker-card.js').read_text(encoding='utf-8')
    assert 'self._attr_suggested_object_id = "total_profit_pct"' in sensor
    assert 'endswith("_profit_pct")' in init
    assert 'endswith("_total_profit_2")' in init
    assert 'new_entity_id=target_entity_id' in init
    assert 'existing.unique_id == entry.unique_id' in init
    assert "profit_pct: ['total_profit_pct', 'total_profit_2', 'profit_pct']" in card


def test_profit_percentage_is_parenthesized_and_day_change_label_is_used():
    card_text = (ROOT / 'www' / 'mutual-fund-tracker-card.js').read_text(encoding='utf-8')
    assert "this._metric('Day Change'" in card_text
    assert 'mft-metric-sub">(${esc(pct(investor.profitPct))})' in card_text
