//! macOS `sayall shortcut`: the CLI owns the persisted preference shared with
//! Linux (`shortcut.json`), and the running app owns registration. Every change
//! is confirmed by the app or rolled back, mirroring the Hyprland transaction.
const std = @import("std");
const cli = @import("cli.zig");
const shortcut_chord = @import("product/shortcut_chord.zig");
const Io = std.Io;
const Allocator = std.mem.Allocator;

pub const default_shortcut = shortcut_chord.default_shortcut;

pub const Key = struct { name: []const u8, code: u16 };

/// Carbon virtual key codes (`kVK_*`). Names are xkb keysym names so a saved
/// shortcut stays meaningful on Linux. Mirrored in the Swift host and checked
/// against tests/contracts-0.2/macos-shortcut-keys.json by both test suites.
pub const keys = [_]Key{
    .{ .name = "A", .code = 0x00 },      .{ .name = "S", .code = 0x01 },         .{ .name = "D", .code = 0x02 },
    .{ .name = "F", .code = 0x03 },      .{ .name = "H", .code = 0x04 },         .{ .name = "G", .code = 0x05 },
    .{ .name = "Z", .code = 0x06 },      .{ .name = "X", .code = 0x07 },         .{ .name = "C", .code = 0x08 },
    .{ .name = "V", .code = 0x09 },      .{ .name = "B", .code = 0x0B },         .{ .name = "Q", .code = 0x0C },
    .{ .name = "W", .code = 0x0D },      .{ .name = "E", .code = 0x0E },         .{ .name = "R", .code = 0x0F },
    .{ .name = "Y", .code = 0x10 },      .{ .name = "T", .code = 0x11 },         .{ .name = "1", .code = 0x12 },
    .{ .name = "2", .code = 0x13 },      .{ .name = "3", .code = 0x14 },         .{ .name = "4", .code = 0x15 },
    .{ .name = "6", .code = 0x16 },      .{ .name = "5", .code = 0x17 },         .{ .name = "EQUAL", .code = 0x18 },
    .{ .name = "9", .code = 0x19 },      .{ .name = "7", .code = 0x1A },         .{ .name = "MINUS", .code = 0x1B },
    .{ .name = "8", .code = 0x1C },      .{ .name = "0", .code = 0x1D },         .{ .name = "BRACKETRIGHT", .code = 0x1E },
    .{ .name = "O", .code = 0x1F },      .{ .name = "U", .code = 0x20 },         .{ .name = "BRACKETLEFT", .code = 0x21 },
    .{ .name = "I", .code = 0x22 },      .{ .name = "P", .code = 0x23 },         .{ .name = "RETURN", .code = 0x24 },
    .{ .name = "L", .code = 0x25 },      .{ .name = "J", .code = 0x26 },         .{ .name = "APOSTROPHE", .code = 0x27 },
    .{ .name = "K", .code = 0x28 },      .{ .name = "SEMICOLON", .code = 0x29 }, .{ .name = "BACKSLASH", .code = 0x2A },
    .{ .name = "COMMA", .code = 0x2B },  .{ .name = "SLASH", .code = 0x2C },     .{ .name = "N", .code = 0x2D },
    .{ .name = "M", .code = 0x2E },      .{ .name = "PERIOD", .code = 0x2F },    .{ .name = "TAB", .code = 0x30 },
    .{ .name = "SPACE", .code = 0x31 },  .{ .name = "GRAVE", .code = 0x32 },     .{ .name = "BACKSPACE", .code = 0x33 },
    .{ .name = "ESCAPE", .code = 0x35 }, .{ .name = "F17", .code = 0x40 },       .{ .name = "F18", .code = 0x4F },
    .{ .name = "F19", .code = 0x50 },    .{ .name = "F20", .code = 0x5A },       .{ .name = "F5", .code = 0x60 },
    .{ .name = "F6", .code = 0x61 },     .{ .name = "F7", .code = 0x62 },        .{ .name = "F3", .code = 0x63 },
    .{ .name = "F8", .code = 0x64 },     .{ .name = "F9", .code = 0x65 },        .{ .name = "F11", .code = 0x67 },
    .{ .name = "F13", .code = 0x69 },    .{ .name = "F16", .code = 0x6A },       .{ .name = "F14", .code = 0x6B },
    .{ .name = "F10", .code = 0x6D },    .{ .name = "F12", .code = 0x6F },       .{ .name = "F15", .code = 0x71 },
    .{ .name = "HELP", .code = 0x72 },   .{ .name = "HOME", .code = 0x73 },      .{ .name = "PAGE_UP", .code = 0x74 },
    .{ .name = "DELETE", .code = 0x75 }, .{ .name = "F4", .code = 0x76 },        .{ .name = "END", .code = 0x77 },
    .{ .name = "F2", .code = 0x78 },     .{ .name = "PAGE_DOWN", .code = 0x79 }, .{ .name = "F1", .code = 0x7A },
    .{ .name = "LEFT", .code = 0x7B },   .{ .name = "RIGHT", .code = 0x7C },     .{ .name = "DOWN", .code = 0x7D },
    .{ .name = "UP", .code = 0x7E },
};

