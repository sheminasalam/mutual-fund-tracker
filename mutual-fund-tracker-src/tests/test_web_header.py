from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'www' / 'index.html').read_text()
JS = (ROOT / 'www' / 'app.js').read_text()
CSS = (ROOT / 'www' / 'style.css').read_text()
MANIFEST = (ROOT / 'manifest.json').read_text()
CONFIG = (ROOT / 'config.yaml').read_text()
CHANGELOG = (ROOT / 'CHANGELOG.md').read_text()
APP = (ROOT / 'app.py').read_text()


class WebHeaderTests(unittest.TestCase):

 def test_v136_fund_editor_close_and_no_cancel_and_hidden_asof(self):
    assert 'fund-close-header-btn' in JS
    assert 'onclick="closeModal()" title="Close fund editor">Close</button>' in JS
    fragment=JS[JS.index('function fundEditHtml'):JS.index('function fundTransactionDetailsHtml')]
    assert 'class="secondary" onclick="closeModal()">Cancel</button>' not in fragment
    assert 'fund-editor-modal' in JS
    assert '.modal.fund-editor-modal .modal-head > .icon-btn{display:none}' in CSS
    assert 'id="asof"' not in INDEX
    assert "const asof=document.getElementById('asof'); if(asof)" in JS

 def test_header_has_three_zones_and_dedicated_status_info(self):
    assert 'class="header-brand"' in INDEX
    assert 'class="header-status-area"' in INDEX
    assert 'class="actions"' in INDEX
    assert 'class="header-status-switches"' in INDEX
    assert 'class="header-status-info"' in INDEX
    assert 'header-status-info-line' in INDEX
    assert 'header-status-nifty' in INDEX
    assert 'header-status-nse-reason' in INDEX
    assert 'header-status-info-sub' in INDEX
    assert 'grid-template-columns:minmax(285px,1.05fr) minmax(330px,1.05fr) minmax(340px,1.15fr)' in CSS
    assert '.refresh-group{display:flex;align-items:center;gap:9px;min-width:0}' in CSS
    assert '.refresh-info{display:flex;flex-direction:column;align-items:flex-start' in CSS
    assert '.refresh-info .refresh-time,.refresh-info .refresh-interval' in CSS
    assert '.icon-action-btn{width:34px;height:34px' in CSS
    assert '.header-status-area{display:grid;grid-template-columns:max-content minmax(0,1fr)' in CSS
    assert '@media(max-width:920px)' in CSS
    assert '.refresh-group{display:flex;align-items:center;gap:9px;min-width:0}' in CSS
    assert '.refresh-info{display:flex;flex-direction:column;align-items:flex-start' in CSS
    assert '.refresh-info .refresh-time,.refresh-info .refresh-interval' in CSS


 def test_plus_menu_has_direct_working_handlers(self):
    assert 'id="plusMenuBtn"' in INDEX
    assert 'id="importBtn"' in INDEX
    assert 'Use the latest consolidated report, preferably today' in JS
    assert 'id="addBtn"' in INDEX
    assert 'onclick="togglePlusMenu' not in INDEX
    assert '.plus-menu.hidden{display:none!important}' in CSS
    assert 'z-index:1000' in CSS
    assert "plusBtn.addEventListener('click'" in JS
    assert "importBtn?.addEventListener('click'" in JS
    assert "addBtn?.addEventListener('click'" in JS
    assert 'function setupHeaderActions()' in JS
    assert 'setupHeaderActions();' in JS
    assert "if(plusBtn.dataset.mftBound==='1') return true;" in JS


 def test_refresh_info_is_stacked_and_populated_by_date_time_interval(self):
    assert 'refresh-date' in INDEX and 'refresh-time' in INDEX and 'refresh-interval' in INDEX
    assert 'formatDate(last.slice(0,10))' in JS
    assert "d.toLocaleTimeString" in JS
    assert 'Every ${mins} min' in JS

 def test_status_info_uses_sensor_exported_values(self):
    assert 'nifty_next_update_at' in APP
    assert 'Next update in ${secs}s' in JS
    assert 'Next update in ${mins}m' in JS
    assert 'nifty_next_update_at' in JS
    assert "'Update stopped'" in JS
    assert 'const niftyEnabled=Number(appSettings?.nifty_poll_interval_seconds||0)>0;' in JS
    assert 'niftyCountdownContext' in JS
    assert 'header-status-nifty-label' in JS
    assert 'header-status-nifty-value' in JS
    assert "const tomorrowClosed=h.tomorrow_nse_open===false;" in JS
    assert "const secondLine=tomorrowClosed?'Tomorrow Closed':'Tomorrow Open';" in JS
    assert "is-tomorrow-holiday" in JS
    assert 'header-status-info-sub' in JS
    assert 'header-status-market-reason' not in JS
    assert 'Update stopped' in JS
    assert '.header-status-nifty-label{color:#111827}' in CSS
    assert '.header-status-nifty-value.is-positive{color:#16a34a}' in CSS
    assert '.header-status-nifty-value.is-negative{color:#dc2626}' in CSS
    assert '.header-status-nse-reason.is-tomorrow-holiday{color:#dc2626;font-weight:800}' in CSS


 def test_lumpsum_submit_handler_is_unique_and_matches_new_flow(self):
    # The old Add Mutual Fund handler used a removed `.ls-nav` field and
    # overrode the new NAV-preview/job flow, causing: Cannot read properties of null (reading 'value').
    assert JS.count('async function submitAdd(e)') == 1
    start = JS.index('async function submitAdd(e)')
    end = JS.index('window.submitAdd=submitAdd;', start)
    handler = JS[start:end]
    assert 'previewLumpsum()' in handler
    assert 'startLumpsumAdd()' in handler
    assert '.ls-nav' not in handler

 def test_add_investor_is_in_plus_menu_not_profile_bar(self):
    assert 'id="addInvestorMenuBtn"' in INDEX
    assert 'role="menuitem">Add Investor</button>' in INDEX
    assert 'id="addUserBtn"' not in INDEX
    assert "addInvestorMenuBtn" in JS
    assert "addUserBtn" not in JS

 def test_button_style_is_consistent(self):
    assert 'class="blue-action-btn blue-action-wide"' in INDEX
    assert 'class="blue-icon-btn"' in INDEX
    assert 'class="blue-icon-btn sort-dir-btn"' in INDEX
    assert 'class="blue-action-btn export-png-btn"' in INDEX
    assert 'fund-row-action-btn' in JS
    assert 'title="Edit fund"' in JS
    assert '.fund-row-action-btn{width:32px;height:32px' in CSS
    assert '.fund-action-icon{width:16px;height:16px' in CSS
    assert 'id="addInvestorMenuBtn"' in INDEX
    assert 'role="menuitem">Add Investor</button>' in INDEX
    assert 'title="Edit Investor"' in INDEX
    assert 'title="Export PNG"' in INDEX
    assert '.blue-action-btn,.blue-icon-btn{' in CSS
    assert '.blue-icon-btn{width:34px;height:34px' in CSS
    assert 'background:#2aa5e8' in CSS
    assert '.blue-action-btn:hover,.blue-icon-btn:hover{background:#168fd1}' in CSS
    assert "b.textContent=sortDir==='asc'?'↑':'↓'" in JS
    assert "b.title=sortDir==='asc'?'Sort ascending':'Sort descending'" in JS

 def test_fund_name_split_handles_spaced_and_unspaced_plan_delimiters(self):
    import subprocess, json, re
    start=JS.index('function splitFundName(name){')
    end=JS.index('function tone(', start)
    fn=JS[start:end]
    script=fn+"\nconst vals=[splitFundName('quant Small Cap Fund - Growth Option - Direct Plan'),splitFundName('Tata Digital India Fund-Direct Plan-Growth'),splitFundName('Motilal Oswal Midcap Fund-Direct Plan-Growth Option'),splitFundName('Mirae Asset Large & Midcap Fund - Direct Plan - Growth')];\nprocess.stdout.write(JSON.stringify(vals));\n"
    out=subprocess.check_output(['node','-e',script], text=True)
    vals=json.loads(out)
    assert vals[0]=={'main':'quant Small Cap Fund','sub':'Growth Option - Direct Plan'}
    assert vals[1]=={'main':'Tata Digital India Fund','sub':'Direct Plan-Growth'}
    assert vals[2]=={'main':'Motilal Oswal Midcap Fund','sub':'Direct Plan-Growth Option'}
    assert vals[3]=={'main':'Mirae Asset Large & Midcap Fund','sub':'Direct Plan - Growth'}
    assert re.search(r'\\s\*-\\s\*', fn)

 def test_v135_fund_table_and_combined_editor(self):
    assert 'class="fund-data-table"' in INDEX
    assert 'col class="col-fund"' in INDEX
    assert 'splitFundName(f.fund_name)' in JS
    assert JS.count('fund-row-action-btn') == 1
    assert 'onclick="editFund(${f.id})"' in JS
    assert 'onclick="viewFund(${f.id})"' not in JS
    assert 'async function openFundEditor' in JS
    assert 'Edit SIP' in JS and 'Transact' in JS and 'Transactions' in JS
    assert 'fundTransactionDetailsHtml' in JS and 'fundTransactionsTableHtml' in JS and 'fundEditHtml' in JS
    assert JS.count('async function openFundEditor') == 1
    assert JS.count('function switchFundTab') == 1
    assert "window.viewFund=(id)=>openFundEditor(id,'transactions')" in JS
    assert 'id="modalHeadActions"' in INDEX
    assert 'deleteFund(${id})' in JS

 def test_v135_dates_are_display_only_and_iso_api_values_remain(self):
    assert 'function formatDate(value)' in JS
    assert 'return `${m[3]}/${m[2]}/${m[1]}`;' in JS
    assert '<html lang="en-GB">' in INDEX
    assert 'lang="en-GB" type="date"' in JS
    assert "formatDate(f.nav_date||'NAV unavailable')" in JS
    assert 'formatDate(t.txn_date)' in JS
    assert "initial_date:document.getElementById('e_initial').value||null" in JS
    assert "nav_date:document.getElementById('tx_nav_date').value" in JS
    assert 'first_sip_date:first' in JS
 def test_legacy_version_metadata(self):
    assert '# v0.1.163' in CHANGELOG
    assert 'version: "1.0.0"' in CONFIG
    assert '# v0.1.171' in CHANGELOG

 def test_current_version_metadata_secondary(self):
    assert '1.0.0' in MANIFEST
    assert '# v0.1.171' in CHANGELOG
    assert '0.1.163' not in MANIFEST


