// Private CPU helper. fd 2 is the verified model handle. In batch mode stdin
// is PCM through EOF. In --stream mode stdin is an already validated growing
// PCM file; SIGUSR1 marks capture closed. stdout is bounded private JSON only.
#include "transcribe.h"
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>
#include <sys/resource.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

static_assert(std::atomic<bool>::is_always_lock_free, "signal handler requires lock-free atomics");
static std::atomic<bool> finished{false};
static void finish_signal(int) {
    // POSIX alarm is async-signal-safe. Start the bounded finish budget even
    // while inference is busy; repeated finish signals must not extend it.
    if (!finished.exchange(true, std::memory_order_relaxed)) alarm(120);
}
static constexpr size_t max_pcm = 16000 * 2 * 300;

static std::string quote(const std::string &text) {
    std::string out = "\"";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32) {
            char escaped[7]; std::snprintf(escaped, sizeof(escaped), "\\u%04x", c); out += escaped;
        } else out += static_cast<char>(c);
    }
    return out + '"';
}
static void convert(const unsigned char *bytes, size_t n, std::vector<float> &audio) {
    audio.resize(n / 2);
    for (size_t i = 0; i < audio.size(); ++i) {
        const int v = bytes[2*i] | (bytes[2*i+1] << 8);
        audio[i] = static_cast<float>(v >= 32768 ? v - 65536 : v) / 32768.0f;
    }
}

int main(int argc, char **argv) {
    const bool streaming = argc == 3 && std::strcmp(argv[2], "--stream") == 0;
    if (argc != 2 && !streaming) return 2;
    char *end = nullptr;
    const auto expected_parent = std::strtoul(argv[1], &end, 10);
    if (!end || *end || expected_parent <= 1 || expected_parent != static_cast<unsigned long>(getppid())) return 1;
#ifdef __linux__
    // Kernel-enforced cleanup also works if inference is stopped or wedged.
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 ||
        expected_parent != static_cast<unsigned long>(getppid())) return 1;
