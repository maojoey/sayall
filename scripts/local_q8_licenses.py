"""Offline validation/copying of the pinned Q8 license packet, not legal approval."""
import hashlib
import json
from pathlib import Path
import shutil

PACKET = Path(__file__).resolve().parents[1] / "licenses/local-q8"
REQUIRED = frozenset({
    "NVIDIA-OPEN-MODEL-LICENSE.md", "NVIDIA-TRUSTWORTHY-AI-TERMS.md",
    "CC-BY-4.0.txt", "Notice.txt", "README.md",
})


def validate(model, packet=PACKET):
    manifest = json.loads((packet / "manifest.json").read_text())
    if (manifest.get("schema_version") != 1
            or manifest.get("model_sha256") != model["sha256"]
            or manifest.get("conversion_revision") != model["revision"]):
        raise ValueError("Q8 license evidence does not match this artifact")
    if manifest.get("status") != "conversion-scope-clarification-required":
        raise ValueError("Q8 license status changed; review bundle policy")
    if set(manifest["files"]) != REQUIRED:
        raise ValueError("Incomplete or unexpected Q8 license packet")
    for name, record in manifest["files"].items():
        path = packet / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or symlinked Q8 license document")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("Q8 license document differs from reviewed snapshot")
    return manifest


def install(model, prefix, packet=PACKET):
    manifest = validate(model, packet)
    destination = prefix / "licenses/local-q8"
    destination.mkdir(parents=True, exist_ok=False)
    for name in (*sorted(REQUIRED), "manifest.json"):
        shutil.copyfile(packet / name, destination / name)
    shutil.copyfile(packet / "Notice.txt", prefix / "Notice.txt")
    return manifest["status"]


def validate_own(conversion, model, runtime, source_pin, runtime_source):
    """Check a locally generated artifact's provenance and exact license inputs."""
    provenance = json.loads((conversion / "provenance.json").read_text())
    expected = {"file": "models/" + model["file"], "sha256": model["sha256"], "bytes": model["bytes"]}
    if (provenance.get("schema_version") != 1 or provenance.get("source") != source_pin
            or provenance.get("runtime") != runtime or provenance.get("q8") != expected
            or provenance.get("f32", {}).get("sha256") != model["f32_sha256"]):
        raise ValueError("Own Q8 provenance does not match reviewed artifact pins")
    required = {
        "licenses/NVIDIA-OPEN-MODEL-LICENSE.md": PACKET / "NVIDIA-OPEN-MODEL-LICENSE.md",
        "licenses/NVIDIA-TRUSTWORTHY-AI-TERMS.md": PACKET / "NVIDIA-TRUSTWORTHY-AI-TERMS.md",
        "licenses/transcribe-LICENSE": runtime_source / "LICENSE",
        "licenses/ggml-LICENSE": runtime_source / "ggml/LICENSE",
    }
    if set(provenance.get("licenses", {})) != set(required):
        raise ValueError("Own Q8 license inventory mismatch")
    for relative, reviewed in required.items():
        path = conversion / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or symlinked own Q8 license")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != provenance["licenses"][relative] or digest != hashlib.sha256(reviewed.read_bytes()).hexdigest():
            raise ValueError("Own Q8 license snapshot mismatch")
    notice = conversion / "Notice.txt"
    if notice.is_symlink() or not notice.is_file():
        raise ValueError("Missing or symlinked own Q8 notice")
    if (hashlib.sha256(notice.read_bytes()).hexdigest() != provenance.get("notice_sha256")
            or "Licensed by NVIDIA Corporation under the NVIDIA Open Model License" not in notice.read_text()
            or source_pin["revision"] not in notice.read_text()):
        raise ValueError("Own Q8 attribution mismatch")
    return provenance


def install_own(conversion, model, runtime, source_pin, runtime_source, prefix):
    provenance = validate_own(conversion, model, runtime, source_pin, runtime_source)
    destination = prefix / "licenses/own-q8"
    destination.mkdir(parents=True, exist_ok=False)
    for relative in provenance["licenses"]:
        shutil.copyfile(conversion / relative, destination / Path(relative).name)
    # Publish an accurate, minimal provenance record, not local argv, home
    # directories, comparison commentary or the full build-environment record.
    public_provenance = {
        "schema_version": 1,
        "source": provenance["source"],
        "runtime": {"project": "transcribe.cpp", "revision": runtime["revision"]},
        "q8": provenance["q8"], "f32": provenance["f32"],
        "license_files": {Path(k).name: v for k, v in provenance["licenses"].items()},
        "conversion_record_sha256": hashlib.sha256((conversion / "provenance.json").read_bytes()).hexdigest(),
    }
    for key in ("wrapper_sha256", "converter_sha256", "quantizer_sha256"):
        if key in provenance:
            public_provenance[key] = provenance[key]
    (destination / "provenance.json").write_text(json.dumps(public_provenance, indent=2) + "\n")
    (prefix / "Notice.txt").write_text(
        "Licensed by NVIDIA Corporation under the NVIDIA Open Model License\n\n"
        "Base model: NVIDIA Parakeet Unified English 0.6B.\n"
        "Converted from NVIDIA's original weights to GGUF and quantized to Q8_0\n"
        "for SayAll using transcribe.cpp. Not retrained. No upstream endorsement\n"
        "is implied. Conversion does not remove the NVIDIA license conditions.\n"
        f"Source: https://huggingface.co/{source_pin['repository']}/tree/{source_pin['revision']}\n"
        f"Q8 SHA-256: {model['sha256']}\n\n"
        "The NVIDIA agreement and incorporated terms, plus the transcribe.cpp\n"
        "and ggml MIT notices, are in licenses/own-q8/. Application and Rust\n"
        "dependency notices are in licenses/sayall/.\n")
    return "direct-nvidia-source; test-build; not-release-approved"
