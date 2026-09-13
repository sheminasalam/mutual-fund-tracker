from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / 'www' / 'style.css').read_text()

TABLE_ROWS = ''.join('''
<tr>
  <td class="fund-name-cell"><strong class="fund-name-main">quant Small Cap Fund</strong><div class="fund-name-sub">Growth Option - Direct Plan</div><div class="muted">17/08/2026</div></td>
  <td>₹3,000</td><td>1</td><td>₹-249</td><td>-0.19%</td><td>₹1,533</td><td>1.21%</td><td>₹1,28,390</td><td>₹14,704</td><td>₹90,000</td><td>₹38,390</td><td>42.66%</td><td>13.82%</td><td><button class="blue-icon-btn fund-row-action-btn">✎</button></td>
</tr>''' for _ in range(8))

HTML = f'''<!doctype html><html><head><style>{CSS}</style></head><body>
<section class="table-wrap">
<table class="fund-data-table"><colgroup>{'<col>'*14}</colgroup>
<thead><tr><th>Fund Name</th><th colspan="2">SIP Info</th><th colspan="2">Day Returns</th><th colspan="2">Month Returns</th><th>Total Value</th><th>Year Returns</th><th>Invested</th><th colspan="2">Total Returns</th><th>XIRR%</th><th></th></tr>
<tr><th></th><th>Amount</th><th>Date</th><th>Amount</th><th>%</th><th>Amount</th><th>%</th><th></th><th></th><th></th><th>Amount</th><th>%</th><th></th><th></th></tr></thead>
<tbody>{TABLE_ROWS}</tbody></table></section>
<div id="modal" class="modal fund-editor-modal">
<div class="modal-card"><div class="modal-head"><div class="modal-head-title-wrap"><h2 id="modalTitle">quant Small Cap Fund - Growth Option - Direct Plan</h2><div id="modalHeadActions"><button class="blue-action-btn fund-delete-header-btn">Delete Fund</button><button class="blue-action-btn fund-close-header-btn">Close</button></div></div><button class="icon-btn">×</button></div>
<div id="modalBody"><div class="fund-editor"><div class="fund-tabs"><button class="fund-tab-btn is-active">Edit SIP</button><button class="fund-tab-btn">Transact</button><button class="fund-tab-btn">Transactions</button></div><section class="fund-tab-panel"><form class="form"><div class="grid2"><div class="field"><label>Units held</label><input value="410.6"></div><div class="field"><label>Total invested</label><input value="90000"></div></div><div class="grid2"><div class="field"><label>Initial holding date</label><input type="date"></div><div class="field"><label>SIP amount</label><input value="3000"></div></div><div class="form-actions"><button class="primary">Save Changes</button></div></form></section><section class="fund-tab-panel"><div class="fund-transactions-table-panel"><div class="txn-list"><table><tbody><tr><td>18/08/2026</td><td>SIP</td><td>₹2,500</td><td>10</td><td>250</td><td>note</td><td>Delete</td></tr></tbody></table></div></div></section></div></div></div>
<div id="mftPersistentHScroll" class="persistent-hscroll"></div></div></body></html>'''


