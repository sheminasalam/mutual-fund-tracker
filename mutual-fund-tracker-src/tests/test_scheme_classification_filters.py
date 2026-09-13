from pathlib import Path
import unittest

import app

ROOT = Path(__file__).resolve().parents[1]


class SchemeClassificationFilterTests(unittest.TestCase):
    def test_direct_growth_small_cap_classification(self):
        c = app.classify_scheme_metadata(
            "quant Small Cap Fund - Growth Option - Direct Plan",
            "Equity Scheme - Small Cap Fund",
            "Open Ended Schemes",
        )
        self.assertEqual(c["plan_type"], "Direct")
        self.assertEqual(c["option_type"], "Growth")
        self.assertEqual(c["asset_class"], "Equity")
        self.assertEqual(c["scheme_category"], "Small Cap Fund")
        self.assertEqual(c["structure_type"], "Open-Ended")
        self.assertEqual(c["management_style"], "Active")

    def test_regular_idcw_debt_liquid_classification(self):
        c = app.classify_scheme_metadata(
            "HDFC Liquid Fund - Regular Plan - IDCW",
            "Debt Scheme - Liquid Fund",
            "Open Ended Schemes",
        )
        self.assertEqual(c["plan_type"], "Regular")
        self.assertEqual(c["option_type"], "IDCW / Dividend")
        self.assertEqual(c["asset_class"], "Debt")
        self.assertEqual(c["scheme_category"], "Liquid Fund")
        self.assertEqual(c["horizon_goal"], "Overnight / Liquid")

    def test_index_fund_is_passive(self):
        c = app.classify_scheme_metadata(
            "Nifty 50 Index Fund - Direct Plan - Growth",
            "Other Scheme - Index Funds",
            "Open Ended Schemes",
        )
        self.assertEqual(c["management_style"], "Passive / Index / ETF")
        self.assertEqual(c["scheme_category"], "Index Funds")

    def test_elss_is_horizon_goal(self):
        c = app.classify_scheme_metadata(
            "Axis ELSS Tax Saver Fund - Direct Plan - Growth",
            "Equity Scheme - ELSS",
            "Open Ended Schemes",
        )
        self.assertEqual(c["asset_class"], "Equity")
        self.assertEqual(c["horizon_goal"], "ELSS (Tax Saving)")

    def test_filter_ui_exposes_supported_classification_controls(self):
        html = (ROOT / "www" / "index.html").read_text()
        for control in ["planFilter", "optionFilter", "categoryFilter"]:
            self.assertIn(f'id="{control}"', html)
        for removed in ["structureFilter", "assetFilter", "managementFilter", "goalFilter"]:
            self.assertNotIn(f'id="{removed}"', html)
        category = html.split('id="categoryFilter"', 1)[1].split('</select>', 1)[0]
        self.assertNotIn('multiple', category)

    def test_classification_fields_exposed_in_portfolio_rows(self):
        src = (ROOT / "app.py").read_text()
        for key in ["plan_type", "option_type", "structure_type", "asset_class", "scheme_category", "management_style", "horizon_goal"]:
            self.assertTrue(f"'{key}':" in src or f'"{key}":' in src)

    def test_scheme_metadata_is_persisted_on_canonical_scheme(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            db = app.Database(Path(td) / 'mft.db')
            scheme_id = db.resolve_or_create_scheme('125497', 'SBI Small Cap Fund - Direct Plan - Growth', 'INF200K01T51', True)
            self.assertTrue(scheme_id)
            self.assertTrue(db.update_scheme_metadata('125497', {
                'fundHouse': 'SBI Mutual Fund',
                'schemeType': 'Open Ended Schemes',
                'schemeCategory': 'Equity Scheme - Small Cap Fund',
                'isinGrowth': 'INF200K01T51',
                'isinDivReinvestment': None,
            }))
            conn = db.conn()
            try:
                row = conn.execute('SELECT scheme_category, scheme_type, fund_house FROM schemes WHERE id=?', (scheme_id,)).fetchone()
            finally:
                conn.close()
            self.assertEqual(row['scheme_category'], 'Equity Scheme - Small Cap Fund')
            self.assertEqual(row['scheme_type'], 'Open Ended Schemes')
            self.assertEqual(row['fund_house'], 'SBI Mutual Fund')


if __name__ == '__main__':
    unittest.main()
