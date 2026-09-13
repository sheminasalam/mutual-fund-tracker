import hashlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT.parent / "custom_components" / "mutual_fund_tracker" / "entity_identity.py"
spec = importlib.util.spec_from_file_location("entity_identity_under_test", MOD_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
investor_entity_key = module.investor_entity_key


class EntityIdentityTests(unittest.TestCase):
    def test_pan_identity_is_deterministic_and_not_profile_id_based(self):
        expected = hashlib.sha256(b"ZZZPA1234Z").hexdigest()[:16]
        a = {"id": 7, "name": "Bruce Wayne", "pan": "zzzpa1234z"}
        b = {"id": 7, "name": "Ananya Mehta", "pan": " ZZZPA1234Z "}
        self.assertEqual(investor_entity_key(a), expected)
        self.assertEqual(investor_entity_key(b), expected)

    def test_different_pans_have_different_keys(self):
        a = {"id": 7, "pan": "ABCDE1234F"}
        b = {"id": 7, "pan": "PQRSX5678K"}
        self.assertNotEqual(investor_entity_key(a), investor_entity_key(b))

    def test_profile_id_reuse_does_not_change_key(self):
        old = {"id": 7, "pan": "ABCDE1234F", "name": "Bruce Wayne"}
        new = {"id": 7, "pan": "PQRSX5678K", "name": "Test Investor - Ananya Mehta"}
        self.assertNotEqual(investor_entity_key(old), investor_entity_key(new))

    def test_missing_pan_has_legacy_fallback(self):
        self.assertEqual(investor_entity_key({"id": 42}), "legacy_42")

    def test_whitespace_and_case_are_normalized(self):
        a = {"id": 7, "pan": " abcde1234f "}
        b = {"id": 99, "pan": "ABCDE1234F"}
        self.assertEqual(investor_entity_key(a), investor_entity_key(b))


if __name__ == "__main__":
    unittest.main()


def test_nav_refresh_error_entity_source_is_declared():
    path = ROOT.parent / 'custom_components' / 'mutual_fund_tracker' / 'binary_sensor.py'
    text = path.read_text(encoding='utf-8')
    assert 'MutualFundNAVRefreshErrorBinarySensor' in text
    assert 'nav_refresh_error' in text
    assert 'NAV Refresh Error' in text
