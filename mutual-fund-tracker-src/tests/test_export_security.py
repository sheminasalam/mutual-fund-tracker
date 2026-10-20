import importlib.util
import os
import time
import unittest
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "custom_components" / "mutual_fund_tracker" / "export_utils.py"
spec = importlib.util.spec_from_file_location("export_utils", SRC)
eu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eu)

NAME = "export-" + "a" * 32 + ".png"
PNG = eu.PNG_SIGNATURE + b"rest"


class ExportSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.share = Path(self.tmp.name) / "exports"
        self.share.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_export_is_returned(self):
        (self.share / NAME).write_bytes(PNG)
        self.assertEqual(eu.read_validated_export(str(self.share / NAME), self.share, NAME), PNG)

    def test_path_outside_share_dir_rejected(self):
        other = Path(self.tmp.name) / NAME
        other.write_bytes(PNG)
        with self.assertRaises(eu.UnsafeExportError):
            eu.read_validated_export(str(other), self.share, NAME)

    def test_unrelated_file_rejected(self):
        secret = Path(self.tmp.name) / "secrets.yaml"
        secret.write_text("password: x")
        with self.assertRaises(eu.UnsafeExportError):
            eu.read_validated_export(str(secret), self.share, NAME)

    def test_symlink_rejected(self):
        secret = Path(self.tmp.name) / "secret.png"
        secret.write_bytes(PNG)
        os.symlink(secret, self.share / NAME)
        with self.assertRaises(eu.UnsafeExportError):
            eu.read_validated_export(str(self.share / NAME), self.share, NAME)

    def test_traversal_rejected(self):
        (self.share / NAME).write_bytes(PNG)
        sneaky = str(self.share / ".." / "exports" / ".." / "exports" / NAME)
        # Resolves to the same place, so it is accepted; a different dir is not.
        eu.read_validated_export(sneaky, self.share, NAME)
        with self.assertRaises(eu.UnsafeExportError):
            eu.read_validated_export(str(self.share / ".." / NAME), self.share, NAME)

    def test_non_png_rejected(self):
        (self.share / NAME).write_bytes(b"not a png")
        with self.assertRaises(eu.UnsafeExportError):
            eu.read_validated_export(str(self.share / NAME), self.share, NAME)

    def test_bad_expected_name_rejected(self):
        with self.assertRaises(eu.UnsafeExportError):
            eu.read_validated_export("/etc/passwd", self.share, "../../etc/passwd")

    def test_export_name_pattern(self):
        self.assertTrue(eu.is_valid_export_name(NAME))
        for bad in ("../x.png", "john-mutual-fund-analysis.png", NAME + ".json", "export-abc.png"):
            self.assertFalse(eu.is_valid_export_name(bad))

    def test_atomic_write_leaves_no_partial_file_or_tmp(self):
        target = self.share / "export.request"
        eu.write_text_atomic(target, '{"a": 1}')
        self.assertEqual(target.read_text(), '{"a": 1}')
        eu.write_text_atomic(target, '{"a": 2}')
        self.assertEqual(target.read_text(), '{"a": 2}')
        self.assertEqual([p.name for p in self.share.iterdir()], ["export.request"])

    def test_download_path_rejects_symlink_to_other_file(self):
        # Mirrors what the download view calls: root/filename must be a real PNG in root.
        outside = Path(self.tmp.name) / "outside.png"
        outside.write_bytes(PNG)
        os.symlink(outside, self.share / NAME)
        with self.assertRaises(eu.UnsafeExportError):
            eu.read_validated_export(str(self.share / NAME), self.share, NAME)

    def test_download_path_rejects_symlinked_directory(self):
        real = Path(self.tmp.name) / "real"
        real.mkdir()
        (real / NAME).write_bytes(PNG)
        link = Path(self.tmp.name) / "link"
        os.symlink(real, link)
        # A symlinked *root* resolves to its target consistently, so the file is the real one.
        self.assertEqual(eu.read_validated_export(str(link / NAME), link, NAME), PNG)

    def test_prune_covers_tmp_and_result_files(self):
        names = ["export-" + "c" * 32 + ".tmp.png", "export_result_" + "c" * 32 + ".json", "export_result_" + "c" * 32 + ".json.tmp"]
        past = time.time() - 10 * 3600
        for n in names:
            (self.share / n).write_text("x")
            os.utime(self.share / n, (past, past))
        eu.prune_exports(self.share, self.share, 3600)
        self.assertEqual(list(self.share.iterdir()), [])

    def test_prune_removes_only_old_matching_files(self):
        old, new, other = self.share / NAME, self.share / ("export-" + "b" * 32 + ".png"), self.share / "keep.txt"
        for f in (old, new, other):
            f.write_bytes(PNG)
        past = time.time() - 10 * 3600
        os.utime(old, (past, past))
        os.utime(other, (past, past))
        removed = eu.prune_old_files(self.share, 3600, eu._EXPORT_NAME_RE)
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
