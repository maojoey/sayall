# Linux Q8 live dictation: isolated local testing

The Linux native host supports `stt.provider: "local"`. The default and only
local model is Parakeet Unified English Q8. Microphone capture, HUD, stop tail,
shortcuts and delivery remain native-owned; Zig handles validation, inference
and cleanup. With `stt.streaming: true` (the local default), Q8 loads at recording
start and processes paced PCM while recording. Stop flushes the remaining context
and delivers only final text. `stt.streaming: false` retains explicit batch-after-
stop behavior. The model is not yet retained between recordings.

No Deepgram/Cerebras keys or cloud fallback are used. Clean is the local
default; Verbatim is also supported. Polished, other models, non-English and
keyterms are explicitly rejected. Existing cloud users/configs are unchanged.
Native macOS local recording remains unsupported and is rejected before keyring
lookup. Portable Linux packaging and distribution approval remain release gates.

## Build

First prepare the pinned runtime and direct NVIDIA-source Q8 conversion as
explained in [model preparation](own-q8-conversion.md). Then:

```sh
python3 scripts/build-local-live-linux.py \
  --cache dist/local-q8 --own-conversion dist/local-q8/conversion \
  --prefix "$HOME/.local/share/sayall/test-builds/local-q8-live"
```

Choose a new prefix for every build. The builder refuses to overwrite an
existing one. It verifies the cached model and runtime revision, builds the
CLI/native host/worker/helper, and copies Q8 and runtime libraries into the
bundle. Relative RPATHs remove dependence on the repository's build tree.
`build.json` records artifact hashes and provenance.

These are CPU-native development binaries for this machine, **not portable
release artifacts**. No downloading, signing, publishing, service changes, or
credential copying is performed by this build script.

## Isolated configuration

Create `<bundle>/config/sayall/` with mode `0700` and a `config.json` with mode
`0600`. Use an actual absolute model path (not a literal `$HOME` in JSON):

```json
{
  "stt": {
    "provider": "local",
    "streaming": true,
    "local_model_path": "/absolute/test-bundle/models/sayall-parakeet-unified-en-0.6b-Q8_0.gguf"
  },
  "processing": { "mode": "clean" },
  "recording": { "max_seconds": 300, "min_ms": 300, "stop_tail_ms": 10 },
  "output": { "method": "paste", "trailing_space": true }
}
```

`local_model_path` is a location, not a model selector. Q8 is automatic; the
`model` field can be omitted. Copy only desired recording/output/HUD preferences
from the existing config, not credentials. Cloud credentials and formatting
values are not forwarded. The `streaming` flag now selects the local incremental
path rather than opening a cloud connection. A switch to Polished fails before saving or recording.

Validate with the bundle-matched CLI/worker:

```sh
XDG_CONFIG_HOME="<bundle>/config" <bundle>/sayall config validate --json
<bundle>/sayall-process --local-readiness /absolute/path/to/Q8.gguf
```

The readiness operation returns bounded v4 `{version,ready,error?}` JSON. It
checks model presence/size/readability and helper presence **before microphone
capture**, not full integrity. `sayall doctor` performs the same bounded probe
for local configurations and reports missing/unavailable artifacts as failures.
SHA-256 verification still happens on the held
model handle before inference. Missing/incompatible artifacts do not start a
cloud session or trigger downloads.

## Activate safely

Preserve existing service drop-ins and the original cloud configuration. Add a
later, separate user-service drop-in such as
`~/.config/systemd/user/sayall-hud.service.d/99-local-q8-test.conf`:

```ini
[Service]
ExecStart=
ExecStart=/absolute/test-bundle/sayall-hud --autostart
Environment=XDG_CONFIG_HOME=/absolute/test-bundle/config
UnsetEnvironment=DEEPGRAM_API_KEY CEREBRAS_API_KEY GROQ_API_KEY SAYALL_STT_MODEL SAYALL_LLM_MODEL SAYALL_TEST_AUDIO_FIXTURE SAYALL_TEST_AUDIO_START_GATE SAYALL_TEST_AUDIO_EOF_PATH
RestrictAddressFamilies=AF_UNIX AF_NETLINK
LimitCORE=0
```

The address-family restriction keeps PipeWire, Wayland and D-Bus available
while disallowing Internet socket families. Set `LimitCORE=0` for the host as
well; the native helper also disables its own core dumps. Test on the target desktop;
this does not constitute a syscall-level no-network-attempt audit.

Run `systemctl --user daemon-reload`, then restart `sayall-hud.service` **only
while `sayall status` is `idle`**. Confirm the new service PID/ExecStart and idle
status before testing. The existing toggle shortcut/control protocol is
unchanged. The system-installed CLI can still toggle/status the new host;
for new local file/configuration commands use the bundle CLI with the isolated
`XDG_CONFIG_HOME`. Do not independently replace the system-installed worker.

Rollback: while idle, remove only the added local-test drop-in, reload systemd,
and restart the service. The prior build override and original config remain
intact. Do not remove unrelated drop-ins or reset desktop configuration.

## Validation without recording or typing

```sh
python3 tests/local_live_smoke.py --network-isolated \
  --bundle "$HOME/.local/share/sayall/test-builds/local-q8-live" \
  --fixture /absolute/consented-16kHz-mono-fixture.wav
```

This runs an opt-in Rust test using the **real native session controller**, paced
fixture capture, the bundle's Q8 worker and a dummy delivery sink. It verifies
Recording/Stopping/Processing/Delivering/Success, exactly one delivery, and
scratch cleanup. It never records a physical microphone, types, or changes the
clipboard, and logs no transcripts. Use a 0.3–30-second public/consented fixture.
It refuses to replace an existing test-worker sibling.

Normal CI remains model-free. See [implementation and validation](local-stt-integration.md)
for explicit real-model and failure-path checks. Physical microphone quality,
real target-application paste behavior, sustained resource use and portable
release packaging remain qualification items.