pub fn keyCode(name: []const u8) ?u16 {
    for (keys) |key| if (std.mem.eql(u8, key.name, name)) return key.code;
    return null;
}

/// Returns the canonical chord, or `error.InvalidShortcut` when macOS cannot
/// register it. A chord needs Control or Command so it never captures typing;
/// F1-F20 may also stand alone. macOS 15.0-15.1 reject Option/Shift-only hotkeys,
/// so those are refused everywhere rather than saved and failing at launch.
pub fn normalize(gpa: Allocator, value: []const u8) ![]const u8 {
    const chord = try shortcut_chord.parse(gpa, value);
    _ = keyCode(chord.key) orelse return error.InvalidShortcut;
    const modifiers = chord.modifiers;
    const bare_function_key = isFunctionKey(chord.key) and !modifiers.any();
    if (!bare_function_key and !(modifiers.super or modifiers.ctrl)) return error.InvalidShortcut;
    return chord.display;
}

fn isFunctionKey(name: []const u8) bool {
    if (name.len < 2 or name[0] != 'F') return false;
    for (name[1..]) |c| if (!std.ascii.isDigit(c)) return false;
    return true;
}

pub const State = struct {
    enabled: bool,
    shortcut: []const u8,
    persisted: bool,
};

pub const Request = union(enum) {
    /// Must already be normalized.
    set: []const u8,
    reset,
    disable,
};

pub const HostReply = union(enum) {
    applied: struct { enabled: bool, shortcut: []const u8 },
    not_running,
    rejected: struct { code: []const u8, message: []const u8 },
    /// The running app predates the `shortcut` control method.
    outdated,
    failed: []const u8,
};

pub const Host = struct {
    context: *anyopaque,
    applyFn: *const fn (*anyopaque) HostReply,

    pub fn apply(self: Host) HostReply {
        return self.applyFn(self.context);
    }
};

pub const Outcome = union(enum) {
    applied: struct { state: State, changed: bool, active: bool },
    rejected: struct { shortcut: []const u8, message: []const u8, restored: bool },
    mismatch: struct { expected: State, actual_enabled: bool, actual: []const u8, restored: bool },
    outdated: struct { restored: bool },
    host_failed: struct { diagnostic: []const u8, restored: bool },
    rollback_failed: []const u8,
};

const Paths = struct { state: []const u8, lock: []const u8 };

/// Matches the app's ConfigurationLoader: a non-empty XDG_CONFIG_HOME wins.
fn resolvePaths(gpa: Allocator, env: *const std.process.Environ.Map) !Paths {
    const xdg = env.get("XDG_CONFIG_HOME") orelse "";
    const config_home = if (xdg.len != 0) xdg else if (env.get("HOME")) |home|
        try std.fmt.allocPrint(gpa, "{s}/.config", .{home})
    else
        return error.ConfigHomeUnavailable;
    if (!std.fs.path.isAbsolute(config_home)) return error.InvalidConfigHome;
    return .{
        .state = try std.fmt.allocPrint(gpa, "{s}/sayall/shortcut.json", .{config_home}),
        .lock = try std.fmt.allocPrint(gpa, "{s}/sayall/shortcut.lock", .{config_home}),
    };
}

