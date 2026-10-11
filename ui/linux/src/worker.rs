use crate::config::{ProcessingProfile, ProviderConfig};
use serde::{Deserialize, Serialize};
use std::io::{self, Read};
use std::os::fd::AsRawFd;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::time::{Duration, Instant};

const CONTROL_MAX: usize = 64 * 1024;
const RESULT_MAX: usize = 1024 * 1024;
const SUPERVISED_MAX: usize = 4097;
const PROTOCOL_VERSION: u32 = 4;

pub struct SupervisedOutput {
    pub status: ExitStatus,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

/// Run a small, non-interactive helper with the same containment policy as the
/// processing worker. Callers must run this off the GTK thread.
pub fn supervised(
    path: &Path,
    args: &[&str],
    env: &[(&str, &std::ffi::OsStr)],
    timeout: Duration,
) -> Result<SupervisedOutput, String> {
    let parent = unsafe { libc::getpid() };
    let mut cmd = Command::new(path);
    cmd.args(args)
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (key, value) in env {
        cmd.env(key, value);
    }
    unsafe {
        cmd.pre_exec(move || {
            if libc::setpgid(0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            #[cfg(target_os = "linux")]
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                return Err(io::Error::last_os_error());
            }
            if libc::getppid() != parent {
                libc::raise(libc::SIGKILL);
            }
            Ok(())
        });
    }
    let mut child = cmd.spawn().map_err(|_| "helper could not be started")?;
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let (output_tx, output_rx) = mpsc::channel();
    let read = |index, mut stream: Box<dyn Read + Send>| {
        let output_tx = output_tx.clone();
        std::thread::spawn(move || {
            let mut out = Vec::new();
            let _ = stream
                .by_ref()
                .take(SUPERVISED_MAX as u64)
                .read_to_end(&mut out);
            let _ = output_tx.send((index, out));
        })
    };
    let out_thread = read(0, Box::new(stdout));
    let err_thread = read(1, Box::new(stderr));
    drop(output_tx);
    let process_group = child.id() as i32;
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            _ => {
                unsafe { libc::kill(-process_group, libc::SIGKILL) };
                let _ = child.wait();
                let _ = out_thread.join();
                let _ = err_thread.join();
                return Err("helper timed out or could not be reaped".into());
            }
        }
    };
    let mut outputs = [Vec::new(), Vec::new()];
    for _ in 0..2 {
        let remaining = deadline.saturating_duration_since(Instant::now());
        let Ok((index, output)) = output_rx.recv_timeout(remaining) else {
            unsafe { libc::kill(-process_group, libc::SIGKILL) };
            let _ = out_thread.join();
            let _ = err_thread.join();
            return Err("helper output timed out".into());
        };
        outputs[index] = output;
    }
    let _ = out_thread.join();
    let _ = err_thread.join();
    let [stdout, stderr] = outputs;
    if stdout.len() >= SUPERVISED_MAX || stderr.len() >= SUPERVISED_MAX {
        unsafe { libc::kill(-process_group, libc::SIGKILL) };
        return Err("helper output exceeded limit".into());
    }
    Ok(SupervisedOutput {
        status,
        stdout,
        stderr,
    })
}

#[derive(Debug, PartialEq)]
pub enum Outcome {
    Transcript {
        text: String,
        processing_profile: ProcessingProfile,
        transport: Transport,
        warning: Option<Warning>,
    },
    NoSpeech {
        processing_profile: ProcessingProfile,
        transport: Transport,
    },
}
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Transport {
    Rest,
    Stream,
    Local,
}
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Warning {
    TransformationFailed,
}
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
enum Status {
    Success,
    NoSpeech,
    Error,
}
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
enum ErrorCode {
    InvalidRequest,
    IncompatibleVersion,
    InvalidAudio,
    AudioTooShort,
    AudioTooLong,
    MissingDeepgramKey,
    DeepgramUnauthorized,
    DeepgramRateLimited,
    DeepgramServer,
    DeepgramNetwork,
    ResponseTooLarge,
    LocalModelMissing,
    LocalModelInvalid,
    LocalUnavailable,
    LocalFailed,
    LocalTimeout,
    UnsupportedFeatures,
    Internal,
}
impl ErrorCode {
    fn as_str(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::IncompatibleVersion => "incompatible_version",
            Self::InvalidAudio => "invalid_audio",
            Self::AudioTooShort => "audio_too_short",
            Self::AudioTooLong => "audio_too_long",
            Self::MissingDeepgramKey => "missing_deepgram_key",
            Self::DeepgramUnauthorized => "deepgram_unauthorized",
            Self::DeepgramRateLimited => "deepgram_rate_limited",
            Self::DeepgramServer => "deepgram_server",
            Self::DeepgramNetwork => "deepgram_network",
            Self::ResponseTooLarge => "response_too_large",
            Self::LocalModelMissing => "local_model_missing",
            Self::LocalModelInvalid => "local_model_invalid",
            Self::LocalUnavailable => "local_unavailable",
            Self::LocalFailed => "local_failed",
            Self::LocalTimeout => "local_timeout",
            Self::UnsupportedFeatures => "unsupported_features",
            Self::Internal => "internal",
        }
    }
}
#[derive(Deserialize)]
struct Info {
    protocol_version: u32,
    build_version: String,
}
#[derive(Deserialize)]
struct Ready {
    version: u32,
    event: String,
    streaming: bool,
}
#[derive(Deserialize)]
struct ResultFrame {
    version: u32,
    status: Status,
    text: Option<String>,
    warning: Option<Warning>,
    error: Option<ErrorCode>,
    processing_profile: ProcessingProfile,
    transport: Transport,
}
#[derive(Serialize)]
struct Request<'a> {
    version: u32,
    wav_path: &'a Path,
    #[serde(skip_serializing_if = "Option::is_none")]
    stt_provider: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    local_model_path: Option<&'a Path>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pcm_path: Option<&'a Path>,
    deepgram_api_key: &'a str,
    deepgram_model: &'a str,
    deepgram_language: &'a str,
    deepgram_region: &'a str,
    deepgram_keyterms: &'a [String],
    deepgram_smart_format: bool,
    deepgram_punctuate: bool,
    deepgram_dictation: bool,
    deepgram_numerals: bool,
    deepgram_measurements: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    stream_finalize_timeout_ms: Option<u32>,
    llm_api_key: &'a str,
    llm_model: &'a str,
    llm_base_url: &'a str,
    processing_profile: ProcessingProfile,
}

