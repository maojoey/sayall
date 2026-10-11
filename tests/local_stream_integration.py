#!/usr/bin/env python3
"""Opt-in Linux private-stream failure tests. No microphone, typing or downloads.
Requires a Q8 bundle; no audio/transcript content is printed.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time
import wave


def frame(process, timeout=15):
    # Read one byte at a time only in this bounded test protocol reader, avoiding
    # BufferedReader/select disagreement across the two result frames.
    deadline = time.monotonic() + timeout
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while len(data) <= 1024 * 1024:
            if not selector.select(max(0, deadline - time.monotonic())):
                raise RuntimeError("worker timeout")
            value = os.read(process.stdout.fileno(), 1)
            if not value:
                raise RuntimeError("worker closed output")
            if value == b"\n":
                return json.loads(data)
            data.extend(value)
    raise RuntimeError("oversized worker frame")


def gone(pid):
    path = Path(f"/proc/{pid}/stat")
    try:
        return path.read_text().split()[2] == "Z"
    except FileNotFoundError:
        return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--slow", action="store_true", help="exercise completion beyond the former 40s stream deadline")
    args = p.parse_args()
    bundle = args.bundle.resolve()
    worker = bundle / "sayall-process"
    model_file = json.loads((bundle / "build.json").read_text())["model"]["file"]
    if Path(model_file).name != model_file:
        raise ValueError("invalid bundle model filename")
    model = bundle / "models" / model_file
    with tempfile.TemporaryDirectory(prefix="sayall-stream-check-") as directory:
        root = Path(directory)
        pcm = root / "audio.pcm"
        wav = root / "audio.wav"
        pcm.write_bytes(bytes(32000))
        pcm.chmod(0o600)
        with wave.open(str(wav), "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
            out.writeframes(bytes(32000))
        wav.chmod(0o600)
        request = {"version": 4, "stt_provider": "local", "local_model_path": str(model),
                   "wav_path": str(wav), "pcm_path": str(pcm), "deepgram_api_key": "",
                   "llm_api_key": "", "processing_profile": "clean"}
        kinds = ["silence", "invalid_finish", "timeout", "parent_death", "parent_death_paused", "disconnect"]
        if args.slow:
            kinds.append("delayed_finish")
        for kind in kinds:
            proc = subprocess.Popen([str(worker), "--local-stream"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={}, start_new_session=True)
            helper = None
            try:
                proc.stdin.write(json.dumps(request).encode() + b"\n"); proc.stdin.flush()
                ready = frame(proc)
                assert ready == {"version": 4, "event": "ready", "streaming": True}
                children = Path(f"/proc/{proc.pid}/task/{proc.pid}/children").read_text().split()
                assert len(children) == 1
                helper = int(children[0])
                if kind in ("parent_death", "parent_death_paused"):
                    if kind == "parent_death_paused":
                        os.kill(helper, signal.SIGSTOP)
                    proc.kill()
                elif kind == "disconnect":
                    proc.stdin.close()
                else:
                    if kind == "delayed_finish":
                        os.kill(helper, signal.SIGSTOP)
                    if kind == "timeout":
                        os.kill(helper, signal.SIGALRM)
                        time.sleep(.05)
                    finish = {"version": 4, "command": "finish", "force_rest": kind == "invalid_finish"}
                    proc.stdin.write(json.dumps(finish).encode() + b"\n"); proc.stdin.flush()
                    if kind == "delayed_finish":
                        time.sleep(46)
                        os.kill(helper, signal.SIGCONT)
                    result = frame(proc)
                    assert result["transport"] == "local"
                    if kind in ("silence", "delayed_finish"):
                        assert result["status"] == "no_speech"
                    else:
                        assert result["status"] == "error"
                        assert result["error"] == ("local_timeout" if kind == "timeout" else "invalid_request")
                proc.wait(timeout=5)
                deadline = time.monotonic() + 3
                while not gone(helper) and time.monotonic() < deadline:
                    time.sleep(.02)
                assert gone(helper), "orphan helper"
                print("PASS:", kind)
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
                if not proc.stdin.closed:
                    proc.stdin.close()
                proc.stdout.close()
        # Unsupported/credential-bearing requests fail before starting helpers.
        for change in ({"deepgram_api_key": "synthetic"}, {"stt_provider": "deepgram"},
                       {"processing_profile": "polished"}, {"version": 3}):
            result = subprocess.run([str(worker), "--local-stream"], input=json.dumps(request | change).encode() + b"\n",
                                    capture_output=True, env={}, timeout=5)
            assert json.loads(result.stdout)["error"] == "invalid_request"
        print("PASS: unsupported requests rejected")


if __name__ == "__main__":
    main()
