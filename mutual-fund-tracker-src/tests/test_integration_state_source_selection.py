import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class IntegrationStateSelectionSourceTests(unittest.TestCase):
    def test_coordinator_selects_newest_snapshot(self):
        source = (ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / '__init__.py').read_text(encoding='utf-8')
        self.assertIn('candidates = []', source)
        self.assertIn('return max(candidates, key=freshness)', source)
        self.assertIn('state_revision', source)

if __name__ == '__main__':
    unittest.main()
