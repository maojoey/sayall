#!/usr/bin/env python3
"""Exercise native Linux capture -> Q8 -> delivery with a public/consented fixture.

Uses the real Controller and worker with paced fixture capture and a dummy
Delivery implementation. Never records a microphone, types, or changes clipboard.
No downloads, API keys or transcript logging. Requires an existing local bundle.
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--fixture", type=Path, required=True)
    p.add_argument("--network-isolated", action="store_true", help="run under a new Linux user/network namespace")
    p.add_argument("--batch", action="store_true", help="compare the explicit local batch path")
    p.add_argument("--cancel", action="store_true", help="cancel during capture; assert no delivery and cleanup")
    args = p.parse_args()
    bundle = args.bundle.resolve()
    fixture_source = args.fixture.resolve()
    assert fixture_source.stat().st_size <= 10 * 1024 * 1024
    model_file = json.loads((bundle / "build.json").read_text())["model"]["file"]
    if Path(model_file).name != model_file:
        raise ValueError("invalid bundle model filename")
    model = bundle / "models" / model_file
    assert model.is_file() and (bundle / "sayall-process").is_file()
    build = subprocess.run(["cargo", "test", "--locked", "--offline", "--no-run", "--message-format=json",
                            "--manifest-path", "ui/linux/Cargo.toml"], cwd=ROOT, capture_output=True, text=True, check=True)
    executables = []
    for line in build.stdout.splitlines():
        event = json.loads(line)
        if event.get("reason") == "compiler-artifact" and event.get("executable") and event["profile"].get("test"):
            executables.append(Path(event["executable"]))
    assert len(executables) == 1
    executable = executables[0]
    # Prefer a bundle-matched sibling over an unrelated installed worker. Refuse
    # to overwrite an existing development helper (including a broken symlink).
    sibling = executable.parent / "sayall-process"
    sibling.symlink_to(bundle / "sayall-process")
    try:
        with tempfile.TemporaryDirectory(prefix="sayall-local-live-") as directory:
            root = Path(directory)
            parent = root / "config/sayall"
            parent.mkdir(parents=True, mode=0o700)
            parent.chmod(0o700)
            config = {"stt": {"provider": "local", "local_model_path": str(model), "streaming": not args.batch},
                      "processing": {"mode": "clean"}, "notifications": False}
            config_file = parent / "config.json"
            config_file.write_text(json.dumps(config))
            config_file.chmod(0o600)
            scratch = root / "sessions"
            scratch.mkdir(mode=0o700)
            fixture = root / "fixture.wav"
            with wave.open(str(fixture_source), "rb") as source:
                assert source.getnchannels() == 1 and source.getsampwidth() == 2 and source.getframerate() == 16000
                assert 0.3 <= source.getnframes() / 16000 <= 30
                with wave.open(str(fixture), "wb") as out:
                    out.setparams(source.getparams())
                    out.writeframes(source.readframes(source.getnframes()))
            env = dict(os.environ, XDG_CONFIG_HOME=str(root / "config"),
                       SAYALL_TEST_AUDIO_FIXTURE=str(fixture), SAYALL_TEST_AUDIO_EOF_PATH=str(root / "eof"),
                       SAYALL_TEST_SESSION_ROOT=str(scratch))
            env.pop("SAYALL_TEST_AUDIO_START_GATE", None)
            env.pop("SAYALL_TEST_CANCEL_LOCAL", None)
            if args.cancel:
                env["SAYALL_TEST_CANCEL_LOCAL"] = "1"
            command = [str(executable), "--ignored", "--exact",
                       "session::tests::real_local_capture_pipeline_delivers_once_and_cleans_audio", "--test-threads=1", "--nocapture"]
            if args.network_isolated:
                command = ["unshare", "--user", "--map-root-user", "--net"] + command
            # 120s startup + at most 30s fixture + 150s completion, plus
            # 30s for capture tail, delivery and shutdown/cleanup overhead.
            result = subprocess.run(command, env=env, capture_output=True, timeout=330)
            if result.returncode:
                raise RuntimeError("native live pipeline check failed; output withheld to avoid content logging")
            timing = re.search(rb'SAYALL_LOCAL_TIMING (\{[^\n]+\})', result.stdout)
            if not timing:
                raise RuntimeError("missing content-free test timing")
            print("PASS:", "cancellation without delivery" if args.cancel else "Q8 lifecycle and exactly-one dummy delivery",
                  "with audio cleanup", json.loads(timing.group(1)))
    finally:
        sibling.unlink()


if __name__ == "__main__":
    main()
