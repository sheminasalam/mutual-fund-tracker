from pathlib import Path
import json
from playwright.sync_api import sync_playwright
from test_custom_card import build_hass
ROOT=Path(__file__).resolve().parents[1]
hass=build_hass()
hass_script=f"window.TEST_HASS = {json.dumps(hass)}; window.TEST_HASS.callService = async (...args) => window.TEST_HASS.calls.push(args);"
html=f"""<!doctype html><html><body style='margin:24px;background:#111;color:#fff'><script>{hass_script}</script></body></html>"""
with sync_playwright() as pw:
    b=pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    p=b.new_page(viewport={'width':1400,'height':1400}, device_scale_factor=1)
    p.set_content(html)
    p.add_script_tag(path=str(ROOT/'www'/'mutual-fund-tracker-card.js'))
    p.evaluate("""() => { const el=document.createElement('mutual-fund-tracker-card'); document.body.appendChild(el); el.hass=window.TEST_HASS; el.setConfig({investors:['sensor.mutual_fund_10_fund_count','sensor.mutual_fund_11_fund_count']}); }""")
    p.wait_for_timeout(100)
    p.screenshot(path='/mnt/data/v56work/card_preview.png', full_page=True)
    b.close()
print('/mnt/data/v56work/card_preview.png')
