#!/usr/bin/env python3
"""Opt-in real Q8 CLI/worker checks. Requires locally built binaries and model.

No downloads or transcript logging. Not part of ordinary CI. --wav must be a
consented/public mono 16kHz PCM fixture, at most 300 seconds / 10 MiB.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cli", type=Path, required=True)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--wav", type=Path, required=True)
    args = p.parse_args()
    cli, model, wav = (x.resolve() for x in (args.cli, args.model, args.wav))
    assert wav.stat().st_size <= 10 * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="sayall-local-integration-") as directory:
        root = Path(directory)
        env = dict(os.environ, HOME=directory, XDG_CONFIG_HOME=directory, TMPDIR=directory,
                   DEEPGRAM_API_KEY="synthetic-must-not-use", CEREBRAS_API_KEY="synthetic-must-not-use")
        (root / "sayall").mkdir()
        (root / "sayall/config.json").write_text("invalid cloud configuration")
        private = root / "input.wav"
        shutil.copyfile(wav, private)
        private.chmod(0o600)

        def run(audio=wav, artifact=model, raw=False, executable=cli):
            result = subprocess.run([str(executable), "transcribe", str(audio), "--local",
                                     "--model-path", str(artifact), "--json"] + (["--raw"] if raw else []),
                                    env=env, capture_output=True, timeout=150)
            assert result.stderr == b"", "unexpected diagnostics (contents withheld)"
            value = json.loads(result.stdout)
            assert value["version"] == 4 and value["transport"] == "local"
            assert result.returncode == (1 if value["status"] == "error" else 0)
            return value

        for raw, profile in ((True, "verbatim"), (False, "clean")):
            r = run(raw=raw)
            assert r["status"] == "success" and r["text"]
            assert r["processing_profile"] == profile
            assert "local_stt_ms" in r["timing"] and "rest_stt_ms" not in r["timing"]
        assert run(artifact=root / "missing.gguf")["error"] == "local_model_missing"
        wrong = root / "wrong.gguf"
        wrong.write_bytes(b"not the pinned model")
        assert run(artifact=wrong)["error"] == "local_model_invalid"
        for size in (731357568, 731357696):  # Both pinned sizes, neither valid hash.
            with wrong.open("wb") as out:
                out.truncate(size)
            assert run(artifact=wrong)["error"] == "local_model_invalid"
        bad_audio = root / "bad.wav"
        bad_audio.write_bytes(b"not audio")
        assert run(audio=bad_audio)["error"] == "invalid_audio"
        silence = root / "silence.wav"
        with wave.open(str(silence), "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
            out.writeframes(bytes(32000))
        assert run(audio=silence)["status"] == "no_speech"

        worker = cli.with_name("sayall-process")
        request = {"version": 4, "wav_path": str(private), "stt_provider": "local",
                   "local_model_path": str(model), "deepgram_api_key": "", "llm_api_key": "",
                   "processing_profile": "verbatim"}
        for change, expected in (({"processing_profile": "polished"}, "unsupported_features"),
                                 ({"version": 3}, "incompatible_version"),
                                 ({"deepgram_api_key": "must-not-use"}, "invalid_request")):
            proc = subprocess.run([str(worker)], input=json.dumps(request | change).encode(),
                                  capture_output=True, env={}, timeout=10)
            assert json.loads(proc.stdout)["error"] == expected

        bundle = root / "bundle"
        bundle.mkdir()
        shutil.copyfile(cli, bundle / "sayall"); (bundle / "sayall").chmod(0o700)
        shutil.copyfile(worker, bundle / "sayall-process"); (bundle / "sayall-process").chmod(0o700)
        assert run(executable=bundle / "sayall")["error"] == "local_unavailable"
        # Overflow must terminate the helper without leaking its output.
        fake = bundle / "sayall-local-q8"
        fake.write_text("#!/usr/bin/python3\nimport sys\nsys.stdin.buffer.read()\nsys.stdout.write('x' * (2 * 1024 * 1024))\n")
        fake.chmod(0o700)
        assert run(executable=bundle / "sayall")["error"] == "response_too_large"

        replacement_checked = False
        if sys.platform == "linux":
            for kind in ("cancel", "timeout", "replace"):
                current = dict(request)
                if kind == "replace":
                    pinned = root / "pinned.gguf"
                    try:
                        os.link(model, pinned)
                    except OSError as exc:
                        if exc.errno == 18:  # Different filesystems; no large implicit copy.
                            continue
                        raise
                    current["local_model_path"] = str(pinned)
                proc = subprocess.Popen([str(worker)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env={})
                try:
                    proc.stdin.write(json.dumps(current).encode()); proc.stdin.close()
                    deadline = time.monotonic() + 10
                    helper = None
                    while time.monotonic() < deadline and proc.poll() is None:
                        children = Path(f"/proc/{proc.pid}/task/{proc.pid}/children").read_text().split()
                        if children:
                            helper = int(children[0]); break
                        time.sleep(0.01)
                    assert helper is not None, "native helper did not start"
                    if kind == "cancel":
                        proc.kill(); proc.wait(timeout=5)
                        while time.monotonic() < deadline:
                            stat = Path(f"/proc/{helper}/stat")
                            if not stat.exists() or stat.read_text().split()[2] == "Z": break
                            time.sleep(0.02)
                        else: raise AssertionError("orphan inference helper")
                    else:
                        if kind == "timeout":
                            os.kill(helper, signal.SIGALRM)
                        else:
                            os.replace(wrong, pinned)  # Never modifies the source model inode.
                        frame = json.loads(proc.stdout.read())
                        proc.wait(timeout=5)
                        if kind == "timeout":
                            assert frame["error"] == "local_timeout"
                        else:
                            assert frame["status"] == "success" and frame["text"]
                            replacement_checked = True
                finally:
                    if proc.poll() is None: proc.kill()
                    proc.wait(timeout=5)
                    proc.stdout.close()
        print("PASS: Q8 Verbatim/Clean, cloud-config isolation, silence, invalid inputs, v3 rejection, missing runtime, output bound" +
              (", Linux cancellation/timeout" if sys.platform == "linux" else "; cancellation not qualified on this platform"))
        print("Atomic model replacement:", "PASS" if replacement_checked else "SKIPPED (requires Linux and same-filesystem scratch)")


if __name__ == "__main__":
    main()
