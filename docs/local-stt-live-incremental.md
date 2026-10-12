# Linux live incremental Q8 integration

The local test service now uses incremental Q8 with Clean. The model loads at
recording start, processes bounded PCM during capture, and finalizes at stop.
There is no partial typing. **Model residency between recordings remains future
work**; this version releases the helper at session completion/cancellation.
Q8 remains the only local model. Polished is not enabled.

## Startup and completion bounds

Local startup has a 120-second host budget, including model hashing and a
90-second helper initialization budget. Capture's configured maximum duration
still applies throughout startup. Pressing the shortcut again while the HUD is
`Starting` cancels initialization, stops capture and deletes the temporary audio
without delivery. Reaching the recording limit before readiness aborts startup
with an explicit error rather than continuing to record. Shutdown interrupts
startup as well. Cancellation is scoped to the session generation, including
the race where readiness arrives just as the user cancels.

After normal recording stops, both local modes allow the native helper's
120-second finish watchdog and a 125-second Zig result deadline. Streaming has
already initialized the model and gets a 150-second host completion budget.
Explicit batch mode starts model hashing only after capture stops, so its host
budget is 270 seconds: a separate 120-second setup allowance plus the full
150-second completion allowance. These are bounded total budgets, not expected
latency or a guarantee for arbitrarily slow filesystems. Neither startup
interruption nor completion failure invokes a cloud or batch fallback.
The opt-in native smoke harness supports `--startup-interrupt cancel`, `limit`
and `shutdown` to verify prompt capture shutdown, zero delivery and audio cleanup
against a deliberately delayed worker. The stream integration harness's `--slow`
mode separately checks delayed readiness and completion.

## Local question endings

Separate recordings of the same question can yield different Q8 punctuation.
The previous `v3` test bundle only repairs existing terminal commas/periods;
it misses unpunctuated endings and subject questions, and has known statement
false positives. The revised pass is now installed in `local-own-q8-endings-v2`,
with the previous bundle retained for rollback. Network-isolated real-model
lifecycle and failure-path tests passed before activation.

