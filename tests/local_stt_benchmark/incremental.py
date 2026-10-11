#!/usr/bin/env python3
"""Compare Q8 batch and paced buffered streaming, without storing transcripts.

Research only: no microphone, clipboard, service changes, downloads or cloud.
Enforce network isolation outside this script (e.g. unshare --user --map-root-user
--net). Batch timing excludes model load; streaming timing includes residual
backlog after the scheduled final audio sample. Neither is native HUD delivery.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile

import benchmark as b
from prepare import CATALOG, sha256

ROOT = Path(__file__).resolve().parents[2]
MODES = ("batch", "buffered-default", "buffered-balanced")
METRICS = ("stop_to_final_ms", "max_feed_ms", "max_backlog_ms", "feeds")


def stream_metrics(frame):
    result = {}
    for key in METRICS:
        value = frame.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid streaming metric")
        if key == "feeds" and (type(value) is not int or value > 3000):
            raise ValueError("invalid feed count")
        result[key] = value
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--limit", type=int, default=8, help="speech clips; all no-speech clips retained")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    p.add_argument("--all-conditions", action="store_true")
    args = p.parse_args()
    if not 1 <= args.limit <= 10000 or not 1 <= args.runs <= 10:
        p.error("invalid bounded run count")
    bundle = args.bundle.resolve()
    manifest, digest = b.load_manifest(args.manifest.resolve())
    catalog = json.loads(CATALOG.read_text())
    model = next(m for m in catalog["models"] if m["id"] == "parakeet-unified-en-q8")
    weights = bundle / "models" / model["file"]
    if weights.stat().st_size != model["bytes"] or sha256(weights) != model["sha256"]:
        raise ValueError("model integrity mismatch")
    identity = json.loads((bundle / "build.json").read_text())
    if identity["runtime_revision"] != catalog["runtimes"]["transcribe"]["revision"]:
        raise ValueError("runtime revision mismatch")
    for name, expected in identity["files"].items():
        if name.startswith("lib/") and sha256(bundle / name) != expected:
            raise ValueError("runtime library mismatch")
    source = ROOT / "dist/local-stt-benchmark/transcribe.cpp"
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    if revision != identity["runtime_revision"] or dirty:
        raise ValueError("header revision mismatch or modified source")
    speech = [c for c in manifest["clips"] if not c["expect_no_speech"] and
              (args.all_conditions or c.get("condition", "clean") == "clean")][:args.limit]
    clips = speech + [c for c in manifest["clips"] if c["expect_no_speech"]]
    if not speech:
        raise ValueError("no matching speech clips")
    report = {"schema_version": 1, "model_id": model["id"], "model_sha256": model["sha256"],
              "runtime_revision": revision, "runtime_files": {k: v for k, v in identity["files"].items() if k.startswith("lib/")},
              "manifest_sha256": digest, "machine": b.machine(), "threads": 4,
              "adapter_source_sha256": sha256(Path(__file__).with_name("adapter.cpp")),
              "runner_sha256": sha256(Path(__file__)), "feed_ms": 100,
              "note": "Paced buffered streaming. Model already loaded in all modes; not native stop-to-delivery. No cloud comparison. Small non-independent sample.",
              "modes": []}
    with tempfile.TemporaryDirectory(prefix="sayall-incremental-") as directory:
        root = Path(directory)
        executable = root / "adapter"
        subprocess.run(["c++", "-std=c++17", "-O3", "-DNDEBUG", "-pthread", "-DLOCAL_TRANSCRIBE",
                        "-I" + str(source / "include"), str(Path(__file__).with_name("adapter.cpp")),
                        str(bundle / "lib/libtranscribe.so"), "-Wl,-rpath," + str(bundle / "lib"),
                        "-o", str(executable)], check=True)
        audio = {}
        for clip in clips:
            wav, pcm = b.provider.make_audio(clip, root, args.manifest.resolve().parent)
            if clip["sha256"] != "runtime-generated" and sha256(wav) != clip["sha256"]:
                raise ValueError("audio integrity mismatch")
            audio[clip["id"]] = (wav.with_suffix(".pcm"), len(pcm) / 32, sha256(wav))
        for mode in args.modes:
            item = {"mode": mode, "clips": [], "status": "running"}
            report["modes"].append(item)
            adapter = b.Adapter(executable, weights, 4, 360, mode=mode)
            try:
                item["load_ms"] = adapter.ready["load_ms"]
                for run in range(args.runs):
                    for clip in clips:
                        path, duration, audio_hash = audio[clip["id"]]
                        frame = adapter.decode(path)
                        row = b.score(clip, frame)
                        row.update(stream_metrics(frame))
                        row.update(audio_duration_ms=duration, audio_sha256=audio_hash, run=run + 1)
                        item["clips"].append(row)
                        del frame  # never persist references or hypotheses
                        b.save(args.output, report)
                item["status"] = "complete"
                item["summary"] = b.summarize(item["clips"])
                rows = [r for r in item["clips"] if not r["expect_no_speech"] and r["classification"] == "speech_ok"]
                item["summary"].update({
                    "stop_to_final_p50_ms": b.percentile([r["stop_to_final_ms"] for r in rows], .5),
                    "stop_to_final_p95_ms": b.percentile([r["stop_to_final_ms"] for r in rows], .95),
                    "max_backlog_ms": max((r["max_backlog_ms"] for r in rows), default=0),
                    "cpu_p50_ms": b.percentile([r["cpu_ms"] for r in rows], .5)})
                print(mode, json.dumps(item["summary"], sort_keys=True), flush=True)
                b.save(args.output, report)
            finally:
                adapter.close()
    return int(any(m["summary"]["failed_cases"] for m in report["modes"]))


if __name__ == "__main__":
    raise SystemExit(main())
