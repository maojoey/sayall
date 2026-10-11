# Local English STT: initial CPU comparison

Date: 2026-10-07. Tracks [SAY-90](https://linear.app/leetsllc/issue/SAY-90).
Reproduction and measurement definitions:
[`tests/local_stt_benchmark/README.md`](../tests/local_stt_benchmark/README.md).

## Implementation decision

The user selected **Q8 as the only initial local model**. Q4 selection is
explicitly deferred to SAY-97; Whisper integration is also outside the first
release. The [local file-transcription slice](local-stt-integration.md) now
implements that selection without changing existing cloud/native dictation.
Qualification and licensing gates below remain open.

## Benchmark recommendation (historical): provisional default candidate

Carry **Parakeet Unified English Q8** forward as the **low-latency default
candidate**, with **Q4 as a selectable lower-memory/download alternative**.
Q8 was faster on this CPU; Q4 was not universally less accurate. Keep Whisper
small.en Q5 as a possible minimal-memory option, but do not qualify it for
production from these results alone: its silence handling needs work and its
workplace-speech accuracy was substantially worse in the tested configuration.

No production provider, configuration default, or model-selection UI has
changed. Model choice is a product requirement, not an invitation to silently
substitute another model or cloud provider when the selected one is unavailable.

This decision is provisional pending held-out dictation, buffered-streaming,
Apple Silicon and full native-host qualification. Parakeet's exact artifact
licensing must be resolved before distribution; the conversion metadata and
upstream model card name different licenses. Deepgram parity is **not**
established: the dedicated benchmark credential was unavailable and no cloud
requests were made.

## Machine and method

- AMD Ryzen AI Max 385, Linux x86_64, kernel 7.2.3-arch1-3, about 30.6 GiB RAM.
- CPU-only inference, four threads, batch size one, Release native builds.
- Models executed sequentially, same randomized case order, three repetitions.
- One persistent model/session per candidate and corpus; first inference is
  warmup, excluded from the warm accuracy/latency summaries.
- English offline transcription, no external VAD, cleanup, translation or
  vocabulary hints. Whisper uses greedy best_of=1 with no temperature fallback;
  these are explicit low-latency decoding settings, not each engine's best
  achievable accuracy under unlimited compute.
- WER/CER reuse the existing Deepgram benchmark normalization (case and
  punctuation ignored). Formatting, spoken-number equivalence, semantic
  preservation and downstream cleanup are not separately scored.
- Latency includes PCM reading/conversion, inference and private IPC. It does
  **not** include microphone capture or delivery, and is **not** streaming
  stop-to-final latency.
- This was a working desktop, not a thermally/power-isolated lab machine.
  Background load was nonzero. Repetitions are not independent speech samples.

## Frozen read-speech + noise corpus

Eight human utterances, seven speakers, 458 unique reference words. Each
utterance is tested clean and with deterministic 10 dB / 5 dB office noise,
plus one synthetic-silence case. Three repetitions produce 75 measured cases
per model. Conditions and repetitions do not increase unique speech coverage.

| Model | WER | CER | Warm p50 | Warm p95 | Peak process RSS | Silence non-empty outputs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Parakeet Unified Q8 | 3.35% | 0.79% | 545 ms | 1,130 ms | 1,229 MiB | 0 / 3 |
| Parakeet Unified Q4 | 3.35% | 0.82% | 722 ms | 1,477 ms | 987 MiB | 0 / 3 |
| Whisper small.en Q5 | 5.09% | 1.54% | 1,732 ms | 3,316 ms | 361 MiB | 3 / 3 |

| Model | Clean WER | 10 dB noise WER | 5 dB noise WER |
| --- | ---: | ---: | ---: |
| Parakeet Unified Q8 | 3.06% | 3.49% | 3.49% |
| Parakeet Unified Q4 | 3.06% | 3.49% | 3.49% |
| Whisper small.en Q5 | 4.80% | 5.46% | 5.02% |

The silence result means the raw adapter returned non-empty text; it is not a
claim about a finished app with VAD or explicit no-speech-marker handling.
Whisper's completed benchmark exits nonzero because the silence gate failed;
it did not crash or time out.

A one-repetition reverse-order check (Whisper, Q4, Q8) reproduced the same
WER/CER and silence behavior. Warm medians were 1,734 / 721 / 551 ms
respectively, preserving the speed ordering. This is a useful order-bias
sanity check, not a substitute for broader hardware qualification.

## AMI workplace-speech regression corpus

28 single-speaker assembled clips, two speakers, 2,316 unique reference words,
765.92 seconds of audio, average clip 27.35 seconds. Three repetitions produce
84 measured cases per model. References are Verbatim, not deterministic Clean.

**Training-overlap warning:** the existing SayAll builder selects AMI's
training split, and [Parakeet's upstream model card](https://huggingface.co/nvidia/parakeet-unified-en-0.6b)
lists AMI among its training datasets. These are
useful compatibility/regression measurements, not an uncontaminated held-out
comparison. Do not extrapolate the accuracy gap to arbitrary user dictation.
Joined utterances are also not continuously spoken recordings.

| Model | WER | CER | Warm p50 | Warm p95 | Peak process RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Parakeet Unified Q8 | 5.01% | 2.40% | 935 ms | 1,093 ms | 1,205 MiB |
| Parakeet Unified Q4 | 4.49% | 2.14% | 1,198 ms | 1,389 ms | 963 MiB |
| Whisper small.en Q5 | 18.70% | 11.91% | 1,806 ms | 3,305 ms | 361 MiB |

All speech cases returned non-empty results. No operational failures occurred.
Q4 made 12 fewer word edits per unique corpus pass than Q8 (104 versus 116);
two speakers are insufficient evidence for a general accuracy ordering.
Q8's roughly 22% lower median latency than Q4 is the reason to prefer it for
the next low-latency prototype, not a claim that it is more accurate.

## Startup and storage tradeoffs

| Model | Model download | Model/session load, read-speech / AMI | First result including startup, read-speech / AMI |
| --- | ---: | ---: | ---: |
| Parakeet Unified Q8 | 731 MB | 171 / 249 ms | 402 / 1,243 ms |
| Parakeet Unified Q4 | 477 MB | 133 / 173 ms | 426 / 1,460 ms |
| Whisper small.en Q5 | 190 MB | 42 / 38 ms | 1,600 / 1,819 ms |

These are single fresh-process samples on different warmup utterances, not
startup p95. SHA-256 verification reads each model immediately before launch,
so model-file page cache is normally warm. Do not describe this as disk-cold
startup. Peak RSS depends on clip duration and decode configuration; it is not
a universal minimum-RAM requirement or GPU-memory estimate.

## Evidence locations

Generated artifacts remain untracked under `dist/local-stt-benchmark/`:

- `extended-report.json`: SHA-256
  `989f5b8e4badd0f61b509bcb4386c7dd5f4470f659b6b8fc52168ca0509c9fdd`
- `ami-report.json`: SHA-256
  `7a2e133264006db20578e74dcef56c8f1be3834d6d913f0f109acb5e6a2e6216`
- `reverse-report.json`: SHA-256
  `b61cd67e624321467a484eea92a7b5f87f0452e2f53715efd79fdcd3636b510e`
- `comparison.md`: generated side-by-side tables.
- `ami/manifest.json`: SHA-256
  `db63436d68848910ad33eeb4b2c216e6858cae803cf2d3001cffe059889130e0`
- Frozen read-speech/noise manifest: SHA-256
  `981a3e79ef71246eb66a73b23ee7b75d46f075c48f6b5677b34b33693e9ef78e`.

Each JSON report includes model/runtime revisions, artifact hashes, hardware,
settings, startup measurements, per-case content-free scores, CPU time,
real-time factor and peak RSS. The model catalog and benchmark source are
checked in; binaries, downloaded models, generated audio and reports are not.

## Next gates

1. Independent held-out dictation: broader speakers/accents, continuous long
   recordings, technical vocabulary and number/formatting fidelity.
2. Paced buffered streaming: CPU backlog, boundary accuracy and post-stop
   finalization, rather than assuming offline speed translates to streaming.
3. Physical Apple Silicon CPU/Metal and supported Linux GPU measurements.
4. Contemporaneous Deepgram baseline with an explicitly supplied benchmark key.
5. Model distribution license review, native-host lifecycle/delivery and
   no-network-attempt qualification before making support or parity claims.
