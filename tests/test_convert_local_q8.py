#!/usr/bin/env python3
"""Hermetic converter archive-boundary tests; no torch/model/download required."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("convert_local_q8", ROOT / "scripts/convert-local-q8.py")
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


class ExtractionTests(unittest.TestCase):
    def extract(self, members, maximum=16):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "source.tar"
            with tarfile.open(archive, "w") as tar:
                for name, kind in members:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.size = 4 if kind == tarfile.REGTYPE else 0
                    entry.linkname = "/sensitive/path" if kind == tarfile.SYMTYPE else ""
                    tar.addfile(entry, io.BytesIO(b"data") if entry.size else None)
            with tarfile.open(archive) as tar:
                target = root / "chosen-output"
                converter.extract_member(tar, "model_weights.ckpt", target, maximum)
                self.assertEqual(target.read_bytes(), b"data")
                with self.assertRaises(FileExistsError):
                    converter.extract_member(tar, "model_weights.ckpt", target, maximum)

    def test_extracts_to_chosen_path_not_member_path(self):
        self.extract([("../../model_weights.ckpt", tarfile.REGTYPE)])

    def test_rejects_duplicates(self):
        with self.assertRaises(ValueError):
            self.extract([("a/model_weights.ckpt", tarfile.REGTYPE), ("b/model_weights.ckpt", tarfile.REGTYPE)])

    def test_rejects_symlinks_directories_and_missing(self):
        for members in ([], [("model_weights.ckpt", tarfile.SYMTYPE)], [("model_weights.ckpt", tarfile.DIRTYPE)]):
            with self.assertRaises(ValueError):
                self.extract(members)

    def test_rejects_oversized(self):
        with self.assertRaises(ValueError):
            self.extract([("model_weights.ckpt", tarfile.REGTYPE)], maximum=3)


if __name__ == "__main__":
    unittest.main()
