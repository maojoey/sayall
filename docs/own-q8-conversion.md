# Q8 model preparation and licensing

SayAll uses NVIDIA Parakeet Unified English 0.6B converted directly from NVIDIA's
weights to GGUF Q8_0. This is conversion, not training or fine-tuning. No
third-party quantized model is needed.

## Pinned inputs and required notices

The source, runtime and output identities are in
[`scripts/local-q8/`](../scripts/local-q8/):
- `nvidia-source.json`: NVIDIA repository, revision, archive size and SHA-256.
- `runtime.json`: exact transcribe.cpp source repository and revision.
- `own-q8.json`: expected F32/Q8 identities.
- `conversion-requirements.txt`: observed Python dependency versions, not a
  wheel-hash lock or a cross-platform reproducibility guarantee.

The Q8 output is 731,357,696 bytes, SHA-256
`41762900886984bfa1283c38b2f20bd9ae9dd18d55a32fe71414af52e0e49e75`.

Redistribution must retain the NVIDIA Open Model License and incorporated
Trustworthy AI terms in [licenses/local-q8](../licenses/local-q8/), the runtime
and ggml MIT notices from the pinned checkout, and this attribution:

> Licensed by NVIDIA Corporation under the NVIDIA Open Model License

The bundle builder retains these in `licenses/own-q8/` and `Notice.txt`, along
with minimal public provenance. Application, websocket.zig and locked Rust
dependency notices are retained in `licenses/sayall/`. Accurate upstream
provenance and copyright notices must not be removed for branding reasons.
Conversion does not waive NVIDIA's conditions or imply endorsement. Automated
integrity checks are not legal clearance.

## Explicit preparation

Normal builds and tests do not download weights or run conversion. Use an
isolated Python 3.12 environment with the listed dependencies; install CPU torch
from the PyTorch CPU index. Provide adequate space for the source archive,
extracted weights, F32 output and Q8 output.

Download `parakeet-unified-en-0.6b.nemo` from the NVIDIA repository at the exact
revision in `nvidia-source.json`, into `dist/local-q8/source/`. The converter
checks its size and SHA-256 before opening it. Never substitute an unpinned
source or overwrite an installed model.

Prepare a clean runtime checkout and CPU quantizer (explicit network access):

```sh
git clone https://github.com/handy-computer/transcribe.cpp.git dist/local-q8/transcribe.cpp
git -C dist/local-q8/transcribe.cpp checkout --detach 135d744cbb87dcac8a04dbc9bab1bb25ada5d701
cmake -S dist/local-q8/transcribe.cpp -B dist/local-q8/quantizer -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_TOOLS=ON \
  -DTRANSCRIBE_BUILD_TESTS=OFF -DTRANSCRIBE_BUILD_EXAMPLES=OFF \
  -DTRANSCRIBE_METAL=OFF -DTRANSCRIBE_VULKAN=OFF -DTRANSCRIBE_CUDA=OFF \
  -DTRANSCRIBE_HIP=OFF -DTRANSCRIBE_USE_SYSTEM_BLAS=OFF
cmake --build dist/local-q8/quantizer --target transcribe-quantize -j 4
```

Run conversion with the isolated environment's Python, preferably in a
network-isolated, resource-limited environment. Choose an unused output path:

```sh
python scripts/convert-local-q8.py \
  --source dist/local-q8/source/parakeet-unified-en-0.6b.nemo \
  --runtime dist/local-q8/transcribe.cpp \
  --quantizer dist/local-q8/quantizer/bin/transcribe-quantize \
  --output dist/local-q8/conversion
```

The loader extracts only bounded, unique regular archive members into chosen
paths, uses CPU `torch.load(weights_only=True, mmap=True)`, and requires a
tensor-only dictionary. Upstream tensor mapping and quantization are unchanged.

Keep the full private `provenance.json` with the conversion. It records package
versions, commands, local paths and hashes; it is not the public distribution
record. The [Linux bundle builder](linux-local-testing.md) verifies the expected
artifact identities and notices before copying the model. Neither conversion
nor bundling installs, activates, signs or publishes anything.

Portable CPU/system-library compatibility, installation/upgrade/rollback,
physical-device delivery, privacy auditing and distribution approval remain
release gates. Follow [the normal release process](releasing.md); these
machine-native development bundles are not approved release assets.
