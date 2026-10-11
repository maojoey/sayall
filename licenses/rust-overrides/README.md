# Rust license snapshots missing from published crates

These texts are copied verbatim from each crate's upstream repository at the
revision in its `.cargo_vcs_info.json`. `manifest.json` records package version,
URL, revision and SHA-256. Normal release assembly reads these files offline;
it never downloads an unpinned license or substitutes another author's MIT
copyright notice. License updates require a new reviewed snapshot when the
locked crate version/revision changes.

The gtk4 layer-shell crates omit the repository-root MIT license from their
published crate archives. The gl/gl_generator/khronos_api archives similarly
omit the repository-root Apache license. The generator also discovers lowercase
license filenames, preserves NOTICE/AUTHORS files, and recognizes r-efi 6.0.0's
complete MIT grant and copyright in its own AUTHORS file.

Runtime dependencies, build dependencies and platform-only dependencies in the
locked Cargo graph are collected. Including a notice does not imply that every
package is linked into a particular target or that the target is supported.
Unknown missing licenses fail the build instead of generating plausible but
incorrect notices. Original license texts and copyright holders must not be
removed for branding reasons.
