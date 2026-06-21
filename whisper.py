#!/usr/bin/python3
"""Toggle speech-to-text using OpenAI Whisper API + ydotool.

Bind this to a GNOME keyboard shortcut for push-to-talk toggle.

Requires: OPENAI_API_KEY in ~/.config/whisper/env
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PIDFILE = Path("/tmp/whisper-recording.pid")
INDICATOR_PIDFILE = Path("/tmp/whisper-indicator.pid")
AUDIO = Path("/tmp/whisper-recording.wav")
ENV_FILE = Path.home() / ".config/whisper/env"
SAMPLE_RATE = 16000
MAX_DURATION = 300


def notify(message, *, urgency="normal", timeout_ms=2000, title="Whisper"):
    subprocess.run(
        ["notify-send", "-u", urgency, "-t", str(timeout_ms), title, message],
        check=False,
    )


def read_pid(pidfile):
    try:
        return int(pidfile.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def pid_alive(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, but owned by someone else
    except OSError:
        return False
    return True


def wait_for_exit(pid, poll=0.05):
    """Block until the given (non-child) process has exited."""
    if pid is None:
        return
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(poll)


def load_env_file(path):
    """Mimic `source`-ing a simple KEY=VALUE env file into os.environ."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ[key.strip()] = value


def indicator_stop():
    pid = read_pid(INDICATOR_PIDFILE)
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    INDICATOR_PIDFILE.unlink(missing_ok=True)


def indicator_signal(sig):
    pid = read_pid(INDICATOR_PIDFILE)
    if pid is not None:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def stop_record_process():
    """Stop the running pw-record/timeout process and wait for it to finish."""
    pid = read_pid(PIDFILE)
    PIDFILE.unlink(missing_ok=True)
    if pid is not None:
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            pass
        wait_for_exit(pid)


def cancel_recording():
    stop_record_process()
    AUDIO.unlink(missing_ok=True)
    indicator_stop()
    notify("Recording cancelled")


def focused_wm_class():
    """Return the wm_class of the focused window via the GNOME Windows ext."""
    try:
        out = subprocess.run(
            [
                "dbus-send", "--session", "--print-reply=literal",
                "--dest=org.gnome.Shell",
                "/org/gnome/Shell/Extensions/Windows",
                "org.gnome.Shell.Extensions.Windows.List",
            ],
            capture_output=True, text=True, check=False,
        ).stdout
        # The literal reply wraps the JSON array; find it.
        start = out.find("[")
        end = out.rfind("]")
        if start == -1 or end == -1:
            return ""
        windows = json.loads(out[start:end + 1])
        for win in windows:
            if win.get("focus"):
                return win.get("wm_class", "") or ""
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        pass
    return ""


def ydotool_key(*codes):
    subprocess.run(["ydotool", "key", *codes], check=False)


def wl_copy(text, *, primary=False):
    cmd = ["wl-copy"]
    if primary:
        cmd.append("--primary")
    subprocess.run(cmd, input=text, text=True, check=False)


def transcribe():
    with AUDIO.open("rb") as f:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            files={"file": (AUDIO.name, f, "audio/wav")},
            data={"model": "whisper-1", "response_format": "text"},
        )
    return resp


def paste_text(body, yolo):
    # ydotool type is slow — it simulates individual key presses. Paste via
    # clipboard instead. Terminals intercept Ctrl+V, so detect the focused
    # window and use Shift+Insert (PRIMARY selection) for terminals, and
    # Ctrl+V (CLIPBOARD) for everything else.
    wm_class = focused_wm_class()

    if "ghostty" in wm_class:
        wl_copy(body, primary=True)
        ydotool_key("42:1", "110:1", "110:0", "42:0")  # Shift+Insert
    else:
        try:
            old_clip = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True, text=True, check=False,
            ).stdout
        except subprocess.SubprocessError:
            old_clip = ""
        wl_copy(body)
        ydotool_key("29:1", "47:1", "47:0", "29:0")  # Ctrl+V
        time.sleep(0.1)
        wl_copy(old_clip)

    if yolo:
        time.sleep(0.1)
        ydotool_key("28:1", "28:0")  # Enter


def stop_recording(yolo):
    stop_record_process()

    if not AUDIO.exists() or AUDIO.stat().st_size == 0:
        AUDIO.unlink(missing_ok=True)
        indicator_stop()
        notify("No audio recorded", urgency="critical", timeout_ms=3000)
        sys.exit(1)

    # Signal indicator to show "Transcribing..." state.
    indicator_signal(signal.SIGUSR1)

    try:
        resp = transcribe()
    finally:
        AUDIO.unlink(missing_ok=True)
        indicator_stop()

    if resp.status_code != 200:
        notify(
            f"API error ({resp.status_code}): {resp.text}",
            urgency="critical", timeout_ms=5000,
        )
        sys.exit(1)

    body = resp.text.rstrip("\n")
    if not body:
        notify("Empty transcription", urgency="critical", timeout_ms=3000)
        sys.exit(1)

    paste_text(body, yolo)


def start_recording():
    record = subprocess.Popen(
        [
            "timeout", str(MAX_DURATION),
            "pw-record", f"--rate={SAMPLE_RATE}",
            "--channels=1", "--format=s16", str(AUDIO),
        ],
        start_new_session=True,
    )
    PIDFILE.write_text(str(record.pid))

    indicator = subprocess.Popen(
        [str(SCRIPT_DIR / "whisper-indicator")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    INDICATOR_PIDFILE.write_text(str(indicator.pid))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yolo", action="store_true",
                        help="press Enter after pasting the transcription")
    parser.add_argument("--cancel", action="store_true",
                        help="cancel an in-progress recording")
    args = parser.parse_args()

    load_env_file(ENV_FILE)

    if not os.environ.get("OPENAI_API_KEY"):
        notify(
            f"OPENAI_API_KEY not set.\nAdd it to {ENV_FILE}\n"
            "https://platform.openai.com/api-keys",
            urgency="critical",
        )
        sys.exit(1)

    os.environ["YDOTOOL_SOCKET"] = f"/run/user/{os.getuid()}/.ydotool_socket"

    recording_active = pid_alive(read_pid(PIDFILE))

    if args.cancel:
        if recording_active:
            cancel_recording()
        else:
            PIDFILE.unlink(missing_ok=True)
        return

    if recording_active:
        stop_recording(args.yolo)
    else:
        PIDFILE.unlink(missing_ok=True)
        start_recording()


if __name__ == "__main__":
    main()
