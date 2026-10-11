# Local Q8 licensing and provenance

## Model and runtime boundaries

NVIDIA trained Parakeet Unified English 0.6B. SayAll's Q8 artifact is a direct
GGUF conversion and quantization of the pinned original NVIDIA weights, not a
newly trained or fine-tuned model. The conversion and native inference tools
are the separately licensed `transcribe.cpp` runtime and ggml components.
SayAll does not claim authorship of that upstream software or endorsement by
its authors.

Q8_0 is roughly 8-bit weight storage with scale and other metadata overhead.
The own-converted artifact is 731,357,696 bytes. The conversion recipe, exact
source revision, checksums and comparison evidence are documented in
[own-q8-conversion.md](own-q8-conversion.md).

## Distribution requirements

The NVIDIA Open Model License permits commercial use, derivatives and
redistribution subject to its terms. It requires the agreement and the notice:

> Licensed by NVIDIA Corporation under the NVIDIA Open Model License

The direct-source bundle includes the agreement, incorporated Trustworthy AI
terms, provenance and the required notice. It also includes the actual MIT
licenses for the application, websocket.zig, transcribe.cpp and ggml, plus
locked Rust dependency license/notice texts. A missing dependency license must
be supplied from verified upstream evidence, not another crate's copyright.

Public provenance includes the original model identity, conversion/tool hashes
and runtime revision. Full local build records are retained separately rather
than exposing local home paths, command lines or comparison commentary.
Required copyright notices are not removed for branding reasons.

Packaging these files is a compliance measure, not blanket legal clearance.
Review the applicable terms and the exact final distributed archive, including
other bundled components. Do not label the NVIDIA model MIT merely because
its inference runtime uses MIT.

## Normal release process

This feature does not add an alternate publication workflow. Follow
[releasing.md](releasing.md), including source review, version agreement,
artifact checks, supported-platform qualification and protected approvals.
The currently running developer bundle is machine-optimized and is not itself
an approved release artifact. No existing cloud user is automatically switched
to the local provider, and native macOS local recording remains unsupported.