pub fn loadState(gpa: Allocator, io: Io, env: *const std.process.Environ.Map) !State {
    const paths = try resolvePaths(gpa, env);
    const bytes = try readOptional(gpa, io, paths.state) orelse
        return .{ .enabled = true, .shortcut = default_shortcut, .persisted = false };
    return decode(gpa, bytes);
}

fn decode(gpa: Allocator, bytes: []const u8) !State {
    const stored = try shortcut_chord.decodeState(gpa, bytes);
    return .{ .enabled = stored.enabled, .shortcut = try normalize(gpa, stored.shortcut), .persisted = true };
}

pub fn apply(gpa: Allocator, io: Io, env: *const std.process.Environ.Map, request: Request, host: Host) !Outcome {
    const paths = try resolvePaths(gpa, env);
    const lock = try acquireLock(io, paths.lock);
    defer lock.close(io);

    const old_bytes = try readOptional(gpa, io, paths.state);
    const state: State = switch (request) {
        .set => |value| .{ .enabled = true, .shortcut = value, .persisted = true },
        .reset => .{ .enabled = true, .shortcut = default_shortcut, .persisted = true },
        .disable => .{
            .enabled = false,
            // An unreadable saved chord must not block disabling the shortcut.
            .shortcut = if (old_bytes) |bytes| if (decode(gpa, bytes)) |saved| saved.shortcut else |_| default_shortcut else default_shortcut,
            .persisted = true,
        },
    };
    const new_bytes = try shortcut_chord.encodeState(gpa, state.enabled, state.shortcut);
    const changed = if (old_bytes) |bytes| !std.mem.eql(u8, bytes, new_bytes) else true;
    if (changed) try atomicWrite(gpa, io, paths.state, new_bytes);

    const unconfirmed: Outcome = switch (host.apply()) {
        .applied => |active| if (active.enabled == state.enabled and
            (!state.enabled or std.mem.eql(u8, active.shortcut, state.shortcut)))
            return .{ .applied = .{ .state = state, .changed = changed, .active = true } }
        else
            .{ .mismatch = .{ .expected = state, .actual_enabled = active.enabled, .actual = active.shortcut, .restored = changed } },
        .not_running => return .{ .applied = .{ .state = state, .changed = changed, .active = false } },
        .rejected => |rejected| .{ .rejected = .{ .shortcut = state.shortcut, .message = rejected.message, .restored = changed } },
        .outdated => .{ .outdated = .{ .restored = changed } },
        .failed => |diagnostic| .{ .host_failed = .{ .diagnostic = diagnostic, .restored = changed } },
    };
    if (changed) {
        restoreIfCurrentMatches(gpa, io, paths.state, new_bytes, old_bytes) catch return .{ .rollback_failed = paths.state };
        // The app keeps its previous registration on failure; this resynchronizes its menu.
        _ = host.apply();
    }
    return unconfirmed;
}

/// Runs `sayall shortcut <args>` with the same grammar as Linux.
pub fn run(gpa: Allocator, io: Io, env: *const std.process.Environ.Map, args: []const []const u8, host: Host) !cli.Presentation {
    if (args.len == 0 or (args.len == 1 and std.mem.eql(u8, args[0], "show"))) {
        const state = loadState(gpa, io, env) catch |err| return failure(gpa, "sayall: cannot read shortcut state ({s}); run 'sayall shortcut reset' to replace it\n", .{@errorName(err)});
        const stdout = if (!state.enabled)
            try std.fmt.allocPrint(gpa, "SayAll shortcut: disabled (saved shortcut: {s})\n", .{state.shortcut})
        else
            try std.fmt.allocPrint(gpa, "SayAll shortcut: {s}{s}\n", .{ state.shortcut, if (state.persisted) "" else " (default)" });
        return .{ .exit_code = 0, .stdout = stdout };
    }
    const request: Request = if (args.len == 1 and std.mem.eql(u8, args[0], "disable"))
        .disable
    else if (args.len == 1 and std.mem.eql(u8, args[0], "reset"))
        .reset
    else if ((args.len == 2 or args.len == 3) and std.mem.eql(u8, args[0], "set")) blk: {
        const requested = if (args.len == 2) args[1] else try std.fmt.allocPrint(gpa, "{s}+{s}", .{ args[1], args[2] });
        const normalized = normalize(gpa, requested) catch return .{
            .exit_code = 2,
            .stderr = try std.fmt.allocPrint(gpa, "sayall: invalid shortcut '{s}'; use a chord such as CTRL+SLASH, CMD+SHIFT+SPACE, or F9; include CTRL or CMD unless using F1-F20 alone (CMD/SUPER is Command, OPTION/ALT is Option)\n", .{requested}),
        };
        break :blk .{ .set = normalized };
    } else return cli.invalid_presentation;

    const outcome = apply(gpa, io, env, request, host) catch |err|
        return failure(gpa, "sayall: cannot update shortcut ({s}); no change was applied\n", .{@errorName(err)});
    return present(gpa, outcome);
}

