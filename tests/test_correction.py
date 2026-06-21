"""Tests for the pure correction engine.

Run with:  python3 -m unittest discover -s tests  (no third-party deps)

The recurring technique here is `apply_ops`: a faithful simulation of how the
keystroke ops mutate the screen *under our documented model*.  It deletes from
the end and -- crucially -- raises if any delete would reach into the committed
region.  So every test that round-trips through `apply_ops` is also implicitly
asserting "the correction never went too far".
"""

import random
import string
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from correction import (  # noqa: E402
    CharDelete,
    Corrector,
    TypeText,
    WordDelete,
    common_prefix_len,
    deleted_char_count,
    plan_deletion,
)


def apply_ops(screen, committed_len, ops):
    """Simulate `ops` against `screen` (cursor at the end).

    Raises AssertionError if a deletion would cross into the first
    `committed_len` characters -- i.e. if a correction went too far.
    """
    buf = list(screen)
    for op in ops:
        if isinstance(op, (WordDelete, CharDelete)):
            n = op.chars if isinstance(op, WordDelete) else op.count
            assert n >= 0, f"negative delete: {op!r}"
            assert len(buf) - n >= committed_len, (
                f"deletion {op!r} would cross into committed region "
                f"(len={len(buf)}, committed={committed_len})"
            )
            del buf[len(buf) - n:]
        elif isinstance(op, TypeText):
            buf.extend(op.text)
        else:  # pragma: no cover - guards against new op types
            raise AssertionError(f"unknown op {op!r}")
    return "".join(buf)


class CommonPrefixTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(common_prefix_len("hello", "help"), 3)
        self.assertEqual(common_prefix_len("", "abc"), 0)
        self.assertEqual(common_prefix_len("abc", ""), 0)
        self.assertEqual(common_prefix_len("same", "same"), 4)
        self.assertEqual(common_prefix_len("abc", "abcdef"), 3)


class PlanDeletionInvariantTests(unittest.TestCase):
    """The non-negotiable invariant: ops delete exactly len(to_delete) chars."""

    SAMPLES = [
        "",
        "a",
        "hello",
        "hello world",
        "the quick brown fox",
        "hello, world!",
        "don't stop",
        "co-op mode",
        "a  b",            # double space (ambiguous)
        "trailing space ",
        " leading",
        "wow!!! really???",
        "mix3d numb3rs 42",
        "tab\tseparated",
        "emoji 😀 here",
        "café crème",      # non-ASCII letters
        "...",
        "word.",
    ]

    def test_exact_char_count_various_neighbors(self):
        for s in self.SAMPLES:
            for left in ("", " ", "x", ".", "9"):
                ops = plan_deletion(s, left)
                self.assertEqual(
                    deleted_char_count(ops),
                    len(s),
                    msg=f"to_delete={s!r} left={left!r} ops={ops!r}",
                )

    def test_simulation_reconstructs_empty(self):
        # Deleting the whole suffix should leave exactly the retained text.
        for s in self.SAMPLES:
            retained = "KEEP"
            ops = plan_deletion(s, retained[-1] if retained else "")
            result = apply_ops(retained + s, len(retained), ops)
            self.assertEqual(result, retained, msg=f"s={s!r} ops={ops!r}")


class PlanDeletionStrategyTests(unittest.TestCase):
    def test_clean_words_use_word_delete(self):
        ops = plan_deletion("hello world", left_neighbor=" ")
        self.assertTrue(all(isinstance(o, WordDelete) for o in ops), ops)
        self.assertEqual(len(ops), 2)

    def test_punctuation_uses_char_delete(self):
        # The "!" must be a char delete; "world" before it a word delete.
        ops = plan_deletion("hello world!", left_neighbor=" ")
        self.assertIsInstance(ops[0], CharDelete)   # the '!'
        self.assertEqual(ops[0].count, 1)
        self.assertTrue(any(isinstance(o, WordDelete) for o in ops))

    def test_returns_to_word_mode_after_ambiguous_char(self):
        # "alpha bravo! charlie": from the right -> charlie(word), space+? ...
        # "!" forces char mode, but "alpha"/"bravo" must still be word deletes.
        ops = plan_deletion("alpha bravo! charlie", left_neighbor="")
        word_ops = [o for o in ops if isinstance(o, WordDelete)]
        self.assertGreaterEqual(len(word_ops), 2, ops)
        self.assertEqual(deleted_char_count(ops), len("alpha bravo! charlie"))

    def test_apostrophe_word_is_char_deleted_then_resumes(self):
        ops = plan_deletion("well don't", left_neighbor="")
        # "don't" contains an apostrophe -> char deletes for that whole token;
        # "well" is clean -> word delete.
        self.assertTrue(any(isinstance(o, WordDelete) for o in ops))
        self.assertEqual(deleted_char_count(ops), len("well don't"))
        self.assertEqual(apply_ops("well don't", 0, ops), "")

    def test_seam_guard_refuses_word_delete_into_retained_word(self):
        # to_delete is a clean word but the retained text to its left ends mid
        # word (no space): a word-delete could eat the retained chars, so we
        # must char-delete instead.
        ops = plan_deletion("world", left_neighbor="o")  # e.g. retained "hello|world"
        self.assertTrue(all(isinstance(o, CharDelete) for o in ops), ops)
        self.assertEqual(deleted_char_count(ops), 5)

    def test_seam_word_delete_ok_when_neighbor_is_space(self):
        ops = plan_deletion("world", left_neighbor=" ")
        self.assertEqual(len(ops), 1)
        self.assertIsInstance(ops[0], WordDelete)

    def test_double_space_is_ambiguous(self):
        # The run "b" should not greedily eat a space when two spaces precede it.
        ops = plan_deletion("a  b", left_neighbor="")
        self.assertEqual(deleted_char_count(ops), 4)
        self.assertEqual(apply_ops("a  b", 0, ops), "")


