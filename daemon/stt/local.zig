//! Q8-only, CPU file inference. No environment credentials or cloud fallback.
const std = @import("std");
const builtin = @import("builtin");
const recorder = @import("../recorder.zig");

pub const model_id = "parakeet-unified-en-q8";
pub const model_filename = "parakeet-unified-en-0.6b-Q8_0.gguf";
pub const model_size = 731_357_568;
pub const model_sha256 = "4b50b6dd862bf6e346929aaf4f5eaacec003bfa3f56462d6c874b41ef2f38795";
pub const own_model_size = 731_357_696;
pub const own_model_sha256 = "41762900886984bfa1283c38b2f20bd9ae9dd18d55a32fe71414af52e0e49e75";
const max_output = 1024 * 1024;

fn allowedSize(size: u64) bool {
    return size == model_size or size == own_model_size;
}

fn allowedArtifact(size: u64, digest: []const u8) bool {
    return (size == model_size and std.mem.eql(u8, digest, model_sha256)) or
        (size == own_model_size and std.mem.eql(u8, digest, own_model_sha256));
}

pub fn validateModel(io: std.Io, path: []const u8) !void {
    const file = try openModel(io, path);
    file.close(io);
}

fn openModelFile(io: std.Io, path: []const u8) !std.Io.File {
    if (!std.fs.path.isAbsolute(path)) return error.ModelMissing;
    const before = std.Io.Dir.cwd().statFile(io, path, .{ .follow_symlinks = false }) catch return error.ModelMissing;
    if (before.kind != .file or !allowedSize(before.size)) return error.ModelInvalid;
    var file = std.Io.Dir.cwd().openFile(io, path, .{ .follow_symlinks = false }) catch return error.ModelMissing;
    errdefer file.close(io);
    const stat = try file.stat(io);
    if (stat.kind != .file or !allowedSize(stat.size)) return error.ModelInvalid;
    return file;
}

pub fn openModel(io: std.Io, path: []const u8) !std.Io.File {
    var file = try openModelFile(io, path);
    errdefer file.close(io);
    var reader = file.reader(io, &.{});
    var buffer: [64 * 1024]u8 = undefined;
    var hash = std.crypto.hash.sha2.Sha256.init(.{});
    var total: usize = 0;
    while (true) {
        const n = reader.interface.readSliceShort(&buffer) catch return error.ModelInvalid;
        if (n == 0) break;
        total += n;
        if (total > @max(model_size, own_model_size)) return error.ModelInvalid;
        hash.update(buffer[0..n]);
    }
    var digest: [32]u8 = undefined;
    hash.final(&digest);
    const hex = std.fmt.bytesToHex(digest, .lower);
    if (!allowedArtifact(total, &hex)) return error.ModelInvalid;
    return file;
}

pub fn transcribe(gpa: std.mem.Allocator, io: std.Io, model_path: []const u8, wav: []const u8) ![]u8 {
    if (builtin.os.tag != .linux and builtin.os.tag != .macos) return error.UnsupportedPlatform;
    const info = try recorder.inspectWav(wav);
    if (info.channels != 1 or info.sample_rate != 16000 or info.seconds < 0.3 or info.seconds > 300)
        return error.InvalidAudio;
    const model = try openModel(io, model_path);
    defer model.close(io);
    const helper = try helperPath(gpa, io);
    defer gpa.free(helper);
    return invoke(gpa, io, helper, model, try recorder.pcmData(wav));
}

pub fn helperPath(gpa: std.mem.Allocator, io: std.Io) ![]u8 {
    const executable = try std.process.executablePathAlloc(io, gpa);
    defer gpa.free(executable);
    return std.fs.path.join(gpa, &.{ std.fs.path.dirname(executable) orelse return error.LocalUnavailable, "sayall-local-q8" });
}

/// Fast pre-capture presence/readability check. Full hashing still occurs on
/// the opened model handle before inference; this is not integrity attestation.
pub fn readiness(gpa: std.mem.Allocator, io: std.Io, model_path: []const u8) !void {
    if (builtin.os.tag != .linux and builtin.os.tag != .macos) return error.UnsupportedPlatform;
    const model = try openModelFile(io, model_path);
    defer model.close(io);
    const helper = try helperPath(gpa, io);
    defer gpa.free(helper);
    const stat = std.Io.Dir.cwd().statFile(io, helper, .{}) catch return error.LocalUnavailable;
    if (stat.kind != .file or stat.permissions.toMode() & 0o111 == 0) return error.LocalUnavailable;
}