fn present(gpa: Allocator, outcome: Outcome) !cli.Presentation {
    const restored_note = "The previous shortcut was restored.";
    const unchanged_note = "No files were changed.";
    switch (outcome) {
        .applied => |applied| {
            const stdout = if (applied.state.enabled)
                try std.fmt.allocPrint(gpa, "SayAll shortcut {s}: {s}.\n", .{ if (applied.changed) "configured" else "already configured", applied.state.shortcut })
            else
                try std.fmt.allocPrint(gpa, "SayAll shortcut {s}.\n", .{if (applied.changed) "disabled" else "already disabled"});
            return .{
                .exit_code = 0,
                .stdout = stdout,
                .stderr = if (applied.active) "" else "sayall: SayAll is not running; the saved shortcut will be registered when SayAll starts.\n",
            };
        },
        .rejected => |rejected| return failure(gpa, "sayall: SayAll could not register {s}: {s}\nChoose another with 'sayall shortcut set <MODIFIERS+KEY>' or run 'sayall shortcut disable'. {s}\n", .{
            rejected.shortcut,
            rejected.message,
            if (rejected.restored) restored_note else unchanged_note,
        }),
        .mismatch => |mismatch| return failure(gpa, "sayall: the running SayAll app registered {s} instead of {s}; it reads a different configuration directory (check XDG_CONFIG_HOME). {s}\n", .{
            if (mismatch.actual_enabled) mismatch.actual else "no shortcut",
            if (mismatch.expected.enabled) mismatch.expected.shortcut else "no shortcut",
            if (mismatch.restored) restored_note else unchanged_note,
        }),
        .outdated => |outdated| return failure(gpa, "sayall: the running SayAll app cannot change its shortcut; quit and reopen SayAll, then retry. {s}\n", .{
            if (outdated.restored) restored_note else unchanged_note,
        }),
        .host_failed => |failed| return failure(gpa, "{s}sayall: the shortcut change was not confirmed by SayAll. {s}\n", .{
            failed.diagnostic,
            if (failed.restored) restored_note else unchanged_note,
        }),
        .rollback_failed => |path| return failure(gpa, "sayall: SayAll rejected the shortcut and {s} changed concurrently, so it was not restored; inspect it before retrying.\n", .{path}),
    }
}

fn failure(gpa: Allocator, comptime format: []const u8, args: anytype) !cli.Presentation {
    return .{ .exit_code = 1, .stderr = try std.fmt.allocPrint(gpa, format, args) };
}

fn readOptional(gpa: Allocator, io: Io, path: []const u8) !?[]const u8 {
    return Io.Dir.cwd().readFileAlloc(io, path, gpa, .limited(shortcut_chord.state_file_limit)) catch |err| switch (err) {
        error.FileNotFound => null,
        else => return err,
    };
}

fn acquireLock(io: Io, path: []const u8) !Io.File {
    const parent = std.fs.path.dirname(path) orelse return error.InvalidShortcutPath;
    const dir = try Io.Dir.cwd().createDirPathOpen(io, parent, .{ .permissions = @enumFromInt(0o700) });
    dir.close(io);
    return Io.Dir.createFileAbsolute(io, path, .{
        .read = true,
        .truncate = false,
        .lock = .exclusive,
        .permissions = @enumFromInt(0o600),
    });
}

