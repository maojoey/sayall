# ADR: Native macOS command-line control

- **Status:** Accepted
- **Applies to:** Unreleased macOS CLI
- **Date:** 2026-08-01 (shortcut amendment 2026-10-11)

## Context

The 0.1.6 macOS architecture deliberately excluded a CLI and control socket
while the native app, permission, recording, and delivery boundaries were being
established. SayAll now needs a small terminal interface without introducing a
persistent Zig daemon or moving microphone and Accessibility authority out of
the signed app.

## Decision

`SayAll.app` bundles a separately signed native `sayall` executable. Its public
macOS commands are `version`, `status`, `toggle`, and `config init`. Toggle is
the only recording control: idle starts recording and recording finishes it;
processing and other non-actionable states return a busy error. The app's Swift
`Coordinator` remains the sole live-state owner.

Status and toggle use a private, bounded, versioned JSON request/response Unix
socket under `~/Library/Application Support/SayAll/control`. The immediate
directory is mode `0700`, the socket is mode `0600`, and both peers require the
other effective UID to match their own. The accepted security boundary is the
login account: another process already running as that user may request status
or toggle. The protocol does not carry credentials, transcripts, clipboard
contents, audio paths, or arbitrary text. Socket I/O runs off the main actor;
decoded actions are dispatched to `Coordinator` on the main actor.

`status` never launches the app. `toggle` may launch the exact app containing
the CLI without activation and waits for control readiness for a bounded time.
Normal background launch does not request notification or Accessibility access;
permission prompts remain tied to an error, recording, or explicit menu action.

The app menu provides an explicit **Install Command Line Tool…** action. After
checking that `/usr/local/bin/sayall` is absent or already points at the current
bundle, macOS administrator authorization creates the symlink. SayAll does not
edit shell startup files and does not replace an unrelated path. Homebrew and
native update adapters remain separate future work.

The CLI and `sayall-process` are signed before the outer app, then the complete
bundle is notarized and stapled. Release verification checks both nested
executables independently.

## Amendment: configurable global shortcut

The CLI also exposes `sayall shortcut [show|set <CHORD>|reset|disable]` with
the Linux grammar and the same version-1 `shortcut.json` preference beside
`config.json`. The CLI owns that file under an exclusive `shortcut.lock`, and
the app owns registration. After an atomic write, the CLI sends the
parameterless v2 `shortcut` method: the app re-reads the file and registers
the chord. It replies with the registered `{enabled, shortcut}`, or with a
`shortcut_conflict`, `shortcut_unavailable`, or `invalid_shortcut` error. The
CLI restores the previous file unless the app confirmed the same chord. A
mismatched echo means the app reads a different configuration directory. An
app that predates the method answers `invalid_request`. A stopped app is
never launched; it applies the saved preference at startup.

The app registers a new Carbon hotkey before releasing the old one, so a failed
change never leaves SayAll without a working shortcut. Enabled system-wide
symbolic hotkeys (for example Spotlight or input-source switching) are
treated as conflicts because they would swallow the chord. Valid keys are a
fixed table of xkb names mapped to `kVK_*` codes. A chord needs Control or
Command, or is F1–F20 alone; Option/Shift-only chords are refused because
macOS 15.0 and 15.1 reject them. Registration is exclusive
(`kEventHotKeyExclusive`), so a press never reaches both SayAll and another app.
Carbon returns `eventHotKeyExistsErr` for some existing registrations (always for
other exclusive ones) and SayAll reports those as conflicts, but no public API
enumerates non-exclusive registrations, so such an owner can be shadowed while
SayAll holds the chord instead of being reported. The Zig and Swift implementations are pinned by
the shared `macos-shortcut-*` contract fixtures.

## Consequences

- macOS gains useful terminal control while native code retains TCC, capture,
  helper orchestration, focused-target delivery, and lifecycle authority.
- The macOS control protocol is private and distinct from Linux protocol v1.
- Same-UID control is simpler than XPC but does not authenticate the caller's
  code signature. XPC is required if signed-client identity becomes a product
  requirement.
- Linux-specific update, microphone, service, and desktop integration commands
  are not copied into this initial macOS CLI; platform-neutral adapters can be
  added behind the small public command set later.
