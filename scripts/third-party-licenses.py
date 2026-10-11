#!/usr/bin/env python3
"""Collect actual locked Rust dependency notices; never borrow crate copyrights."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

OVERRIDES = Path(__file__).resolve().parents[1] / "licenses/rust-overrides"


def license_files(package, overrides=OVERRIDES):
    root = Path(package["manifest_path"]).parent.resolve()
    files = {p for p in root.iterdir() if p.is_file() and
             p.name.lower().startswith(("license", "copying", "unlicense"))}
    if package.get("license_file"):
        declared = (root / package["license_file"]).resolve()
        declared.relative_to(root)
        if not declared.is_file():
            raise RuntimeError("missing declared license file")
        files.add(declared)
    # r-efi 6.0.0 carries its complete MIT grant and copyright in AUTHORS,
    # not a LICENSE file. Preserve the actual file, not a generic MIT template.
    authors = root / "AUTHORS"
    if not files and package["name"] == "r-efi" and package["version"] == "6.0.0" and authors.is_file():
        text = authors.read_text()
        if "AUTHORS-MIT:" in text and "Permission is hereby granted" in text and "COPYRIGHT:" in text:
            files.add(authors)
    if not files:
        key = package["name"] + "@" + package["version"]
        record = json.loads((overrides / "manifest.json").read_text()).get(key)
        if record is None:
            raise RuntimeError(f"no reviewed license text for {key}; add a pinned upstream snapshot")
        if Path(record["file"]).name != record["file"]:
            raise RuntimeError("invalid override filename")
        path = overrides / record["file"]
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise RuntimeError("reviewed license snapshot integrity failure")
        vcs = root / ".cargo_vcs_info.json"
        if not vcs.is_file() or json.loads(vcs.read_text())["git"]["sha1"] != record["source_revision"]:
            raise RuntimeError(f"license snapshot revision does not match {key}")
        files.add(path)
    # NOTICE/author files can impose additional preservation requirements.
    files.update(p for p in root.iterdir() if p.is_file() and
                 p.name.lower().startswith(("notice", "authors", "copyright")))
    return sorted(files)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} OUTPUT", file=sys.stderr)
        return 2
    metadata = json.loads(subprocess.check_output([
        "cargo", "metadata", "--locked", "--format-version", "1",
        "--manifest-path", "ui/linux/Cargo.toml"], text=True))
    packages = sorted(
        (p for p in metadata["packages"] if p["name"] != "sayall-hud"),
        key=lambda p: (p["name"], p["version"]))
    # Validate every package before producing an apparently complete output.
    contents = []
    for package in packages:
        contents.append((package, [(p.name, p.read_text(encoding="utf-8")) for p in license_files(package)]))
    with Path(sys.argv[1]).open("w", encoding="utf-8") as notices:
        notices.write("Rust dependencies in the locked SayAll Cargo graph\n")
        notices.write("Includes platform/build-only dependencies; not a runtime support claim.\n")
        for package, files in contents:
            notices.write(f"\n{'=' * 72}\n{package['name']} {package['version']}\n"
                          f"Declared license: {package.get('license') or 'not specified'}\n")
            for name, text in files:
                notices.write(f"\n--- {name} ---\n{text}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
