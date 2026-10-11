#!/usr/bin/env python3
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("notices", ROOT / "scripts/third-party-licenses.py")
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.crate = self.root / "crate"
        self.crate.mkdir()
        self.overrides = self.root / "overrides"
        self.overrides.mkdir()
        (self.overrides / "manifest.json").write_text("{}")
        self.package = {"name": "example", "version": "1.0.0", "license": "MIT", "manifest_path": str(self.crate / "Cargo.toml")}

    def test_case_insensitive_files_and_notice_preserved(self):
        (self.crate / "license-mit").write_text("Copyright Actual Crate Author")
        (self.crate / "NOTICE").write_text("required notice")
        files = notices.license_files(self.package, self.overrides)
        self.assertEqual({p.name for p in files}, {"license-mit", "NOTICE"})
        self.assertIn("Copyright Actual Crate Author", "\n".join(p.read_text() for p in files))

    def test_missing_license_fails_instead_of_borrowing_mit(self):
        (self.overrides / "LICENSE-MIT").write_text("Copyright Unrelated Crate")
        with self.assertRaises(RuntimeError):
            notices.license_files(self.package, self.overrides)

    def test_pinned_override_checks_hash_and_crate_revision(self):
        license = self.overrides / "example-LICENSE"
        license.write_text("Copyright Actual Upstream")
        record = {"file": license.name, "sha256": hashlib.sha256(license.read_bytes()).hexdigest(), "source_revision": "abc"}
        (self.overrides / "manifest.json").write_text(json.dumps({"example@1.0.0": record}))
        vcs = self.crate / ".cargo_vcs_info.json"
        vcs.write_text(json.dumps({"git": {"sha1": "abc"}}))
        self.assertEqual(notices.license_files(self.package, self.overrides), [license])
        vcs.write_text(json.dumps({"git": {"sha1": "wrong"}}))
        with self.assertRaises(RuntimeError):
            notices.license_files(self.package, self.overrides)
        vcs.write_text(json.dumps({"git": {"sha1": "abc"}}))
        license.write_text("changed")
        with self.assertRaises(RuntimeError):
            notices.license_files(self.package, self.overrides)

    def test_r_efi_preserves_own_authors_license(self):
        self.package.update(name="r-efi", version="6.0.0")
        authors = self.crate / "AUTHORS"
        authors.write_text("AUTHORS-MIT:\nPermission is hereby granted\nCOPYRIGHT: Original authors")
        self.assertEqual(notices.license_files(self.package, self.overrides), [authors])


if __name__ == "__main__":
    unittest.main()