class ImportSipReconciliationWebTests(unittest.TestCase):
 def test_import_ui_and_payload_support_recent_sip_reconciliation(self):
    assert 'Recent SIP reconciliation' in JS
    assert 'Executed' in JS and 'Skipped' in JS
    assert 'Will execute automatically' in JS
    assert 'Will not execute · SIP inactive' in JS
    assert "sipStatusForKey=(key,code)=>" in JS
    assert 'setImportSipReconciliation' in JS
    assert 'sip_reconciliation_mapping' in JS
    assert 'last 10 days' in JS
 def test_import_backend_has_ten_day_guard_and_reconciliation_flow(self):
    assert 'statement_generated_date' in APP
    assert 'Imports are limited to statements generated within the last 10 days' in APP
    assert 'def _build_import_sip_reconciliations' in APP
    assert 'def execute_post_import_sips' in APP
    assert 'live_sip_executions' in APP
    assert 'import_reconciled_sip' in APP


class AddMutualFundLumpsumWebTests(unittest.TestCase):
 def test_lumpsum_ui_uses_nav_date_and_amount_or_units_confirmation_flow(self):
    assert 'id="lumpsumCurrentSummary"' in JS
    assert 'summary.style.display=mode===\'lumpsum\'?\'none\':\'grid\'' in JS
    assert '<label>NAV date</label>' in JS
    assert 'either the amount invested or the units purchased' in JS
    assert "./api/funds/lumpsum-quote" in JS
    assert "./api/funds/lumpsum/start" in JS
    assert "./api/funds/lumpsum/status/" in JS
    assert 'Confirm & Add Fund' in JS
    assert 'Get NAV & Preview' in JS
    assert 'input_mode' not in JS[JS.index('async function previewLumpsum'):JS.index('let searchTimer;')]

 def test_lumpsum_backend_endpoints_and_validation_exist(self):
    assert "if path == '/api/funds/lumpsum/start'" in APP
    assert "if path.startswith('/api/funds/lumpsum/status/')" in APP
    assert "if path == '/api/funds/lumpsum-quote'" in APP
    assert 'def _prepare_lumpsum_payload' in APP
    assert 'amount and units do not match NAV' in APP

 def test_button_style_is_consistent(self):
    assert 'class="blue-action-btn blue-action-wide"' in INDEX
    assert 'class="blue-icon-btn"' in INDEX
    assert 'class="blue-icon-btn sort-dir-btn"' in INDEX
    assert 'id="addInvestorMenuBtn"' in INDEX
    assert 'role="menuitem">Add Investor</button>' in INDEX
    assert 'title="Edit Investor"' in INDEX
    assert 'title="Export PNG"' in INDEX
    assert '.blue-action-btn,.blue-icon-btn{' in CSS
    assert '.blue-icon-btn{width:34px;height:34px' in CSS
    assert 'background:#2aa5e8' in CSS
    assert '.blue-action-btn:hover,.blue-icon-btn:hover{background:#168fd1}' in CSS
    assert "b.textContent=sortDir==='asc'?'↑':'↓'" in JS
    assert "b.title=sortDir==='asc'?'Sort ascending':'Sort descending'" in JS

 def test_legacy_version_metadata_secondary(self):
    assert '# v0.1.163' in CHANGELOG
    assert '1.0.0' in APP
    assert '0.1.163' not in APP


