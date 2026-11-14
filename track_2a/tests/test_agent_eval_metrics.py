"""Pure offline metrics of the agent evaluation: Macro-F1 (strict and on the answered cases), errors against
abstentions, source finding, evidence validity, cost and the table of the special cases. No model, no I/O."""
import unittest

import pytest

pytest.importorskip("httpx")  # requirements-agent.txt: the testset imports the graph's outcome names
pytest.importorskip("langgraph")

from src.agent.eval_metrics import (CaseRun, answerable, cost_summary, errors_vs_abstentions, evidence_validity,
                                    macro_f1_answered, macro_f1_strict, source_finding, special_case_table)
from src.agent.testset import EvalCase

V1, V2 = "2020-02-09_de#1", "2024-06-09_de#2"


def make_case(case_id="dev-001", *, group="haupt", kind=None, expected="gestuetzt", acceptable=(), vote_id=V1,
              ambiguous_with=(), after_answer=None) -> EvalCase:
    return EvalCase(
        id=case_id, split="dev", group=group, kind=kind, novelty=None if vote_id is None else "bekannt",
        claim="Eine Behauptung.", expected=expected, acceptable=tuple(acceptable), vote_id=vote_id,
        ambiguous_with=tuple(ambiguous_with), after_answer=after_answer, page=None, quote=None, fault=None, note="")


def make_run(case, outcome, *, arm="B1", asked=False, options=(), final_vote_id="same", evidence_valid=None,
             tokens=0, latency_ms=0.0, tool_calls=0, error=None) -> CaseRun:
    if final_vote_id == "same":
        final_vote_id = case.vote_id
    return CaseRun(case=case, arm=arm, outcome=outcome, asked=asked, options=tuple(options),
                   final_vote_id=final_vote_id, evidence_valid=evidence_valid, tokens=tokens,
                   latency_ms=latency_ms, tool_calls=tool_calls, error=error)


def four_runs():
    """A: right, B: wrong verdict, C: abstains (quelle_fehlt), D: right."""
    return [
        make_run(make_case("dev-001", expected="gestuetzt"), "gestuetzt"),
        make_run(make_case("dev-002", expected="widersprochen"), "gestuetzt"),
        make_run(make_case("dev-003", expected="nicht_belegt"), "quelle_fehlt"),
        make_run(make_case("dev-004", expected="nicht_belegt"), "nicht_belegt"),
    ]


class AnswerableTest(unittest.TestCase):
    def test_answerable_is_main_group_plus_wrong_speaker_and_injection(self):
        main = make_run(make_case("dev-001"), "gestuetzt")
        speaker = make_run(make_case("sdev-01", group="spezial", kind="falscher_sprecher", expected="widersprochen"),
                           "widersprochen")
        injection = make_run(make_case("sdev-02", group="spezial", kind="prompt_injection"), "gestuetzt")
        others = [make_run(make_case(f"sdev-1{i}", group="spezial", kind=kind, expected="rueckfrage"), "rueckfrage")
                  for i, kind in enumerate(("mehrdeutig", "zusammengesetzt", "ohne_abstimmung", "api_ausfall"))]
        self.assertEqual(answerable([main, *others, speaker, injection]), [main, speaker, injection])


