# English local-model benchmark (SAY-90)

Research-only comparison of three **selectable candidates**, not a product
backend or a shipped default:

| ID | Runtime | Model file |
| --- | --- | --- |
| `parakeet-unified-en-q8` | transcribe.cpp | Unified English 0.6B Q8_0 |
| `parakeet-unified-en-q4` | transcribe.cpp | Unified English 0.6B Q4_K_M |
| `whisper-small-en-q5` | whisper.cpp | small.en Q5_1 |

The runtime revisions, model revisions, exact sizes and SHA-256 hashes are
pinned in `candidates.json`. The catalog is a benchmark shortlist, not a claim
that every candidate is qualified for product distribution. In particular,
Parakeet conversion and upstream license descriptions disagree; resolve the
license for the pinned artifacts before shipping them.

## Prepare explicitly

Requirements: Python 3 standard library, Git, CMake, Ninja and a C++17 compiler.
The native adapter supports Linux and macOS, but each platform needs its own
build and physical qualification. No downloads occur in unit tests or normal
SayAll builds.

```sh
# About 1.4 GB of model files, plus native build dependencies/output.
python3 tests/local_stt_benchmark/prepare.py --download-models --build
```

Preparation builds CPU-only Release runtimes and fingerprints the adapters and
shared libraries. Builds may use native CPU instructions; do not redistribute
these research binaries as portable packages. A mismatched model or dirty /
wrong-revision runtime checkout is rejected. Interrupted `.part` files can be
removed explicitly before retrying. Nothing is installed system-wide.

All generated/downloaded artifacts are ignored beneath
`dist/local-stt-benchmark/`. Keep the source license notices with the downloaded
runtimes and models. No release packaging, publishing or signing is performed.

## Run

```sh
# Frozen corpus: 8 human utterances, 7 speakers, 3 acoustic conditions,
# plus one silence case. Three repetitions of each case.
python3 tests/local_stt_benchmark/benchmark.py \
  --runs 3 --threads 4 \
  --output dist/local-stt-benchmark/extended-report.json

# Workplace speech: at least 2,000 words, two speakers, 20–40 second clips.
python3 tests/dictation_corpus/build_ami.py \
  --output dist/local-stt-benchmark/ami --max-rows 6000
python3 tests/dictation_corpus/validate.py \
  dist/local-stt-benchmark/ami/manifest.json
python3 tests/local_stt_benchmark/benchmark.py \
  --manifest dist/local-stt-benchmark/ami/manifest.json \
  --runs 3 --threads 4 \
  --output dist/local-stt-benchmark/ami-report.json

# Select candidates explicitly, including a different execution order:
python3 tests/local_stt_benchmark/benchmark.py \
  --models whisper-small-en-q5 parakeet-unified-en-q8 --runs 3
```

Render a content-free side-by-side summary:

```sh
python3 tests/local_stt_benchmark/compare.py \
  dist/local-stt-benchmark/extended-report.json \
  dist/local-stt-benchmark/ami-report.json \
  --output dist/local-stt-benchmark/comparison.md
```

Run models sequentially. Do not compare simultaneous competing processes.
Run a reversed candidate order on an otherwise idle machine before final
qualification to reduce cache/thermal/order bias. The same randomized clip
schedule and seed are used for every model. Default decoding is English,
offline, batch size one, without external VAD, cleanup, vocabulary hints, or
translation. Whisper uses greedy decoding, best_of=1, temperature=0, no
sampling-temperature fallback and no cross-recording context. Parakeet uses
its runtime's default greedy decoder and native punctuation/capitalization.

The same checksummed audio generation and WER/CER scorer used by the Deepgram
benchmark are reused here. The AMI builder uses licensed public speech; see
`../dictation_corpus/README.md` for provenance and limitations. **It selects the
AMI training split, and the Parakeet model card lists AMI among its training
data.** Treat that corpus as a product regression/compatibility check, not an
uncontaminated held-out estimate or proof of model superiority. Final selection
needs independent held-out dictation, including additional accents/speakers.

## Q8 incremental-processing prototype

`incremental.py` compares loaded-model batch inference with real, paced Q8
buffered inference (`transcribe_stream_begin/feed/finalize`). It uses a previously
built isolated local bundle; no model or runtime download is performed. It does
not change the installed microphone workflow or service.

```sh
unshare --user --map-root-user --net \
  python3 tests/local_stt_benchmark/incremental.py \
  --bundle "$HOME/.local/share/sayall/test-builds/local-q8-live-20261008" \
  --manifest tests/deepgram_benchmark/manifest-v3.json \
  --limit 8 --runs 1 \
  --output dist/local-stt-benchmark/incremental-read.json
```

Modes are `batch`, `buffered-default` (5600/1040/1040 ms left/chunk/right), and
`buffered-balanced` (5600/560/560 ms). This is a runtime experiment, not a product
model selector. Q8 alone is tested. Feed blocks arrive on an absolute 100 ms
schedule; slow inference cannot move the producer clock and hide backlog.
Only finalized text is scored; partial hypotheses are never delivered/printed.

