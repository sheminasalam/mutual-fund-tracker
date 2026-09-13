import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'www' / 'index.html').read_text()
CSS = (ROOT / 'www' / 'style.css').read_text()
JS = (ROOT / 'www' / 'app.js').read_text()


class DarkModeTests(unittest.TestCase):
    def test_dark_mode_toggle_structure_and_persistence_logic(self):
        self.assertIn('id="themeBtn"', HTML)
        self.assertIn('class="icon-action-btn theme-toggle-btn"', HTML)
        self.assertIn('mft_theme', JS)
        self.assertIn('function getTheme()', JS)
        self.assertIn('function applyTheme(theme)', JS)
        self.assertIn('function toggleTheme()', JS)
        self.assertIn("localStorage.setItem('mft_theme'", JS)
        self.assertIn("localStorage.getItem('mft_theme')", HTML)
        self.assertIn('dataset.theme', JS)
        self.assertIn('applyTheme(getTheme())', JS)

    def test_dark_theme_covers_core_app_surfaces(self):
        self.assertIn('html[data-theme="dark"]{', CSS)
        required = [
            '.topbar', '.profile-bar', '.summary-card', '.table-wrap',
            '.modal-card', '.modal-head', '.field input', '.txn-list',
            '.choice-card', '.scheme-results', '.quote-box', '.progress-box',
            '.plus-menu', '.theme-toggle-btn', '.investor-table-card', '.detail-table'
        ]
        for selector in required:
            self.assertIn(selector, CSS)
        self.assertIn('html[data-theme="dark"] .detail-table', CSS)
        self.assertIn('html[data-theme="dark"] .detail-table th', CSS)
        self.assertIn('html[data-theme="dark"] .detail-table td', CSS)
        import_required = [
            '.import-help', '.import-summary', '.error-box', '.warning-box',
            '.import-list', '.import-list th', '.import-list td', '.info-box',
            '.import-ready', '.import-not-ready', '.identity-alert',
            '.identity-ok', '.identity-new', '.sip-confirm-section'
        ]
        for selector in import_required:
            self.assertIn(selector, CSS)
        self.assertIn('html[data-theme="dark"] .import-help', CSS)
        self.assertIn('html[data-theme="dark"] .import-list th', CSS)
        self.assertIn('html[data-theme="dark"] .import-list td', CSS)
        self.assertIn('html[data-theme="dark"] .warning-box', CSS)
        dark_css = CSS.split('html[data-theme="dark"]', 1)[-1]
        self.assertNotIn('mutual-fund-tracker-card', dark_css)


if __name__ == '__main__':
    unittest.main()
