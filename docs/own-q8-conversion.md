# SayAll's NVIDIA-sourced Q8 conversion

## Result and limits

A separate Q8 artifact was built directly from the pinned NVIDIA archive,
without using third-party quantized weights during conversion. It uses the pinned
MIT-licensed transcribe.cpp conversion/quantization code. This is a conversion,
not a fine-tune or a newly trained model.

Two independent conversions produced identical F32 and Q8 hashes. All **961
quantized tensor payloads, names, shapes and types match** the reference Q8 artifact.
The only GGUF differences are KV count and three explicit false capability
flags (language detection, translation, speaker diarization) absent from the
baseline. Tokenizer and other metadata match. Both actual GGUFs already name
the NVIDIA license. Our direct-source path records the original NVIDIA archive
and applicable license without depending on third-party conversion declarations.

**94 paired cases / 188 inferences** produced byte-identical transcripts and
identical result codes. There were no operational or no-speech classification
failures. This establishes parity on these samples, not superior recognition
or general release readiness. The improvement is provenance/reproducibility.

**Installed for local testing** in the isolated `local-own-q8-v2` bundle.
Zig accepts the exact size/hash pairs of this artifact and the previously tested Q8 artifact;
the native helper accepts their two sizes, with full hash verification still
performed by Zig on the held descriptor. No arbitrary model bypass or fallback
was added. The previous `local-q8-questions-v4` bundle remains available for
rollback. Public release and cross-machine/platform qualification are pending.

The new artifact passed network-isolated native file Clean/Verbatim, streaming,
silence, invalid inputs, both allowed sizes with invalid hashes, worker parent
death, timeout, disconnect, atomic pathname replacement, output bounds, and
native Controller tests for incremental, batch and cancellation. Observed
incremental startup/stop-to-dummy-delivery: 690/321 ms; cancellation cleanup:
8 ms; batch stop-to-dummy-delivery: 984 ms. These are single fixture observations,
not actual paste latency percentiles. Service activation was performed while idle.

```sh
~/.local/share/sayall/test-builds/local-own-q8-v2/rollback.sh
```

The builder selects this artifact explicitly with
`--own-conversion dist/own-q8/conversion-2`, verifies provenance and notices,
and copies the NVIDIA license packet without applying the converter's CC-BY
model-card declaration. It still does not download or switch services.

## Artifact identity

- NVIDIA repository: `nvidia/parakeet-unified-en-0.6b`
- Source revision: `d4ac9928f3bf238223ff0779c06b8149bf8ac4e1`
- Original `.nemo`: 2,474,055,680 bytes
- Original SHA-256: `ec23ed9150c8fde49072c3e2d61678ab903dbcef389d658db833420cbc1da35b`
- Tools: `transcribe.cpp`, `135d744cbb87dcac8a04dbc9bab1bb25ada5d701`
- F32 SHA-256: `369a057c0f2dd2085055f41f989cde07d5e4b299376e31ed5f636093c75b821a`
- Own Q8: 731,357,696 bytes
- Own Q8 SHA-256: `41762900886984bfa1283c38b2f20bd9ae9dd18d55a32fe71414af52e0e49e75`
- Checked-in pins: `tests/local_stt_benchmark/{nvidia-source,own-q8}.json`
- Local artifact: `dist/own-q8/conversion-2/models/sayall-parakeet-unified-en-0.6b-Q8_0.gguf`
- Independent repeat: `dist/own-q8/conversion-3/`

Each successful output contains `provenance.json` (source/tool/package hashes
and versions, commands and artifact identities), `Notice.txt`, the NVIDIA
agreement/Trustworthy AI terms, and runtime/ggml MIT licenses. No converter
CC-BY declaration is applied to the NVIDIA-derived weights. The NVIDIA license
still has conditions; direct conversion does not waive them or imply endorsement.

## Sampled comparisons

Both candidates used the same runtime, adapter, four CPU threads, and audio.
Model order alternated per clip/repetition. Adapter inference ran in a network
namespace. No transcript/reference text was persisted in reports. Models were
loaded before scoring, with no discarded warmup. Streaming uses real-time
100 ms feeds and measures the residual after the scheduled final audio sample.

| Sample | Mode | Runs | Paired cases | WER, both | Reference p50 | Own p50 |
|---|---|---:|---:|---:|---:|---:|
| 8 clean read clips + silence | batch | 2 | 18 | 3.06% | 558 ms | 563 ms |
| same | incremental | 2 | 18 | 3.06% | 293 ms | 294 ms |
| 4 AMI clips | batch | 1 | 4 | 6.07% | 987 ms | 992 ms |
| same | incremental | 1 | 4 | 6.07% | 293 ms | 293 ms |
| 8 read clips × 3 conditions + silence | batch | 2 | 50 | 3.35% | 555 ms | 559 ms |

