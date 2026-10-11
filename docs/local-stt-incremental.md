# Q8 incremental-processing experiment

## Status

A real CPU buffered-streaming **research prototype** is implemented and measured
on the local Linux machine. It invokes `transcribe_stream_begin/feed/finalize`
with paced audio, rather than repeatedly transcribing an entire growing file.
The Linux native recording path has subsequently been connected and enabled in
an isolated local test build. See [live incremental integration](local-stt-live-incremental.md)
for its implementation, controller tests, activation and rollback. The experiment
below remains an inference-only benchmark, not a desktop delivery measurement.

Q8 remains the only initial local model. No cloud credentials, downloads,
fallback, new model picker, or keyword implementation are involved. The paused
authentication work and existing installed configuration are unchanged.

## Method

- Same pinned Q8 weights and CPU runtime as the installed isolated test bundle;
  four inference threads on the Ryzen AI Max 385 Linux machine.
- Modes run sequentially: loaded-model batch, default buffered streaming, then
  balanced buffered streaming. This is one repetition, without discarded warmup
  or reversed order. First-inference allocation costs remain in the samples.
- Streaming feeds at absolute 100 ms audio deadlines, including a shorter final
  block. Slow inference does not shift the producer clock. Reported
  `stop_to_final_ms` includes residual backlog after the scheduled last sample.
- Default tuple: left/chunk/right = 5600/1040/1040 ms. Balanced tuple:
  5600/560/560 ms. Both are trained configurations exposed by the pinned runtime.
- Commitment is final-only. Interim hypotheses stay private; only finalized
  text is scored. No partial text is typed or delivered to another application.
- Same canonical audio, reference normalization and scorer across all modes.
  Reports retain counts/hashes/timings, not references or hypotheses.
- Runs executed with `unshare --user --map-root-user --net`. The script itself
  does not establish network isolation or audit attempted syscalls.

**Timing scope:** all models are already loaded. Batch time is full inference;
stream time is remaining inference after audio ends. These numbers exclude
model hashing/loading, capture startup, native worker IPC, Clean, clipboard
handling and target-application paste. They are not measured app stop-to-delivery
latencies and do not establish parity with Deepgram.

## Results

### Read speech: eight clean clips, seven speakers, 458 words

Each mode also received one silence case. The noise variants from the larger
read/noise corpus were not included in this first incremental experiment.

| Mode | WER | CER | Post-audio p50 | Post-audio p95* | CPU time p50 |
|---|---:|---:|---:|---:|---:|
| Loaded batch | 3.06% | 0.72% | 546 ms | 1,142 ms | 2.13 s |
| Buffered default | 3.06% | 0.72% | 290 ms | 463 ms | 13.07 s |
| Buffered balanced | 3.06% | 0.72% | 397 ms | 470 ms | 22.13 s |

All three had 14 normalized word edits. All returned empty output on silence.
Maximum observed feed-start backlog: default 182 ms; balanced 203 ms.

### Meeting speech: first four AMI clips, 313 words

This is a small subset of the existing AMI corpus, not an independent accuracy
qualification. In particular, the builder selects AMI's training split and
Parakeet lists AMI among its training data.

| Mode | WER | CER | Post-audio p50 | Post-audio p95* | CPU time p50 |
|---|---:|---:|---:|---:|---:|
| Loaded batch | 6.07% | 4.24% | 996 ms | 1,102 ms | 3.88 s |
| Buffered default | 6.07% | 4.68% | 292 ms | 313 ms | 25.40 s |
| Buffered balanced | 5.43% | 3.97% | 240 ms | 293 ms | 40.07 s |

Batch and default had 19 word edits; balanced had 17. Identical aggregate WER
does not mean identical transcripts or identical error locations. Default's
CER was slightly worse than batch. Maximum observed feed-start backlog:
default 685 ms; balanced 131 ms. No run failed or missed speech.

All quantiles use nearest rank. With eight/four speech samples, p95 is the observed
maximum; this is not a reliable production-tail estimate.

Process-lifetime peak RSS was approximately 1.07 GiB for streaming versus
1.17–1.18 GiB for batch. CPU time includes all inference threads; it is not wall
latency or measured energy. Nonzero desktop background load was not controlled.

## Interpretation

The default buffered configuration reduced median remaining inference by about
47% on read speech and 71% on these meeting clips, with unchanged aggregate WER
in this small sample. That supports proceeding to native integration, **not** a
claim of equal quality or established end-to-end speed.

The cost is roughly 6–6.5 times the median CPU work of batch inference: this
model re-encodes sliding context windows. Balanced used about 10 times batch CPU
work and was slower to finish on the read set, despite its shorter lookahead.
Smaller chunks are therefore not automatically the best choice for dictation
that delivers only after stop. Start native qualification with the default
configuration; do not add another user-facing model/tuple selector.

## Native integration requirements

The first Linux implementation is now connected; see the linked integration
report above. Persistent model residency and broader qualification remain pending.

1. Start model verification/loading at recording start, then keep inference fed
   from the existing host-owned, bounded PCM stream. Do not block capture on
   inference. Ultimately retain the model in a host-owned worker across sessions,
   with explicit reset/unload and configuration-change handling, not a competing
   daemon.
2. Preserve the private worker boundary and held model descriptor. Validate
   local-only features and pinned artifacts; do not fall through to Deepgram.
3. Finish only after capture has closed/flushed. Drain the remaining PCM and
   final lookahead, then apply existing deterministic Clean/Verbatim processing
   and exactly-once delivery. Never type provisional hypotheses.
4. Exercise cancellation during load/feed/finalize, parent death, malformed
   frames, deadlines, input limits, slow/backlogged inference and restart races.
   Keep audio/text out of diagnostics and core dumps.
5. Measure actual shortcut/stop-to-delivery with the same physical microphone
   and target application. Include short taps, several-minute recordings,
   pauses, endpoint/chunk-boundary cases, noise, held-out dictation and sustained
   CPU/memory behavior. Do not substitute model-only timings for this test.
6. Only then build a separate install bundle and switch while idle, retaining
   the existing working batch build and rollback path.

## Reproduction and artifacts

See [benchmark README](../tests/local_stt_benchmark/README.md) for the read-set
command. The meeting subset used:

```sh
unshare --user --map-root-user --net \
  python3 tests/local_stt_benchmark/incremental.py \
  --bundle "$HOME/.local/share/sayall/test-builds/local-q8-live-20261008" \
  --manifest dist/local-stt-benchmark/ami/manifest.json \
  --limit 4 --all-conditions \
  --output dist/local-stt-benchmark/incremental-ami.json
```

Generated content-free reports are ignored under `dist/local-stt-benchmark/`:

- `incremental-read.json`: SHA-256
  `36bbc1b59ecd50413d4af717860746c9ad033090514c1eb770a3e9f57d3c2d58`.
- `incremental-ami.json`: SHA-256
  `4cc2776472a8e25bbf4af9c0c433d600fb79652253c6afe17a178a1c2491b8c9`.

Each report records the model/runtime/library, manifest, adapter-source and
runner fingerprints plus machine/load information. Runner validation was
strengthened between these two runs; their runner fingerprints differ, while
the inference adapter and model/runtime are unchanged. An initial two-clip
pilot is separate and is not included in the tables above.

Validation: 12 hermetic benchmark tests passed. All 39 scored model cases across
the two reported experiments completed without operational/no-speech-gate failures
(including three expected-empty silence cases). WER/CER remain descriptive; no
numerical non-inferiority gate has been established. Native microphone/desktop
streaming was not part of this original experiment; subsequent Linux controller
tests are documented in the linked integration report. Physical macOS remains
untested.