class AddMutualFundSIPWebTests(unittest.TestCase):
 def test_sip_ui_uses_same_nav_preview_and_confirmation_pattern(self):
    assert 'id="sipQuote"' in JS
    assert 'id="sipProgress"' in JS
    assert "./api/funds/sip/preview/start" in JS
    assert "./api/funds/sip/preview/status/" in JS
    assert "./api/funds/sip/start" in JS
    assert "./api/funds/sip/status/" in JS
    assert 'async function previewSip()' in JS
    assert 'async function startSipAdd()' in JS
    assert 'Confirm & Add Fund' in JS
    assert 'Get NAV & Preview' in JS

 def test_sip_submit_no_longer_posts_directly_to_funds_endpoint(self):
    start=JS.index('async function submitAdd(e)')
    end=JS.index('window.submitAdd=submitAdd;', start)
    handler=JS[start:end]
    assert 'previewSip()' in handler
    assert 'startSipAdd()' in handler
    assert "./api/funds',{method:'POST'" not in handler

 def test_sip_backend_endpoints_and_preview_helpers_exist(self):
    assert "if path == '/api/funds/sip/preview/start'" in APP
    assert "if path.startswith('/api/funds/sip/preview/status/')" in APP
    assert "if path == '/api/funds/sip/start'" in APP
    assert "if path.startswith('/api/funds/sip/status/')" in APP
    assert 'def _prepare_sip_preview' in APP
    assert 'SIP_PREVIEWS' in APP