struct Running {
    child: Child,
    input: Option<ChildStdin>,
    frames: Receiver<FrameEvent>,
    output_allowed: Arc<AtomicBool>,
}
enum FrameEvent {
    Started { before_finish: bool },
    Frame { bytes: Vec<u8>, before_finish: bool },
    Failed { bytes_seen: bool },
}
struct RecvFailure {
    message: String,
    bytes_seen: bool,
}
pub struct Worker {
    running: Option<Running>,
    local_stream: bool,
    completion_timeout: Duration,
    request: Vec<u8>,
    path: PathBuf,
    shutdown: Arc<AtomicBool>,
}

pub fn resolve() -> io::Result<PathBuf> {
    let exe = std::env::current_exe()?;
    let candidates = [
        exe.parent().unwrap().join("sayall-process"),
        exe.parent().unwrap().join("../lib/sayall/sayall-process"),
        PathBuf::from("/usr/lib/sayall/sayall-process"),
    ];
    if let Some(path) = candidates.into_iter().find(|p| p.is_file()) {
        return Ok(path);
    }
    #[cfg(debug_assertions)]
    {
        let development =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../zig-out/bin/sayall-process");
        if development.is_file() {
            return Ok(development);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "private processing worker not installed",
    ))
}

/// Check local resources before opening the microphone. Integrity is verified
/// again by Zig on the exact model handle used for inference.
pub fn local_readiness(path: &Path, cfg: &ProviderConfig) -> Result<(), String> {
    let Some(model) = &cfg.local_model_path else {
        return Ok(());
    };
    let model = model.to_str().ok_or("local model path must be UTF-8")?;
    let output = supervised(
        path,
        &["--local-readiness", model],
        &[],
        Duration::from_secs(2),
    )?;
    #[derive(Deserialize)]
    struct LocalReady {
        version: u32,
        ready: bool,
        error: Option<ErrorCode>,
    }
    let ready: LocalReady =
        serde_json::from_slice(&output.stdout).map_err(|_| "invalid local readiness response")?;
    if !output.status.success() || ready.version != PROTOCOL_VERSION {
        return Err("local worker is incompatible".into());
    }
    if !ready.ready || ready.error.is_some() {
        return Err(match ready.error {
            Some(ErrorCode::LocalModelMissing) => "Q8 model is missing; check stt.local_model_path",
            Some(ErrorCode::LocalModelInvalid) => {
                "Q8 model is invalid; reinstall the pinned Q8 model"
            }
            _ => "local Q8 runtime is unavailable in this installation",
        }
        .into());
    }
    Ok(())
}

fn startup_timeout(cfg: &ProviderConfig) -> Duration {
    // Local startup includes hashing the 731 MB model, then up to 90s for the
    // helper to load it. Keep cloud connection readiness at its existing 2s.
    Duration::from_secs(if cfg.local_model_path.is_some() {
        120
    } else {
        2
    })
}

fn completion_timeout(cfg: &ProviderConfig) -> Duration {
    // Local batch inference and streaming backlog draining each have a 120s
    // helper watchdog / 125s reader deadline. Include setup and cleanup margin.
    // Cloud processing keeps its existing budget.
    Duration::from_secs(if cfg.local_model_path.is_some() {
        150
    } else {
        45
    })
}

impl Worker {
    pub fn start(
        path: &Path,
        wav: &Path,
        pcm: &Path,
        cfg: &ProviderConfig,
        shutdown: Arc<AtomicBool>,
    ) -> Result<Self, String> {
        probe(path, &shutdown)?;
        let request = encode(wav, Some(pcm), cfg)?;
        let local_stream = cfg.local_model_path.is_some() && cfg.streaming;
        if !cfg.streaming {
            return Ok(Self {
                running: None,
                local_stream,
                completion_timeout: completion_timeout(cfg),
                request,
                path: path.to_owned(),
                shutdown,
            });
        }
        let mut running = spawn(
            path,
            &[if local_stream {
                "--local-stream"
            } else {
                "--stream"
            }],
            false,
        )?;
        let ready_deadline = Instant::now() + startup_timeout(cfg);
        if let Err(error) = write_line(
            running.input.as_mut(),
            &request,
            "processing worker input failed",
            ready_deadline,
            &shutdown,
            None,
        ) {
            terminate(&mut running.child, ready_deadline);
            return Err(error);
        }
        let ready_frame = match recv_until(&running.frames, ready_deadline, 4096, &shutdown, false)
        {
            Ok(v) => v,
            Err(e) => {
                terminate(&mut running.child, ready_deadline);
                return Err(e.message);
            }
        };
        let ready: Ready = decode(ready_frame).map_err(|e| {
            terminate(&mut running.child, ready_deadline);
            e
        })?;
        if ready.version != PROTOCOL_VERSION || ready.event != "ready" {
            terminate(&mut running.child, ready_deadline);
            return Err("processing worker protocol is incompatible".into());
        }
        if !ready.streaming {
            terminate(&mut running.child, ready_deadline);
            if local_stream {
                return Err("local incremental worker is unavailable".into());
            }
            return Ok(Self {
                running: None,
                local_stream,
                completion_timeout: completion_timeout(cfg),
                request,
                path: path.to_owned(),
                shutdown,
            });
        }
        Ok(Self {
            running: Some(running),
            local_stream,
            completion_timeout: completion_timeout(cfg),
            request,
            path: path.to_owned(),
            shutdown,
        })
    }