fn atomicWrite(gpa: Allocator, io: Io, path: []const u8, data: []const u8) !void {
    var nonce: u64 = undefined;
    try std.Io.randomSecure(io, std.mem.asBytes(&nonce));
    const temp = try std.fmt.allocPrint(gpa, "{s}.tmp-{x}", .{ path, nonce });
    errdefer Io.Dir.deleteFileAbsolute(io, temp) catch {};
    const file = try Io.Dir.createFileAbsolute(io, temp, .{ .permissions = @enumFromInt(0o600) });
    defer file.close(io);
    try file.writeStreamingAll(io, data);
    try file.sync(io);
    try Io.Dir.rename(.cwd(), temp, .cwd(), path, io);
}

fn restoreIfCurrentMatches(gpa: Allocator, io: Io, path: []const u8, expected: []const u8, previous: ?[]const u8) !void {
    const current = try readOptional(gpa, io, path);
    if (current == null or !std.mem.eql(u8, current.?, expected)) return error.ConcurrentShortcutModification;
    if (previous) |contents| return atomicWrite(gpa, io, path, contents);
    try Io.Dir.deleteFileAbsolute(io, path);
}

const fixtures = @import("protocol_fixtures");

test "key table matches the shared macOS key fixture" {
    var arena_state: std.heap.ArenaAllocator = .init(std.testing.allocator);
    defer arena_state.deinit();
    const gpa = arena_state.allocator();
    const Fixture = struct { version: u8, keys: std.json.ArrayHashMap(u16) };
    const fixture = try std.json.parseFromSliceLeaky(Fixture, gpa, fixtures.macos_shortcut_keys, .{});
    try std.testing.expectEqual(@as(u8, 1), fixture.version);
    try std.testing.expectEqual(keys.len, fixture.keys.map.count());
    for (keys) |key| try std.testing.expectEqual(key.code, fixture.keys.map.get(key.name).?);
}

test "macOS chord validation matches the shared chord fixture" {
    var arena_state: std.heap.ArenaAllocator = .init(std.testing.allocator);
    defer arena_state.deinit();
    const gpa = arena_state.allocator();
    const Valid = struct { input: []const u8, shortcut: []const u8, key_code: u16, modifiers: []const []const u8 };
    const Fixture = struct { version: u8, valid: []const Valid, invalid: []const []const u8 };
    const fixture = try std.json.parseFromSliceLeaky(Fixture, gpa, fixtures.macos_shortcut_chords, .{});
    for (fixture.valid) |case| {
        const normalized = try normalize(gpa, case.input);
        try std.testing.expectEqualStrings(case.shortcut, normalized);
        const chord = try shortcut_chord.parse(gpa, normalized);
        try std.testing.expectEqual(case.key_code, keyCode(chord.key).?);
        var expected: shortcut_chord.Modifiers = .{};
        for (case.modifiers) |name| {
            if (std.mem.eql(u8, name, "SUPER")) expected.super = true else if (std.mem.eql(u8, name, "CTRL")) expected.ctrl = true else if (std.mem.eql(u8, name, "ALT")) expected.alt = true else if (std.mem.eql(u8, name, "SHIFT")) expected.shift = true else return error.UnknownFixtureModifier;
        }
        try std.testing.expectEqual(expected, chord.modifiers);
    }
    for (fixture.invalid) |input| try std.testing.expectError(error.InvalidShortcut, normalize(gpa, input));
}

const FakeHost = struct {
    replies: []const HostReply,
    calls: usize = 0,

    fn applyFn(ctx: *anyopaque) HostReply {
        const self: *FakeHost = @ptrCast(@alignCast(ctx));
        defer self.calls += 1;
        return if (self.calls < self.replies.len) self.replies[self.calls] else .not_running;
    }
    fn host(self: *FakeHost) Host {
        return .{ .context = self, .applyFn = applyFn };
    }
};

const TestHome = struct {
    tmp: std.testing.TmpDir,
    arena_state: std.heap.ArenaAllocator,
    env: std.process.Environ.Map,
    state_path: []const u8,

    fn init(self: *TestHome) !void {
        self.tmp = std.testing.tmpDir(.{});
        self.arena_state = .init(std.testing.allocator);
        const allocator = self.arena_state.allocator();
        const relative = try std.fmt.allocPrint(allocator, ".zig-cache/tmp/{s}", .{self.tmp.sub_path});
        const base = try Io.Dir.cwd().realPathFileAlloc(std.testing.io, relative, allocator);
        self.env = std.process.Environ.Map.init(allocator);
        try self.env.put("HOME", base);
        self.state_path = try std.fmt.allocPrint(allocator, "{s}/.config/sayall/shortcut.json", .{base});
    }
    fn deinit(self: *TestHome) void {
        self.env.deinit();
        self.arena_state.deinit();
        self.tmp.cleanup();
    }
    fn gpa(self: *TestHome) Allocator {
        return self.arena_state.allocator();
    }
    fn invoke(self: *TestHome, args: []const []const u8, fake: *FakeHost) !cli.Presentation {
        return run(self.gpa(), std.testing.io, &self.env, args, fake.host());
    }
    fn saved(self: *TestHome) !?[]const u8 {
        return readOptional(self.gpa(), std.testing.io, self.state_path);
    }
};