#endif
    const int model_fd = dup(STDERR_FILENO);
    const int sink = open("/dev/null", O_WRONLY);
    if (model_fd < 0 || sink < 0 || dup2(sink, STDERR_FILENO) < 0) return 1;
    close(sink);
    struct stat model_stat{};
    if (fstat(model_fd, &model_stat) != 0 || !S_ISREG(model_stat.st_mode) ||
        (model_stat.st_size != 731357568 && model_stat.st_size != 731357696) ||
        lseek(model_fd, 0, SEEK_SET) < 0) return 2;
    const auto model_path = std::string("/dev/fd/") + std::to_string(model_fd);
    rlimit core{0, 0};
    if (setrlimit(RLIMIT_CORE, &core) != 0) return 1;
    if (std::signal(SIGALRM, SIG_DFL) == SIG_ERR ||
        (streaming && std::signal(SIGUSR1, finish_signal) == SIG_ERR)) return 1;
    sigset_t signals;
    sigemptyset(&signals);
    sigaddset(&signals, SIGALRM);
    if (streaming) sigaddset(&signals, SIGUSR1);
    if (sigprocmask(SIG_UNBLOCK, &signals, nullptr) != 0) return 1;
    alarm(streaming ? 425 : 120);
    const auto parent = static_cast<pid_t>(expected_parent);
    try {
        std::thread([parent] {
            while (true) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
                if (getppid() != parent) std::_Exit(1);
            }
        }).detach();
        // Suppress third-party output, including printf outside the callback.
        const int output = dup(STDOUT_FILENO);
        if (output < 0 || dup2(STDERR_FILENO, STDOUT_FILENO) < 0) return 1;
        transcribe_log_set([](transcribe_log_level, const char *, void *) {}, nullptr);
        if (transcribe_init_backends_default() != TRANSCRIBE_OK) return 1;
        transcribe_model_load_params mp; transcribe_model_load_params_init(&mp);
        mp.backend = TRANSCRIBE_BACKEND_CPU;
        transcribe_session_params sp; transcribe_session_params_init(&sp);
        sp.n_threads = 4;
        transcribe_session *raw = nullptr;
        const auto loaded = transcribe_open(model_path.c_str(), &mp, &sp, &raw);
        std::unique_ptr<transcribe_session, decltype(&transcribe_session_free)> session(raw, transcribe_session_free);
        if (loaded != TRANSCRIBE_OK || !session) return 1;
        transcribe_run_params rp; transcribe_run_params_init(&rp);
        rp.language = "en";
        rp.timestamps = TRANSCRIBE_TIMESTAMPS_NONE;
        size_t consumed = 0;
        std::vector<float> audio;
        if (streaming) {
            struct stat stat{};
            if (fstat(STDIN_FILENO, &stat) != 0 || !S_ISREG(stat.st_mode) || lseek(STDIN_FILENO, 0, SEEK_SET) < 0) return 2;
            transcribe_stream_params params; transcribe_stream_params_init(&params);
            params.commit_policy = TRANSCRIBE_STREAM_COMMIT_ON_FINALIZE;
            // Fixed highest-accuracy default tuple: 5600/1040/1040 ms.
            if (transcribe_stream_begin(session.get(), &rp, &params) != TRANSCRIBE_OK) return 1;
            if (dprintf(output, "{\"version\":1,\"ready\":true}\n") < 0) return 1;
            unsigned char bytes[3200];
            bool finishing = false;
            while (true) {
                if (finished.load(std::memory_order_relaxed)) finishing = true;
                if (fstat(STDIN_FILENO, &stat) != 0 || stat.st_size < 0 ||
                    static_cast<uint64_t>(stat.st_size) < consumed) return 2;
                // The host stops capture on a timer and trims its final chunk
                // in place before finish. Never feed beyond 300s while waiting
                // for that stop; the final worker check still requires exact
                // agreement between consumed bytes, the PCM inode and WAV.
                const size_t available = std::min(static_cast<uint64_t>(stat.st_size),
                                                  static_cast<uint64_t>(max_pcm));
                const size_t remaining = available - consumed;
                if (remaining < sizeof(bytes) && !finishing) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(10));
                    continue;
                }
                const size_t count = std::min(remaining, sizeof(bytes)) & ~size_t(1);
                if (count) {
                    const auto n = read(STDIN_FILENO, bytes, count);
                    if (n < 0 && errno == EINTR) continue;
                    if (n <= 0 || n % 2) return 2;
                    consumed += static_cast<size_t>(n);
                    convert(bytes, static_cast<size_t>(n), audio);
                    if (transcribe_stream_feed(session.get(), audio.data(), static_cast<int>(audio.size()), nullptr) != TRANSCRIBE_OK) return 1;
                } else if (finishing) {
                    if (remaining || consumed < 9600) return 2;
                    if (transcribe_stream_finalize(session.get(), nullptr) != TRANSCRIBE_OK) return 1;
                    break;
                } else std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
        } else {
            std::vector<unsigned char> bytes;
            char buffer[65536];
            while (std::cin.read(buffer, sizeof(buffer)) || std::cin.gcount()) {
                const auto n = static_cast<size_t>(std::cin.gcount());
                if (bytes.size() + n > max_pcm) return 2;
                bytes.insert(bytes.end(), buffer, buffer + n);
            }
            if (std::cin.bad() || bytes.size() < 9600 || bytes.size() % 2) return 2;
            consumed = bytes.size();
            convert(bytes.data(), bytes.size(), audio);
            if (transcribe_run(session.get(), audio.data(), static_cast<int>(audio.size()), &rp) != TRANSCRIBE_OK) return 1;
        }
        const char *text = transcribe_full_text(session.get());
        if (!text || strnlen(text, 128 * 1024 + 1) > 128 * 1024) return 1;
        const auto encoded = quote(text);
        session.reset();
        close(model_fd);
        std::fflush(stdout);
        if (dup2(output, STDOUT_FILENO) < 0) return 1;
        close(output);
        std::cout << "{\"version\":1,\"model\":\"parakeet-unified-en-q8\",\"pcm_bytes\":" << consumed
                  << ",\"text\":" << encoded << "}" << std::endl;
        return std::cout.good() ? 0 : 1;
    } catch (...) {
        return 1; // never log exceptions, audio, paths or transcripts
    }
}
