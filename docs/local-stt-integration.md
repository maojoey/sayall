# Local Q8 implementation and validation

Local mode is opt-in, English-only Parakeet Unified Q8, with Clean or Verbatim.
It has no API-key requirement, implicit download, model/cloud fallback, LLM,
translation or recognition keyterms. Existing cloud configurations are unchanged.
Linux owns microphone capture, cancellation, HUD and final delivery. Zig owns
validation, model identity, inference supervision and deterministic cleanup.
Native macOS local recording is explicitly unsupported; compile checks do not
qualify native inference on other platforms.

See [Linux setup](linux-local-testing.md) and [model preparation](own-q8-conversion.md).
Standard release assembly does not yet include the optional model/helper.

## CLI and model boundary

Use the matched bundle CLI, worker and helper:

```sh
/absolute/bundle/sayall transcribe /absolute/clip.wav --local \
  --model-path /absolute/bundle/models/sayall-parakeet-unified-en-0.6b-Q8_0.gguf
```

Clean is the default; `--raw` selects Verbatim. `--json` emits the canonical
result with `transport: "local"` and optional `timing.local_stt_ms`.
Configured `stt.provider: "local"` also bypasses credential resolution.
Without an explicit model path, lookup retains the compatibility filename
`sayall/models/parakeet-unified-en-0.6b-Q8_0.gguf` under nonempty
`XDG_DATA_HOME`, otherwise `$HOME/.local/share`. Nothing is downloaded.

Audio must be mono s16le, 16 kHz, 0.3–300 seconds, WAV size at most 10 MiB.
The CLI copies input to private scratch storage. Worker audio paths must be
absolute, private regular files; unsafe permissions, symlinks and aliases are
rejected. Model size and SHA-256 are checked on a held read-only handle that
stays open during inference. Model updates must use immutable artifacts and
atomic replacement, never in-place modification. The runtime retains recognition
of the previous pinned Q8 artifact for compatibility; builders distribute only
the direct NVIDIA-source artifact.

Zig launches only the fixed sibling `sayall-local-q8`, with an empty environment,
four CPU threads and no core dumps. The private helper receives the verified
model handle on fd 2 (not stderr), suppresses native logs and emits a single
bounded result. Cancellation, parent death, timeout and completion terminate
and reap it. No model is retained between recordings.

## Protocol and lifecycle

Worker protocol v5 must be updated together across Zig, Rust, Swift and fixtures;
v3 is rejected. Host control remains v2, with legacy Linux control v1.
Local requests contain empty credential fields, `stt_provider: "local"`,
`local_model_path`, and local-compatible processing settings. Unknown fields
and unsupported features fail closed.

With `stt.streaming: true`, the host starts `sayall-process --local-stream`
with growing `pcm_path` and `wav_path`. The worker emits `ready` after model
initialization. After capture is flushed, the host sends `finish` and receives
one final result—never partial typing. Stream failure does not trigger batch
fallback. Explicit `streaming: false` performs batch inference after capture.

- Startup: 120-second host budget, including a 90-second helper initialization
  budget. Generation-scoped cancellation also covers readiness races.
- Recording limit during startup: freeze capture, preserve audio and finalize
  after readiness. Explicit cancellation, capture failure and shutdown discard
  audio without delivery.
- Completion: 120-second helper finish watchdog, 125-second Zig result deadline,
  150-second streaming host budget. Batch/CLI allow 270 seconds including setup.
  Repeated finish signals do not extend the helper watchdog.
- PCM is capped at 300 seconds. Native capture trims final recorder overshoot
  in place before WAV generation, preserving the worker's held inode.
- These are failure bounds, not expected latency. Cloud budgets are unchanged.

Malformed frames/audio requests return `invalid_request`. Other local errors
are `local_model_missing`, `local_model_invalid`, `local_unavailable`,
`local_failed`, `local_timeout` and `unsupported_features`.

## Checks

```sh
zig build test test-batch check-darwin-core check-darwin-cli check-windows-core
cargo test --locked --manifest-path ui/linux/Cargo.toml
python3 tests/test_local_q8_licenses.py
python3 tests/test_third_party_licenses.py
python3 tests/test_convert_local_q8.py
```

Real-model tests require explicitly prepared artifacts; no downloads or
transcript logging occur:

```sh
python3 tests/local_stt_integration.py --cli /absolute/bundle/sayall \
  --model /absolute/bundle/models/sayall-parakeet-unified-en-0.6b-Q8_0.gguf \
  --wav /absolute/consented-fixture.wav
unshare --user --map-root-user --net \
  python3 tests/local_stream_integration.py --bundle /absolute/bundle
python3 tests/local_live_smoke.py --network-isolated --bundle /absolute/bundle \
  --fixture /absolute/consented-fixture.wav
```

File tests cover both profiles, configured mode, invalid cloud config isolation,
model/input failures, protocol rejection, bounded output, cancellation and atomic
model replacement. Set `TMPDIR` to a private location on the model filesystem
to exercise the replacement case rather than skipping it.

Stream tests cover protocol/audio-path failures and helper lifecycle; `--slow`
checks delayed readiness/completion and `--long` the 300-second boundary.
Controller tests use paced fixture capture and dummy delivery, not a microphone
or clipboard. Add `--batch`, `--cancel`, or
`--startup-interrupt {cancel,limit,shutdown,capture-exit}` for those paths.
Network isolation proves offline operation, not absence of attempted networking.
Real paste behavior and physical platform qualification require separate checks.