test "shortcut CLI shows defaults and persists confirmed changes in the Linux format" {
    var home: TestHome = undefined;
    try home.init();
    defer home.deinit();

    var idle = FakeHost{ .replies = &.{} };
    const shown = try home.invoke(&.{}, &idle);
    try std.testing.expectEqualStrings("SayAll shortcut: CTRL+SLASH (default)\n", shown.stdout);
    try std.testing.expectEqual(@as(usize, 0), idle.calls);

    var confirmed = FakeHost{ .replies = &.{.{ .applied = .{ .enabled = true, .shortcut = "SUPER+SHIFT+SPACE" } }} };
    const set = try home.invoke(&.{ "set", "cmd+shift", "space" }, &confirmed);
    try std.testing.expectEqual(@as(u8, 0), set.exit_code);
    try std.testing.expectEqualStrings("SayAll shortcut configured: SUPER+SHIFT+SPACE.\n", set.stdout);
    try std.testing.expectEqualStrings("", set.stderr);
    try std.testing.expectEqual(@as(usize, 1), confirmed.calls);
    try std.testing.expectEqualStrings(try shortcut_chord.encodeState(home.gpa(), true, "SUPER+SHIFT+SPACE"), (try home.saved()).?);

    var again = FakeHost{ .replies = &.{.{ .applied = .{ .enabled = true, .shortcut = "SUPER+SHIFT+SPACE" } }} };
    try std.testing.expectEqualStrings("SayAll shortcut already configured: SUPER+SHIFT+SPACE.\n", (try home.invoke(&.{ "set", "SUPER+SHIFT+SPACE" }, &again)).stdout);

    var stopped = FakeHost{ .replies = &.{.not_running} };
    const disabled = try home.invoke(&.{"disable"}, &stopped);
    try std.testing.expectEqual(@as(u8, 0), disabled.exit_code);
    try std.testing.expectEqualStrings("SayAll shortcut disabled.\n", disabled.stdout);
    try std.testing.expect(std.mem.indexOf(u8, disabled.stderr, "registered when SayAll starts") != null);
    try std.testing.expectEqualStrings("SayAll shortcut: disabled (saved shortcut: SUPER+SHIFT+SPACE)\n", (try home.invoke(&.{"show"}, &stopped)).stdout);

    var reset_host = FakeHost{ .replies = &.{.{ .applied = .{ .enabled = true, .shortcut = "CTRL+SLASH" } }} };
    try std.testing.expectEqualStrings("SayAll shortcut configured: CTRL+SLASH.\n", (try home.invoke(&.{"reset"}, &reset_host)).stdout);
    try std.testing.expectEqualStrings("SayAll shortcut: CTRL+SLASH\n", (try home.invoke(&.{"show"}, &stopped)).stdout);
}

test "shortcut CLI rejects invalid chords and arguments without contacting the app" {
    var home: TestHome = undefined;
    try home.init();
    defer home.deinit();
    var fake = FakeHost{ .replies = &.{} };
    for ([_][]const []const u8{ &.{ "set", "SHIFT+K" }, &.{ "set", "CTRL+XF86AudioMute" }, &.{ "set", "CTRL", "SLASH", "extra" } }, [_]u8{ 2, 2, 2 }) |args, code| {
        const result = try home.invoke(args, &fake);
        try std.testing.expectEqual(code, result.exit_code);
    }
    try std.testing.expect(std.mem.indexOf(u8, (try home.invoke(&.{ "set", "SHIFT+K" }, &fake)).stderr, "invalid shortcut 'SHIFT+K'") != null);
    try std.testing.expectEqual(cli.invalid_presentation.exit_code, (try home.invoke(&.{"remove"}, &fake)).exit_code);
    try std.testing.expectEqual(@as(usize, 0), fake.calls);
    try std.testing.expect((try home.saved()) == null);
}

