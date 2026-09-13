from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "www" / "app.js").read_text()
CSS = (ROOT / "www" / "style.css").read_text()
CARD = (ROOT.parent / "custom_components" / "mutual_fund_tracker" / "static" / "mutual-fund-tracker-card.js").read_text()

def test_web_nifty_label_and_value_are_split_and_colored():
    assert 'header-status-nifty-label' in APP
    assert 'header-status-nifty-value' in APP
    assert 'header-status-nifty-label{color:#111827}' in CSS
    assert 'header-status-nifty-value.is-positive{color:#16a34a}' in CSS
    assert 'header-status-nifty-value.is-negative{color:#dc2626}' in CSS

def test_card_nifty_label_and_value_are_split_and_colored():
    assert 'mft-nifty-label' in CARD
    assert 'mft-nifty-value ${niftyClass}' in CARD
    assert '.mft-nifty-label{color:var(--primary-text-color)}' in CARD
    assert '.mft-nifty-value.is-positive{color:#16a34a}' in CARD
    assert '.mft-nifty-value.is-negative{color:#dc2626}' in CARD

def test_nifty_display_name_is_nifty50_not_nifty_50():
    assert 'Nifty50' in APP
    assert 'Nifty50' in CARD
    assert '<span class=\"mft-nifty-label\">Nifty50</span>' in CARD
    assert 'mft-nifty-value ${niftyClass}' in CARD
