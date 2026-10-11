# Local Q8 license evidence and packaging

## Finding

NVIDIA's Open Model License permits commercial use, derivatives and
redistribution subject to its conditions (§§2–3). There is no identified
blanket prohibition against distributing Q8. Section 3.1 requires a copy of
the agreement and the attribution carried in `Notice.txt`. Section 2.3
incorporates the Trustworthy AI terms; other obligations include guardrails,
trade compliance and the license's update provision. Parakeet is not Cosmos;
the Cosmos-specific branding provision is not a Parakeet branding requirement.

The pinned handy-computer conversion claims CC-BY-4.0 was inherited from the
base model. NVIDIA's pinned upstream card says NVIDIA Open Model License.
The converter's own embedded copy of that card also says NVIDIA Open Model
License. We cannot fix this inconsistent third-party declaration ourselves.
NVIDIA §3.3 permits additional terms for modifications, but does not establish
that the converter intended that interpretation or owns all rights needed
for its CC-BY declaration. Merely bundling two licenses is not proof of that.

**Status: provenance and missing license packaging addressed; the conversion's
licensing scope still needs clarification before public model redistribution.**
Do not describe this evidence as legal approval or change the weight license
to CC-BY-only. Existing locally installed test bundles are not retroactively
updated by changes to the builder.

## Closure options

1. Obtain confirmation/corrected metadata from the conversion publisher that
   distinguishes NVIDIA weights from any separately licensed conversion work.
   Keep the evidence tied to the exact artifact revision and hash. A proposed
   clarification request is in `docs/local-q8-licensing.md`; none has been sent.
2. Alternatively, explicitly obtain NVIDIA's original weights and reproduce
   our own GGUF/Q8 conversion using the MIT runtime tools. Record original and
   converted hashes, tool revision and conversion command; qualify the new
   artifact before changing the allowlisted hash. This avoids relying on the
   third-party weight declaration, but is not implemented by this review.

The builder remains local-test-only and validates this license packet before
building. It copies the packet and runtime MIT notices into new bundles and
hashes them in build.json. Actual release packaging and third-party dependency
inventory still require their own review; this packet covers the named model
and the two runtime notices, not every system library.

## Evidence sources

- NVIDIA model card at upstream revision referenced by the converter:
  https://huggingface.co/nvidia/parakeet-unified-en-0.6b/blob/d4ac9928f3bf238223ff0779c06b8149bf8ac4e1/README.md
- Conversion card:
  https://huggingface.co/handy-computer/parakeet-unified-en-0.6b-gguf/blob/d5249700b2382bf5c5024c2421d101b8db54a629/README.md
- NVIDIA agreement (version October 24, 2025):
  https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/
- Incorporated terms:
  https://www.nvidia.com/en-us/agreements/trustworthy-ai/terms/
- Converter-declared license:
  https://creativecommons.org/licenses/by/4.0/legalcode.txt
- Runtime MIT licenses at pinned revision:
  https://github.com/handy-computer/transcribe.cpp/tree/135d744cbb87dcac8a04dbc9bab1bb25ada5d701

License texts were retrieved from these official license URLs during review.
NVIDIA snapshots are readable Markdown extractions, not translations or
summaries; the CC text is a text snapshot. Source URLs and file SHA-256 values
are recorded in manifest.json. Recheck the official terms when preparing a
release or changing artifacts, especially because NVIDIA §2.1 allows updates.
No model downloads, upstream issue submission or release publication were
performed as part of this review.