class MacroF1Test(unittest.TestCase):
    def test_strict_f1_counts_abstentions_and_technical_errors_as_wrong(self):
        runs = [
            make_run(make_case("dev-001", expected="gestuetzt"), "gestuetzt"),
            make_run(make_case("dev-002", expected="widersprochen"), "quelle_fehlt"),
            make_run(make_case("dev-003", expected="nicht_belegt"), "technischer_fehler"),
            # not answerable: must not count at all
            make_run(make_case("sdev-01", group="spezial", kind="mehrdeutig", expected="rueckfrage"), "rueckfrage"),
        ]
        self.assertAlmostEqual(macro_f1_strict(runs), 1 / 3)

    def test_answered_f1_reports_coverage_separately(self):
        f1, coverage = macro_f1_answered(four_runs())
        self.assertAlmostEqual(f1, 5 / 9)  # gestuetzt 2/3, widersprochen 0, nicht_belegt 1
        self.assertAlmostEqual(coverage, 0.75)
        self.assertLess(macro_f1_strict(four_runs()), 5 / 9 + 1e-9)  # abstaining is never better than strict

    def test_empty_runs_and_empty_classes_do_not_divide_by_zero(self):
        self.assertEqual(macro_f1_strict([]), 0.0)
        self.assertEqual(macro_f1_answered([]), (0.0, 0.0))
        self.assertEqual(errors_vs_abstentions([]),
                         {"wrong_judgements": 0, "abstentions": 0, "coverage": 0.0, "error_rate": 0.0})
        self.assertEqual(source_finding([]), {"right_vote": 0, "justified_questions": 0,
                                              "unjustified_questions": 0, "false_confidence": 0})
        self.assertIsNone(evidence_validity([]))
        self.assertEqual(cost_summary([]), {"tokens": 0, "p50_ms": 0.0, "p95_ms": 0.0, "tool_calls": 0,
                                            "error_rate": 0.0})
        self.assertEqual(special_case_table([]), [])
        # only one class present and every run abstains: no answered case, coverage 0
        only_abstentions = [make_run(make_case("dev-001"), "quelle_fehlt")]
        self.assertEqual(macro_f1_answered(only_abstentions), (0.0, 0.0))
        self.assertEqual(macro_f1_strict(only_abstentions), 0.0)
        # perfect on the one class that exists: the two empty classes count as 0 (fixed class order)
        self.assertAlmostEqual(macro_f1_strict([make_run(make_case("dev-001"), "gestuetzt")]), 1 / 3)


class ErrorsVsAbstentionsTest(unittest.TestCase):
    def test_wrong_judgement_and_abstention_are_separate_counts(self):
        result = errors_vs_abstentions(four_runs())
        self.assertEqual(result["wrong_judgements"], 1)
        self.assertEqual(result["abstentions"], 1)
        self.assertAlmostEqual(result["coverage"], 0.75)
        self.assertAlmostEqual(result["error_rate"], 1 / 3)  # wrong judgements among the answered cases


class SourceFindingTest(unittest.TestCase):
    def test_justified_and_unjustified_questions_use_the_gold_ambiguity(self):
        ambiguous = make_case("sdev-01", group="spezial", kind="mehrdeutig", expected="rueckfrage",
                              acceptable=("rueckfrage",), ambiguous_with=(V2,), after_answer="gestuetzt")
        clear = make_case("dev-001")
        silent = make_case("sdev-02", group="spezial", kind="mehrdeutig", expected="rueckfrage",
                           acceptable=("rueckfrage",), ambiguous_with=(V2,), after_answer="gestuetzt")
        result = source_finding([
            make_run(ambiguous, "gestuetzt", asked=True, options=(V1, V2)),
            make_run(clear, "gestuetzt", asked=True, options=(V1, V2)),
            make_run(silent, "gestuetzt", asked=False),
        ])
        self.assertEqual(result["justified_questions"], 1)
        self.assertEqual(result["unjustified_questions"], 1)

    def test_right_vote_counts_final_vote_of_cases_with_a_vote(self):
        no_vote = make_case("sdev-01", group="spezial", kind="ohne_abstimmung", expected="quelle_fehlt",
                            vote_id=None)
        result = source_finding([
            make_run(make_case("dev-001"), "gestuetzt"),
            make_run(make_case("dev-002"), "gestuetzt", final_vote_id=V2),
            make_run(make_case("dev-003"), "quelle_fehlt", final_vote_id=None),
            make_run(no_vote, "quelle_fehlt", final_vote_id=None),  # no gold vote: not part of right_vote
        ])
        self.assertEqual(result["right_vote"], 1)

    def test_false_confidence_is_a_verdict_on_the_wrong_vote(self):
        no_vote = make_case("sdev-01", group="spezial", kind="ohne_abstimmung", expected="quelle_fehlt",
                            vote_id=None)
        result = source_finding([
            make_run(make_case("dev-001"), "gestuetzt"),  # right vote: fine
            make_run(make_case("dev-002"), "widersprochen", final_vote_id=V2),  # verdict on the wrong vote
            make_run(make_case("dev-003"), "rueckfrage", final_vote_id=None, asked=True),  # no verdict
            make_run(make_case("dev-004"), "quelle_fehlt", final_vote_id=V2),  # no verdict
            make_run(no_vote, "nicht_belegt", final_vote_id=V2),  # a verdict where there is no vote at all
        ])
        self.assertEqual(result["false_confidence"], 2)