    pub fn finish_session(self) -> Result<Outcome, String> {
        let timeout = self.completion_timeout;
        self.finish(timeout)
    }

    pub fn finish(mut self, timeout: Duration) -> Result<Outcome, String> {
        let deadline = Instant::now() + timeout;
        if let Some(mut running) = self.running.take() {
            match running.frames.try_recv() {
                Ok(FrameEvent::Started { .. } | FrameEvent::Frame { .. })
                | Ok(FrameEvent::Failed { bytes_seen: true }) => {
                    terminate(&mut running.child, deadline);
                    return Err("processing worker returned output before finish".into());
                }
                Ok(FrameEvent::Failed { bytes_seen: false })
                | Err(mpsc::TryRecvError::Disconnected) => {
                    terminate(&mut running.child, deadline);
                    return self.retry_batch(deadline);
                }
                Err(mpsc::TryRecvError::Empty) => {}
            }
            let finish = serde_json::to_vec(
                &serde_json::json!({"version":PROTOCOL_VERSION,"command":"finish","force_rest":false}),
            )
            .unwrap();
            if let Err(first) = write_line(
                running.input.as_mut(),
                &finish,
                "processing worker stopped unexpectedly",
                deadline,
                &self.shutdown,
                Some(&running.output_allowed),
            ) {
                terminate(&mut running.child, deadline);
                if self.shutdown.load(Ordering::Acquire) {
                    return Err("processing cancelled".into());
                }
                return if conclusively_no_output(&running.frames, deadline) {
                    self.retry_batch(deadline).map_err(|_| first)
                } else {
                    Err(first)
                };
            }
            running.input.take();
            // Receiving any frame is terminal, even if its contents are bad. Only a
            // transport failure before a frame permits the single batch fallback.
            match recv_until(&running.frames, deadline, RESULT_MAX, &self.shutdown, true) {
                Ok(frame) => {
                    let result = decode_result(frame);
                    if result.is_ok() {
                        reap_clean(&mut running.child, deadline, &self.shutdown)?;
                    } else {
                        terminate(&mut running.child, deadline);
                    }
                    result
                }
                Err(first) => {
                    terminate(&mut running.child, deadline);
                    if first.bytes_seen || !conclusively_no_output(&running.frames, deadline) {
                        Err(first.message)
                    } else {
                        self.retry_batch(deadline).map_err(|_| first.message)
                    }
                }
            }
        } else {
            batch(&self.path, &self.request, deadline, &self.shutdown)
        }
    }
    fn retry_batch(&self, deadline: Instant) -> Result<Outcome, String> {
        if self.local_stream {
            return Err("local incremental processing failed; no fallback was attempted".into());
        }
        batch(&self.path, &self.request, deadline, &self.shutdown)
    }
    pub fn cancel(mut self) {
        if let Some(mut r) = self.running.take() {
            terminate(&mut r.child, Instant::now());
        }
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        if let Some(r) = self.running.as_mut() {
            terminate(&mut r.child, Instant::now());
        }
    }
}

