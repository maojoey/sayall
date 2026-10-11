#!/usr/bin/env python3
"""Hermetic license-packet tests: no model, network, compiler or service required."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import local_q8_licenses as licenses


class LicenseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packet = self.root / "packet"
        shutil.copytree(licenses.PACKET, self.packet)
        catalog = json.loads((ROOT / "tests/local_stt_benchmark/candidates.json").read_text())
        self.model = next(m for m in catalog["models"] if m["id"] == "parakeet-unified-en-q8")

    def test_install_retains_all_notices_and_unresolved_status(self):
        out = self.root / "bundle"
        status = licenses.install(self.model, out, self.packet)
        self.assertEqual(status, "conversion-scope-clarification-required")
        for name in (*licenses.REQUIRED, "manifest.json"):
            self.assertEqual((out / "licenses/local-q8" / name).read_bytes(), (self.packet / name).read_bytes())
        self.assertIn("Licensed by NVIDIA Corporation under the NVIDIA Open Model License", (out / "Notice.txt").read_text())

    def test_missing_document_fails_before_creating_bundle(self):
        (self.packet / "CC-BY-4.0.txt").unlink()
        with self.assertRaises(ValueError):
            licenses.install(self.model, self.root / "bundle", self.packet)
        self.assertFalse((self.root / "bundle").exists())

    def test_modified_document_fails(self):
        (self.packet / "Notice.txt").write_text("incorrect attribution")
        with self.assertRaises(ValueError):
            licenses.validate(self.model, self.packet)

    def test_symlink_document_fails(self):
        p = self.packet / "Notice.txt"
        p.unlink()
        p.symlink_to(licenses.PACKET / "Notice.txt")
        with self.assertRaises(ValueError):
            licenses.validate(self.model, self.packet)

    def test_wrong_artifact_or_revision_fails(self):
        for field in ("sha256", "revision"):
            model = copy.copy(self.model)
            model[field] = "unexpected"
            with self.assertRaises(ValueError):
                licenses.validate(model, self.packet)

    def test_manifest_cannot_omit_notices_or_escape_packet(self):
        for name in ("Notice.txt", "../outside"):
            path = self.packet / "manifest.json"
            original = path.read_text()
            manifest = json.loads(original)
            if name in manifest["files"]:
                del manifest["files"][name]
            else:
                manifest["files"][name] = {"sha256": "invalid"}
            path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                licenses.validate(self.model, self.packet)
            path.write_text(original)


class OwnLicenseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conversion = self.root / "conversion"
        (self.conversion / "licenses").mkdir(parents=True)
        self.runtime_source = self.root / "runtime"
        (self.runtime_source / "ggml").mkdir(parents=True)
        (self.runtime_source / "LICENSE").write_text("synthetic runtime license")
        (self.runtime_source / "ggml/LICENSE").write_text("synthetic ggml license")
        self.model = json.loads((ROOT / "tests/local_stt_benchmark/own-q8.json").read_text())
        self.source_pin = json.loads((ROOT / "tests/local_stt_benchmark/nvidia-source.json").read_text())
        self.runtime = json.loads((ROOT / "tests/local_stt_benchmark/candidates.json").read_text())["runtimes"]["transcribe"]
        originals = {
            "NVIDIA-OPEN-MODEL-LICENSE.md": licenses.PACKET / "NVIDIA-OPEN-MODEL-LICENSE.md",
            "NVIDIA-TRUSTWORTHY-AI-TERMS.md": licenses.PACKET / "NVIDIA-TRUSTWORTHY-AI-TERMS.md",
            "transcribe-LICENSE": self.runtime_source / "LICENSE",
            "ggml-LICENSE": self.runtime_source / "ggml/LICENSE",
        }
        for name, source in originals.items():
            shutil.copyfile(source, self.conversion / "licenses" / name)
        (self.conversion / "Notice.txt").write_text("Licensed by NVIDIA Corporation under the NVIDIA Open Model License\n" + self.source_pin["revision"])
        self.provenance = {
            "schema_version": 1, "source": self.source_pin, "runtime": self.runtime,
            "q8": {"file": "models/" + self.model["file"], "bytes": self.model["bytes"], "sha256": self.model["sha256"]},
            "f32": {"sha256": self.model["f32_sha256"]},
            "licenses": {"licenses/" + name: hashlib.sha256(source.read_bytes()).hexdigest() for name, source in originals.items()},
            "notice_sha256": hashlib.sha256((self.conversion / "Notice.txt").read_bytes()).hexdigest(),
        }
        self.save()

    def save(self):
        (self.conversion / "provenance.json").write_text(json.dumps(self.provenance))

    def validate(self):
        return licenses.validate_own(self.conversion, self.model, self.runtime, self.source_pin, self.runtime_source)

    def test_own_packet_installs_without_converter_cc_license(self):
        out = self.root / "bundle"
        licenses.install_own(self.conversion, self.model, self.runtime, self.source_pin, self.runtime_source, out)
        self.assertTrue((out / "Notice.txt").is_file())
        self.assertTrue((out / "licenses/own-q8/provenance.json").is_file())
        self.assertFalse((out / "licenses/own-q8/CC-BY-4.0.txt").exists())
        public = json.loads((out / "licenses/own-q8/provenance.json").read_text())
        self.assertEqual(public["source"], self.source_pin)
        self.assertEqual(public["runtime"], {"project": "transcribe.cpp", "revision": self.runtime["revision"]})
        self.assertEqual(public["conversion_record_sha256"], hashlib.sha256((self.conversion / "provenance.json").read_bytes()).hexdigest())
        self.assertNotIn("argv", public)
        self.assertNotIn("handy", (out / "Notice.txt").read_text().lower())
        self.assertNotIn("handy", json.dumps(public).lower())
        for relative in self.provenance["licenses"]:
            self.assertEqual((self.conversion / relative).read_bytes(), (out / "licenses/own-q8" / Path(relative).name).read_bytes())

    def test_changed_license_is_rejected_even_if_local_provenance_rehashed(self):
        relative = "licenses/NVIDIA-OPEN-MODEL-LICENSE.md"
        (self.conversion / relative).write_text("wrong license")
        self.provenance["licenses"][relative] = hashlib.sha256(b"wrong license").hexdigest()
        self.save()
        with self.assertRaises(ValueError):
            self.validate()

    def test_wrong_artifact_and_source_are_rejected(self):
        original = copy.deepcopy(self.provenance)
        for key in ("source", "runtime", "q8", "f32"):
            self.provenance = copy.deepcopy(original)
            self.provenance[key] = {}
            self.save()
            with self.assertRaises(ValueError):
                self.validate()

    def test_notice_and_inventory_are_required(self):
        self.provenance["licenses"]["../outside"] = "wrong"
        self.save()
        with self.assertRaises(ValueError):
            self.validate()
        del self.provenance["licenses"]["../outside"]
        self.save()
        (self.conversion / "Notice.txt").write_text("missing attribution")
        with self.assertRaises(ValueError):
            self.validate()


if __name__ == "__main__":
    unittest.main()
