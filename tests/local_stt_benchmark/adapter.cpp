// Research-only persistent CPU adapter. Not shipped in SayAll.
// Input: one absolute canonical s16le/16kHz/mono PCM pathname per line.
// Output: bounded JSON ready/result frames. Transcripts travel only over the
// private stdout pipe; the Python runner scores and discards them.
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <thread>
#include <algorithm>
#include <unistd.h>
#include <sys/resource.h>
#ifdef LOCAL_TRANSCRIBE
#include "transcribe.h"
#include "transcribe/parakeet.h"
#else
#include "whisper.h"
#endif

using Clock = std::chrono::steady_clock;
static double ms(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}
static rusage usage() {
    rusage value{};
    if (getrusage(RUSAGE_SELF, &value) != 0) throw std::runtime_error("resource_measurement");
    return value;
}
static double cpu_ms() {
    const auto u = usage();
    return (u.ru_utime.tv_sec + u.ru_stime.tv_sec) * 1000.0 +
           (u.ru_utime.tv_usec + u.ru_stime.tv_usec) / 1000.0;
}
static uint64_t peak_bytes() {
#ifdef __APPLE__
    return static_cast<uint64_t>(usage().ru_maxrss);
#else
    return static_cast<uint64_t>(usage().ru_maxrss) * 1024;
#endif
}
static std::string quote(const std::string &text) {
    if (text.size() > 128 * 1024) throw std::runtime_error("output_limit");
    std::string out = "\"";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32) {
            char encoded[7]; std::snprintf(encoded, sizeof(encoded), "\\u%04x", c); out += encoded;
        } else out += static_cast<char>(c);
    }
    return out + '"';
}
static std::vector<float> pcm(const std::string &path) {
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    const auto size = in.tellg();
    if (!in || size < 9600 || size > 16000 * 2 * 300 || size % 2) throw std::runtime_error("invalid_audio");
    in.seekg(0);
    std::vector<unsigned char> bytes(static_cast<size_t>(size));
    if (!in.read(reinterpret_cast<char *>(bytes.data()), size)) throw std::runtime_error("invalid_audio");
    std::vector<float> audio(bytes.size() / 2);
    for (size_t i = 0; i < audio.size(); ++i) {
        const int value = bytes[2*i] | (bytes[2*i+1] << 8);
        audio[i] = static_cast<float>(value >= 32768 ? value - 65536 : value) / 32768.0f;
    }
    return audio;
}

