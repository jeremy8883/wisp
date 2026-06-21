"""OpenAI Realtime transcription streaming client.

Two layers:

  TranscriptRouter -- PURE.  Maps realtime transcription events onto the
                      Corrector and returns keystroke ops.  Fully unit-tested,
                      no sockets involved.

  StreamingTranscriber -- the live plumbing: opens the WebSocket, streams
                          pw-record PCM up, feeds received events through the
                          router, and presses the resulting keys.  This part
                          needs a real machine (mic + ydotool + network) and is
                          verified by running it, not by unit tests.

Realtime API reference (GA, model: gpt-4o-transcribe):
  wss://api.openai.com/v1/realtime?intent=transcription
  Send:    session.update (session.type = "transcription"), input_audio_buffer.append
  Receive: conversation.item.input_audio_transcription.delta     (incremental)
           conversation.item.input_audio_transcription.completed (authoritative)

Set WISP_DEBUG=1 to log every incoming event type (and the full text of any
error) to stderr -- useful when the API schema shifts.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading

from correction import Corrector

DELTA_EVENT = "conversation.item.input_audio_transcription.delta"
COMPLETED_EVENT = "conversation.item.input_audio_transcription.completed"

WS_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

# GA realtime requires an input rate >= 24000 Hz.
SAMPLE_RATE = 24000
# 16-bit mono => 2 bytes/sample.  ~100 ms per frame keeps latency low.
FRAME_BYTES = SAMPLE_RATE * 2 // 10


class TranscriptRouter:
    """Turn realtime transcription events into keystroke ops (pure)."""

    def __init__(self, corrector=None, separator=" "):
        self.corrector = corrector if corrector is not None else Corrector()
        self.separator = separator
        self._delta = ""  # accumulated delta text for the in-progress item

    def handle(self, event) -> list:
        etype = event.get("type")

        if etype == DELTA_EVENT:
            # Deltas are append-only fragments of the current segment.
            self._delta += event.get("delta", "")
            return self.corrector.set_pending(self._delta)

        if etype == COMPLETED_EVENT:
            # Authoritative (possibly corrected) text for the segment.  Apply the
            # correction, freeze it, then drop in a separator before the next.
            final = event.get("transcript", "")
            ops = self.corrector.set_pending(final)
            ops += self.corrector.commit()
            ops += self.corrector.append(self.separator)
            self._delta = ""
            return ops

        # error / session / speech_started / etc. -- nothing to type.
        return []


# How long the server must hear silence before it finalizes a segment.  The API
# default (~500ms) chops slow/deliberate speech into separate sentences, which
# stops later words from correcting earlier ones.  Wait longer by default.
DEFAULT_SILENCE_MS = 1000


def session_config(model="gpt-4o-transcribe", language=None,
                   silence_ms=DEFAULT_SILENCE_MS):
    """Build the GA session.update payload for a transcription session.

    GA restructured this from the beta `transcription_session.update`: the event
    is now `session.update`, the session is typed `"transcription"`, and audio
    config lives under `session.audio.input`.
    """
    transcription = {"model": model}
    if language:
        transcription["language"] = language
    # Server-side VAD segments speech for us; each segment yields one completed
    # event.  A longer silence window keeps a pause-heavy sentence in one segment.
    turn_detection = {"type": "server_vad"}
    if silence_ms is not None:
        turn_detection["silence_duration_ms"] = silence_ms
    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                    "transcription": transcription,
                    "turn_detection": turn_detection,
                },
            },
        },
    }


def _debug(msg):
    if os.environ.get("WISP_DEBUG"):
        print(f"[wisp] {msg}", file=sys.stderr, flush=True)


class StreamingTranscriber:
    """Live mic -> Realtime API -> corrected typing.

    Requires the `websocket-client` package and a running ydotoold + pw-record.
    """

    def __init__(self, api_key, keyboard, *, model="gpt-4o-transcribe",
                 language=None, silence_ms=DEFAULT_SILENCE_MS, on_error=None):
        self.api_key = api_key
        self.keyboard = keyboard
        self.model = model
        self.language = language
        self.silence_ms = silence_ms
        self.on_error = on_error or (lambda msg: None)

        self.router = TranscriptRouter()
        self._stop = threading.Event()
        self._record = None
        self._ws = None
        self._audio_thread = None

    # -- audio capture ------------------------------------------------------

    def _pump_audio(self):
        """Read PCM from pw-record and push it to the socket until stopped."""
        self._record = subprocess.Popen(
            [
                "pw-record", f"--rate={SAMPLE_RATE}",
                "--channels=1", "--format=s16", "-",
            ],
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            while not self._stop.is_set():
                chunk = self._record.stdout.read(FRAME_BYTES)
                if not chunk:
                    break
                self._send({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                })
        finally:
            self._terminate_recorder()
            # Flush whatever is buffered so the final words get transcribed.
            self._send({"type": "input_audio_buffer.commit"})

    def _terminate_recorder(self):
        if self._record and self._record.poll() is None:
            self._record.terminate()
            try:
                self._record.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._record.kill()

    def _send(self, payload):
        ws = self._ws
        if ws is not None:
            try:
                ws.send(json.dumps(payload))
            except Exception:  # socket closing/closed -- nothing more to send
                pass

    # -- websocket callbacks ------------------------------------------------

    def _on_open(self, ws):
        self._send(session_config(self.model, self.language, self.silence_ms))
        self._audio_thread = threading.Thread(target=self._pump_audio, daemon=True)
        self._audio_thread.start()

    def _on_message(self, ws, raw):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return
        etype = event.get("type")
        _debug(f"event: {etype}")
        if etype == "error":
            # Notifications truncate; stderr gets the whole thing.
            err = event.get("error", "unknown realtime error")
            print(f"[wisp] realtime error: {json.dumps(err)}",
                  file=sys.stderr, flush=True)
            self.on_error(str(err))
            return
        ops = self.router.handle(event)
        if ops:
            self.keyboard.apply(ops)

    def _on_error(self, ws, error):
        print(f"[wisp] socket error: {error}", file=sys.stderr, flush=True)
        self.on_error(str(error))

    # -- lifecycle ----------------------------------------------------------

    def run(self):
        """Open the socket and block until stop() (or the socket closes)."""
        import websocket  # lazy import: only the streaming path needs it

        # The realtime API is GA: no "OpenAI-Beta: realtime=v1" header (it now
        # rejects the beta opt-in). Plain bearer auth on /v1/realtime.
        self._ws = websocket.WebSocketApp(
            WS_URL,
            header=[f"Authorization: Bearer {self.api_key}"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
        )
        self._ws.run_forever()

    def stop(self):
        """Stop recording, flush, and close the socket."""
        self._stop.set()
        self._terminate_recorder()
        if self._audio_thread:
            self._audio_thread.join(timeout=3)
        if self._ws is not None:
            self._ws.close()
