//! Linux host-owned local recording session. Inference follows a validated PCM
//! descriptor during capture; finish drains it and delivers only finalized text.
const std = @import("std");
const builtin = @import("builtin");
const batch = @import("batch.zig");
const stream = @import("stream_batch.zig");
const local = @import("stt/local.zig");
const protocol = @import("worker_protocol.zig");
const secure_audio = @import("secure_audio.zig");
const recorder = @import("recorder.zig");

// The host allows 120s total, including model hashing before this load phase.
const startup_timeout_seconds = 90;

pub fn run(gpa: std.mem.Allocator, io: std.Io) !void {
    var storage: [batch.max_request_bytes + 1]u8 = undefined;
    var input = std.Io.File.stdin().reader(io, &storage);
    const header = input.interface.takeDelimiter('\n') catch return writeInvalidRequest(gpa, io);
    const parsed = stream.parseRequest(gpa, header orelse return writeInvalidRequest(gpa, io)) catch
        return writeInvalidRequest(gpa, io);
    defer parsed.deinit();
    const r = parsed.value;
    const result = execute(gpa, io, r, &input.interface) catch |err| batch.Result{
        .status = .@"error",
        .@"error" = if (err == error.InvalidRequest) .invalid_request else batch.mapLocalError(err),
        .processing_profile = r.processing_profile,
        .transport = .local,
    };
    defer if (result.text) |text| gpa.free(text);
    try write(gpa, io, result);
}

fn writeInvalidRequest(gpa: std.mem.Allocator, io: std.Io) !void {
    return write(gpa, io, batch.Result{
        .status = .@"error",
        .@"error" = .invalid_request,
        .processing_profile = .clean,
        .transport = .local,
    });
}

fn validate(r: stream.Request) !void {
    if (r.version != protocol.version or r.stt_provider != .local or
        (r.processing_profile != .verbatim and r.processing_profile != .clean) or
        r.deepgram_api_key.len != 0 or r.llm_api_key.len != 0 or
        !std.mem.eql(u8, r.deepgram_language, "en") or r.deepgram_keyterms.len != 0 or
        r.deepgram_smart_format or r.deepgram_punctuate or r.deepgram_dictation or
        r.deepgram_numerals or r.deepgram_measurements or
        !std.fs.path.isAbsolute(r.local_model_path) or
        !std.fs.path.isAbsolute(r.wav_path) or !std.fs.path.isAbsolute(r.pcm_path) or
        std.mem.eql(u8, r.wav_path, r.pcm_path)) return error.InvalidRequest;
}

fn execute(gpa: std.mem.Allocator, io: std.Io, r: stream.Request, input: *std.Io.Reader) !batch.Result {
    if (builtin.os.tag != .linux) return error.UnsupportedPlatform;
    try validate(r);
    const pcm = secure_audio.open(io, r.pcm_path, 9_600_000, .growing) catch return error.InvalidRequest;
    defer pcm.file.close(io);
    const wav = secure_audio.open(io, r.wav_path, batch.max_audio_bytes, .nonempty) catch return error.InvalidRequest;
    defer wav.file.close(io);
    if (secure_audio.sameFile(pcm.identity, wav.identity)) return error.InvalidRequest;
    const model = try local.openModel(io, r.local_model_path);
    defer model.close(io);
    const helper = try local.helperPath(gpa, io);
    defer gpa.free(helper);
    var env = std.process.Environ.Map.init(gpa);
    defer env.deinit();
    var pid_buf: [32]u8 = undefined;
    const parent = try std.fmt.bufPrint(&pid_buf, "{d}", .{std.os.linux.getpid()});
    var child = try std.process.spawn(io, .{
        .argv = &.{ helper, parent, "--stream" },
        .environ_map = &env,
        .stdin = .{ .file = pcm.file },
        .stdout = .pipe,
        .stderr = .{ .file = model },
    });
    defer child.kill(io);
    var buffers: std.Io.File.MultiReader.Buffer(1) = undefined;
    var output: std.Io.File.MultiReader = undefined;
    output.init(gpa, io, buffers.toStreams(), &.{child.stdout.?});
    defer output.deinit();
    const startup: std.Io.Timeout = .{ .deadline = .fromNow(io, .{ .clock = .awake, .raw = .fromSeconds(startup_timeout_seconds) }) };
    while (std.mem.indexOfScalar(u8, output.reader(0).buffered(), '\n') == null) {
        try output.fill(256, startup);
        if (output.reader(0).buffered().len > 4096) return error.ResponseTooLarge;
    }
    const ready = (try output.reader(0).takeDelimiter('\n')) orelse return error.LocalFailed;
    if (!std.mem.eql(u8, ready, "{\"version\":1,\"ready\":true}")) return error.LocalFailed;
    // Only acknowledge after the helper has installed its finish handler and
    // loaded the verified model. Capture already runs into the bounded PCM file.
    try write(gpa, io, protocol.Ready{ .streaming = true });
    try validateFinish(gpa, input.takeDelimiter('\n') catch return error.InvalidRequest);
    const started = std.Io.Clock.now(.awake, io).toMilliseconds();
    if (std.os.linux.kill(child.id.?, .USR1) != 0) return error.LocalFailed;
    const deadline: std.Io.Timeout = .{ .deadline = .fromNow(io, .{ .clock = .awake, .raw = .fromSeconds(local.result_timeout_seconds) }) };
    while (output.fill(4096, deadline)) |_| {
        if (output.reader(0).buffered().len > batch.max_output_bytes) return error.ResponseTooLarge;
    } else |err| switch (err) {
        error.EndOfStream => {},
        else => return err,
    }
    try output.checkAnyError();
    const term = try child.wait(io);
    if (term != .exited or term.exited != 0) {
        if (term == .signal and term.signal == .ALRM) return error.Timeout;
        return error.LocalFailed;
    }
    const bytes = output.reader(0).buffered();
    const text = try local.parseOutput(gpa, bytes);
    defer gpa.free(text);
    const Frame = struct { pcm_bytes: usize };
    const frame = try std.json.parseFromSlice(Frame, gpa, bytes, .{ .ignore_unknown_fields = true });
    defer frame.deinit();
    const stat = try pcm.file.stat(io);
    if (frame.value.pcm_bytes != stat.size) return error.InvalidRequest;
    var context = Transcript{ .text = text, .pcm_bytes = frame.value.pcm_bytes };
    // Existing batch validation/cleanup/delivery semantics, but the local seam
    // consumes our private finalized result instead of doing a second decode.
    var result = batch.process(gpa, io, stream.batchRequest(r), .{
        .context = &context,
        .local_transcribe = completed,
    });
    if (result.timing) |*timing| {
        const elapsed: u64 = @intCast(@max(0, std.Io.Clock.now(.awake, io).toMilliseconds() - started));
        timing.local_stt_ms = elapsed -| timing.deterministic_processing_ms;
        timing.processing_total_ms = elapsed;
    }
    return result;
}

