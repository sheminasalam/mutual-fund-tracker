from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_app_is_not_marked_experimental():
    cfg = (ROOT / "config.yaml").read_text()
    assert "stage: experimental" not in cfg
    assert 'version: "1.0.0"' in cfg


def test_add_investor_is_menu_action():
    index = (ROOT / "www" / "index.html").read_text()
    js = (ROOT / "www" / "app.js").read_text()
    assert 'id="addInvestorMenuBtn"' in index
    assert 'id="addUserBtn"' not in index
    assert 'addInvestorMenuBtn' in js
    assert 'addUserBtn' not in js


def test_readme_covers_card_nifty_nse_dark_mode():
    readme = (ROOT / "README.md").read_text()
    assert "Custom Lovelace card" in readme
    assert "NIFTY 50" in readme
    assert "dark mode" in readme.lower()
    assert "XIRR" in readme

def test_custom_integration_manifest_version_matches_release():
    manifest = (ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / 'manifest.json').read_text()
    assert '"version": "1.0.0"' in manifest