The default selects the first eight clean speech clips plus all no-speech cases.
Use `--all-conditions` for corpora whose conditions are not `clean`, and adjust
`--limit`, `--runs`, or `--modes` explicitly. Modes run sequentially in the supplied
order. The adapter keeps one model loaded within each mode, but **does not discard
a warmup inference**; first-inference allocation costs remain in the samples.

`stop_to_final_ms` includes inference backlog after the scheduled last sample,
not merely the duration of `finalize()`. Batch's corresponding value is its full
inference time with the model already loaded. Both exclude model loading,
recording-device startup, native worker IPC, Clean, clipboard and target-app
handling. Stream `decode_ms`/`request_to_result_ms` include paced recording time;
the generic `realtime_factor` therefore includes waiting and **must not be read
as streaming compute throughput**. Compare post-stop metrics, CPU time, maximum
feed/backlog time, accuracy and failures instead.

Results and limitations: [incremental experiment](../../docs/local-stt-incremental.md).
Changing `adapter.cpp` invalidates prior build identities for `benchmark.py`;
explicitly rebuild its research adapter before running that older harness again.
Historical reports remain tied to the source fingerprints recorded at collection.

## What the measurements mean

- **Accuracy:** micro-averaged normalized WER/CER, separately per acoustic
  condition. Failed or empty speech is counted as deletions, not excluded.
  Repeated recordings do not add independent speakers or unique words.
- **Warm latency:** one model stays loaded in a private adapter process. One
  initial speech inference is excluded as warmup. `request_to_result_ms`
  includes local PCM reading/conversion, inference, and private IPC;
  `decode_ms` measures native inference plus text extraction.
- **Fresh-process startup:** model/session loading, first inference and total
  process-start-to-first-result are recorded separately. This is one sample
  per model per invocation. Integrity verification reads the model immediately
  before launch, normally warming the filesystem page cache; caches are not
  flushed. It is not a disk-cold p95 measurement.
- **Real-time factor:** total successful speech decode time / audio duration.
  Smaller is better. Silence does not make the headline faster.
- **Memory:** process-lifetime peak RSS reported by getrusage. Includes model,
  activations and runtime, not just the model file. It is not per-clip memory
  growth, total system RAM consumption or GPU memory. Units are normalized to
  bytes on both supported platforms.
- **CPU time:** process CPU milliseconds, including worker threads, for each
  inference. Battery/energy and GPU use are not measured in this CPU pass.

Reports contain hashes, counts and timings—not reference/hypothesis text.
Transient canonical audio is in a private temporary directory and deleted on
normal completion/error. Native stdout is captured and scored, not logged;
stderr is discarded. Helper environment is allowlisted and contains no
provider credentials. Crashed/hung/overlong helpers fail the model run and are
killed as a process group. A failed/incomplete run must not be used to select
a default. The harness exits nonzero for operational failures, missing speech,
or false speech; WER is descriptive, not an implicitly selected release gate.

No model download occurs during inference. Network blocking/attempt monitoring
is **not** enforced by this harness, so these results are not an offline
privacy certification.

## Default and user choice

The initial local product uses **Q8 only**, without a model picker. Optional Q4
selection is deferred to SAY-97; Whisper remains a research comparison, not an
initial product option. Cloud-versus-local provider selection is separate from
model selection. Later installation/settings work should show download size,
readiness and qualified hardware requirements. Unsupported features are rejected;
never substitute a different model or cloud provider silently.

Choose a default only after comparing both corpora, silence behavior, and
hardware-specific latency/memory. Do not assume Q4 is faster than Q8. Publish
any CPU-only recommendation as provisional until streaming and the full
native-host boundary are measured on Linux and Apple Silicon. Quantized
English Parakeet and small.en do not implement cross-language translation.

## Still required before parity claims

- A contemporaneous Deepgram run on the same manifest (requires an explicitly
  supplied benchmark credential), compared using matching REST timing.
- Native buffered-streaming integration and actual stop-to-delivery latency.
  The paced Q8 prototype above measures the inference layer only. Offline decode
  is **not** streaming stop-to-final and cannot stand in for that measurement.
- Apple Silicon CPU/Metal and qualified Linux GPU tests.
- Longer continuous speech, additional accents/speakers, real microphones,
  competing speech, and technical-name/numeral/formatting fidelity checks.
- Model lifetime, cancellation, overload, no-network-attempt and release
  packaging tests; license clarification for Parakeet distribution.

## Tests

```sh
python3 -m unittest discover -s tests/local_stt_benchmark -p 'test_*.py'
python3 -m unittest discover -s tests/deepgram_benchmark -p 'test_*.py'
```

The tests use synthetic fake adapters and temporary artifacts; they do not
load models, download files, read credentials, or call Deepgram.
