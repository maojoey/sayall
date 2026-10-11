#!/usr/bin/env python3
"""Offline, privacy-safe model comparison; not a native-host release qualifier."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import wave

from prepare import CATALOG, sha256

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sayall_provider_benchmark", HERE.parent / "deepgram_benchmark/benchmark.py")
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)
MAX_FRAME = 1024 * 1024
COUNTS = ("word_edits", "reference_words", "char_edits", "reference_chars")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def finite_positive(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be positive and finite")
    return result


def frame_valid(frame: object, expected: str) -> bool:
    if (not isinstance(frame, dict) or type(frame.get("version")) is not int or
            frame["version"] != 1 or frame.get("type") != expected):
        return False
    fields = ("load_ms", "peak_rss_bytes") if expected == "ready" else ("decode_ms", "cpu_ms", "peak_rss_bytes")
    for key in fields:
        value = frame.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return False
    if expected == "ready":
        return frame.get("backend") == "cpu"
    return type(frame.get("code")) is int and isinstance(frame.get("text"), str)


class Adapter:
    def __init__(self, executable: Path, model: Path, threads: int, timeout: float, *, mode: str = "batch"):
        self.timeout = timeout
        self.buffer = bytearray()
        self.started = time.perf_counter()
        self.process = subprocess.Popen(
            [str(executable), str(model), str(threads)] + ([] if mode == "batch" else [mode]), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        os.set_blocking(self.process.stdout.fileno(), False)
        try:
            self.ready = self.read("ready")
            self.startup_ms = (time.perf_counter() - self.started) * 1000
        except BaseException:
            self.close()
            raise

    def read(self, expected: str) -> dict:
        deadline = time.monotonic() + self.timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while True:
                newline = self.buffer.find(b"\n")
                if newline >= 0:
                    line = bytes(self.buffer[:newline])
                    del self.buffer[:newline + 1]
                    if len(line) > MAX_FRAME:
                        raise ValueError("oversized adapter frame")
                    frame = json.loads(line)
                    if not frame_valid(frame, expected):
                        raise ValueError("invalid adapter frame")
                    return frame
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("adapter deadline")
                chunk = os.read(self.process.stdout.fileno(), min(65536, MAX_FRAME + 1 - len(self.buffer)))
                if not chunk:
                    raise ValueError("adapter closed output")
                self.buffer.extend(chunk)
                if len(self.buffer) > MAX_FRAME:
                    raise ValueError("oversized adapter frame")

    def decode(self, path: Path) -> dict:
        value = str(path.resolve())
        if "\n" in value or "\r" in value or len(value.encode()) > 4096:
            raise ValueError("unsupported input path")
        started = time.perf_counter()
        self.process.stdin.write((value + "\n").encode())
        self.process.stdin.flush()
        result = self.read("result")
        result["request_to_result_ms"] = (time.perf_counter() - started) * 1000
        return result

    def close(self) -> None:
        # Kill the complete process group even when the adapter leader exited.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=10)
        self.process.stdin.close()
        self.process.stdout.close()


def load_manifest(path: Path) -> tuple[dict, str]:
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("manifest exceeds size limit")
    manifest = json.loads(raw)
    clips = manifest.get("clips", [])
    if not clips or len(clips) > 10000:
        raise ValueError("invalid clip count")
    seen = set()
    for clip in clips:
        identifier = clip.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}", identifier) or identifier in seen:
            raise ValueError("unsafe or duplicate clip identifier")
        seen.add(identifier)
        if type(clip.get("expect_no_speech")) is not bool:
            raise ValueError("missing speech expectation")
        if not clip.get("language", "").lower().startswith("en"):
            raise ValueError("only English is qualified")
        provenance = clip.get("provenance")
        if not isinstance(provenance, (str, dict)) or not provenance:
            raise ValueError("missing fixture provenance")
        for key in ("license", "sha256"):
            if not isinstance(clip.get(key), str) or not clip[key]:
                raise ValueError("missing fixture license or integrity")
        provider.reference_for(clip, "verbatim")
        source = clip["source"]
        if source["type"] == "wav":
            if not re.fullmatch(r"[0-9a-f]{64}", clip["sha256"]):
                raise ValueError("human audio requires a pinned hash")
            root = path.parent.resolve()
            wav = (root / source["path"]).resolve(strict=True)
            wav.relative_to(root)
            if not wav.is_file() or wav.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("audio must be a regular file within the size limit")
            with wave.open(str(wav), "rb") as audio:
                seconds = audio.getnframes() / audio.getframerate()
        elif source["type"] in ("silence", "noise"):
            seconds = source["duration_seconds"]
        else:
            raise ValueError("use human WAV fixtures or deterministic no-speech cases")
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0.3 <= seconds <= 300:
            raise ValueError("audio duration outside product limits")
    if not any(not c["expect_no_speech"] for c in clips):
        raise ValueError("corpus needs a speech clip for warmup")
    return manifest, hashlib.sha256(raw).hexdigest()


def score(clip: dict, result: dict) -> dict:
    hypothesis = result["text"] if result["code"] == 0 else ""
    status = "error" if result["code"] else ("success" if hypothesis.strip() else "no_speech")
    counts = provider.error_counts(provider.reference_for(clip, "verbatim"), hypothesis)
    return {"id": clip["id"], "condition": clip.get("condition", "clean"),
            "expect_no_speech": clip["expect_no_speech"],
            "classification": provider.classify(clip["expect_no_speech"], {"status": status, "text": hypothesis}),
            "adapter_code": result["code"], **counts,
            **{k: result[k] for k in ("decode_ms", "request_to_result_ms", "cpu_ms", "peak_rss_bytes")}}


def summarize(rows: list[dict]) -> dict:
    speech = [row for row in rows if not row["expect_no_speech"]]
    # Failed/missing speech counts as deletions, never dropped from accuracy.
    quality = provider.aggregate([{k: row[k] for k in COUNTS} for row in speech])
    successes = [row for row in speech if row["classification"] == "speech_ok"]
    latencies = [row["request_to_result_ms"] for row in successes]
    duration = sum(row["audio_duration_ms"] for row in successes)
    return {**quality, "cases": len(rows), "speech_cases": len(speech),
            "failed_cases": sum(row["classification"] not in ("speech_ok", "expected_no_speech") for row in rows),
            "false_speech_cases": sum(row["classification"] == "false_speech" for row in rows),
            "latency_success_samples": len(successes),
            "request_to_result_p50_ms": percentile(latencies, .5),
            "request_to_result_p95_ms": percentile(latencies, .95),
            "decode_p50_ms": percentile([row["decode_ms"] for row in successes], .5),
            "realtime_factor": sum(row["decode_ms"] for row in successes) / duration if duration else None,
            "peak_process_rss_bytes": max((row["peak_rss_bytes"] for row in rows), default=0)}


def machine() -> dict:
    cpu = platform.processor()
    path = Path("/proc/cpuinfo")
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    memory = {}
    if Path("/proc/meminfo").exists():
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                memory[key + "_bytes"] = int(value.split()[0]) * 1024
    return {"os": platform.system(), "kernel": platform.release(), "architecture": platform.machine(),
            "cpu": cpu, "logical_cpus": os.cpu_count(), "memory_at_start": memory,
            "load_average_at_start": list(os.getloadavg())}


def save(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("dist/local-stt-benchmark"))
    parser.add_argument("--manifest", type=Path, default=HERE.parent / "deepgram_benchmark/manifest-v3.json")
    parser.add_argument("--models", nargs="+", help="candidate IDs; defaults to all in catalog order")
    parser.add_argument("--runs", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--threads", type=int, choices=range(1, 65), default=4)
    parser.add_argument("--timeout", type=finite_positive, default=120)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=Path("dist/local-stt-benchmark/report.json"))
    args = parser.parse_args(argv)
    catalog = json.loads(CATALOG.read_text())
    selected = args.models or [m["id"] for m in catalog["models"]]
    if len(set(selected)) != len(selected) or set(selected) - {m["id"] for m in catalog["models"]}:
        parser.error("unknown or duplicate model ID")
    manifest, manifest_hash = load_manifest(args.manifest)
    report = {"schema_version": 1, "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "scope": "CPU engine-only offline ASR; no VAD, cleanup, capture, or delivery",
              "timing_definition": "request_to_result includes PCM read/conversion + inference + private IPC; decode_ms is native inference/text extraction. Not streaming stop-to-final.",
              "cold_definition": "one fresh-process load and first inference per model; integrity hashing immediately before launch normally warms the model file page cache; not disk-cold",
              "memory_definition": "process-lifetime peak RSS, includes weights/activations/runtime; not per-clip allocation or GPU memory",
              "network_isolation": "not enforced by harness; adapters use local native libraries only",
              "manifest_sha256": manifest_hash, "corpus_id": manifest["corpus_id"],
              "catalog_sha256": sha256(CATALOG), "harness_sha256": sha256(Path(__file__)),
              "scorer_sha256": sha256(HERE.parent / "deepgram_benchmark/benchmark.py"),
              "hardware": machine(), "threads": args.threads, "runs": args.runs, "seed": args.seed,
              "quality_note": "Micro-averaged normalized WER/CER; repeated clips are not independent speakers. Punctuation/case are ignored. No formatting or semantic fidelity score.",
              "models": []}
    directory = args.directory.resolve()
    with tempfile.TemporaryDirectory(prefix="sayall-local-benchmark-") as td:
        audio = {}
        for clip in manifest["clips"]:
            wav, pcm = provider.make_audio(clip, Path(td), args.manifest.parent)
            digest = sha256(wav)
            if clip["sha256"] != "runtime-generated" and digest != clip["sha256"]:
                raise ValueError("canonical fixture hash mismatch")
            audio[clip["id"]] = (wav.with_suffix(".pcm"), len(pcm) / 32, digest)
        first = next(c for c in manifest["clips"] if not c["expect_no_speech"])
        schedule = [(run, clip) for run in range(1, args.runs + 1) for clip in manifest["clips"]]
        random.Random(args.seed).shuffle(schedule)
        for identifier in selected:
            model = next(m for m in catalog["models"] if m["id"] == identifier)
            print("Benchmarking", identifier, flush=True)
            result = {"id": identifier, "model": model, "runtime": catalog["runtimes"][model["runtime"]],
                      "status": "running", "clips": []}
            report["models"].append(result)
            adapter = None
            try:
                weights = directory / "models" / model["file"]
                if weights.stat().st_size != model["bytes"] or sha256(weights) != model["sha256"]:
                    raise ValueError("model integrity mismatch")
                executable = directory / (model["runtime"] + "-adapter")
                identity = json.loads((directory / (model["runtime"] + "-build-identity.json")).read_text())
                if (identity["revision"] != result["runtime"]["revision"] or
                    identity["adapter_sha256"] != sha256(executable) or
                    identity["adapter_source_sha256"] != sha256(HERE / "adapter.cpp")):
                    raise ValueError("adapter identity mismatch")
                if not identity.get("runtime_files"):
                    raise ValueError("runtime fingerprints missing")
                for relative, digest in identity["runtime_files"].items():
                    library = (directory / relative).resolve(strict=True)
                    library.relative_to(directory)
                    if sha256(library) != digest:
                        raise ValueError("runtime library identity mismatch")
                result["build_identity"] = identity
                adapter = Adapter(executable, weights, args.threads, args.timeout)
                cold = adapter.decode(audio[first["id"]][0])
                if cold["code"] != 0:
                    raise ValueError("warmup failed")
                result["startup"] = {"load_ms": adapter.ready["load_ms"], "process_ready_ms": adapter.startup_ms,
                                     "first_decode_ms": cold["decode_ms"],
                                     "process_start_to_first_result_ms": (time.perf_counter() - adapter.started) * 1000,
                                     "warmup_clip": first["id"]}
                for run, clip in schedule:
                    path, duration, digest = audio[clip["id"]]
                    frame = adapter.decode(path)
                    row = score(clip, frame)
                    # Never retain native frames or transcripts in the report.
                    row.update(run=run, audio_duration_ms=duration, audio_sha256=digest)
                    result["clips"].append(row)
                result["status"] = "complete"
            except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
                # Third-party exception messages can contain transcripts or paths.
                result["status"] = "failed"
                result["error"] = "timeout" if isinstance(error, TimeoutError) else "adapter_or_artifact_failure"
            finally:
                if adapter:
                    adapter.close()
                result["summary"] = summarize(result["clips"])
                result["conditions"] = {condition: summarize([r for r in result["clips"] if r["condition"] == condition])
                                        for condition in sorted({r["condition"] for r in result["clips"]})}
                result["expected_cases"] = len(schedule)
                save(args.output, report)
            print(identifier, result["status"], json.dumps(result["summary"], sort_keys=True), flush=True)
    return int(any(m["status"] != "complete" or m["summary"]["failed_cases"] for m in report["models"]))


if __name__ == "__main__":
    sys.exit(main())