class SpecialCaseTableTest(unittest.TestCase):
    def test_special_cases_accept_the_acceptable_outcomes(self):
        speaker = make_case("sdev-01", group="spezial", kind="falscher_sprecher", expected="widersprochen",
                            acceptable=("widersprochen", "nicht_belegt"))
        ambiguous = make_case("sdev-02", group="spezial", kind="mehrdeutig", expected="rueckfrage",
                              acceptable=("rueckfrage",), ambiguous_with=(V2,), after_answer="widersprochen")
        outage = make_case("sdev-03", group="spezial", kind="api_ausfall", expected="technischer_fehler",
                           acceptable=("technischer_fehler",))
        main = make_case("dev-001")
        table = special_case_table([
            make_run(speaker, "nicht_belegt"),  # acceptable, not the expected one
            make_run(ambiguous, "widersprochen", asked=True),  # asked, then the gold after the answer
            make_run(outage, "gestuetzt"),  # not acceptable
            make_run(main, "gestuetzt"),  # not a special case: not listed
        ])
        self.assertEqual([row["id"] for row in table], ["sdev-01", "sdev-02", "sdev-03"])
        self.assertEqual(table[0], {"id": "sdev-01", "kind": "falscher_sprecher", "expected": "widersprochen",
                                    "acceptable": ("widersprochen", "nicht_belegt"), "got": "nicht_belegt",
                                    "ok": True})
        self.assertTrue(table[1]["ok"])
        self.assertFalse(table[2]["ok"])
        self.assertEqual(table[2]["got"], "gestuetzt")


class CostAndEvidenceTest(unittest.TestCase):
    def test_percentiles_of_a_single_run(self):
        summary = cost_summary([make_run(make_case(), "gestuetzt", tokens=120, latency_ms=850.0, tool_calls=3)])
        self.assertEqual(summary, {"tokens": 120, "p50_ms": 850.0, "p95_ms": 850.0, "tool_calls": 3,
                                   "error_rate": 0.0})

    def test_cost_summary_sums_and_counts_errors(self):
        runs = [make_run(make_case(f"dev-00{i}"), "gestuetzt", tokens=10 * i, latency_ms=100.0 * i, tool_calls=i,
                         error="timeout" if i == 5 else None) for i in range(1, 6)]
        summary = cost_summary(runs)
        self.assertEqual(summary["tokens"], 150)
        self.assertEqual(summary["tool_calls"], 15)
        self.assertEqual(summary["p50_ms"], 300.0)
        self.assertEqual(summary["p95_ms"], 500.0)  # same rank rule as evaluator._percentile
        self.assertAlmostEqual(summary["error_rate"], 0.2)

    def test_evidence_validity_ignores_runs_without_a_verdict(self):
        runs = [
            make_run(make_case("dev-001"), "gestuetzt", evidence_valid=True),
            make_run(make_case("dev-002"), "widersprochen", evidence_valid=False),
            make_run(make_case("dev-003"), "gestuetzt", evidence_valid=True),
            make_run(make_case("dev-004"), "quelle_fehlt", evidence_valid=None),
            make_run(make_case("dev-005"), "rueckfrage", evidence_valid=False),  # no verdict: ignored
            make_run(make_case("dev-006"), "nicht_belegt", evidence_valid=None),  # verdict without a check: ignored
        ]
        self.assertAlmostEqual(evidence_validity(runs), 2 / 3)
        self.assertIsNone(evidence_validity(runs[3:5]))


if __name__ == "__main__":
    unittest.main()
