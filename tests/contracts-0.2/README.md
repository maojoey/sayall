# SayAll 0.2 contract fixtures

These UTF-8 JSON files are language-neutral compatibility fixtures consumed by
Zig, Swift, and Rust tests.

`worker-result-v3-rejected.json` is a negative compatibility fixture: all three
language readers must reject it. Version 4 is also rejected by current readers.

Other `worker-*` fixtures describe the private `sayall-process` protocol version 5,
which renames the closed `polished` processing profile to `llm` without an alias. The required `processing_profile` previously replaced `cleanup_enabled`. Request decoders are strict; result readers validate required fields
and ignore unknown additive object fields. Requests are intentionally omitted
because they contain credentials and platform-owned secure audio paths; their
typed schemas and bounds remain tested in Zig and Swift.
`worker-info`, both ready modes, and `worker-finish` contain no credentials and
freeze the compatibility and streaming control frames shared by future hosts.
Terminal worker results require the effective `processing_profile` and
authoritative `transport` (`rest` or `stream`) so hosts can distinguish a
completed stream from automatic REST fallback and report processing routing.

`host-*` fixtures freeze the future unified native-host control protocol
version 2. Version 2 is intentionally distinct from the released private macOS
version-1 protocol because errors become structured `{code,message}` values.
Status never launches a host. Toggle may launch the exact installed host, but a
client must not retry after the mutation could have been accepted. Reload
validates configuration while idle and applies it to the next dictation.
`shortcut` asks a host that owns global shortcut registration to re-read the
saved `shortcut.json` and register it; a successful reply echoes the registered
`shortcut` object. Hosts without that ownership answer `invalid_request`.

`macos-shortcut-keys.json` and `macos-shortcut-chords.json` freeze the macOS
shortcut key table (xkb name to Carbon `kVK_*` code) and chord validation shared
by the Zig CLI and the Swift app.

Unknown object fields are additive. Worker status/warning/error values and host
states are closed unless the architecture ADR explicitly marks a field open;
adding a closed value requires a protocol version change. Host error `code` is
open and clients must ignore unknown codes after presenting `message`.