Batch values are request-to-result; incremental values are stop-to-final, not
entire paced-session duration. Small differences are not evidence of a speed
improvement. AMI streaming CER is 4.68% for both (batch 4.24%). Read/noise CER is
0.79% for both. Full result codes/text match, beyond normalized WER/CER.

For AMI, peak RSS was approximately 1,200 MiB for batch and 1,097/1,098 MiB for
incremental (reference/own). Noise batch peaks were approximately 1,231/1,232 MiB.
The first read comparison's RSS is **not interpretable as adapter-only peak**:
in-process tensor inspection inflated the pre-exec inherited high-water mark.
The harness now isolates tensor inspection in a child; AMI/noise runs use that
fix. Timing/text comparisons from the first run remain useful.

This is a small, non-independent corpus. AMI includes training-overlap risk.
Repeated clips are not new speakers. CPU background load and memory pressure
were uncontrolled; jobs were capped at four CPU equivalents, 5 GiB memory and
512 MiB swap. No hardware tiers, disk-cold performance, energy, physical macOS,
or cloud parity were qualified by the engine comparison. Native Linux smoke
and failure-path results from subsequent integration are listed above.

Reports: `dist/own-q8/comparison-{read,ami,noise}.json`,
`tensor-comparison.json`, `reproducibility.json`. These are ignored local
artifacts, not committed model weights or personal transcripts.

## Reproduction

No ordinary build or test downloads anything. Explicit preparation requires
network access and sufficient disk space. The source hash/size were taken from
Hugging Face's LFS metadata at the pinned NVIDIA revision, not an unpinned main.

1. Create an isolated Python 3.12 environment. The observed package versions are
   in `tests/local_stt_benchmark/conversion-requirements.txt`. Install CPU torch
   from the PyTorch CPU index; other dependencies from PyPI. This version list
   is not a wheel-hash lock or a cross-platform reproducibility guarantee.
2. Download the pinned source explicitly using `prepare.download`:

   ```sh
   python3 - <<'PY'
   import json, pathlib, sys
   sys.path.insert(0, 'tests/local_stt_benchmark')
   from prepare import download
   pin = json.loads(pathlib.Path('tests/local_stt_benchmark/nvidia-source.json').read_text())
   download(pin, pathlib.Path('dist/own-q8/source'))
   PY
   ```

3. Build `transcribe-quantize` from the clean pinned runtime in a separate CMake
   directory with `TRANSCRIBE_BUILD_TOOLS=ON`, Release mode, and GPU backends
   disabled. See `dist/own-q8/runtime-build.log` for this machine's build.
4. Run the converter in a network-isolated, resource-limited environment. Use
   an unused output path; never overwrite a model:

   ```sh
   unshare --user --map-root-user --net dist/own-q8/venv/bin/python \
     scripts/convert-local-q8.py \
     --source dist/own-q8/source/parakeet-unified-en-0.6b.nemo \
     --runtime dist/local-stt-benchmark/transcribe.cpp \
     --quantizer dist/own-q8/runtime-build/bin/transcribe-quantize \
     --output dist/own-q8/new-conversion
   ```

   Our executed conversions additionally used systemd user resource limits:
   MemoryHigh=3G, MemoryMax=5G, MemorySwapMax=1G, CPUQuota=200%, Nice=10,
   LimitCORE=0, RestrictAddressFamilies=AF_UNIX. Each successful conversion
   took approximately 20 seconds after dependencies/source were available;
   this excludes setup, downloads and comparison time.

   The loader extracts only bounded, uniquely named regular archive members
   into chosen paths. It uses `torch.load(weights_only=True, mmap=True)` on
   CPU and refuses non-tensor state dictionaries. It never falls back to
   unsafe pickle, NeMo class loading or implicit Hugging Face downloads.
   Upstream tensor mapping and Q8 quantization are unchanged. The wrapper
   revision/hash and Python package versions are recorded with each output.

5. Compare against the existing baseline bundle without modifying it:

   ```sh
   unshare --user --map-root-user --net dist/own-q8/venv/bin/python \
     tests/local_stt_benchmark/compare_own_q8.py \
     --conversion dist/own-q8/new-conversion \
     --bundle "$HOME/.local/share/sayall/test-builds/local-q8-questions-v4" \
     --manifest tests/deepgram_benchmark/manifest-v3.json \
     --limit 8 --clean-only --runs 2 --output dist/own-q8/new-report.json
   ```

   Reports must complete without failures. Investigate any tensor, transcript,
   status, accuracy or material performance regression before promotion. Do
   not infer superiority merely from a successful conversion.