fn encode(wav: &Path, pcm: Option<&Path>, c: &ProviderConfig) -> Result<Vec<u8>, String> {
    let local = c.local_model_path.is_some();
    if local
        && !matches!(
            c.processing_profile,
            ProcessingProfile::Clean | ProcessingProfile::Verbatim
        )
    {
        return Err("local mode supports only Verbatim and Clean".into());
    }
    let pcm = if local && !c.streaming { None } else { pcm };
    let r = Request {
        version: PROTOCOL_VERSION,
        wav_path: wav,
        stt_provider: local.then_some("local"),
        local_model_path: c.local_model_path.as_deref(),
        pcm_path: pcm,
        deepgram_api_key: if local { "" } else { &c.deepgram_api_key },
        deepgram_model: if local { "nova-3" } else { &c.deepgram_model },
        deepgram_language: if local { "en" } else { &c.deepgram_language },
        deepgram_region: if local { "global" } else { &c.deepgram_region },
        deepgram_keyterms: if local { &[] } else { &c.keyterms },
        deepgram_smart_format: !local && c.smart_format,
        deepgram_punctuate: !local && c.punctuate,
        deepgram_dictation: !local && c.dictation,
        deepgram_numerals: !local && c.numerals,
        deepgram_measurements: !local && c.measurements,
        stream_finalize_timeout_ms: pcm.map(|_| c.finalize_ms),
        llm_api_key: if local { "" } else { &c.llm_api_key },
        llm_model: if local { "gpt-oss-120b" } else { &c.llm_model },
        llm_base_url: if local {
            "https://api.cerebras.ai/v1/chat/completions"
        } else {
            &c.llm_base_url
        },
        processing_profile: c.processing_profile,
    };
    let b = serde_json::to_vec(&r).map_err(|_| "invalid processing request")?;
    if b.len() > CONTROL_MAX {
        Err("processing request too large".into())
    } else {
        Ok(b)
    }
}
fn probe(path: &Path, shutdown: &Arc<AtomicBool>) -> Result<(), String> {
    let mut r = spawn(path, &["--worker-info", "--wait"], true)?;
    let deadline = Instant::now() + Duration::from_secs(2);
    let frame = match recv_until(&r.frames, deadline, 4096, shutdown, false) {
        Ok(v) => v,
        Err(e) => {
            terminate(&mut r.child, deadline);
            return Err(e.message);
        }
    };
    let info: Info = match decode(frame) {
        Ok(info) => info,
        Err(error) => {
            terminate(&mut r.child, deadline);
            return Err(error);
        }
    };
    if info.protocol_version != PROTOCOL_VERSION {
        terminate(&mut r.child, deadline);
        return Err("processing worker protocol is incompatible".into());
    }
    if info.build_version != crate::BUILD_VERSION {
        terminate(&mut r.child, deadline);
        return Err("processing worker build is incompatible".into());
    }
    r.input.take();
    reap_clean(&mut r.child, deadline, shutdown)
}
fn spawn(path: &Path, args: &[&str], allow_output: bool) -> Result<Running, String> {
    let parent = unsafe { libc::getpid() };
    let mut cmd = Command::new(path);
    cmd.args(args)
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    unsafe {
        cmd.pre_exec(move || {
            if libc::setpgid(0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            #[cfg(target_os = "linux")]
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                return Err(io::Error::last_os_error());
            }
            if libc::getppid() != parent {
                libc::raise(libc::SIGKILL);
            }
            Ok(())
        });
    }
    let mut child = cmd
        .spawn()
        .map_err(|_| "processing worker could not start")?;
    let input = child.stdin.take();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = mpsc::sync_channel(1);
    let output_allowed = Arc::new(AtomicBool::new(allow_output));
    let reader_output_allowed = output_allowed.clone();
    std::thread::spawn(move || {
        let mut r = out;
        loop {
            let mut frame = Vec::new();
            let mut before_finish = false;
            let mut byte = [0];
            loop {
                match r.read(&mut byte) {
                    Ok(0) => {
                        if !frame.is_empty() {
                            // One-shot protocol output is EOF-delimited rather than
                            // newline-delimited. Partial JSON still becomes a
                            // terminal frame and therefore can never be retried.
                            let _ = tx.send(FrameEvent::Frame {
                                bytes: frame,
                                before_finish,
                            });
                        }
                        return;
                    }
                    Ok(_) => {
                        if frame.is_empty() {
                            before_finish = !reader_output_allowed.load(Ordering::Acquire);
                            if tx.send(FrameEvent::Started { before_finish }).is_err() {
                                return;
                            }
                        }
                        if byte[0] == b'\n' {
                            if tx
                                .send(FrameEvent::Frame {
                                    bytes: frame,
                                    before_finish,
                                })
                                .is_err()
                            {
                                return;
                            }
                            break;
                        }
                        if frame.len() == RESULT_MAX + 1 {
                            let _ = tx.send(FrameEvent::Failed { bytes_seen: true });
                            return;
                        }
                        frame.push(byte[0]);
                    }
                    Err(_) => {
                        let _ = tx.send(FrameEvent::Failed {
                            bytes_seen: !frame.is_empty(),
                        });
                        return;
                    }
                }
            }
        }
    });
    Ok(Running {
        child,
        input,
        frames: rx,
        output_allowed,
    })
}
fn write_line(
    input: Option<&mut ChildStdin>,
    bytes: &[u8],
    message: &str,
    deadline: Instant,
    shutdown: &AtomicBool,
    allow_output: Option<&AtomicBool>,
) -> Result<(), String> {
    let input = input.ok_or_else(|| message.to_owned())?;
    let fd = input.as_raw_fd();
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFL);
        if flags < 0 || libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
            return Err(message.into());
        }
    }
    write_bytes(fd, bytes, message, deadline, shutdown)?;
    if let Some(allowed) = allow_output {
        allowed.store(true, Ordering::Release);
    }
    write_bytes(fd, b"\n", message, deadline, shutdown)
}

