#!/usr/bin/env python3
"""Paired, transcript-free own-Q8 comparison. No service/config changes or downloads.

Use a network namespace and the conversion venv (gguf) explicitly. Both models
use the same bundle runtime and adapter. This is sampled parity evidence, not
proof of superiority or native-host qualification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import benchmark as b
from incremental import stream_metrics
from prepare import CATALOG, sha256

ROOT = Path(__file__).resolve().parents[2]


def tensor_comparison(baseline, candidate):
    from gguf import GGUFReader
    a, c = GGUFReader(str(baseline)), GGUFReader(str(candidate))
    def inventory(reader):
        return {t.name: (str(t.tensor_type), t.shape.tolist(), hashlib.sha256(memoryview(t.data)).hexdigest()) for t in reader.tensors}
    x, y = inventory(a), inventory(c)
    differing = [k for k in sorted(set(x) | set(y)) if x.get(k) != y.get(k)]
    fields = sorted(set(a.fields) | set(c.fields))
    changed = [k for k in fields if (a.fields[k].contents() if k in a.fields else None) != (c.fields[k].contents() if k in c.fields else None)]
    return {"baseline_tensors": len(x), "candidate_tensors": len(y), "differing_tensors": differing, "differing_metadata_keys": changed}


def isolated_tensor_comparison(baseline, candidate):
    # Inspect in a child so touching both mmaps cannot inflate the benchmark
    # adapters' inherited pre-exec ru_maxrss high-water mark.
    code = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
            "from compare_own_q8 import tensor_comparison;"
            "print(json.dumps(tensor_comparison(sys.argv[2],sys.argv[3])))")
    return json.loads(subprocess.check_output([
        sys.executable, "-c", code, str(Path(__file__).resolve().parent),
        str(baseline), str(candidate)], text=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--conversion", type=Path, required=True)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--runs", type=int, choices=range(1, 4), default=1)
    p.add_argument("--clean-only", action="store_true")
    p.add_argument("--modes", nargs="+", choices=("batch", "buffered-default"), default=["batch", "buffered-default"])
    args = p.parse_args()
    if not 1 <= args.limit <= 10000 or args.output.exists():
        p.error("bounded positive limit and unused output path required")
    bundle, conversion = args.bundle.resolve(), args.conversion.resolve()
    catalog = json.loads(CATALOG.read_text())
    model = next(m for m in catalog["models"] if m["id"] == "parakeet-unified-en-q8")
    baseline = bundle / "models" / model["file"]
    provenance = json.loads((conversion / "provenance.json").read_text())
    pin = json.loads((ROOT / "tests/local_stt_benchmark/nvidia-source.json").read_text())
    if provenance["source"] != pin or provenance["runtime"] != catalog["runtimes"]["transcribe"]:
        raise ValueError("candidate source/runtime provenance mismatch")
    candidate = (conversion / provenance["q8"]["file"]).resolve()
    candidate.relative_to(conversion)
    for path, record in ((baseline, model), (candidate, provenance["q8"])):
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError("model integrity mismatch")
    identity = json.loads((bundle / "build.json").read_text())
    source = ROOT / "dist/local-stt-benchmark/transcribe.cpp"
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != identity["runtime_revision"] or revision != provenance["runtime"]["revision"] or subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"]):
        raise ValueError("runtime/header mismatch")
    for name, digest in identity["files"].items():
        if name.startswith("lib/") and sha256(bundle / name) != digest:
            raise ValueError("runtime integrity mismatch")
    manifest, digest = b.load_manifest(args.manifest.resolve())
    clips = [c for c in manifest["clips"] if not c["expect_no_speech"] and (not args.clean_only or c.get("condition", "clean") == "clean")][:args.limit]
    clips += [c for c in manifest["clips"] if c["expect_no_speech"]]
    report = {"schema_version": 1, "status": "running", "source": pin,
              "baseline_sha256": model["sha256"], "candidate_sha256": provenance["q8"]["sha256"],
              "tensor_comparison": isolated_tensor_comparison(baseline, candidate),
              "manifest_sha256": digest, "machine": b.machine(), "threads": 4,
              "runtime_revision": revision, "bundle_identity_sha256": sha256(bundle / "build.json"),
              "runner_sha256": sha256(Path(__file__)), "adapter_source_sha256": sha256(Path(__file__).with_name("adapter.cpp")),
              "note": "Alternating paired order, no discarded warmup. Small sample; no transcripts persisted. AMI training overlap limits generalization. Timing under background load, not release qualification.", "modes": []}
    b.save(args.output, report)
    with tempfile.TemporaryDirectory(prefix="sayall-own-q8-") as td:
        root = Path(td)
        adapter_exe = root / "adapter"
        subprocess.run(["c++", "-std=c++17", "-O3", "-DNDEBUG", "-pthread", "-DLOCAL_TRANSCRIBE",
                        "-I" + str(source / "include"), str(Path(__file__).with_name("adapter.cpp")),
                        str(bundle / "lib/libtranscribe.so"), "-Wl,-rpath," + str(bundle / "lib"), "-o", str(adapter_exe)], check=True)
        audio = {}
        for clip in clips:
            wav, pcm = b.provider.make_audio(clip, root, args.manifest.resolve().parent)
            if clip["sha256"] != "runtime-generated" and sha256(wav) != clip["sha256"]:
                raise ValueError("audio integrity mismatch")
            audio[clip["id"]] = wav.with_suffix(".pcm"), len(pcm) / 32
        for mode in args.modes:
            item = {"mode": mode, "baseline": [], "candidate": [], "exact_output_mismatches": 0}
            report["modes"].append(item)
            adapters = {}
            try:
                for name, weights in (("baseline", baseline), ("candidate", candidate)):
                    adapters[name] = b.Adapter(adapter_exe, weights, 4, 360, mode=mode)
                for run in range(args.runs):
                    for index, clip in enumerate(clips):
                        path, duration = audio[clip["id"]]
                        order = ("baseline", "candidate") if (run + index) % 2 == 0 else ("candidate", "baseline")
                        frames = {}
                        for name in order:
                            frame = adapters[name].decode(path)
                            frames[name] = frame
                            row = b.score(clip, frame)
                            row.update(audio_duration_ms=duration, run=run + 1)
                            if mode != "batch":
                                row.update(stream_metrics(frame))
                            item[name].append(row)
                        item["exact_output_mismatches"] += int(any(frames['baseline'][k] != frames['candidate'][k] for k in ("text", "code")))
                        del frames, frame
                        b.save(args.output, report)
                item["summary"] = {}
                for name in ("baseline", "candidate"):
                    rows = item[name]
                    summary = b.summarize(rows)
                    if mode != "batch":
                        summary["stop_to_final_p50_ms"] = b.percentile([r["stop_to_final_ms"] for r in rows if r["classification"] == "speech_ok"], .5)
                    item["summary"][name] = summary
                print(mode, json.dumps(item["summary"], sort_keys=True), flush=True)
            finally:
                for adapter in adapters.values():
                    adapter.close()
    report["status"] = "complete"
    b.save(args.output, report)
    return int(any(m["exact_output_mismatches"] or m["summary"]["candidate"]["failed_cases"] for m in report["modes"]))


if __name__ == "__main__":
    raise SystemExit(main())
