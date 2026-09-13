import json
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WWW = ROOT / "www"
ROWS = [
    {"id": 1, "fund_name": "Alpha Flexi Cap Fund - Direct Plan - Growth", "scheme_name": "Alpha Flexi Cap Fund", "sip_enabled": True, "sip_status": "active", "sip_amount": 2500, "sip_day": 10, "nav_date": "2026-08-18", "day_change": 100, "day_pct": 0.25, "month_change": 500, "month_pct": 1.2, "value": 50000, "year_change": 6000, "invested": 40000, "profit": 10000, "profit_pct": 25, "xirr": 12.5},
    {"id": 2, "fund_name": "Beta Small Cap Fund - Direct Plan - Growth", "scheme_name": "Beta Small Cap Fund", "sip_enabled": False, "sip_status": "inactive", "sip_amount": 1500, "sip_day": 15, "nav_date": "2026-08-18", "day_change": -40, "day_pct": -0.2, "month_change": -100, "month_pct": -0.4, "value": 20000, "year_change": -500, "invested": 21000, "profit": -1000, "profit_pct": -4.76, "xirr": 4.2},
    {"id": 3, "fund_name": "Gamma ELSS Fund - Direct Plan - Growth", "scheme_name": "Gamma ELSS Fund", "sip_enabled": False, "sip_status": "none", "sip_amount": 0, "sip_day": None, "nav_date": "2026-08-18", "day_change": 0, "day_pct": 0, "month_change": 100, "month_pct": 0.5, "value": 30000, "year_change": 1500, "invested": 30000, "profit": 0, "profit_pct": 0, "xirr": None},
]

class PortfolioViewsUITests(unittest.TestCase):
    INDEX = (WWW / "index.html").read_text()
    CSS = (WWW / "style.css").read_text()
    JS = (WWW / "app.js").read_text()

    def make_page(self, pw, width=1366, height=768):
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={"width": width, "height": height})
        html=self.INDEX.replace('<script src="./app.js?v=0.1.166"></script>','')
        html=html.replace('<head>','<head><style>'+self.CSS+'</style>')
        page.set_content(html, wait_until='domcontentloaded')
        payloads={
          'profiles':[{"id":1,"name":"Test Investor","pan":"ABCDE1234F","fund_count":3,"emails":[],"phone":""}],
          'portfolio':{"updated_at":"2026-08-19T12:00:00Z","rows":ROWS,"nav_date":"2026-08-18","nse_holiday":{},"market_status":{"market_open":False,"reason":"Closed"},"total":{"value":100000,"invested":91000,"profit":9000,"profit_pct":9.89,"xirr":10.2,"day_change":60,"month_change":500,"year_change":7000}},
          'settings':{"refresh_interval_seconds":7200,"nifty_poll_interval_seconds":60,"last_nav_refresh":"2026-08-19T11:30:00Z","nav_date":"2026-08-18"},
          'refresh/status':{"status":"idle","progress":100},
          'health':{"ok":True},
        }
        graph_result={"parameters":["total_value","profit_pct"],"series":[
          {"holding_id":1,"fund_name":ROWS[0]["fund_name"],"parameter":"total_value","points":[{"date":"2026-08-01","total_value":40000},{"date":"2026-08-02","total_value":42000},{"date":"2026-08-03","total_value":45000}]},
          {"holding_id":2,"fund_name":ROWS[1]["fund_name"],"parameter":"total_value","points":[{"date":"2026-08-01","total_value":18000},{"date":"2026-08-02","total_value":19000},{"date":"2026-08-03","total_value":20000}]},
          {"holding_id":1,"fund_name":ROWS[0]["fund_name"],"parameter":"profit_pct","points":[{"date":"2026-08-01","profit_pct":10},{"date":"2026-08-02","profit_pct":15},{"date":"2026-08-03","profit_pct":18}]}
        ],"warnings":[]}
        page.evaluate("""({payloads,graphResult})=>{window.fetch=async(url,opts)=>{const u=String(url);if(u.includes('/api/profiles'))return new Response(JSON.stringify(payloads.profiles),{status:200});if(u.includes('/api/portfolio'))return new Response(JSON.stringify(payloads.portfolio),{status:200});if(u.includes('/api/settings'))return new Response(JSON.stringify(payloads.settings),{status:200});if(u.includes('/api/refresh/status'))return new Response(JSON.stringify(payloads['refresh/status']),{status:200});if(u.includes('/api/graphs/start'))return new Response(JSON.stringify({job_id:'g1',status:'queued'}),{status:202});if(u.includes('/api/graphs/status/g1'))return new Response(JSON.stringify({status:'completed',progress:100,message:'Historical graph ready.',result:graphResult}),{status:200});return new Response(JSON.stringify(payloads.health),{status:200});};}""",{"payloads":payloads,"graphResult":graph_result})
        page.add_script_tag(content="const localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{},clear:()=>{}};"+self.JS)
        return browser,page

    def test_filters_and_historical_graphs(self):
        with sync_playwright() as pw:
            browser,page=self.make_page(pw)
            page.wait_for_selector('#fundRows tr')
            page.locator('#filterToggleBtn').click();page.select_option('#sipFilter','active');self.assertEqual(page.locator('#fundRows tr').count(),1)
            page.locator('#portfolioGraphsTab').click()
            self.assertTrue(page.locator('#portfolioGraphsPanel').is_visible())
            self.assertEqual(page.locator('#graphFundPicker input').count(),3)
            page.locator('#buildGraphsBtn').click();page.wait_for_timeout(500)
            self.assertEqual(page.locator('.historical-graph-card').count(),2)
            self.assertGreater(page.locator('.mft-history-svg').count(),0)
            self.assertIn('Historical graph ready',page.locator('#graphStatus').inner_text())
            browser.close()

    def test_graph_mobile_layout(self):
        with sync_playwright() as pw:
            browser,page=self.make_page(pw,390,844)
            page.wait_for_selector('#fundRows tr');page.locator('#portfolioGraphsTab').click()
            self.assertGreater(page.locator('.graph-controls').count(),0)
            self.assertGreater(page.locator('#graphFundPicker input').count(),0);self.assertGreater(page.locator('.graph-controls').count(),0)
            browser.close()

if __name__=='__main__': unittest.main()
