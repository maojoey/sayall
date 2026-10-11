#!/usr/bin/env python3
"""Build an isolated, relocatable Linux Q8 test bundle. No service/config changes.

Requires the explicitly prepared benchmark source/model cache. No downloads,
release publishing, signing, system-wide installation or credential copying.
CPU-native artifacts are for this machine, not portable release packages.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import local_q8_licenses

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/local_stt_benchmark"))
import prepare


def run(*args):
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prefix", type=Path, required=True)
    p.add_argument("--cache", type=Path, default=ROOT / "dist/local-stt-benchmark")
    p.add_argument("--own-conversion", type=Path, help="Explicit verified NVIDIA-source Q8 conversion directory; no downloads")
    args = p.parse_args()
    if sys.platform != "linux":
        p.error("this live test bundle is Linux-only")
    prefix = args.prefix.expanduser().absolute()
    if prefix.exists():
        p.error("prefix must not already exist; never overwrite an active test build")
    cache = args.cache.resolve()
    catalog = json.loads(prepare.CATALOG.read_text())
    runtime = catalog["runtimes"]["transcribe"]
    conversion = args.own_conversion.resolve() if args.own_conversion else None
    model = (json.loads((ROOT / "tests/local_stt_benchmark/own-q8.json").read_text()) if conversion else
             next(m for m in catalog["models"] if m["id"] == "parakeet-unified-en-q8"))
    source_pin = json.loads((ROOT / "tests/local_stt_benchmark/nvidia-source.json").read_text()) if conversion else None
    source = cache / "transcribe.cpp"
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    if revision != runtime["revision"] or dirty:
        raise ValueError("runtime source must be the clean pinned revision")
    if conversion:
        local_q8_licenses.validate_own(conversion, model, runtime, source_pin, source)
    else:
        local_q8_licenses.validate(model)
    model_file = (conversion if conversion else cache) / "models" / model["file"]
    if model_file.stat().st_size != model["bytes"] or prepare.sha256(model_file) != model["sha256"]:
        raise ValueError("Q8 model must match its pinned hash")
    build = ROOT / "dist/local-live-runtime-cpu"
    run("cmake", "-S", source, "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
        "-DTRANSCRIBE_BUILD_SHARED=ON", "-DTRANSCRIBE_BUILD_TESTS=OFF", "-DTRANSCRIBE_BUILD_EXAMPLES=OFF",
        "-DTRANSCRIBE_METAL=OFF", "-DTRANSCRIBE_VULKAN=OFF", "-DTRANSCRIBE_CUDA=OFF",
        "-DTRANSCRIBE_HIP=OFF", "-DTRANSCRIBE_USE_SYSTEM_BLAS=OFF",
        "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON", "-DCMAKE_INSTALL_RPATH=$ORIGIN")
    run("cmake", "--build", build, "-j", "4")
    run("zig", "build", "-Doptimize=ReleaseFast")
    run("zig", "build", "process", "-Doptimize=ReleaseFast")
    run("cargo", "build", "--locked", "--offline", "--release", "--manifest-path", "ui/linux/Cargo.toml")
    prefix.mkdir(parents=True, mode=0o700)
    libraries = prefix / "lib"
    libraries.mkdir()
    for path in build.rglob("lib*.so*"):
        if not path.is_file():
            continue
        destination = libraries / path.name
        if destination.exists() and prepare.sha256(path) != prepare.sha256(destination):
            raise ValueError("ambiguous runtime library basename")
        shutil.copy2(path, destination)
    for name in ("sayall", "sayall-process"):
        shutil.copy2(ROOT / "zig-out/bin" / name, prefix / name)
    shutil.copy2(ROOT / "ui/linux/target/release/sayall-hud", prefix / "sayall-hud")
    run("c++", "-std=c++17", "-O3", "-DNDEBUG", "-pthread", "-Wall", "-Wextra",
        "-I" + str(source / "include"), ROOT / "daemon/stt/local_q8.cpp",
        libraries / "libtranscribe.so", "-Wl,-rpath,$ORIGIN/lib", "-o", prefix / "sayall-local-q8")
    models = prefix / "models"
    models.mkdir()
    shutil.copyfile(model_file, models / model["file"])
    (models / model["file"]).chmod(0o400)
    if prepare.sha256(models / model["file"]) != model["sha256"]:
        raise ValueError("copied model integrity mismatch")
    if conversion:
        license_status = local_q8_licenses.install_own(conversion, model, runtime, source_pin, source, prefix)
        review = "Direct NVIDIA-source Q8. See Notice.txt and licenses/own-q8/ for provenance, NVIDIA terms and runtime notices."
    else:
        license_status = local_q8_licenses.install(model, prefix)
        review = model["license_review"] + "\nSee licenses/local-q8/README.md and Notice.txt."
    (prefix / "MODEL-LICENSE-REVIEW.txt").write_text(review + "\nLocal testing only; not a release distribution.\n")
    shutil.copyfile(source / "LICENSE", prefix / "transcribe-LICENSE")
    shutil.copyfile(source / "ggml/LICENSE", prefix / "ggml-LICENSE")
    app_licenses = prefix / "licenses/sayall"
    app_licenses.mkdir(parents=True)
    shutil.copyfile(ROOT / "LICENSE", app_licenses / "LICENSE")
    shutil.copyfile(ROOT / "licenses/websocket.zig-LICENSE", app_licenses / "websocket.zig-LICENSE")
    subprocess.run([sys.executable, str(ROOT / "scripts/third-party-licenses.py"),
                    str(app_licenses / "RUST-THIRD-PARTY-LICENSES.txt")],
                   cwd=ROOT, check=True, env=dict(os.environ, CARGO_NET_OFFLINE="true"))
    (prefix / "build.json").write_text(json.dumps({
        "runtime_revision": revision, "model": model,
        "model_license_status": license_status,
        "sayall_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)),
        "files": {str(path.relative_to(prefix)): prepare.sha256(path) for path in prefix.rglob("*") if path.is_file()},
    }, indent=2) + "\n")
    print("Built isolated Q8 test bundle:", prefix)


if __name__ == "__main__":
    main()