class FundEditorTabTests(unittest.TestCase):
 def test_fund_editor_has_three_tabs_and_transactions_table_is_only_scroll_container(self):
    assert 'Transact' in JS
    assert 'fundTabTransact' in JS
    assert 'fundTabTransactions' in JS
    assert '.modal.fund-editor-modal #modalBody{overflow:auto;min-height:0;flex:1 1 auto;overscroll-behavior:contain}' in CSS
    assert '.modal.fund-editor-modal #modalBody.fund-transactions-active{overflow:hidden}' in CSS
    assert '.modal.fund-editor-modal #modalBody .fund-transactions-table-panel .txn-list{max-height:calc(100dvh - 250px);overflow:auto}' in CSS
    assert '@media(max-width:700px)' in CSS
    assert '.modal.fund-editor-modal #modalBody .fund-transactions-table-panel .txn-list{max-height:calc(100dvh - 210px)}' in CSS
    assert 'fund-transactions-active' in JS


def test_nav_refresh_prefetch_and_two_hour_reuse_are_wired():
    assert 'threading.Thread(target=self._prefetch_nav_reference_cache, daemon=True).start()' in APP
    assert 'def _prefetch_nav_reference_cache' in APP
    assert 'amfi_nav_history_v2' in APP
    assert 'NAV_SNAPSHOT_CACHE_SECONDS = 7200' in APP
    assert 'def refresh_all_if_stale' in APP
    assert "last successful refresh is less than 2 hours old" in APP
    assert 'TRACKER.refresh_all_if_stale()' in APP



def test_nse_header_has_today_tomorrow_trading_switch_order():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / 'www' / 'index.html').read_text()
    assert '<span class="app-status-title">NSE Today</span>' in html
    assert '<span class="app-status-title">NSE Tomorrow</span>' in html
    assert '<span class="app-status-title">NSE Trading</span>' in html
    assert html.index('NSE Today') < html.index('NSE Tomorrow') < html.index('NSE Trading')
    css = (root / 'www' / 'style.css').read_text()
    assert 'grid-template-columns:repeat(3,max-content)' in css
    assert '.header-status-switches{display:grid;grid-template-columns:repeat(3,max-content);align-items:center;gap:4px;min-width:0}' in css
    assert '.header-status-item{display:flex;align-items:center;gap:2px;white-space:nowrap}' in css
    assert '.header-status-info{min-width:0;border-left:1px solid #e5e7eb;padding-left:8px;' in css
    js = (root / 'www' / 'app.js').read_text()
    assert "const tomorrowClosed=h.tomorrow_nse_open===false;" in js
    assert "const secondLine=tomorrowClosed?'Tomorrow Closed':'Tomorrow Open';" in js
