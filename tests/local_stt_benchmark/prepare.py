#!/usr/bin/env python3
"""Explicitly download pinned research models and build CPU benchmark adapters.

Never invoked by tests or normal product builds. Artifacts stay under dist/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "candidates.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(model: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / model["file"]
    if path.exists():
        if path.stat().st_size == model["bytes"] and sha256(path) == model["sha256"]:
            print(model["id"], "verified", flush=True)
            return
        raise ValueError("existing model fails integrity check; remove it explicitly")
    url = f'https://huggingface.co/{model["repository"]}/resolve/{model["revision"]}/{model["file"]}?download=true'
    temporary = path.with_suffix(path.suffix + ".part")
    owned = False
    try:
        with temporary.open("xb") as out:
            owned = True
            with urllib.request.urlopen(url, timeout=60) as response:
                digest, size = hashlib.sha256(), 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > model["bytes"]:
                        raise ValueError("model exceeds pinned size")
                    digest.update(chunk)
                    out.write(chunk)
                if size != model["bytes"] or digest.hexdigest() != model["sha256"]:
                    raise ValueError("download fails pinned integrity check")
        os.replace(temporary, path)
    finally:
        if owned:
            temporary.unlink(missing_ok=True)
    print(model["id"], "downloaded and verified", flush=True)


def run(argv: list[str]) -> None:
    subprocess.run(argv, check=True)


def build(name: str, runtime: dict, directory: Path, jobs: int) -> None:
    source, output = directory / (name + ".cpp"), directory / (name + "-build")
    if not source.exists():
        run(["git", "init", str(source)])
        run(["git", "-C", str(source), "remote", "add", "origin", runtime["repository"]])
        run(["git", "-C", str(source), "fetch", "--depth", "1", "origin", runtime["revision"]])
        run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    if revision != runtime["revision"] or dirty:
        raise ValueError("runtime checkout must match the clean pinned revision")
    common = ["cmake", "-S", str(source), "-B", str(output), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release"]
    if name == "transcribe":
        options = ["-DTRANSCRIBE_BUILD_SHARED=ON", "-DTRANSCRIBE_BUILD_TESTS=OFF", "-DTRANSCRIBE_BUILD_EXAMPLES=OFF",
                   "-DTRANSCRIBE_METAL=OFF", "-DTRANSCRIBE_VULKAN=OFF", "-DTRANSCRIBE_CUDA=OFF",
                   "-DTRANSCRIBE_HIP=OFF", "-DTRANSCRIBE_USE_SYSTEM_BLAS=OFF"]
    else:
        options = ["-DBUILD_SHARED_LIBS=ON", "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_EXAMPLES=OFF",
                   "-DGGML_CUDA=OFF", "-DGGML_VULKAN=OFF", "-DGGML_METAL=OFF", "-DGGML_BLAS=OFF"]
    run(common + options)
    run(["cmake", "--build", str(output), "-j", str(jobs)])
    libraries = list(output.rglob("lib" + name + ".so")) + list(output.rglob("lib" + name + ".dylib"))
    if len(libraries) != 1:
        raise ValueError("could not identify a unique runtime library")
    library = libraries[0].resolve()
    binary = directory / (name + "-adapter")
    run(["c++", "-std=c++17", "-O3", "-DNDEBUG", "-DLOCAL_" + name.upper(),
         "-I" + str(source / "include"), "-I" + str(source / "ggml/include"),
         str(HERE / "adapter.cpp"), str(library), "-Wl,-rpath," + str(library.parent), "-o", str(binary)])
    runtime_files = sorted(p for p in output.rglob("lib*")
                           if p.is_file() and not p.is_symlink() and (".so" in p.name or p.suffix == ".dylib"))
    (directory / (name + "-build-identity.json")).write_text(json.dumps({
        "runtime": name, "revision": revision, "build_options": options, "backend": "cpu",
        "adapter_sha256": sha256(binary), "runtime_library_sha256": sha256(library),
        "adapter_source_sha256": sha256(HERE / "adapter.cpp"),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "runtime_files": {str(p.relative_to(directory)): sha256(p) for p in runtime_files},
    }, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("dist/local-stt-benchmark"))
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--jobs", type=int, choices=range(1, 17), default=4)
    args = parser.parse_args()
    if not args.download_models and not args.build:
        parser.error("select --download-models and/or --build explicitly")
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(CATALOG.read_text())
    if args.download_models:
        for model in catalog["models"]:
            download(model, directory / "models")
    if args.build:
        for name, runtime in catalog["runtimes"].items():
            build(name, runtime, directory, args.jobs)


if __name__ == "__main__":
    main()