test "shortcut CLI restores the previous preference when the app rejects a change" {
    var home: TestHome = undefined;
    try home.init();
    defer home.deinit();
    var first = FakeHost{ .replies = &.{.{ .applied = .{ .enabled = true, .shortcut = "CTRL+F10" } }} };
    _ = try home.invoke(&.{ "set", "CTRL+F10" }, &first);
    const before = (try home.saved()).?;

    const cases = [_]struct { reply: HostReply, text: []const u8 }{
        .{ .reply = .{ .rejected = .{ .code = "shortcut_conflict", .message = "SUPER+SPACE is reserved by a macOS keyboard shortcut" } }, .text = "could not register SUPER+SPACE: SUPER+SPACE is reserved" },
        .{ .reply = .outdated, .text = "quit and reopen SayAll" },
        .{ .reply = .{ .failed = "sayall: cannot reach macOS host\n" }, .text = "not confirmed" },
        .{ .reply = .{ .applied = .{ .enabled = true, .shortcut = "CTRL+SLASH" } }, .text = "different configuration directory" },
    };
    for (cases) |case| {
        var fake = FakeHost{ .replies = &.{ case.reply, .{ .applied = .{ .enabled = true, .shortcut = "CTRL+F10" } } } };
        const result = try home.invoke(&.{ "set", "SUPER+SPACE" }, &fake);
        try std.testing.expectEqual(@as(u8, 1), result.exit_code);
        try std.testing.expect(std.mem.indexOf(u8, result.stderr, case.text) != null);
        try std.testing.expect(std.mem.indexOf(u8, result.stderr, "previous shortcut was restored") != null);
        try std.testing.expectEqual(@as(usize, 2), fake.calls);
        try std.testing.expectEqualStrings(before, (try home.saved()).?);
    }

    var fresh: TestHome = undefined;
    try fresh.init();
    defer fresh.deinit();
    var rejected = FakeHost{ .replies = &.{.{ .rejected = .{ .code = "shortcut_conflict", .message = "in use" } }} };
    const result = try fresh.invoke(&.{"reset"}, &rejected);
    try std.testing.expectEqual(@as(u8, 1), result.exit_code);
    try std.testing.expect((try fresh.saved()) == null);
}

test "shortcut CLI reports an unchanged rejected preference without claiming a restore" {
    var home: TestHome = undefined;
    try home.init();
    defer home.deinit();
    var stopped = FakeHost{ .replies = &.{.not_running} };
    _ = try home.invoke(&.{ "set", "CTRL+SPACE" }, &stopped);
    var rejected = FakeHost{ .replies = &.{.{ .rejected = .{ .code = "shortcut_conflict", .message = "in use" } }} };
    const result = try home.invoke(&.{ "set", "CTRL+SPACE" }, &rejected);
    try std.testing.expectEqual(@as(u8, 1), result.exit_code);
    try std.testing.expect(std.mem.indexOf(u8, result.stderr, "No files were changed.") != null);
    try std.testing.expectEqual(@as(usize, 1), rejected.calls);
}

test "an unreadable saved shortcut is reported and can still be disabled or replaced" {
    var home: TestHome = undefined;
    try home.init();
    defer home.deinit();
    var stopped = FakeHost{ .replies = &.{.not_running} };
    _ = try home.invoke(&.{"reset"}, &stopped);
    try Io.Dir.cwd().writeFile(std.testing.io, .{ .sub_path = home.state_path, .data = "{\"version\":1,\"shortcut\":\"SHIFT+K\"}" });
    const shown = try home.invoke(&.{}, &stopped);
    try std.testing.expectEqual(@as(u8, 1), shown.exit_code);
    try std.testing.expect(std.mem.indexOf(u8, shown.stderr, "sayall shortcut reset") != null);
    _ = try home.invoke(&.{"disable"}, &stopped);
    try std.testing.expectEqualStrings("SayAll shortcut: disabled (saved shortcut: CTRL+SLASH)\n", (try home.invoke(&.{}, &stopped)).stdout);
}