fn invoke(gpa: std.mem.Allocator, io: std.Io, helper: []const u8, model: std.Io.File, pcm: []const u8) ![]u8 {
    var env = std.process.Environ.Map.init(gpa);
    defer env.deinit();
    const pid = switch (builtin.os.tag) {
        .linux => std.os.linux.getpid(),
        .macos => std.c.getpid(),
        else => return error.UnsupportedPlatform,
    };
    var pid_buffer: [32]u8 = undefined;
    const parent_pid = try std.fmt.bufPrint(&pid_buffer, "{d}", .{pid});
    var child = std.process.spawn(io, .{
        .argv = &.{ helper, parent_pid },
        .environ_map = &env,
        .stdin = .pipe,
        .stdout = .pipe,
        // Private fd protocol: fd 2 carries the already verified read-only
        // model handle, not logs. This pins the inode across atomic replacement.
        // The helper duplicates it and redirects stderr to /dev/null immediately.
        .stderr = .{ .file = model },
    }) catch return error.LocalUnavailable;
    defer child.kill(io);
    // The fixed native helper also has a 120s watchdog, including stdin reads,
    // and exits if its worker parent dies. Never execute a request-supplied tool.
    child.stdin.?.writeStreamingAll(io, pcm) catch |err| {
        // The native deadline can fire while the PCM pipe is still being
        // written. Preserve timeout classification rather than report EPIPE.
        const term = child.wait(io) catch return err;
        if (term == .signal and term.signal == .ALRM) return error.Timeout;
        return err;
    };
    child.stdin.?.close(io);
    child.stdin = null;
    var storage: std.Io.File.MultiReader.Buffer(1) = undefined;
    var reader: std.Io.File.MultiReader = undefined;
    reader.init(gpa, io, storage.toStreams(), &.{child.stdout.?});
    defer reader.deinit();
    const deadline: std.Io.Timeout = .{ .deadline = .fromNow(io, .{ .clock = .awake, .raw = .fromSeconds(125) }) };
    while (reader.fill(4096, deadline)) |_| {
        if (reader.reader(0).buffered().len > max_output) return error.ResponseTooLarge;
    } else |err| switch (err) {
        error.EndOfStream => {},
        else => return err,
    }
    try reader.checkAnyError();
    const term = try child.wait(io);
    if (term != .exited or term.exited != 0) {
        if (term == .signal and term.signal == .ALRM) return error.Timeout;
        return error.LocalFailed;
    }
    return parseOutput(gpa, reader.reader(0).buffered());
}

pub fn parseOutput(gpa: std.mem.Allocator, bytes: []const u8) ![]u8 {
    if (bytes.len == 0) return error.LocalFailed;
    if (bytes.len > max_output) return error.ResponseTooLarge;
    const Frame = struct { version: u32, model: []const u8, text: []const u8, pcm_bytes: ?usize = null };
    const parsed = std.json.parseFromSlice(Frame, gpa, bytes, .{}) catch return error.LocalFailed;
    defer parsed.deinit();
    if (parsed.value.version != 1 or !std.mem.eql(u8, parsed.value.model, model_id) or
        !std.unicode.utf8ValidateSlice(parsed.value.text) or parsed.value.text.len > 128 * 1024)
        return error.LocalFailed;
    return gpa.dupe(u8, parsed.value.text);
}

test "local model pins match complete artifact pairs, never size or hash alone" {
    try std.testing.expect(allowedArtifact(model_size, model_sha256));
    try std.testing.expect(allowedArtifact(own_model_size, own_model_sha256));
    try std.testing.expect(!allowedArtifact(model_size, own_model_sha256));
    try std.testing.expect(!allowedArtifact(own_model_size, model_sha256));
    try std.testing.expect(!allowedArtifact(own_model_size + 1, own_model_sha256));
    try std.testing.expect(!allowedArtifact(own_model_size, "unknown"));
    try std.testing.expect(!allowedSize(477_274_496)); // Q4 remains unsupported.
}

test "local adapter result is bounded, versioned and Q8-only" {
    const gpa = std.testing.allocator;
    const text = try parseOutput(gpa, "{\"version\":1,\"model\":\"parakeet-unified-en-q8\",\"text\":\"hello\"}");
    defer gpa.free(text);
    try std.testing.expectEqualStrings("hello", text);
    for ([_][]const u8{
        "",
        "{}",
        "{\"version\":2,\"model\":\"parakeet-unified-en-q8\",\"text\":\"hello\"}",
        "{\"version\":1,\"model\":\"parakeet-unified-en-q4\",\"text\":\"hello\"}",
        "{\"version\":1,\"model\":\"parakeet-unified-en-q8\",\"text\":null}",
    }) |invalid| try std.testing.expectError(error.LocalFailed, parseOutput(gpa, invalid));
}

test "local model rejects missing paths and invalid artifacts before inference" {
    try std.testing.expectError(error.ModelMissing, validateModel(std.testing.io, "relative.gguf"));
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    try tmp.dir.writeFile(std.testing.io, .{ .sub_path = "wrong.gguf", .data = "not Q8" });
    const path = try tmp.dir.realPathFileAlloc(std.testing.io, "wrong.gguf", std.testing.allocator);
    defer std.testing.allocator.free(path);
    try std.testing.expectError(error.ModelInvalid, validateModel(std.testing.io, path));
}
