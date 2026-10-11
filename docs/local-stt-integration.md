# Q8-only local transcription (development slice)

The first local mode uses **Parakeet Unified English 0.6B Q8_0**, automatically.
There is no model picker, Q4 integration, translation, cloud fallback, or API-key
requirement. Q4 selection is deferred to SAY-97. Existing cloud users are not automatically switched.

Linux live dictation can now use Q8 with the existing microphone, HUD and
at-most-once delivery path. `stt.streaming: true` enables incremental inference
while recording; `false` retains batch after stop. See
[Linux local testing](linux-local-testing.md) for isolated installation and rollback,
and [incremental results](local-stt-incremental.md) for measurements. Model residency
between recordings, installation UX and release packaging remain separate work. Parakeet distribution
licensing and physical macOS qualification are still release gates.

## Build and use

Normal Zig builds do not download models or build a third-party runtime. Build
the CLI/worker, then explicitly prepare the pinned CPU runtime and Q8 helper:

```sh
zig build -Doptimize=ReleaseFast
zig build process -Doptimize=ReleaseFast
python3 scripts/build-local-q8.py --prepare

zig-out/bin/sayall transcribe /absolute/clip.wav --local \
  --model-path "$PWD/dist/local-stt-benchmark/models/parakeet-unified-en-0.6b-Q8_0.gguf"
```

The script downloads **only Q8**, not Q4 or Whisper. If the benchmark runtime
is already prepared, omit `--prepare` to verify its fingerprints and build just
the new helper. It requires Python 3, Git, CMake, Ninja and a C++17 compiler.
The development helper uses build-tree shared libraries and native CPU flags;
it is **not a portable release artifact**. Do not copy it into release packages.

- `--local`: Q8, English, deterministic **Clean** by default.
- `--raw`: **Verbatim** instead; no semantic/LLM cleanup.
- `--json`: canonical worker result, including `transport: "local"` and
  `timing.local_stt_ms`. Human output remains transcript-only.
- `--model-path`: an absolute regular Q8 file path, not a model selector.
  Symlinks and any other model revision/quantization are rejected.
- Without `--model-path`, lookup is
  `$XDG_DATA_HOME/sayall/models/parakeet-unified-en-0.6b-Q8_0.gguf`, or
  `$HOME/.local/share/sayall/models/parakeet-unified-en-0.6b-Q8_0.gguf`.
  This does not install/download anything.

Inputs retain the existing limit: mono 16-bit PCM WAV at 16 kHz, 0.3–300 seconds,
up to 10 MiB. Arbitrary validated WAV chunk layouts are supported. Explicit `--local` mode
bypasses cloud configuration. Configured `stt.provider: "local"` also selects
Q8, without resolving provider credentials or loading legacy
keyword stores. Cloud formatting fields are not forwarded. Polished/legacy processing,
non-English language requests and provider formatting/keyterm options are
rejected by the local worker path rather than silently ignored or sent online.

Missing models, checksum mismatches, missing runtime, native failures and
native timeouts have distinct error codes. A missing model/runtime does not
launch a browser, install dependencies, or fall back to Deepgram.

## Ownership and private boundary

The public CLI copies audio into its existing private scratch directory and
sends the bounded request to `sayall-process`. Zig owns validation, Q8 identity,
provider selection, deterministic cleanup and canonical results. It launches
only the fixed sibling `sayall-local-q8`, never an executable supplied by a
request. Native UIs do not invoke the CLI or the native helper directly.

The worker validates Q8's exact 731,357,568-byte size and SHA-256
`4b50b6dd862bf6e346929aaf4f5eaacec003bfa3f56462d6c874b41ef2f38795`.
It keeps the verified read-only model handle open through inference, so an
atomic pathname replacement cannot substitute another model between validation
and loading. Do not modify model files in place while in use; a future installer
must use immutable artifacts and atomic installation/removal rules.

The development native-helper protocol is private and deliberately minimal:

- argv carries only the worker PID, to reject orphan startup even under a
  subreaper; stdin carries validated bounded s16le/16kHz/mono PCM until EOF.
- fd 2 carries the verified model handle, **not stderr**. The helper duplicates
  it, redirects stderr to `/dev/null`, and loads through `/dev/fd/<handle>`.
- stdout contains one bounded version-1 `{version,model,text}` result. Native
  logging is disabled and third-party stdout is suppressed during inference.
- The child environment is empty. CPU-only inference uses four threads.
- A native 120-second alarm includes input reads/load/inference. A parent-death
  watcher exits within roughly 100 ms after worker cancellation. Worker-side
  output collection is also bounded and timed; all exits reap/kill the child.
- Native core dumps are disabled. Session destruction and process exit release
  weights/audio/decoder state; this slice is intentionally not warm/persistent.

Local timing includes model hashing/loading and helper startup, unlike the warm
benchmark numbers. Do not claim streaming latency or Deepgram parity from it.

## Worker protocol v4

The private worker contract is now **v4**, updated together in Zig, Rust, Swift,
shared fixtures, and the provider benchmark harness. Host control remains v2;
legacy Linux control remains v1.

Batch requests add `stt_provider` (`deepgram` by default, or `local`) and
`local_model_path`. Existing required key fields remain present but must be
empty in local requests. All unknown fields/providers are rejected. There is
no local streaming request yet; the strict streaming schema rejects local
fields rather than starting a cloud session.

Results add closed transport `local`, optional `local_stt_ms`, and error codes
`local_model_missing`, `local_model_invalid`, `local_unavailable`, `local_failed`,
`local_timeout`, and `unsupported_features`. Local timing does not populate
Deepgram connection/finalization or REST timing fields. Result readers reject
v3; CLI/worker/native hosts must be updated together, never independently.

## Checks

```sh
zig build test test-batch
zig build check-darwin-core check-darwin-cli check-windows-core
cargo test --locked --manifest-path ui/linux/Cargo.toml
python3 -m unittest discover -s tests/deepgram_benchmark -p 'test_*.py'

# Explicit real-model tests: no downloads or transcript logging.
python3 tests/local_stt_integration.py \
  --cli zig-out/bin/sayall \
  --model dist/local-stt-benchmark/models/parakeet-unified-en-0.6b-Q8_0.gguf \
  --wav /absolute/consented-fixture.wav
```

On Linux with user namespaces enabled, prefix the real-model test command with
`unshare --user --map-root-user --net` to run without an external network.
Set `TMPDIR="$PWD/dist"` before that command to exercise atomic model replacement
on the same filesystem as the downloaded model (otherwise that check skips).
This demonstrates offline operation, not an audit of every attempted syscall.
The integration test covers both profiles despite malformed cloud configuration
and synthetic provider keys, silence, bad/missing models, malformed audio,
unsupported requests, v3 rejection, absent runtime, output overflow, and Linux
worker cancellation/native timeout. A same-size sparse corrupt model exercises
checksum rejection, and atomic pathname replacement exercises verified-handle
loading. No private audio/transcript is logged.
macOS cancellation, `/dev/fd` runtime loading and native inference require
physical qualification; cross-compilation is not evidence of support.