class CorrectorTests(unittest.TestCase):
    def test_append_only_growth_types_no_deletes(self):
        c = Corrector()
        ops = c.set_pending("the quick")
        self.assertEqual(ops, [TypeText("the quick")])
        ops = c.set_pending("the quick brown")
        self.assertEqual(ops, [TypeText(" brown")])  # pure append
        self.assertEqual(c.text, "the quick brown")

    def test_correction_backspaces_only_changed_suffix(self):
        c = Corrector()
        c.set_pending("the quick brown fax")
        ops = c.set_pending("the quick brown fox")
        # Only "fax" changed; common prefix is "the quick brown f".
        self.assertEqual(deleted_char_count(ops), len("ax"))
        self.assertEqual(ops[-1], TypeText("ox"))
        self.assertEqual(c.text, "the quick brown fox")

    def test_set_pending_never_deletes_more_than_pending(self):
        c = Corrector()
        c.committed = "PERMANENT TEXT "
        c.pending = "some words here"
        old_len = len(c.pending)
        # Replace with something completely different.
        ops = c.set_pending("totally other stuff entirely")
        self.assertLessEqual(deleted_char_count(ops), old_len)

    def test_commit_makes_text_untouchable(self):
        c = Corrector()
        c.set_pending("first segment")
        c.commit()
        self.assertEqual(c.committed, "first segment")
        self.assertEqual(c.pending, "")
        # A brand-new segment that is later cleared must not delete the committed
        # text -- simulate against the real screen.
        screen = c.text
        ops = c.set_pending("second")
        screen = apply_ops(screen, len(c.committed), ops)
        ops = c.set_pending("")  # the segment got wiped
        screen = apply_ops(screen, len(c.committed), ops)
        self.assertEqual(screen, "first segment")

    def test_full_screen_roundtrip_with_committed(self):
        c = Corrector()
        c.set_pending("hello")
        c.commit()
        screen = "hello"
        for revision in ["wor", "world", "word", "worlds", "worlds!"]:
            ops = c.set_pending(revision)
            screen = apply_ops(screen, len(c.committed), ops)
            self.assertEqual(screen, c.committed + revision)


class RealisticStreamTests(unittest.TestCase):
    def test_typical_revision_sequence(self):
        """Mimic deltas streaming in then a corrected final."""
        c = Corrector()
        screen = ""
        deltas = [
            "I",
            "I think",
            "I think we",
            "I think we should",
            "I think we should meat",   # mis-heard
        ]
        for d in deltas:
            ops = c.set_pending(d)
            screen = apply_ops(screen, len(c.committed), ops)
            self.assertEqual(screen, d)
        # Final corrected transcript for the segment.
        ops = c.set_pending("I think we should meet.")
        screen = apply_ops(screen, len(c.committed), ops)
        self.assertEqual(screen, "I think we should meet.")
        c.commit()
        self.assertEqual(c.committed, "I think we should meet.")


class FuzzTests(unittest.TestCase):
    ALPHABET = string.ascii_letters + string.digits + " .,!?'-\t😀é"

    def _rand_text(self, rng, max_len=24):
        n = rng.randint(0, max_len)
        return "".join(rng.choice(self.ALPHABET) for _ in range(n))

    def test_fuzz_roundtrip_and_safety(self):
        rng = random.Random(1234)
        for _ in range(5000):
            committed = self._rand_text(rng)
            old_pending = self._rand_text(rng)
            new_pending = self._rand_text(rng)

            c = Corrector()
            c.committed = committed
            c.pending = old_pending

            ops = c.set_pending(new_pending)

            # 1. Never delete more than the old pending length.
            self.assertLessEqual(
                deleted_char_count(ops), len(old_pending),
                msg=f"committed={committed!r} old={old_pending!r} new={new_pending!r}",
            )

            # 2. Applying the ops to the real screen reproduces the target and
            #    never crosses into committed (apply_ops raises if it does).
            screen = apply_ops(committed + old_pending, len(committed), ops)
            self.assertEqual(
                screen, committed + new_pending,
                msg=f"committed={committed!r} old={old_pending!r} new={new_pending!r} ops={ops!r}",
            )

            # 3. Committed region is byte-for-byte intact.
            self.assertEqual(screen[: len(committed)], committed)


if __name__ == "__main__":
    unittest.main()
