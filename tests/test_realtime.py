"""Tests for the pure realtime event router (no sockets / no network)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from correction import TypeText  # noqa: E402
from realtime import (  # noqa: E402
    COMPLETED_EVENT,
    DELTA_EVENT,
    TranscriptRouter,
    session_config,
)
from test_correction import apply_ops  # noqa: E402


def delta(text):
    return {"type": DELTA_EVENT, "delta": text}


def completed(text):
    return {"type": COMPLETED_EVENT, "transcript": text}


def feed(r, screen, event):
    """Route an event and apply the ops, guarding against crossing committed.

    The safety bound is the committed length *before* the event is handled: a
    completed event commits within the same call, but its deletions were planned
    against pending while committed was still its prior length.
    """
    committed_len = len(r.corrector.committed)
    return apply_ops(screen, committed_len, r.handle(event))


class RouterTests(unittest.TestCase):
    def test_deltas_accumulate_and_type(self):
        r = TranscriptRouter()
        screen = feed(r, "", delta("Hel"))
        screen = feed(r, screen, delta("lo"))
        self.assertEqual(screen, "Hello")

    def test_completed_corrects_then_commits_with_separator(self):
        r = TranscriptRouter(separator=" ")
        screen = ""
        for ev in [delta("helo"), delta(" wrld")]:
            screen = feed(r, screen, ev)
        self.assertEqual(screen, "helo wrld")
        # The completed event carries the corrected transcript.
        screen = feed(r, screen, completed("hello world"))
        self.assertEqual(screen, "hello world ")  # trailing separator
        self.assertEqual(r.corrector.committed, "hello world ")
        self.assertEqual(r.corrector.pending, "")

    def test_next_segment_does_not_disturb_committed(self):
        r = TranscriptRouter()
        screen = feed(r, "", delta("first"))
        screen = feed(r, screen, completed("first."))
        # A second segment that gets heavily revised must never touch "first.".
        for ev in [delta("sek"), delta("ond"), completed("second word")]:
            screen = feed(r, screen, ev)  # raises if it crosses committed
        self.assertTrue(screen.startswith("first. "))
        self.assertEqual(screen, "first. second word ")

    def test_delta_resets_between_segments(self):
        r = TranscriptRouter()
        r.handle(delta("aaa"))
        r.handle(completed("aaa"))
        # New segment's delta should not be concatenated onto the previous one.
        ops = r.handle(delta("bbb"))
        self.assertEqual(ops, [TypeText("bbb")])

    def test_unknown_events_are_ignored(self):
        r = TranscriptRouter()
        self.assertEqual(r.handle({"type": "input_audio_buffer.speech_started"}), [])
        self.assertEqual(r.handle({"type": "error", "error": "x"}), [])


class SessionConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = session_config()
        self.assertEqual(cfg["type"], "session.update")
        s = cfg["session"]
        self.assertEqual(s["type"], "transcription")
        inp = s["audio"]["input"]
        self.assertEqual(inp["format"]["type"], "audio/pcm")
        self.assertEqual(inp["transcription"]["model"], "gpt-4o-transcribe")
        self.assertNotIn("language", inp["transcription"])
        self.assertEqual(inp["turn_detection"]["type"], "server_vad")

    def test_language_included_when_set(self):
        cfg = session_config(language="en")
        inp = cfg["session"]["audio"]["input"]
        self.assertEqual(inp["transcription"]["language"], "en")

    def test_silence_duration_included_by_default(self):
        td = session_config()["session"]["audio"]["input"]["turn_detection"]
        self.assertEqual(td["silence_duration_ms"], 1000)

    def test_silence_duration_custom_and_omittable(self):
        td = session_config(silence_ms=2500)["session"]["audio"]["input"]["turn_detection"]
        self.assertEqual(td["silence_duration_ms"], 2500)
        td = session_config(silence_ms=None)["session"]["audio"]["input"]["turn_detection"]
        self.assertNotIn("silence_duration_ms", td)


if __name__ == "__main__":
    unittest.main()
