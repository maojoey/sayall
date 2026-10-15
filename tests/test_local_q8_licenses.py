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
        self.model = json.loads((ROOT / "scripts/local-q8/own-q8.json").read_text())
        self.source_pin = json.loads((ROOT / "scripts/local-q8/nvidia-source.json").read_text())
        self.runtime = json.loads((ROOT / "scripts/local-q8/runtime.json").read_text())
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

    def test_missing_or_symlinked_documents_fail_before_install(self):
        for relative in (*self.provenance["licenses"], "Notice.txt"):
            with self.subTest(document=relative):
                path = self.conversion / relative
                original = path.read_bytes()
                path.unlink()
                out = self.root / "rejected-bundle"
                with self.assertRaises(ValueError):
                    licenses.install_own(self.conversion, self.model, self.runtime,
                                         self.source_pin, self.runtime_source, out)
                self.assertFalse(out.exists())
                target = self.root / "outside-document"
                target.write_bytes(original)
                path.symlink_to(target)
                with self.assertRaises(ValueError):
                    self.validate()
                path.unlink()
                path.write_bytes(original)

    def test_streaming_hash_matches_artifact_digest(self):
        artifact = self.root / "artifact"
        data = b"synthetic artifact" * 100000
        artifact.write_bytes(data)
        self.assertEqual(licenses.sha256(artifact), hashlib.sha256(data).hexdigest())

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
