#!/usr/bin/env python3
"""Explicit, offline NVIDIA -> F32 -> Q8 conversion; never switches installed models.

Run in the isolated conversion environment. Fetch the source explicitly first
using the pins in scripts/local-q8/; see docs/own-q8-conversion.md.
This wrapper replaces only the upstream NeMo archive loader: bounded extraction,
weights_only=True, CPU mmap. Tensor mapping and quantization are upstream code.
"""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
from local_q8_licenses import sha256

PINS = ROOT / "scripts/local-q8"


def extract_member(archive, suffix, destination, maximum):
    matches = [m for m in archive.getmembers() if m.name.endswith(suffix)]
    if len(matches) != 1 or not matches[0].isfile() or not 0 < matches[0].size <= maximum:
        raise ValueError("invalid/ambiguous source archive member: " + suffix)
    member = matches[0]
    with archive.extractfile(member) as source, destination.open("xb") as out:
        shutil.copyfileobj(source, out, length=1024 * 1024)
    if destination.stat().st_size != member.size:
        raise ValueError("incomplete extracted member")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--quantizer", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    source, runtime, quantizer, output = (x.resolve() for x in (args.source, args.runtime, args.quantizer, args.output))
    pin = json.loads((PINS / "nvidia-source.json").read_text())
    runtime_pin = json.loads((PINS / "runtime.json").read_text())
    if source.is_symlink() or source.stat().st_size != pin["bytes"] or sha256(source) != pin["sha256"]:
        raise ValueError("source does not match pinned NVIDIA weights")
    revision = subprocess.check_output(["git", "-C", str(runtime), "rev-parse", "HEAD"], text=True).strip()
    if revision != runtime_pin["revision"] or subprocess.check_output(["git", "-C", str(runtime), "status", "--porcelain"]):
        raise ValueError("converter checkout must be the clean pinned revision")
    # Exclusive directory, never overwrite an active/previous artifact.
    output.mkdir(parents=True, exist_ok=False)
    extracted = output / "source-members"
    extracted.mkdir()
    with tarfile.open(source) as archive:
        extract_member(archive, "model_config.yaml", extracted / "config.yaml", 2 * 1024 * 1024)
        extract_member(archive, "model_weights.ckpt", extracted / "weights.ckpt", 3 * 1024**3)
        extract_member(archive, "tokenizer.model", extracted / "tokenizer.model", 4 * 1024 * 1024)

    import torch
    import yaml
    torch.set_num_threads(2)
    sys.path.insert(0, str(runtime / "scripts"))
    spec = importlib.util.spec_from_file_location("upstream_parakeet_converter", runtime / "scripts/convert-parakeet.py")
    converter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(converter)
    config = yaml.safe_load((extracted / "config.yaml").read_text())
    # Do not fall back to unsafe pickle or instantiate arbitrary NeMo classes.
    state = torch.load(extracted / "weights.ckpt", map_location="cpu", weights_only=True, mmap=True)
    if not isinstance(state, dict) or not state or not all(isinstance(k, str) and isinstance(v, torch.Tensor) for k, v in state.items()):
        raise ValueError("expected tensor-only NVIDIA state dictionary")
    model = converter._DirectNemoArchive(config, state, (extracted / "tokenizer.model").read_bytes())
    converter.load_nemo_model = lambda model_spec, prefer_direct=False: model
    f32_dir = output / "reference/parakeet-unified-en-0.6b"
    f32_dir.mkdir(parents=True)
    f32 = f32_dir / "parakeet-unified-en-0.6b-F32.gguf"
    with torch.inference_mode():
        converter.convert(str(source), f32, repo_id=pin["repository"])
    models = output / "models"
    models.mkdir()
    q8 = models / "sayall-parakeet-unified-en-0.6b-Q8_0.gguf"
    command = [str(quantizer), str(f32), str(q8), "--quant", "Q8_0"]
    subprocess.run(command, check=True, env={"PATH": "/usr/bin:/bin", "OMP_NUM_THREADS": "2"})
    from gguf import GGUFReader
    reader = GGUFReader(str(q8))
    if reader.fields["general.license.name"].contents() != "nvidia-open-model-license":
        raise ValueError("unexpected model license metadata")
    license_dir = output / "licenses"
    license_dir.mkdir()
    for name in ("NVIDIA-OPEN-MODEL-LICENSE.md", "NVIDIA-TRUSTWORTHY-AI-TERMS.md"):
        shutil.copyfile(ROOT / "licenses/local-q8" / name, license_dir / name)
    shutil.copyfile(runtime / "LICENSE", license_dir / "transcribe-LICENSE")
    shutil.copyfile(runtime / "ggml/LICENSE", license_dir / "ggml-LICENSE")
    (output / "Notice.txt").write_text(
        "Licensed by NVIDIA Corporation under the NVIDIA Open Model License\n\n"
        "NVIDIA Parakeet Unified English 0.6B. Converted to GGUF and quantized\n"
        "to Q8_0 by SayAll using the MIT-licensed transcribe.cpp tools.\n"
        "Not retrained. The input weights come directly from NVIDIA.\n"
        "No upstream endorsement is implied.\n"
        f"Source: https://huggingface.co/{pin['repository']}/tree/{pin['revision']}\n"
        "See provenance.json for hashes and tool revisions; licenses/ for terms.\n")
    provenance = {
        "schema_version": 1, "source": pin, "runtime": runtime_pin,
        "loader": "SayAll bounded archive extraction, torch weights_only CPU mmap; upstream tensor mapping unchanged",
        "wrapper_sha256": sha256(Path(__file__)),
        "converter_sha256": sha256(runtime / "scripts/convert-parakeet.py"),
        "quantizer_sha256": sha256(quantizer), "quantizer_command": command,
        "python": sys.version, "argv": sys.argv,
        "packages": {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
        "f32": {"sha256": sha256(f32), "bytes": f32.stat().st_size},
        "q8": {"sha256": sha256(q8), "bytes": q8.stat().st_size, "file": str(q8.relative_to(output))},
        "licenses": {str(f.relative_to(output)): sha256(f) for f in license_dir.iterdir()},
        "notice_sha256": sha256(output / "Notice.txt"),
        "qualification": "not-yet-compared; not activated or approved for release",
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print("Created separate Q8 artifact:", q8)
    print("SHA-256:", provenance["q8"]["sha256"])


if __name__ == "__main__":
    main()
