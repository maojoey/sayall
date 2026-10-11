import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sayall_local_benchmark", HERE / "benchmark.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
import prepare
import compare
import incremental


class LocalBenchmarkTests(unittest.TestCase):
    def fixture(self, root):
        wav = root / "input.wav"
        with wave.open(str(wav), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\x01\x00" * 16000)
        digest = b.sha256(wav)
        clip = {"id": "fixture", "source": {"type": "wav", "path": wav.name, "wav_sha256": digest},
                "language": "en", "expected_transcript": "private reference", "expect_no_speech": False,
                "provenance": "synthetic unit test", "license": "test-only", "sha256": digest}
        path = root / "manifest.json"
        path.write_text(json.dumps({"schema_version": 1, "corpus_id": "unit", "clips": [clip]}))
        return path, clip

    def executable(self, root, body):
        path = root / "fake-adapter"
        path.write_text("#!/usr/bin/python3\nimport sys,json,time,os\n" + body)
        path.chmod(0o700)
        return path

    def ready(self):
        return {"version": 1, "type": "ready", "backend": "cpu", "load_ms": 1, "peak_rss_bytes": 1024}

    def result(self, text="private hypothesis", code=0):
        return {"version": 1, "type": "result", "code": code, "decode_ms": 2,
                "cpu_ms": 4, "peak_rss_bytes": 2048, "text": text, "request_to_result_ms": 3}

    def test_incremental_metrics_are_finite_bounded_and_content_free(self):
        values = {"stop_to_final_ms": 250, "max_feed_ms": 200, "max_backlog_ms": 100, "feeds": 30}
        self.assertEqual(incremental.stream_metrics({**values, "text": "private"}), values)
        for key, value in [("feeds", 1.5), ("feeds", True), ("feeds", 3001),
                           ("max_feed_ms", -1), ("stop_to_final_ms", float("nan")),
                           ("max_backlog_ms", float("inf"))]:
            with self.assertRaises(ValueError):
                incremental.stream_metrics({**values, key: value})
        with self.assertRaises(ValueError):
            incremental.stream_metrics({})

    def test_buffered_mode_is_explicit_and_private_pipe_is_still_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            exe = self.executable(root,
                "assert len(sys.argv)==4 and sys.argv[3]=='buffered-default'\n"
                + f"print({json.dumps(json.dumps(self.ready()))},flush=True)\n"
                + f"for line in sys.stdin: print({json.dumps(json.dumps(self.result()))},flush=True)\n")
            adapter = b.Adapter(exe, root / "model", 4, 2, mode="buffered-default")
            try:
                self.assertEqual(adapter.decode(root / "audio.pcm")["code"], 0)
            finally:
                adapter.close()

    def test_frame_validation_is_strict_and_finite(self):
        self.assertTrue(b.frame_valid(self.ready(), "ready"))
        self.assertTrue(b.frame_valid(self.result(), "result"))
        for field, value in [("decode_ms", float("nan")), ("cpu_ms", -1), ("code", True),
                             ("text", None), ("version", 2), ("version", True), ("peak_rss_bytes", "2048")]:
            frame = self.result()
            frame[field] = value
            self.assertFalse(b.frame_valid(frame, "result"))
        self.assertFalse(b.frame_valid({**self.ready(), "backend": "auto"}, "ready"))

    def test_scoring_drops_transcript_and_counts_failed_speech(self):
        with tempfile.TemporaryDirectory() as td:
            _, clip = self.fixture(Path(td))
            row = b.score(clip, self.result("", 5))
            row["audio_duration_ms"] = 1000
            self.assertEqual(row["classification"], "provider_error")
            self.assertEqual(row["word_edits"], 2)
            self.assertNotIn("text", row)
            self.assertEqual(b.summarize([row])["wer"], 1)
            self.assertEqual(b.summarize([row])["failed_cases"], 1)
            self.assertIsNone(b.summarize([row])["request_to_result_p50_ms"])
            clip["expect_no_speech"] = True
            clip["expected_transcript"] = ""
            hallucinated = b.score(clip, self.result("unexpected speech"))
            hallucinated["audio_duration_ms"] = 1000
            self.assertEqual(b.summarize([hallucinated])["false_speech_cases"], 1)

    def test_quantiles_and_realtime_factor_use_speech_only(self):
        self.assertIsNone(b.percentile([], .95))
        self.assertEqual(b.percentile([3, 1, 2], .5), 2)
        with tempfile.TemporaryDirectory() as td:
            _, clip = self.fixture(Path(td))
            row = b.score(clip, self.result())
            row["audio_duration_ms"] = 1000
            silent = {**row, "expect_no_speech": True, "classification": "expected_no_speech", "decode_ms": 999}
            summary = b.summarize([row, silent])
            self.assertEqual(summary["realtime_factor"], .002)
            self.assertEqual(summary["latency_success_samples"], 1)

    def test_manifest_rejects_unsafe_paths_ids_and_unpinned_human_audio(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path, clip = self.fixture(root)
            b.load_manifest(path)
            structured = {**clip, "provenance": {"dataset": "synthetic", "revision": "test"}}
            path.write_text(json.dumps({"clips": [structured]}))
            b.load_manifest(path)
            for changed in [{**clip, "id": "../escape"}, {**clip, "sha256": "runtime-generated"},
                            {**clip, "language": "de"}, {**clip, "expect_no_speech": "false"}]:
                path.write_text(json.dumps({"clips": [changed]}))
                with self.assertRaises(ValueError):
                    b.load_manifest(path)
            path.write_text(json.dumps({"clips": [clip, clip]}))
            with self.assertRaises(ValueError):
                b.load_manifest(path)

    def test_adapter_has_deadline_and_strict_frames(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for body in ["time.sleep(10)\n", "print('{}',flush=True)\ntime.sleep(10)\n",
                         "print('x'*1100000,flush=True)\ntime.sleep(10)\n"]:
                exe = self.executable(root, body)
                with self.assertRaises((TimeoutError, ValueError)):
                    b.Adapter(exe, root / "model", 1, .1)

    def test_adapter_does_not_inherit_provider_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            exe = self.executable(root, "assert 'DEEPGRAM_API_KEY' not in os.environ\n" +
                                  f"print({json.dumps(self.ready())!r},flush=True)\n" +
                                  f"for line in sys.stdin: print({json.dumps(self.result())!r},flush=True)\n")
            with patch.dict(os.environ, {"DEEPGRAM_API_KEY": "synthetic-never-forward"}):
                adapter = b.Adapter(exe, root / "model", 1, 1)
                try:
                    self.assertEqual(adapter.decode(root / "pcm")["code"], 0)
                finally:
                    adapter.close()

    def test_end_to_end_report_contains_scores_not_transcripts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest, _ = self.fixture(root)
            exe = self.executable(root, f"print({json.dumps(self.ready())!r},flush=True)\n" +
                                  f"for line in sys.stdin: print({json.dumps(self.result())!r},flush=True)\n")
            exe.rename(root / "test-adapter")
            weights = root / "models"
            weights.mkdir()
            model = weights / "weights.bin"
            model.write_bytes(b"test")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"runtimes": {"test": {"revision": "pinned"}}, "models": [{
                "id": "test", "runtime": "test", "file": "weights.bin", "bytes": 4, "sha256": b.sha256(model)}]}))
            library = root / "library"
            library.write_bytes(b"library")
            identity = {"revision": "pinned", "adapter_sha256": b.sha256(root / "test-adapter"),
                        "adapter_source_sha256": b.sha256(HERE / "adapter.cpp"),
                        "runtime_files": {"library": b.sha256(library)}}
            (root / "test-build-identity.json").write_text(json.dumps(identity))
            output = root / "report.json"
            args = ["--directory", str(root), "--manifest", str(manifest), "--output", str(output), "--runs", "2"]
            with patch.object(b, "CATALOG", catalog), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(b.main(args), 0)
            serialized = output.read_text()
            self.assertNotIn("private hypothesis", serialized)
            self.assertNotIn("private reference", serialized)
            report = json.loads(serialized)
            self.assertEqual(report["models"][0]["summary"]["cases"], 2)
            self.assertEqual(report["models"][0]["status"], "complete")
            library.write_bytes(b"tampered")
            with patch.object(b, "CATALOG", catalog), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(b.main(args), 1)
            self.assertEqual(json.loads(output.read_text())["models"][0]["status"], "failed")

    def test_download_verifies_size_and_hash_without_overwriting_existing_partial(self):
        data = b"test-model"
        model = {"id": "test", "file": "test.bin", "repository": "test/repo", "revision": "pinned",
                 "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            partial = root / "test.bin.part"
            partial.write_bytes(b"previous-download")
            with self.assertRaises(FileExistsError):
                prepare.download(model, root)
            self.assertEqual(partial.read_bytes(), b"previous-download")
            partial.unlink()
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaises(ValueError):
                    prepare.download(model, root)
            self.assertFalse(partial.exists())
            self.assertFalse((root / "test.bin").exists())
            with patch("urllib.request.urlopen", return_value=io.BytesIO(data)), contextlib.redirect_stdout(io.StringIO()):
                prepare.download(model, root)
            self.assertEqual((root / "test.bin").read_bytes(), data)

    def test_comparison_does_not_rank_incomplete_runs(self):
        report = {"corpus_id": "unit", "models": [{"id": "test", "status": "failed",
                  "summary": {"cases": 1}, "expected_cases": 3}]}
        text = compare.render(report)
        self.assertIn("INCOMPLETE—do not rank", text)
        self.assertNotIn("0.00%", text)

    def test_catalog_has_three_unique_pinned_candidates(self):
        catalog = json.loads(prepare.CATALOG.read_text())
        self.assertEqual(len({m["id"] for m in catalog["models"]}), 3)
        for model in catalog["models"]:
            self.assertRegex(model["revision"], r"^[0-9a-f]{40}$")
            self.assertRegex(model["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(model["bytes"], 0)
            self.assertIn(model["runtime"], catalog["runtimes"])


if __name__ == "__main__":
    unittest.main()
