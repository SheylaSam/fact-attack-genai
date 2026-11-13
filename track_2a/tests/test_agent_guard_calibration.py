"""Guard calibration: the error counts, the choice rule and the dev-only reading. No index, no model, no network."""
import contextlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent import settings
from src.agent.guard import Thresholds

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_guard.py"
REPORT = Path(__file__).resolve().parents[1] / "results" / "genai" / "agent_guard_calibration.json"

spec = importlib.util.spec_from_file_location("calibrate_guard", SCRIPT)
calibrate = importlib.util.module_from_spec(spec)
sys.modules["calibrate_guard"] = calibrate
spec.loader.exec_module(calibrate)


def row(case_id, gold, scores, *, kind=None, ambiguous_with=()):
    """A calibration row; `scores` is a list of (vote_id, score, hits) in rank order."""
    return {"id": case_id, "kind": kind, "gold": gold, "ambiguous_with": list(ambiguous_with),
            "candidates": [{"vote_id": v, "score": s, "hits": h} for v, s, h in scores]}


T = Thresholds(0.02, 0.5, None)


class EvaluateTests(unittest.TestCase):
    def test_evaluate_counts_the_three_error_kinds(self):
        rows = [
            # ambiguous claim, the guard sees a clear winner: missed
            row("m1", "A", [("A", 0.80, 10), ("B", 0.70, 10)], kind="mehrdeutig", ambiguous_with=["B"]),
            # ambiguous claim, both are close: caught
            row("m2", "A", [("A", 0.80, 10), ("B", 0.79, 10)], kind="mehrdeutig", ambiguous_with=["B"]),
            # clear winner, but not the gold vote: false confidence
            row("f1", "A", [("B", 0.80, 10), ("A", 0.60, 10)]),
            # two close votes, top is gold: unnecessary question
            row("u1", "A", [("A", 0.80, 10), ("B", 0.79, 10)]),
            # fine
            row("ok", "A", [("A", 0.80, 10), ("B", 0.60, 10)]),
            # no gold vote: counts nowhere
            row("none", None, [("B", 0.80, 10), ("C", 0.79, 10)], kind="ohne_abstimmung"),
        ]
        result = calibrate.evaluate(rows, T)
        self.assertEqual((result["missed_ambiguous"], result["false_confidence"], result["unnecessary_questions"]),
                         (1, 1, 1))
        self.assertEqual((result["score_margin"], result["min_hits_ratio"]), (0.02, 0.5))

    def test_missed_ambiguous_needs_all_gold_votes_inside_close(self):
        rows = [row("m", "A", [("A", 0.80, 10), ("C", 0.79, 10), ("B", 0.50, 10)], kind="mehrdeutig",
                    ambiguous_with=["B"])]
        self.assertEqual(calibrate.evaluate(rows, T)["missed_ambiguous"], 1)  # mehrdeutig, but B is not close

    def test_ambiguous_case_is_not_a_false_confidence_or_unnecessary_question(self):
        rows = [row("m", "A", [("B", 0.80, 10), ("A", 0.50, 10)], kind="mehrdeutig", ambiguous_with=["B"])]
        result = calibrate.evaluate(rows, T)
        self.assertEqual((result["false_confidence"], result["unnecessary_questions"]), (0, 0))


class ChooseTests(unittest.TestCase):
    def test_choose_prefers_catching_ambiguity_then_avoiding_false_confidence(self):
        # delta 0.03 for both rows: a margin of at least 0.035 is needed to catch m, and that margin turns the
        # clear case u into an unnecessary question, which is accepted for catching m
        rows = [row("m", "A", [("A", 0.80, 10), ("B", 0.77, 10)], kind="mehrdeutig", ambiguous_with=["B"]),
                row("u", "A", [("A", 0.80, 10), ("B", 0.77, 10)])]
        chosen = calibrate.choose(rows)
        self.assertEqual(chosen["missed_ambiguous"], 0)
        self.assertEqual(chosen["unnecessary_questions"], 1)
        self.assertEqual(chosen["score_margin"], 0.035)

    def test_choose_avoids_false_confidence_before_unnecessary_questions(self):
        # f: B leads A by 0.02, so only a margin above 0.02 makes A close and the guard ask instead of being wrong
        rows = [row("f", "A", [("B", 0.80, 10), ("A", 0.78, 10)]),
                row("u", "A", [("A", 0.80, 10), ("B", 0.77, 10)])]
        chosen = calibrate.choose(rows)
        self.assertEqual(chosen["false_confidence"], 0)
        self.assertEqual(chosen["score_margin"], 0.025)

    def test_choose_tie_takes_the_smallest_values(self):
        rows = [row("ok", "A", [("A", 0.80, 10), ("B", 0.10, 10)])]
        chosen = calibrate.choose(rows)
        self.assertEqual((chosen["score_margin"], chosen["min_hits_ratio"]),
                         (min(calibrate.GRID_MARGINS), min(calibrate.GRID_HITS_RATIOS)))
        self.assertIsNone(chosen["none_below"])


class MainTests(unittest.TestCase):
    def test_main_reads_only_the_dev_split(self):
        calls = []

        def fake_load(*args, **kwargs):
            calls.append((args, kwargs))
            return []

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            with patch.object(calibrate, "load_testset", fake_load), \
                    patch.object(calibrate, "open_search", lambda: contextlib.nullcontext(lambda claim: [])):
                self.assertEqual(calibrate.main(["--output", str(out)]), 0)
            report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(calls, [(("dev",), {})])  # exactly one call, positional "dev", no allow_test
        for key in ("rows", "grid", "chosen", "rule", "provisional", "testset_cases_sha256", "none_below_note"):
            self.assertIn(key, report)
        self.assertEqual(len(report["grid"]), len(calibrate.GRID_MARGINS) * len(calibrate.GRID_HITS_RATIOS))


class SettingsMatchReportTests(unittest.TestCase):
    @unittest.skipUnless(REPORT.exists(), "the calibration report is not there yet")
    def test_settings_match_the_calibration_report(self):
        chosen = json.loads(REPORT.read_text(encoding="utf-8"))["chosen"]
        self.assertEqual((settings.GUARD_SCORE_MARGIN, settings.GUARD_MIN_HITS_RATIO, settings.GUARD_NONE_BELOW),
                         (chosen["score_margin"], chosen["min_hits_ratio"], chosen["none_below"]))


if __name__ == "__main__":
    unittest.main()