fn validateFinish(gpa: std.mem.Allocator, line: ?[]const u8) !void {
    const finish = std.json.parseFromSlice(stream.Finish, gpa, line orelse return error.InvalidRequest, .{}) catch |err| {
        if (err == error.OutOfMemory) return err;
        return error.InvalidRequest;
    };
    defer finish.deinit();
    if (finish.value.version != protocol.version or
        !std.mem.eql(u8, finish.value.command, "finish") or finish.value.force_rest)
        return error.InvalidRequest;
}

test "local finish rejects missing or malformed frames as protocol errors" {
    for ([_]?[]const u8{ null, "", "{", "{}", "{\"version\":5,\"command\":1}", "{\"version\":3,\"command\":\"finish\"}" }) |line|
        try std.testing.expectError(error.InvalidRequest, validateFinish(std.testing.allocator, line));
    try validateFinish(std.testing.allocator, "{\"version\":5,\"command\":\"finish\",\"force_rest\":false}");
}

const Transcript = struct { text: []const u8, pcm_bytes: usize };
fn completed(ptr: ?*anyopaque, gpa: std.mem.Allocator, _: std.Io, _: []const u8, wav: []const u8) ![]u8 {
    const context: *Transcript = @ptrCast(@alignCast(ptr.?));
    if ((try recorder.pcmData(wav)).len != context.pcm_bytes) return error.InvalidAudio;
    return gpa.dupe(u8, context.text);
}
fn write(gpa: std.mem.Allocator, io: std.Io, value: anytype) !void {
    const bytes = try std.json.Stringify.valueAlloc(gpa, value, .{ .emit_null_optional_fields = false });
    defer gpa.free(bytes);
    try std.Io.File.stdout().writeStreamingAll(io, bytes);
    try std.Io.File.stdout().writeStreamingAll(io, "\n");
}

test "local stream rejects cloud, credentials, formatting and unsupported profiles" {
    const valid: stream.Request = .{ .version = protocol.version, .stt_provider = .local, .local_model_path = "/q8.gguf", .wav_path = "/audio.wav", .pcm_path = "/audio.pcm", .deepgram_api_key = "", .llm_api_key = "", .processing_profile = .clean };
    try validate(valid);
    var invalid = valid;
    invalid.stt_provider = .deepgram;
    try std.testing.expectError(error.InvalidRequest, validate(invalid));
    invalid = valid;
    invalid.deepgram_api_key = "synthetic";
    try std.testing.expectError(error.InvalidRequest, validate(invalid));
    invalid = valid;
    invalid.processing_profile = .llm;
    try std.testing.expectError(error.InvalidRequest, validate(invalid));
    invalid = valid;
    invalid.deepgram_keyterms = &.{"unsupported"};
    try std.testing.expectError(error.InvalidRequest, validate(invalid));
    for ([_][]const u8{ "", "relative.wav" }) |path| {
        invalid = valid;
        invalid.wav_path = path;
        try std.testing.expectError(error.InvalidRequest, validate(invalid));
        invalid = valid;
        invalid.pcm_path = path;
        try std.testing.expectError(error.InvalidRequest, validate(invalid));
    }
}