def test_responsive_table_and_fund_modal():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        desktop = browser.new_page(viewport={'width': 1366, 'height': 768})
        desktop.set_content(HTML)
        table = desktop.locator('.table-wrap')
        assert table.evaluate('(el) => el.scrollWidth > el.clientWidth')
        before = table.evaluate('(el) => el.scrollLeft')
        table.evaluate('(el) => el.scrollLeft = 300')
        assert table.evaluate('(el) => el.scrollLeft') > before

        mobile = browser.new_page(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
        mobile.set_content(HTML)
        mt = mobile.locator('.table-wrap')
        assert mt.evaluate('(el) => el.scrollWidth > el.clientWidth')
        modal = mobile.locator('#modal .modal-card')
        box = modal.bounding_box()
        assert box and box['x'] >= -0.1 and box['x'] + box['width'] <= 390.1
        assert mobile.locator('#modalBody').evaluate('(el) => getComputedStyle(el).overflowY === "auto"')
        assert mobile.locator('#modal').evaluate('(el) => getComputedStyle(el).overflowY === "hidden"')
        assert mobile.locator('.txn-list').evaluate('(el) => getComputedStyle(el).overflowY === "auto"')
        assert mobile.locator('body').evaluate('(el) => getComputedStyle(el).overflowY === "visible"')
        # With the modal open, the portfolio floating scrollbar must be hidden so it cannot become a second visual scrollbar.
        mobile.evaluate("document.body.classList.add('modal-open')")
        assert mobile.locator('#mftPersistentHScroll').count() == 0 or mobile.locator('#mftPersistentHScroll').evaluate('(el) => getComputedStyle(el).display === "none"')
        assert mobile.locator('#modal .grid2').first.evaluate("(el) => getComputedStyle(el).gridTemplateColumns.trim().split(' ').length === 1")
        assert mobile.locator('.fund-close-header-btn').is_visible()
        assert mobile.locator('.fund-delete-header-btn').is_visible()
        # Edit SIP / Transact tabs must retain a vertical scrollable modal body on mobile.
        mobile.locator('#modalBody').evaluate("el => { el.classList.remove('fund-transactions-active'); el.innerHTML = '<div style=\"height:1200px;\">Tall edit form</div>'; }")
        assert mobile.locator('#modalBody').evaluate('(el) => getComputedStyle(el).overflowY === "auto"')
        assert mobile.locator('#modalBody').evaluate('(el) => el.scrollHeight > el.clientHeight')
        # Transactions tab uses the dedicated transaction-table scroll container instead.
        mobile.locator('#modalBody').evaluate("el => { el.classList.add('fund-transactions-active'); el.innerHTML = '<div class=\"fund-transactions-table-panel\"><div class=\"txn-list\" style=\"height:300px;overflow:auto;\"><div style=\"height:900px;\"></div></div></div>'; }")
        assert mobile.locator('#modalBody').evaluate('(el) => getComputedStyle(el).overflowY === "hidden"')
        # The editor's form content must be wider than one column only by stacking, not by overflowing the viewport.
        fields = mobile.locator('#modal .grid2 .field').all()
        for field in fields:
            fb = field.bounding_box()
            assert fb and fb['x'] >= 0 and fb['x'] + fb['width'] <= 390
        browser.close()

if __name__ == '__main__':
    test_responsive_table_and_fund_modal()
    print('responsive UI test passed')


def test_fund_editor_transaction_tabs_have_separate_scroll_container():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1366, 'height': 768})
        page.set_content(HTML.replace('fundTabs','fundTabs'))
        # Structural checks against the source-backed CSS/markup model.
        labels = page.locator('.fund-tab-btn').all_text_contents()
        assert labels == ['Edit SIP', 'Transact', 'Transactions']
        assert page.locator('.fund-transactions-table-panel').count() == 1
        assert page.locator('.fund-transactions-table-panel .txn-list').count() == 1
        assert page.locator('.fund-transactions-table-panel').evaluate('(el) => getComputedStyle(el).display === "flex"')
        assert page.locator('.fund-transactions-table-panel .txn-list').evaluate('(el) => getComputedStyle(el).overflowY === "auto"')
        assert page.locator('.fund-editor').evaluate('(el) => getComputedStyle(el).overflowY !== "auto"')
        browser.close()


def test_fund_editor_tab_switching_behaviour():
    js = (ROOT / 'www' / 'app.js').read_text()
    start = js.index('function switchFundTab')
    end = js.index('function setupTxnInputMode', start)
    fn = js[start:end]
    html = '''<!doctype html><html><body>
<div class="fund-tabs">
  <button class="fund-tab-btn is-active">Edit SIP</button>
  <button class="fund-tab-btn">Transact</button>
  <button class="fund-tab-btn">Transactions</button>
</div>
<section id="fundTabEdit" class="fund-tab-panel"></section>
<section id="fundTabTransact" class="fund-tab-panel hidden"></section>
<section id="fundTabTransactions" class="fund-tab-panel hidden"></section>
<script>function setupTxnInputMode(){}
''' + fn + '''</script></body></html>'''
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 900, 'height': 700})
        page.set_content(html)
        def states():
            return page.locator('.fund-tab-panel').evaluate_all("els => els.map(e => !e.classList.contains('hidden'))")
        assert states() == [True, False, False]
        page.evaluate("switchFundTab(1,'transact')")
        assert states() == [False, True, False]
        assert page.locator('.fund-tab-btn').nth(1).evaluate("e => e.classList.contains('is-active')")
        page.evaluate("switchFundTab(1,'transactions')")
        assert states() == [False, False, True]
        assert page.locator('.fund-tab-btn').nth(2).evaluate("e => e.classList.contains('is-active')")
        page.evaluate("switchFundTab(1,'edit')")
        assert states() == [True, False, False]
        browser.close()
