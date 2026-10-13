#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
    echo 'usage: linux-installed-artifact-smoke.sh <version> <binary-archive>' >&2
    exit 2
fi

version=$1
archive=$(realpath "$2")
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
payload="$work/payload"
home="$work/home"
runtime="$work/runtime"
mkdir -m700 "$payload" "$home" "$runtime"

# The archive is data under test. Only this trusted checkout harness performs
# extraction and installation; nothing from the payload is sourced as a script.
tar -xzf "$archive" -C "$payload"
source="$payload/sayall-$version-linux-x86_64"
[[ -d "$source" ]]

# Mirror the README's per-user archive installation in a disposable HOME.
install -Dm755 -t "$home/.local/bin" "$source/bin/sayall" "$source/bin/sayall-hud"
install -Dm755 "$source/lib/sayall/sayall-process" \
    "$home/.local/lib/sayall/sayall-process"
install -Dm644 -t "$home/.config/systemd/user" "$source"/share/systemd/user/*.service
install -Dm644 -t "$home/.local/share/applications" "$source"/share/applications/*.desktop
install -Dm644 -t "$home/.local/share/icons/hicolor/scalable/apps" \
    "$source"/share/icons/hicolor/scalable/apps/*.svg

mode() { stat -c '%a' "$1"; }
owner() { stat -c '%u:%g' "$1"; }
expected_owner=$(id -u):$(id -g)
for executable in "$home/.local/bin/sayall" "$home/.local/bin/sayall-hud" \
    "$home/.local/lib/sayall/sayall-process"; do
    [[ -x "$executable" && $(mode "$executable") == 755 ]]
    [[ $(owner "$executable") == "$expected_owner" ]]
done
for data in "$home/.config/systemd/user/sayall-hud.service" \
    "$home/.local/share/applications/dev.sayall.Hud.desktop" \
    "$home/.local/share/icons/hicolor/scalable/apps/dev.sayall.Hud.svg"; do
    [[ -f "$data" && $(mode "$data") == 644 ]]
    [[ $(owner "$data") == "$expected_owner" ]]
done

export HOME="$home" XDG_CONFIG_HOME="$home/.config" XDG_RUNTIME_DIR="$runtime"
export PATH="$home/.local/bin:/usr/bin:/bin"
[[ $(sayall --version) == "sayall $version" ]]
[[ $(sayall-hud --version) == "sayall-hud $version" ]]
worker="$home/.local/lib/sayall/sayall-process"
[[ $("$worker" --version) == "sayall-process $version" ]]
! command -v sayall-process >/dev/null 2>&1

info=$("$worker" --worker-info)
grep -Fq '"protocol_version":4' <<<"$info"
grep -Fq "\"build_version\":\"$version\"" <<<"$info"
bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/worker-info-wait.sh" "$worker"

service="$home/.config/systemd/user/sayall-hud.service"
grep -Fxq 'ExecStart=%h/.local/bin/sayall-hud --autostart' "$service"
[[ -x "$home/.local/bin/sayall-hud" ]]
desktop="$home/.local/share/applications/dev.sayall.Hud.desktop"
grep -Fxq 'Exec=sayall-hud' "$desktop"
grep -Fxq 'Icon=dev.sayall.Hud' "$desktop"
[[ -x "$home/.local/bin/$(sed -n 's/^Exec=//p' "$desktop")" ]]
icon=$(sed -n 's/^Icon=//p' "$desktop")
[[ -f "$home/.local/share/icons/hicolor/scalable/apps/$icon.svg" ]]

# Public config commands require neither a graphical session nor hardware.
sayall config init >/dev/null
config="$home/.config/sayall/config.json"
[[ -f "$config" && $(mode "$home/.config/sayall") == 700 && $(mode "$config") == 600 ]]
[[ $(owner "$home/.config/sayall") == "$expected_owner" ]]
[[ $(owner "$config") == "$expected_owner" ]]
sayall config validate >/dev/null
checksum=$(sha256sum "$config")
! sayall config init >/dev/null 2>&1
[[ $(sha256sum "$config") == "$checksum" ]]
[[ $(mode "$runtime") == 700 ]]

# Local doctor probes the installed worker's sibling helper without inference,
# credentials, downloads, real model data, or a running graphical host.
python3 - "$config" "$worker" "$version" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys
import time

config, worker = map(Path, sys.argv[1:3])
version = sys.argv[3]
model = Path(os.environ["HOME"]) / ".local/share/sayall/models/parakeet-unified-en-0.6b-Q8_0.gguf"
helper = worker.with_name("sayall-local-q8")
backup = worker.with_name("sayall-process.real")
env = dict(os.environ, XDG_DATA_HOME="", DEEPGRAM_API_KEY="synthetic-do-not-forward")

def configure(path=None):
    stt = {"provider": "local"}
    if path is not None:
        stt["local_model_path"] = str(path)
    config.write_text(json.dumps({"stt": stt, "processing": {"mode": "clean"}}))

def doctor(expected):
    result = subprocess.run(["sayall", "doctor", "--json"], env=env,
                            capture_output=True, timeout=10)
    assert result.stderr == b"", "unexpected doctor diagnostics"
    report = json.loads(result.stdout)
    assert report["configuration"].startswith(expected), report["configuration"]
    assert result.returncode == (1 if report["failures"] else 0)
    return report

try:
    configure(model)
    missing = doctor("local Q8 model is missing")
    assert missing["failures"] > 0
    model.parent.mkdir(parents=True)
    model.write_bytes(b"not Q8")
    doctor("local Q8 model is invalid")
    with model.open("wb") as out:
        out.truncate(731357696)  # sparse: readiness does not hash or load weights
    doctor("local Q8 helper is missing")
    helper.write_text("#!/bin/sh\nexit 99\n")
    helper.chmod(0o600)
    doctor("local Q8 helper is missing")
    helper.chmod(0o700)
    present = doctor("local artifacts present")
    assert missing["failures"] == present["failures"] + 1, (missing, present)
    configure()  # empty XDG_DATA_HOME falls back to HOME, just like transcription
    doctor("local artifacts present")
    env["XDG_DATA_HOME"] = str(model.parents[2])
    doctor("local artifacts present")
    env["XDG_DATA_HOME"] = str(model.parent / "missing-data-home")
    doctor("local Q8 model is missing")
    configure(model)  # explicit configuration overrides the data-home default
    doctor("local artifacts present")

    worker.rename(backup)
    info = json.dumps({"protocol_version": 4, "build_version": version})
    prefix = "#!/bin/sh\nif [ \"$1\" = --worker-info ]; then\nprintf '%s\\n' '" + info + "'\nexit 0\nfi\n"
    for body, expected in (
        ('test -z "${DEEPGRAM_API_KEY-}" || exit 9\nprintf \'%s\\n\' \'{"version":4,"ready":true}\'\n', "local artifacts present"),
        ("printf '{}\\n'\n", "local readiness probe failed"),
        ("exit 23\n", "local readiness probe failed"),
        ("exec /usr/bin/head -c 5000 /dev/zero\n", "local readiness probe failed"),
        ("exec /bin/sleep 10\n", "local readiness probe failed"),
    ):
        worker.write_text(prefix + body)
        worker.chmod(0o700)
        started = time.monotonic()
        doctor(expected)
        assert time.monotonic() - started < 8, "local readiness was not bounded"
finally:
    if backup.exists():
        worker.unlink(missing_ok=True)
        backup.rename(worker)
    helper.unlink(missing_ok=True)
    model.unlink(missing_ok=True)
PY

# Model default manual uninstall without contacting a real user manager. Remove
# every installed payload file, while retaining user-owned configuration.
rm -f -- "$home/.local/bin/sayall" "$home/.local/bin/sayall-hud" \
    "$home/.local/lib/sayall/sayall-process" \
    "$home/.config/systemd/user/sayall-hud.service" \
    "$home/.local/share/applications/dev.sayall.Hud.desktop" \
    "$home/.local/share/icons/hicolor/scalable/apps/dev.sayall.Hud.svg"
! find "$home/.local" "$home/.config/systemd" -type f -print -quit | grep -q .
[[ -f "$config" && $(mode "$config") == 600 ]]
