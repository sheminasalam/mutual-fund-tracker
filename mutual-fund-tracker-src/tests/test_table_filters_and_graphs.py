from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'www' / 'index.html').read_text()
JS = (ROOT / 'www' / 'app.js').read_text()
CSS = (ROOT / 'www' / 'style.css').read_text()
README = (ROOT / 'README.md').read_text()


def test_portfolio_view_has_table_and_graph_tabs():
    assert 'id="portfolioTableTab"' in INDEX
    assert 'id="portfolioGraphsTab"' in INDEX
    assert 'Table' in INDEX and 'Graphs' in INDEX
    assert 'portfolioTablePanel' in INDEX
    assert 'portfolioGraphsPanel' in INDEX


def test_filters_have_search_sip_and_return_controls():
    assert 'id="filterBox"' in INDEX
    assert 'id="filterToggleBtn"' in INDEX
    assert 'id="sipFilter"' in INDEX
    assert 'id="returnsFilter"' in INDEX
    assert 'id="clearFiltersBtn"' in INDEX
    assert 'Active SIP' in INDEX
    assert 'Stopped / inactive SIP' in INDEX
    assert 'Profitable' in INDEX
    assert 'Loss-making' in INDEX


def test_filter_logic_affects_table_and_export():
    assert 'function getFilteredFunds()' in JS
    assert "f.sip==='active'" in JS
    assert "f.sip==='inactive'" in JS
    assert "f.sip==='none'" in JS
    assert "f.returns==='profit'" in JS
    assert "f.returns==='loss'" in JS
    assert "function getVisibleFundsForExport(){return getFilteredFunds();}" in JS
    assert 'const visible=getFilteredFunds();' in JS


def test_filtered_totals_are_marked_and_xirr_not_falsely_aggregated():
    assert 'function aggregateRows(rows)' in JS
    assert "total.xirr=null;" in JS
    assert "filtered?'Filtered totals':'Totals'" in JS
    assert "filtered?'—':pct(ft.xirr)" in JS


def test_graphs_render_historical_without_external_chart_dependency():
    assert 'function renderGraphs()' in JS
    assert 'function renderHistoricalGraphs(dataset)' in JS
    assert 'function buildGraphs()' in JS
    assert 'id="graphFundPicker"' in INDEX
    assert 'id="graphParameterPicker"' in INDEX
    assert 'id="graphPeriodPicker"' in INDEX
    assert 'id="historicalGraphGrid"' in INDEX
    assert 'mft-history-svg' in JS
    assert 'https://' not in JS.split('function renderHistoricalGraphs',1)[1].split('function exportToast',1)[0]

def test_graphs_are_wired_to_backend_job_and_period_selection():
    assert "document.getElementById('buildGraphsBtn')?.addEventListener('click',buildGraphs)" in JS
    assert "document.querySelectorAll('#graphPeriodPicker button')" in JS
    assert "./api/graphs/start" in JS
    assert 'graph_job' not in JS.lower()  # client uses simple job_id polling

def test_docs_mention_table_filters_and_graphs():
    assert 'Table** and **Graphs** tabs' in README
    assert 'line charts' in README


def test_numeric_parameter_relationship_value_filter_controls_and_logic():
    assert 'id="metricFilter"' in INDEX
    assert 'id="metricRelation"' in INDEX
    assert 'id="metricValue"' in INDEX
    assert "parameter:'',relation:'>',value:''" in JS
    assert 'function compareMetric(actual, relation, target)' in JS
    assert "f.parameter && f.value!==''" in JS
    for key in ['xirr','profit_pct','value','invested','day_pct','month_pct']:
        assert key in JS


def test_numeric_parameter_filter_is_wired_to_render_and_clear():
    assert "document.getElementById('metricFilter')?.addEventListener('change',onFundFilterChange)" in JS
    assert "document.getElementById('metricRelation')?.addEventListener('change',onFundFilterChange)" in JS
    assert "document.getElementById('metricValue')?.addEventListener('input',handleMetricValueInput)" in JS
    assert "parameter:'',relation:'>',value:''" in JS


def test_graph_crosshair_tooltip_and_single_fund_statistics_ui():
    assert 'graph-crosshair' in JS
    assert 'graph-hover-tooltip' in JS
    assert 'graph-crosshair-point' in JS
    assert 'points.forEach' in JS
    assert 'mousemove' in JS
    assert 'showAt' in JS
    assert 'const crosshairXpx=(x/w)*shellW' in JS
    assert 'crosshairXpx+18' in JS and 'crosshairXpx-tw-18' in JS
    assert 'function renderGraphStats' in JS
    assert 'Maximum' in JS and 'Median' in JS and 'Minimum' in JS
    assert 'dataset.holding_count' in JS


def test_graph_backend_returns_full_resolution_statistics():
    APP = (ROOT / 'app.py').read_text()
    assert "'statistics':single_stats" in APP
    assert "'holding_count':len(selected)" in APP
    assert "'max_date':max_date" in APP
    assert "'min_date':min_date" in APP


def test_numeric_filter_accepts_leading_negative_values_during_input():
    assert 'id="metricValue" type="text" inputmode="decimal"' in INDEX
    assert 'function sanitizeMetricValueInput(el)' in JS
    assert 'function handleMetricValueInput()' in JS
    assert "metricValue')?.addEventListener('input',handleMetricValueInput)" in JS
    assert "raw.replace(/[^0-9.\\-]/g,'')" in JS
    assert "const negative=cleaned.startsWith('-')" in JS


def test_single_fund_multiple_parameters_tooltip_labels_parameters_not_fund_name():
    assert 'uniqueHoldingIds.length===1&&params.length>1' in JS
    assert 'graphParamLabel(ss.parameter)' in JS
    assert 'graphLabel(ss.fund_name)} · ${graphParamLabel(ss.parameter)' in JS


def test_single_fund_multiple_parameters_render_as_one_combined_graph_with_stats_for_all():
    assert "const groups=singleFund&&allParams.length>1?[allParams]:allParams.map(p=>[p]);" in JS
    assert "singleFund?renderGraphStats(dataset.statistics,params):''" in JS
    assert 'function renderGraphStats(stats,params)' in JS
    assert 'graph-stat-head' in JS and 'graph-stat-row' in JS
    assert 'for(let i=0;i<list.length;i+=2) panels.push(list.slice(i,i+2));' in JS
    assert 'graph-stats-grid' in JS


def test_graph_statistics_use_larger_type_without_growing_vertical_spacing():
    assert '.graph-stat-head{padding:1px 6px 2px;' in CSS
    assert 'font-size:10px;line-height:11px' in CSS
    assert '.graph-stats-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));' in CSS
    assert '.graph-stats-grid>.graph-stats:only-child{grid-column:1 / -1}' in CSS
    assert '.graph-stat-row{padding:2px 6px;font-size:11px;line-height:12px;' in CSS
    assert '.graph-stat-row small{display:inline-block;margin-left:8px;color:inherit;font-size:11px;line-height:12px;vertical-align:baseline}' in CSS


def test_combined_graph_uses_separate_percentage_axis_when_profit_percent_is_selected():
    assert "const moneyParams=params.filter(p=>p!=='profit_pct'),pctParams=params.filter(p=>p==='profit_pct');" in JS
    assert 'yForRight' in JS
    assert 'graph-axis-label-right' in JS
    assert 'graph-axis-line-right' in JS