int main(int argc, char **argv) {
    if (argc != 3 && argc != 4) return 2;
    const std::string mode = argc == 4 ? argv[3] : "batch";
    if (mode != "batch" && mode != "buffered-default" && mode != "buffered-balanced") return 2;
#ifndef LOCAL_TRANSCRIBE
    if (mode != "batch") return 2;
#endif
    rlimit core{0, 0};
    if (setrlimit(RLIMIT_CORE, &core) != 0) return 1;
    const int threads = std::atoi(argv[2]);
    if (threads < 1 || threads > 64) return 2;
    try {
        const auto started = Clock::now();
#ifdef LOCAL_TRANSCRIBE
        transcribe_log_set([](transcribe_log_level, const char *, void *) {}, nullptr);
        if (transcribe_init_backends_default() != TRANSCRIBE_OK) throw std::runtime_error("backend_init");
        transcribe_model_load_params mp; transcribe_model_load_params_init(&mp);
        mp.backend = TRANSCRIBE_BACKEND_CPU;
        transcribe_session_params sp; transcribe_session_params_init(&sp);
        sp.n_threads = threads;
        transcribe_session *session = nullptr;
        if (transcribe_open(argv[1], &mp, &sp, &session) != TRANSCRIBE_OK) throw std::runtime_error("model_load");
        const auto cleanup = [&] { transcribe_session_free(session); };
#else
        whisper_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
        auto cp = whisper_context_default_params();
        cp.use_gpu = false;
        auto *session = whisper_init_from_file_with_params(argv[1], cp);
        if (!session) throw std::runtime_error("model_load");
        const auto cleanup = [&] { whisper_free(session); };
#endif
        std::cout << "{\"version\":1,\"type\":\"ready\",\"backend\":\"cpu\",\"load_ms\":" << ms(started)
                  << ",\"peak_rss_bytes\":" << peak_bytes() << "}" << std::endl;
        char line[4098];
        while (std::fgets(line, sizeof(line), stdin)) {
            std::string path(line);
            if (path.empty() || path.back() != '\n') { cleanup(); return 2; }
            path.pop_back();
            if (path.empty()) break;
            auto audio = pcm(path);
            const double cpu_start = cpu_ms();
            const auto decode_start = Clock::now();
            std::string text;
            int status = 0;
            double stop_to_final_ms = 0, max_feed_ms = 0, max_backlog_ms = 0;
            unsigned feeds = 0;
#ifdef LOCAL_TRANSCRIBE
            transcribe_run_params rp; transcribe_run_params_init(&rp);
            rp.language = "en";
            rp.timestamps = TRANSCRIBE_TIMESTAMPS_NONE;
            if (mode == "batch") {
                status = transcribe_run(session, audio.data(), static_cast<int>(audio.size()), &rp);
            } else {
                transcribe_stream_params stream; transcribe_stream_params_init(&stream);
                transcribe_parakeet_buffered_stream_ext ext;
                transcribe_parakeet_buffered_stream_ext_init(&ext);
                if (mode == "buffered-balanced") { ext.chunk_ms = 560; ext.right_ms = 560; }
                stream.family = &ext.ext;
                stream.commit_policy = TRANSCRIBE_STREAM_COMMIT_ON_FINALIZE;
                status = transcribe_stream_begin(session, &rp, &stream);
                // Producer deadlines never move when inference is slow: any
                // backlog remains charged to actual stop-to-final latency.
                for (size_t offset = 0; status == 0 && offset < audio.size(); offset += 1600) {
                    const size_t count = std::min<size_t>(1600, audio.size() - offset);
                    const auto available = decode_start + std::chrono::microseconds((offset + count) * 1000000 / 16000);
                    std::this_thread::sleep_until(available);
                    const auto feed_start = Clock::now();
                    max_backlog_ms = std::max(max_backlog_ms, std::chrono::duration<double, std::milli>(feed_start - available).count());
                    status = transcribe_stream_feed(session, audio.data() + offset, static_cast<int>(count), nullptr);
                    max_feed_ms = std::max(max_feed_ms, ms(feed_start));
                    ++feeds;
                }
                if (status == 0) status = transcribe_stream_finalize(session, nullptr);
                stop_to_final_ms = std::max(0.0, ms(decode_start) - audio.size() / 16.0);
            }
            if (status == 0) text = transcribe_full_text(session);
#else
            auto rp = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
            rp.n_threads = threads;
            rp.language = "en";
            rp.translate = false;
            rp.no_context = true;
            rp.no_timestamps = true;
            rp.print_special = false;
            rp.print_progress = false;
            rp.print_realtime = false;
            rp.print_timestamps = false;
            rp.temperature = 0;
            rp.temperature_inc = 0;
            rp.greedy.best_of = 1;
            status = whisper_full(session, rp, audio.data(), static_cast<int>(audio.size()));
            if (status == 0) for (int i = 0; i < whisper_full_n_segments(session); ++i) {
                if (!text.empty()) text += ' ';
                text += whisper_full_get_segment_text(session, i);
            }
#endif
            const double decode_ms = ms(decode_start), cpu = cpu_ms() - cpu_start;
            std::cout << "{\"version\":1,\"type\":\"result\",\"code\":" << status
                      << ",\"decode_ms\":" << decode_ms << ",\"cpu_ms\":" << cpu
                      << ",\"peak_rss_bytes\":" << peak_bytes()
                      << ",\"stop_to_final_ms\":" << (mode == "batch" ? decode_ms : stop_to_final_ms)
                      << ",\"max_feed_ms\":" << max_feed_ms << ",\"max_backlog_ms\":" << max_backlog_ms
                      << ",\"feeds\":" << feeds << ",\"text\":" << quote(text) << "}" << std::endl;
        }
        cleanup();
        return 0;
    } catch (...) {
        // Never include a pathname, transcript, or third-party exception text.
        std::cout << "{\"version\":1,\"type\":\"error\",\"code\":\"adapter_failure\"}" << std::endl;
        return 1;
    }
}
