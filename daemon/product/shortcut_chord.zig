//! Platform-neutral shortcut grammar and persisted preference shared by the
//! Linux Hyprland integration and the macOS host. Platform layers decide which
//! keys they can register; this module only owns syntax and canonical form.
const std = @import("std");
const Allocator = std.mem.Allocator;

pub const default_shortcut = "CTRL+SLASH";
pub const state_version = 1;
pub const state_file_limit = 1024 * 1024;

pub const StoredState = struct {
    version: u8 = state_version,
    enabled: bool = true,
    shortcut: []const u8 = default_shortcut,
};

pub const Modifiers = struct {
    super: bool = false,
    ctrl: bool = false,
    alt: bool = false,
    shift: bool = false,

    pub fn any(self: Modifiers) bool {
        return self.super or self.ctrl or self.alt or self.shift;
    }
};

pub const Chord = struct {
    modifiers: Modifiers,
    /// ASCII upper-case key name.
    key: []const u8,
    /// Canonical `SUPER+CTRL+ALT+SHIFT+KEY` form.
    display: []const u8,
};

pub fn parse(gpa: Allocator, value: []const u8) !Chord {
    const trimmed = std.mem.trim(u8, value, " \t\r\n");
    if (trimmed.len == 0 or std.mem.indexOfScalar(u8, trimmed, ',') != null) return error.InvalidShortcut;

    var parts = std.mem.splitScalar(u8, trimmed, '+');
    var modifiers: Modifiers = .{};
    var key: ?[]const u8 = null;
    while (parts.next()) |raw| {
        const part = std.mem.trim(u8, raw, " \t");
        if (part.len == 0) return error.InvalidShortcut;
        const flag: ?*bool = if (eqlAny(part, &.{ "CTRL", "CONTROL" }))
            &modifiers.ctrl
        else if (eqlAny(part, &.{ "ALT", "OPTION", "OPT" }))
            &modifiers.alt
        else if (eqlAny(part, &.{"SHIFT"}))
            &modifiers.shift
        else if (eqlAny(part, &.{ "SUPER", "WIN", "MOD4", "CMD", "COMMAND" }))
            &modifiers.super
        else
            null;
        if (flag) |set| {
            if (set.* or key != null) return error.InvalidShortcut;
            set.* = true;
        } else {
            if (key != null or !safeKey(part)) return error.InvalidShortcut;
            key = part;
        }
    }
    const upper_key = try std.ascii.allocUpperString(gpa, key orelse return error.InvalidShortcut);
    const prefix = try joinModifiers(gpa, modifiers, '+');
    return .{
        .modifiers = modifiers,
        .key = upper_key,
        .display = if (prefix.len == 0)
            upper_key
        else
            try std.fmt.allocPrint(gpa, "{s}+{s}", .{ prefix, upper_key }),
    };
}

/// Joins active modifiers in canonical order without a trailing separator.
pub fn joinModifiers(gpa: Allocator, modifiers: Modifiers, separator: u8) ![]const u8 {
    var result = std.ArrayList(u8).empty;
    const ordered = [_]struct { active: bool, name: []const u8 }{
        .{ .active = modifiers.super, .name = "SUPER" },
        .{ .active = modifiers.ctrl, .name = "CTRL" },
        .{ .active = modifiers.alt, .name = "ALT" },
        .{ .active = modifiers.shift, .name = "SHIFT" },
    };
    for (ordered) |modifier| {
        if (!modifier.active) continue;
        if (result.items.len > 0) try result.append(gpa, separator);
        try result.appendSlice(gpa, modifier.name);
    }
    return result.toOwnedSlice(gpa);
}

pub fn safeKey(value: []const u8) bool {
    if (value.len == 0 or value.len > 64) return false;
    for (value) |c| {
        if (!std.ascii.isAlphanumeric(c) and c != '_' and c != '-' and c != ':' and c != '.') return false;
    }
    return true;
}

pub fn decodeState(gpa: Allocator, bytes: []const u8) !StoredState {
    const stored = try std.json.parseFromSliceLeaky(StoredState, gpa, bytes, .{
        .allocate = .alloc_always,
        .ignore_unknown_fields = true,
    });
    if (stored.version != state_version) return error.UnsupportedShortcutStateVersion;
    return .{
        .enabled = stored.enabled,
        .shortcut = (try parse(gpa, stored.shortcut)).display,
    };
}

pub fn encodeState(gpa: Allocator, enabled: bool, shortcut: []const u8) ![]const u8 {
    return std.json.Stringify.valueAlloc(gpa, StoredState{
        .enabled = enabled,
        .shortcut = shortcut,
    }, .{ .whitespace = .indent_2 });
}

fn eqlAny(value: []const u8, candidates: []const []const u8) bool {
    for (candidates) |candidate| if (std.ascii.eqlIgnoreCase(value, candidate)) return true;
    return false;
}

test "shortcut grammar normalizes order and platform modifier aliases" {
    var arena_state: std.heap.ArenaAllocator = .init(std.testing.allocator);
    defer arena_state.deinit();
    const gpa = arena_state.allocator();
    try std.testing.expectEqualStrings("SUPER+CTRL+SHIFT+SLASH", (try parse(gpa, "win+shift+control+slash")).display);
    try std.testing.expectEqualStrings("SUPER+ALT+K", (try parse(gpa, " cmd + option + k ")).display);
    try std.testing.expectEqualStrings("F9", (try parse(gpa, "f9")).display);
    for ([_][]const u8{ "", "CTRL", "CTRL++K", "CTRL+K+ALT", "CMD+SUPER+K", "CTRL+SLASH,exec", "CTRL+K/", "CTRL+A+B" }) |invalid| {
        try std.testing.expectError(error.InvalidShortcut, parse(gpa, invalid));
    }
}

test "persisted state round-trips through the canonical encoder" {
    var arena_state: std.heap.ArenaAllocator = .init(std.testing.allocator);
    defer arena_state.deinit();
    const gpa = arena_state.allocator();
    const encoded = try encodeState(gpa, false, "SUPER+H");
    const decoded = try decodeState(gpa, encoded);
    try std.testing.expect(!decoded.enabled);
    try std.testing.expectEqualStrings("SUPER+H", decoded.shortcut);
    try std.testing.expectEqualStrings("CTRL+SLASH", (try decodeState(gpa, "{\"version\":1,\"shortcut\":\"control+slash\",\"future\":1}")).shortcut);
    try std.testing.expectError(error.UnsupportedShortcutStateVersion, decodeState(gpa, "{\"version\":2}"));
    try std.testing.expectError(error.InvalidShortcut, decodeState(gpa, "{\"version\":1,\"shortcut\":\"CTRL+\"}"));
}