The revised local Clean pass can replace a terminal comma/period or insert `?`
at an unpunctuated line/recording end for recognized English question patterns:
auxiliary inversion, straight/curly apostrophe contractions, a small wh-phrase
lexicon, and short subject questions (currently "what/who happened/called", with
optional "today/yesterday/here/there"). Leading mm/hmm/um/uh are ignored for
classification without deleting source bytes. A bounded discourse prefix
("and/or/but/so", "I mean", with fillers) is also recognized without deleting
words. Auxiliary-first questions support simple noun phrases ("Is the model
working") and negative contractions ("Isn't/Isn’t it ready"). The expanded
regression matrix covers auxiliary families, punctuation variants, embedded
clauses ("Is this where the file is"), conjunctions, statement lookalikes,
imperatives and inverted conditionals. Arbitrary clipped letters such as
"Or d is ..." are deliberately not skipped: they may represent a name/label.
Regression cases include
"What is your name", "Mm, what happened", "What's your name", and "Who called"
with missing, comma, period, and question-mark endings.

This is still a conservative pattern recognizer, **not a grammatical parser**.
Explicit protections cover tested free relatives ("What I need is this"),
reporting tails, leading quoted/bracketed content, imperatives, headings,
ellipses, and exclamations. Long direct auxiliary questions can be recognized
from their opening prefix; wh/free-relative and conditional-compatible question
openings still abstain when the 32-word prefix buffer fills. Unknown structures
are left alone. It does not infer missing internal sentence boundaries
or question intonation, and does not guarantee correctness for all statements.
Every source word/spacing byte is preserved by this punctuation pass; Verbatim
and cloud Clean bypass it. Tests also cover idempotence, allocation failures,
and routing through real Clean.

Before adding a parser/classifier, evaluate it against a separate, held-out
English set: direct/subject/negative/tag questions, fillers and contractions,
free relatives and embedded questions, quoted text, fragments, and ambiguous
statement/question pairs. Track false question insertions separately from
missed questions; require abstention on ambiguous cases, word preservation,
local-only execution, bounded latency/memory, and acceptable licensing. No
classifier dependency or model download has been added by this refinement.

Local vocabulary corrections ("deep grammar" → "Deepgram") require the SAY-64
personalization contract and are not part of this increment; local keyterms
remain explicitly rejected.

## Missing final statement periods

The active `local-own-q8-endings-v2` bundle adds a final `.` in local Clean for
recognized declarative patterns after the question-mark pass. Examples:
"I recorded this in two separate recordings", "This is working", "It works",
and "What I need is this". The pass preserves words and trailing whitespace;
existing terminal punctuation (including commas, ellipses and exclamations)
is not replaced by a period. Only the final clause is considered, not every
line or an inferred boundary within a recording.

This is a bounded rule-based pass, not full grammatical understanding. It
recognizes a small subject/predicate lexicon, contractions, simple noun phrases
and a narrow free-relative pattern. Long declarative recordings now use a
bounded opening prefix and the actual final word, not the truncated prefix's
last word. This covers long "I mean ..." statements while preserving incomplete
endings such as "because" or "and". Unknown patterns, fragments, technical text,
leading quotations and list items are conservatively left alone. Verbatim and cloud Clean bypass
this pass. Tests cover statement/question routing through real Clean,
idempotence, whitespace preservation and allocation failures. Real-model file,
incremental Controller, cancellation and worker failure-path tests passed
before the idle service switch. Current native observations: 331 ms
stop-to-dummy-sink, 6 ms cancellation cleanup; not actual paste percentiles.

## Boundary and lifecycle

- New private `sayall-process --local-stream` operation, using bounded v4
  request/ready/finish/result frames. The matched host/worker/helper are installed
  together; an older helper fails closed rather than selecting cloud or batch.
- The host retains microphone ownership. Zig opens validated PCM/WAV files,
  checks local-only features and credentials, hashes the model and keeps its
  verified descriptor open. The fixed sibling helper receives an empty
  environment, model on fd 2, and the held growing PCM file on stdin.
- The helper follows 100 ms PCM blocks using the runtime's default buffered
  tuple (5600/1040/1040 ms). Ready is acknowledged only after model initialization
  and installation of its finish handler. The host's capture file buffers audio
  collected during startup.
- After capture closes/flushed WAV creation completes, a private finish command
  signals the helper to drain remaining PCM and finalize lookahead. The signal
  handler uses a lock-free atomic; no provisional text crosses the result boundary.
- Final PCM byte count is checked against the held PCM file and finalized WAV.
  The existing Zig validation and Clean/Verbatim processing consume the private
  finalized transcript instead of re-running inference. The host delivers once.
- Local stream failures do not trigger batch or cloud retries. Deepgram retains
  its existing behavior. Local `stt.streaming: false` explicitly selects batch;
  true/default selects incremental processing.
- Startup and post-stop deadlines, bounded input/output and the helper watchdog
  remain enforced. Linux parent-death SIGKILL covers even a stopped helper; the
  parent watcher remains as an additional guard. Core dumps are disabled.
- `transport` remains `local`. For an incremental result, `local_stt_ms` measures
  residual post-stop processing, excluding recording-time inference/model load.
  It must not be interpreted as the total CPU work of the session.

Native macOS local recording remains explicitly unsupported. Compile readiness
is not physical platform qualification. No signing, release publication, model
redistribution approval, provider login or credential provisioning was performed.

## Controller measurements

Network-isolated tests used the real Linux session controller and bundle-matched
worker/helper, with paced public fixtures and a dummy delivery sink. They never
opened a physical microphone, typed, or changed the clipboard.

| Fixture | Incremental stop-to-sink | Explicit batch stop-to-sink |
|---|---:|---:|
| Short read speech | 318 ms | 936 ms |
| Longer AMI speech | 338 ms | 1,634 ms |

These are single observations, **not production percentiles or actual paste
latency**. Incremental recording startup took approximately 610–660 ms versus
21 ms for batch, reflecting verification/loading moved to recording start.
Keeping the model resident between sessions is the next startup improvement.

The [inference experiment](local-stt-incremental.md) describes accuracy and CPU
tradeoffs: default incremental processing required roughly 6–6.5 times batch
CPU work on its small sample. Longer sessions, noise, independent held-out
speech, real-device startup, target-app paste, sustained memory/CPU behavior and
physical macOS remain qualification work.

## Validation

- 125 passing Rust tests, plus the opt-in real-controller test.
- Zig tests, batch tests and Darwin core/CLI and Windows core compile checks.
- Real cancellation during capture: no delivery and no scratch audio left.
- Silence, invalid finish, timeout, disconnect, parent death (including a stopped
  helper), unsupported profiles, wrong protocol and credential-bearing requests.
- Failed local streams do not invoke the batch fallback.
- Existing file-mode Verbatim/Clean, cloud-config isolation, invalid models/audio,
  checksum checks, bounded output, cancellation/timeout and atomic model pathname
  replacement checks still pass.

```sh
python3 tests/local_live_smoke.py --network-isolated --bundle /absolute/bundle \
  --fixture /absolute/public-fixture.wav
# Add --batch for comparison or --cancel for cancellation during capture.
unshare --user --map-root-user --net \
  python3 tests/local_stream_integration.py --bundle /absolute/bundle
```

Ordinary CI remains model-free. The tests above require explicit local artifacts;
no implicit model downloads or transcript logging are added.

## Installed test and rollback

The active user bundle is:

```text
~/.local/share/sayall/test-builds/local-own-q8-endings-v2/
```

This bundle uses [SayAll's NVIDIA-source Q8 conversion](own-q8-conversion.md),
with verified SHA-256 `41762900886984bfa1283c38b2f20bd9ae9dd18d55a32fe71414af52e0e49e75`.
Its isolated config selects local Q8, `streaming: true`, and Clean, preserving
existing recording, output and HUD preferences. The managed
`99-local-q8-test.conf` service override selects it. Activation was performed while
idle. Internet socket families remain restricted, provider-key environment
variables are unset, and `LimitCORE=0` also covers the native host.

The previous Q8 incremental bundle/configuration remain untouched. Roll back while idle:

```sh
~/.local/share/sayall/test-builds/local-own-q8-endings-v2/rollback.sh
```

The script checks the override against its saved installed copy, then atomically
restores the previous override. It refuses to overwrite later user edits. Older
bundles retain their rollback scripts for stepping back through earlier builds.
No system-installed executables or desktop shortcuts were replaced. Use the usual dictation shortcut to test the active service; the
system-installed CLI still supports its unchanged toggle/status control protocol.
