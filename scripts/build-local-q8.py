#!/usr/bin/env python3
"""Build the development-only private Q8 helper. No release packaging/signing.

--prepare explicitly downloads only Q8 and builds its pinned CPU runtime.
Without --prepare, require the already prepared, fingerprinted runtime.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/local_stt_benchmark"))
import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--runtime-directory", type=Path, default=ROOT / "dist/local-stt-benchmark")
    parser.add_argument("--output-directory", type=Path, default=ROOT / "zig-out/bin")
    args = parser.parse_args()
    if sys.platform not in ("linux", "darwin"):
        parser.error("local Q8 runtime supports Linux/macOS development builds only")
    directory = args.runtime_directory.resolve()
    catalog = json.loads(prepare.CATALOG.read_text())
    runtime = catalog["runtimes"]["transcribe"]
    model = next(m for m in catalog["models"] if m["id"] == "parakeet-unified-en-q8")
    if args.prepare:
        directory.mkdir(parents=True, exist_ok=True)
        prepare.download(model, directory / "models")
        prepare.build("transcribe", runtime, directory, 4)
    source = directory / "transcribe.cpp"
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    identity = json.loads((directory / "transcribe-build-identity.json").read_text())
    if revision != runtime["revision"] or dirty or identity["revision"] != revision or identity["backend"] != "cpu":
        raise ValueError("a clean pinned CPU runtime is required; run with --prepare")
    for relative, digest in identity["runtime_files"].items():
        artifact = (directory / relative).resolve()
        artifact.relative_to(directory)
        if prepare.sha256(artifact) != digest:
            raise ValueError("runtime fingerprint mismatch; rebuild explicitly")
    libraries = list((directory / "transcribe-build").rglob("libtranscribe.so")) + list((directory / "transcribe-build").rglob("libtranscribe.dylib"))
    if len(libraries) != 1:
        raise ValueError("expected exactly one runtime library")
    library = libraries[0].resolve()
    if prepare.sha256(library) != identity["runtime_library_sha256"]:
        raise ValueError("runtime library fingerprint mismatch")
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(["c++", "-std=c++17", "-O3", "-DNDEBUG", "-pthread", "-Wall", "-Wextra",
                    "-I" + str(source / "include"), str(ROOT / "daemon/stt/local_q8.cpp"), str(library),
                    "-Wl,-rpath," + str(library.parent), "-o", str(output / "sayall-local-q8")], check=True)
    print("Built development helper beside the worker. Uses build-tree libraries; not a release artifact.")


if __name__ == "__main__":
    main()