fn write_bytes(
    fd: libc::c_int,
    data: &[u8],
    message: &str,
    deadline: Instant,
    shutdown: &AtomicBool,
) -> Result<(), String> {
    let mut written = 0;
    while written < data.len() {
        if shutdown.load(Ordering::Acquire) {
            return Err("processing cancelled".into());
        }
        if Instant::now() >= deadline {
            return Err("processing worker timed out".into());
        }
        let n = unsafe { libc::write(fd, data[written..].as_ptr().cast(), data.len() - written) };
        if n > 0 {
            written += n as usize;
            continue;
        }
        let error = io::Error::last_os_error();
        if n < 0 && error.kind() != io::ErrorKind::WouldBlock {
            return Err(message.into());
        }
        poll_fd(fd, libc::POLLOUT, deadline, shutdown)?;
    }
    Ok(())
}
fn recv_until(
    rx: &Receiver<FrameEvent>,
    deadline: Instant,
    max: usize,
    shutdown: &AtomicBool,
    reject_before_finish: bool,
) -> Result<Vec<u8>, RecvFailure> {
    let mut seen = false;
    loop {
        if shutdown.load(Ordering::Acquire) {
            return Err(RecvFailure {
                message: "processing cancelled".into(),
                bytes_seen: seen,
            });
        }
        let Some(left) = deadline.checked_duration_since(Instant::now()) else {
            return Err(RecvFailure {
                message: "processing worker timed out".into(),
                bytes_seen: seen,
            });
        };
        match rx.recv_timeout(left.min(Duration::from_millis(50))) {
            Ok(FrameEvent::Started { before_finish }) => {
                seen = true;
                if reject_before_finish && before_finish {
                    return Err(RecvFailure {
                        message: "processing worker returned output before finish".into(),
                        bytes_seen: true,
                    });
                }
            }
            Ok(FrameEvent::Frame {
                bytes,
                before_finish,
            }) if !bytes.is_empty()
                && bytes.len() <= max
                && !(reject_before_finish && before_finish) =>
            {
                return Ok(bytes);
            }
            Ok(FrameEvent::Frame { .. }) => {
                return Err(RecvFailure {
                    message: "processing worker returned invalid output".into(),
                    bytes_seen: true,
                });
            }
            Ok(FrameEvent::Failed { bytes_seen }) => {
                return Err(RecvFailure {
                    message: "processing worker output failed".into(),
                    bytes_seen: seen || bytes_seen,
                });
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err(RecvFailure {
                    message: "processing worker output failed".into(),
                    bytes_seen: seen,
                });
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
    }
}

fn conclusively_no_output(rx: &Receiver<FrameEvent>, deadline: Instant) -> bool {
    loop {
        let Some(left) = deadline.checked_duration_since(Instant::now()) else {
            return false;
        };
        match rx.recv_timeout(left.min(Duration::from_millis(50))) {
            Ok(FrameEvent::Started { .. } | FrameEvent::Frame { .. })
            | Ok(FrameEvent::Failed { bytes_seen: true }) => return false,
            Ok(FrameEvent::Failed { bytes_seen: false }) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => return true,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
    }
}

fn decode<T: for<'a> Deserialize<'a>>(b: Vec<u8>) -> Result<T, String> {
    serde_json::from_slice(&b).map_err(|_| "processing worker returned malformed output".into())
}
fn decode_result(b: Vec<u8>) -> Result<Outcome, String> {
    let r: ResultFrame = decode(b)?;
    if r.version != PROTOCOL_VERSION {
        return Err("processing worker protocol is incompatible".into());
    }
    let valid = match r.status {
        Status::Success => {
            r.text.as_ref().is_some_and(|text| !text.is_empty()) && r.error.is_none()
        }
        Status::NoSpeech => r.text.is_none() && r.warning.is_none() && r.error.is_none(),
        Status::Error => r.text.is_none() && r.warning.is_none() && r.error.is_some(),
    };
    if !valid {
        return Err("processing worker returned malformed output".into());
    }
    match r.status {
        Status::Success => r
            .text
            .filter(|x| !x.is_empty())
            .map(|text| Outcome::Transcript {
                text,
                processing_profile: r.processing_profile,
                transport: r.transport,
                warning: r.warning,
            })
            .ok_or_else(|| "processing worker returned empty text".into()),
        Status::NoSpeech => Ok(Outcome::NoSpeech {
            processing_profile: r.processing_profile,
            transport: r.transport,
        }),
        Status::Error => Err(format!(
            "processing failed ({})",
            r.error.expect("error code validated").as_str()
        )),
    }
}
fn batch(
    path: &Path,
    stream_request: &[u8],
    deadline: Instant,
    shutdown: &AtomicBool,
) -> Result<Outcome, String> {
    if shutdown.load(Ordering::Acquire) {
        return Err("processing cancelled".into());
    }
    if Instant::now() >= deadline {
        return Err("processing worker timed out".into());
    }
    let mut v: serde_json::Value =
        serde_json::from_slice(stream_request).map_err(|_| "invalid processing request")?;
    let o = v.as_object_mut().ok_or("invalid processing request")?;
    o.remove("pcm_path");
    o.remove("stream_finalize_timeout_ms");
    let b = serde_json::to_vec(&v).map_err(|_| "invalid processing request")?;
    let mut r = spawn(path, &[], true)?;
    if let Err(e) = write_line(
        r.input.as_mut(),
        &b,
        "processing fallback failed",
        deadline,
        shutdown,
        None,
    ) {
        terminate(&mut r.child, deadline);
        return Err(e);
    }
    r.input.take();
    let frame = match recv_until(&r.frames, deadline, RESULT_MAX, shutdown, false) {
        Ok(v) => v,
        Err(e) => {
            terminate(&mut r.child, deadline);
            return Err(e.message);
        }
    };
    let result = decode_result(frame);
    if result.is_ok() {
        reap_clean(&mut r.child, deadline, shutdown)?;
    } else {
        terminate(&mut r.child, deadline);
    }
    result
}
fn reap_clean(c: &mut Child, deadline: Instant, shutdown: &AtomicBool) -> Result<(), String> {
    loop {
        if shutdown.load(Ordering::Acquire) {
            terminate(c, deadline);
            return Err("processing cancelled".into());
        }
        match c.try_wait() {
            Ok(Some(s)) => return clean(s),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            Ok(None) => {
                terminate(c, deadline);
                return Err("processing worker timed out".into());
            }
            Err(_) => {
                terminate(c, deadline);
                return Err("processing worker exit failed".into());
            }
        }
    }
}
fn clean(s: ExitStatus) -> Result<(), String> {
    if s.success() {
        Ok(())
    } else {
        Err("processing worker exited unsuccessfully".into())
    }
}
fn poll_fd(
    fd: libc::c_int,
    events: libc::c_short,
    deadline: Instant,
    shutdown: &AtomicBool,
) -> Result<(), String> {
    while Instant::now() < deadline {
        if shutdown.load(Ordering::Acquire) {
            return Err("processing cancelled".into());
        }
        let left = deadline.saturating_duration_since(Instant::now());
        let timeout = left.min(Duration::from_millis(50)).as_millis().max(1) as i32;
        let mut pfd = libc::pollfd {
            fd,
            events,
            revents: 0,
        };
        let rc = unsafe { libc::poll(&mut pfd, 1, timeout) };
        if rc > 0 {
            return Ok(());
        }
        if rc < 0 && io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
            return Err("processing worker input failed".into());
        }
    }
    Err("processing worker timed out".into())
}

fn terminate(c: &mut Child, deadline: Instant) {
    if c.try_wait().ok().flatten().is_some() {
        return;
    }
    unsafe {
        libc::kill(-(c.id() as i32), libc::SIGTERM);
    }
    if Instant::now() >= deadline {
        unsafe { libc::kill(-(c.id() as i32), libc::SIGKILL) };
        let _ = c.wait();
        return;
    }
    let end = (Instant::now() + Duration::from_millis(500)).min(deadline);
    while Instant::now() < end {
        if c.try_wait().ok().flatten().is_some() {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    unsafe {
        libc::kill(-(c.id() as i32), libc::SIGKILL);
    }
    let _ = c.wait();
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Fixture {
        root: PathBuf,
        worker: PathBuf,
        wav: PathBuf,
        pcm: PathBuf,
    }

    impl Fixture {
        fn new(body: &str) -> Self {
            let root = std::env::temp_dir().join(format!(
                "sayall-worker-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
            let worker = root.join("sayall-process");
            fs::write(&worker, format!("#!/bin/sh\nset -eu\n{body}\n")).unwrap();
            fs::set_permissions(&worker, fs::Permissions::from_mode(0o700)).unwrap();
            let wav = root.join("audio.wav");
            let pcm = root.join("audio.pcm");
            fs::write(&wav, b"wav").unwrap();
            fs::write(&pcm, b"pcm").unwrap();
            Self {
                root,
                worker,
                wav,
                pcm,
            }
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    fn config(streaming: bool) -> ProviderConfig {
        ProviderConfig {
            local_model_path: None,
            deepgram_api_key: "secret-deepgram".into(),
            deepgram_model: "nova-3".into(),
            deepgram_language: "en".into(),
            deepgram_region: "global".into(),
            keyterms: vec![],
            smart_format: false,
            punctuate: false,
            dictation: false,
            numerals: false,
            measurements: false,
            streaming,
            finalize_ms: 2000,
            llm_api_key: String::new(),
            llm_model: "gpt-oss-120b".into(),
            llm_base_url: "https://api.cerebras.ai/v1/chat/completions".into(),
            processing_profile: ProcessingProfile::Clean,
        }
    }

    #[test]
    fn completion_budget_preserves_the_full_local_watchdog() {
        for streaming in [false, true] {
            let mut cfg = config(streaming);
            assert_eq!(completion_timeout(&cfg), Duration::from_secs(45));
            assert_eq!(startup_timeout(&cfg), Duration::from_secs(2));
            cfg.local_model_path = Some(PathBuf::from("/models/q8.gguf"));
            assert_eq!(completion_timeout(&cfg), Duration::from_secs(150));
            assert_eq!(startup_timeout(&cfg), Duration::from_secs(120));
        }
    }

    fn shutdown() -> Arc<AtomicBool> {
        Arc::new(AtomicBool::new(false))
    }

    #[test]
    fn supervised_bounds_pipe_inheriting_descendants() {
        let fixture = Fixture::new("/bin/sleep 30 &\nexit 0");
        let started = Instant::now();
        assert!(supervised(&fixture.worker, &[], &[], Duration::from_millis(100)).is_err());
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn supervised_kills_descendants_after_oversized_output() {
        let fixture = Fixture::new("/bin/sleep 30 &\n/usr/bin/yes x | /usr/bin/head -c 5000");
        let started = Instant::now();
        assert!(supervised(&fixture.worker, &[], &[], Duration::from_secs(1)).is_err());
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    fn probe() -> String {
        format!(
            r#"if [ "${{1-}}" = "--worker-info" ]; then
  printf '%s\n' '{{"protocol_version":4,"build_version":"{}"}}'
  IFS= read -r ignored || true
  exit 0
fi"#,
            crate::BUILD_VERSION
        )
    }

    #[test]
    #[ignore = "13-second startup deadline regression; run explicitly"]
    fn local_startup_accepts_readiness_after_former_deadline() {
        let body = format!(
            r#"{}
[ "${{1-}}" = "--local-stream" ] || exit 31
IFS= read -r request
sleep 13
printf '%s\n' '{{"version":4,"event":"ready","streaming":true}}'
IFS= read -r finish
printf '%s\n' '{{"version":4,"status":"no_speech","processing_profile":"clean","transport":"local"}}'
"#,
            probe()
        );
        let fixture = Fixture::new(&body);
        let mut cfg = config(true);
        cfg.local_model_path = Some("/models/q8.gguf".into());
        let worker = Worker::start(
            &fixture.worker,
            &fixture.wav,
            &fixture.pcm,
            &cfg,
            shutdown(),
        )
        .unwrap();
        assert!(worker.running.is_some());
        assert!(worker.finish_session().is_ok());
    }

    #[test]
    fn local_explicit_batch_keeps_cloud_credentials_out() {
        let body = format!(
            r#"{}
if [ "${{1-}}" = "--local-readiness" ]; then
  printf '%s' '{{"version":4,"ready":true}}'; exit 0
fi
[ "${{1-}}" != "--stream" ] || exit 31
IFS= read -r request || true
case "$request" in *secret-deepgram*|*secret-cerebras*|*pcm_path*|*stream_finalize_timeout_ms*) exit 32;; esac
case "$request" in *'"stt_provider":"local"'*) ;; *) exit 33;; esac
printf '%s' '{{"version":4,"status":"success","text":"local result","processing_profile":"clean","transport":"local"}}'
"#,
            probe()
        );
        let fixture = Fixture::new(&body);
        let mut cfg = config(false);
        cfg.local_model_path = Some("/models/q8.gguf".into());
        cfg.llm_api_key = "secret-cerebras".into();
        local_readiness(&fixture.worker, &cfg).unwrap();
        let worker = Worker::start(
            &fixture.worker,
            &fixture.wav,
            &fixture.pcm,
            &cfg,
            shutdown(),
        )
        .unwrap();
        assert!(worker.running.is_none());
        assert_eq!(worker.completion_timeout, Duration::from_secs(150));
        assert_eq!(
            worker.finish_session().unwrap(),
            Outcome::Transcript {
                text: "local result".into(),
                processing_profile: ProcessingProfile::Clean,
                transport: Transport::Local,
                warning: None,
            }
        );
        cfg.processing_profile = ProcessingProfile::Polished;
        assert!(encode(&fixture.wav, None, &cfg).is_err());
    }

    #[test]
    fn local_incremental_uses_private_mode_and_never_retries_failed_streams() {
        for success in [true, false] {
            let terminal = if success {
                "printf '%s\\n' '{\"version\":4,\"status\":\"success\",\"text\":\"local result\",\"processing_profile\":\"clean\",\"transport\":\"local\"}'"
            } else {
                "exit 0"
            };
            let body = format!(
                r#"{}
if [ "${{1-}}" != --local-stream ]; then touch "$(dirname "$0")/fallback"; exit 99; fi
IFS= read -r request
case "$request" in *secret-deepgram*|*secret-cerebras*) exit 32;; esac
case "$request" in *'"pcm_path"'*) ;; *) exit 33;; esac
printf '%s\n' '{{"version":4,"event":"ready","streaming":true}}'
IFS= read -r finish
{}
"#,
                probe(),
                terminal
            );
            let fixture = Fixture::new(&body);
            let mut cfg = config(true);
            cfg.local_model_path = Some("/models/q8.gguf".into());
            let worker = Worker::start(
                &fixture.worker,
                &fixture.wav,
                &fixture.pcm,
                &cfg,
                shutdown(),
            )
            .unwrap();
            assert!(worker.running.is_some());
            let result = worker.finish(Duration::from_secs(2));
            if success {
                assert!(matches!(
                    result,
                    Ok(Outcome::Transcript {
                        transport: Transport::Local,
                        ..
                    })
                ));
            } else {
                assert!(result.is_err());
            }
            assert!(!fixture.root.join("fallback").exists());
        }
    }

    #[test]
    fn stream_exchange_clears_environment_and_sends_secrets_on_stdin() {
        let body = format!(
            r#"{}
[ "${{SAYALL_MUST_NOT_LEAK+x}}" != x ]
[ "${{1-}}" = "--stream" ]
IFS= read -r request
case "$request" in *secret-deepgram*) ;; *) exit 31;; esac
printf '%s\n' '{{"version":4,"event":"ready","streaming":true}}'
IFS= read -r finish
[ "$finish" = '{{"command":"finish","force_rest":false,"version":4}}' ]
printf '%s\n' '{{"version":4,"status":"success","text":"hello","processing_profile":"clean","transport":"stream"}}'"#,
            probe()
        );
        let fixture = Fixture::new(&body);
        unsafe { std::env::set_var("SAYALL_MUST_NOT_LEAK", "present") };
        let worker = Worker::start(
            &fixture.worker,
            &fixture.wav,
            &fixture.pcm,
            &config(true),
            shutdown(),
        )
        .unwrap();
        unsafe { std::env::remove_var("SAYALL_MUST_NOT_LEAK") };
        assert_eq!(
            worker.finish(Duration::from_secs(2)).unwrap(),
            Outcome::Transcript {
                text: "hello".into(),
                processing_profile: ProcessingProfile::Clean,
                transport: Transport::Stream,
                warning: None,
            }
        );
    }

    #[test]
    fn disabled_streaming_runs_batch_without_launching_stream_mode() {
        let body = format!(
            r#"{}
if [ "${{1-}}" = "--stream" ]; then exit 32; fi
IFS= read -r request
case "$request" in *pcm_path*) exit 33;; esac
printf '%s' '{{"version":4,"status":"no_speech","processing_profile":"clean","transport":"rest"}}'"#,
            probe()
        );
        let fixture = Fixture::new(&body);
        let worker = Worker::start(
            &fixture.worker,
            &fixture.wav,
            &fixture.pcm,
            &config(false),
            shutdown(),
        )
        .unwrap();
        assert_eq!(
            worker.finish(Duration::from_secs(2)).unwrap(),
            Outcome::NoSpeech {
                processing_profile: ProcessingProfile::Clean,
                transport: Transport::Rest,
            }
        );
    }

    #[test]
    fn preterminal_stream_failure_falls_back_exactly_once() {
        let log = std::env::temp_dir().join(format!(
            "sayall-worker-log-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let body = format!(
            r#"{}
if [ "${{1-}}" = "--stream" ]; then
  printf S >> '{}'
  IFS= read -r request
  case "$request" in *'"processing_profile":"clean"'*) ;; *) exit 37;; esac
  printf '%s\n' '{{"version":4,"event":"ready","streaming":true}}'
  exit 0
fi
printf B >> '{}'
IFS= read -r request
case "$request" in *'"processing_profile":"clean"'*) ;; *) exit 38;; esac
printf '%s' '{{"version":4,"status":"success","text":"fallback","processing_profile":"clean","transport":"rest"}}'"#,
            probe(),
            log.display(),
            log.display()
        );
        let fixture = Fixture::new(&body);
        let worker = Worker::start(
            &fixture.worker,
            &fixture.wav,
            &fixture.pcm,
            &config(true),
            shutdown(),
        )
        .unwrap();
        assert_eq!(
            worker.finish(Duration::from_secs(2)).unwrap(),
            Outcome::Transcript {
                text: "fallback".into(),
                processing_profile: ProcessingProfile::Clean,
                transport: Transport::Rest,
                warning: None,
            }
        );
        assert_eq!(fs::read_to_string(&log).unwrap(), "SB");
        let _ = fs::remove_file(log);
    }

    #[test]
    fn received_malformed_terminal_frame_is_not_retried() {
        let log = std::env::temp_dir().join(format!(
            "sayall-worker-log-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let body = format!(
            r#"{}
if [ "${{1-}}" = "--stream" ]; then
  printf S >> '{}'
  IFS= read -r request
  printf '%s\n' '{{"version":4,"event":"ready","streaming":true}}'
  IFS= read -r finish
  printf '%s\n' '{{'
  exit 0
fi
printf B >> '{}'
exit 34"#,
            probe(),
            log.display(),
            log.display()
        );
        let fixture = Fixture::new(&body);
        let worker = Worker::start(
            &fixture.worker,
            &fixture.wav,
            &fixture.pcm,
            &config(true),
            shutdown(),
        )
        .unwrap();
        assert!(worker.finish(Duration::from_secs(2)).is_err());
        assert_eq!(fs::read_to_string(&log).unwrap(), "S");
        let _ = fs::remove_file(log);
    }

    #[test]
    fn partial_terminal_prefix_without_newline_is_not_retried() {
        let log = std::env::temp_dir().join(format!(
            "sayall-worker-log-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let body = format!(
            r#"{}
if [ "${{1-}}" = "--stream" ]; then
  printf S >> '{}'
  IFS= read -r request
  printf '%s\n' '{{"version":4,"event":"ready","streaming":true}}'
  IFS= read -r finish
  printf '{{'
  /bin/sleep 2
  exit 0
fi
printf B >> '{}'
exit 35"#,
            probe(),
            log.display(),
            log.display()
        );
        let fixture = Fixture::new(&body);
        let worker = Worker::start(
            &fixture.worker,
            &fixture.wav,
            &fixture.pcm,
            &config(true),
            shutdown(),
        )
        .unwrap();
        assert!(worker.finish(Duration::from_millis(150)).is_err());
        assert_eq!(fs::read_to_string(&log).unwrap(), "S");
        let _ = fs::remove_file(log);
    }

    #[test]
    fn output_before_finish_is_rejected_without_fallback() {
        let log = std::env::temp_dir().join(format!(
            "sayall-worker-log-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let body = format!(
            r#"{}
if [ "${{1-}}" = "--stream" ]; then
  printf S >> '{}'
  IFS= read -r request
  printf '%s\n' '{{"version":4,"event":"ready","streaming":true}}'
  printf '%s\n' '{{"version":4,"status":"success","text":"unsolicited","processing_profile":"clean","transport":"stream"}}'
  IFS= read -r finish || true
  exit 0
fi
printf B >> '{}'
exit 36"#,
            probe(),
            log.display(),
            log.display()
        );
        let fixture = Fixture::new(&body);
        let worker = Worker::start(
            &fixture.worker,
            &fixture.wav,
            &fixture.pcm,
            &config(true),
            shutdown(),
        )
        .unwrap();
        std::thread::sleep(Duration::from_millis(50));
        assert!(worker.finish(Duration::from_secs(1)).is_err());
        assert_eq!(fs::read_to_string(&log).unwrap(), "S");
        let _ = fs::remove_file(log);
    }

    #[test]
    fn incompatible_worker_build_is_rejected() {
        let fixture = Fixture::new(
            r#"if [ "${1-}" = "--worker-info" ]; then
  printf '%s\n' '{"protocol_version":4,"build_version":"different"}'
  IFS= read -r ignored || true
fi"#,
        );
        assert!(
            Worker::start(
                &fixture.worker,
                &fixture.wav,
                &fixture.pcm,
                &config(false),
                shutdown()
            )
            .is_err()
        );
    }

    #[test]
    fn v4_codec_requires_metadata_and_projects_transformation_warning() {
        let local = include_bytes!("../../../tests/contracts-0.2/worker-result-local.json");
        assert_eq!(
            decode_result(local.to_vec()).unwrap(),
            Outcome::Transcript {
                text: "Local speech.".into(),
                processing_profile: ProcessingProfile::Clean,
                transport: Transport::Local,
                warning: None,
            }
        );
        assert!(
            decode_result(
                include_bytes!("../../../tests/contracts-0.2/worker-result-v3-rejected.json")
                    .to_vec()
            )
            .is_err()
        );
        let request: serde_json::Value =
            serde_json::from_slice(&encode(Path::new("/tmp/a.wav"), None, &config(false)).unwrap())
                .unwrap();
        assert_eq!(request["version"], 4);
        assert_eq!(request["processing_profile"], "clean");
        assert!(request.get("cleanup_enabled").is_none());

        assert_eq!(
            decode_result(
                br#"{"version":4,"status":"success","text":"raw","warning":"transformation_failed","processing_profile":"polished","transport":"stream"}"#.to_vec()
            )
            .unwrap(),
            Outcome::Transcript {
                text: "raw".into(),
                processing_profile: ProcessingProfile::Polished,
                transport: Transport::Stream,
                warning: Some(Warning::TransformationFailed),
            }
        );
        for invalid in [
            br#"{"version":2,"status":"no_speech","processing_profile":"clean","transport":"rest"}"#.as_slice(),
            br#"{"version":4,"status":"no_speech","transport":"rest"}"#.as_slice(),
            br#"{"version":4,"status":"no_speech","processing_profile":"clean"}"#.as_slice(),
            br#"{"version":4,"status":"success","text":"raw","warning":"provider_failed","processing_profile":"polished","transport":"stream"}"#.as_slice(),
            br#"{"version":4,"status":"success","text":"raw","error":"internal","processing_profile":"polished","transport":"stream"}"#.as_slice(),
            br#"{"version":4,"status":"no_speech","text":"raw","processing_profile":"clean","transport":"rest"}"#.as_slice(),
            br#"{"version":4,"status":"error","processing_profile":"clean","transport":"rest"}"#.as_slice(),
            br#"{"version":4,"status":"error","error":"future_error","processing_profile":"clean","transport":"rest"}"#.as_slice(),
            br#"{"version":4,"status":"future_status","processing_profile":"clean","transport":"rest"}"#.as_slice(),
        ] {
            assert!(decode_result(invalid.to_vec()).is_err());
        }
    }
}
