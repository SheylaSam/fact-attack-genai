"""Evaluate campaign result folders of the agent evaluation (offline, no request, no model).

Usage (from track_2a/):
  python scripts/evaluate_agent_runs.py --runs DIR [DIR ...] --out PREFIX [--freeze FILE]

Each DIR is a campaign folder of `run_agent_eval.py` (`campaign.json` and `cases.jsonl`, plus `journals/`). The report is
written to `PREFIX.md` (German tables) and `PREFIX.json`; an existing file is never overwritten.

* Arms b0, b1, b2: all metrics of `src/agent/eval_metrics.py` per campaign (strict F1, answered F1 and coverage, wrong
  judgements against abstentions, source finding, evidence validity, cost), the requests per run counted from the
  journals, the novelty groups and the special-case table. Records without a result (`nicht_gewertet`: B0 has none for the
  case; `nicht_gelaufen`: cap or stop) are left out of the scores and counted. For the cases that did not run the strict F1
  is also shown with them counted as abstentions. B0 finds the source by oracle: its source finding is marked as such.
* Arms regression-b0 and regression-agent: the pair (one folder each) goes through `regression.compare_paired`; every
  changed case is listed with its reason and the verdict (at most one case net worse).
* The cases are rebuilt without reading the test split unless the campaign is a test campaign: a dev campaign reads
  `load_testset("dev")`; a campaign whose records carry their case fields reads nothing; otherwise `campaign.json` (or,
  for older folders, `summary.json`) must say `split` and a test campaign additionally needs `--freeze` with that arm's
  recorded test run in exactly that folder. No split recorded: refused, never guessed.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.agent import eval_metrics, eval_runner
from src.agent.eval_metrics import CaseRun
from src.agent.regression import (MAX_NET_WORSE, REGRESSION_ARMS, CaseOutcome, compare_paired, outcome_from_record)
from src.agent.testset import FIELD_NAMES, EvalCase, load_testset

EXIT_OK, EXIT_REFUSED = 0, 1
ORACLE_NOTE = "Orakel (nicht aussagekräftig)"
NO_RESULT = (eval_runner.NOT_SCORED, eval_runner.NOT_RUN)
NO_NOVELTY = "keine"


class EvaluationError(Exception):
    """A folder or call the evaluation cannot use; the message is German and nothing was written."""


# --- reading a campaign -----------------------------------------------------------------------------------------------

def _read_json(path: Path, required: bool = True) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise EvaluationError(f"{path.parent}: {path.name} fehlt, das ist kein Ergebnisordner des Runners.") from None
        return None
    except (OSError, ValueError):
        raise EvaluationError(f"{path}: nicht lesbar oder kein gültiges JSON.") from None
    if not isinstance(data, dict):
        raise EvaluationError(f"{path}: ein JSON-Objekt wird erwartet.")
    return data


class Campaign:
    """One result folder: its metadata and records."""

    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)
        self.meta = _read_json(self.dir / eval_runner.CAMPAIGN_NAME)
        self.summary = _read_json(self.dir / eval_runner.SUMMARY_NAME, required=False) or {}
        self.arm = self.meta.get("arm")
        if self.arm not in eval_runner.ARMS:
            raise EvaluationError(f"{self.dir}: unbekannter Arm {self.arm!r}.")
        if not (self.dir / eval_runner.CASES_NAME).exists():
            raise EvaluationError(f"{self.dir}: {eval_runner.CASES_NAME} fehlt.")
        try:
            self.records = eval_runner.read_records(self.dir)  # an unterminated last line is no record
        except ValueError as exc:
            raise EvaluationError(str(exc)) from None
        ids = [record.get("id") for record in self.records]
        if len(set(ids)) != len(ids):
            raise EvaluationError(f"{self.dir}: Fälle kommen in {eval_runner.CASES_NAME} doppelt vor.")
        if any(record.get("arm") != self.arm for record in self.records):
            raise EvaluationError(f"{self.dir}: Ergebnisse eines anderen Arms als {self.arm}.")
        self.total = int(self.meta.get("cases", len(self.records)))

    @property
    def split(self) -> Optional[str]:
        return self.meta.get("split") or self.summary.get("split")

    @property
    def mock(self) -> Optional[bool]:
        return self.summary.get("mock")

    def requests_by_case(self) -> Dict[str, int]:
        """Requests sent per case: the journal lines (what was really sent, repeated checks included); the records'
        `requests` only if the folder has no journals."""
        journals = self.dir / eval_runner.JOURNAL_DIR
        if not journals.is_dir():
            return {record["id"]: int(record.get("requests", 0)) for record in self.records}
        counts: Dict[str, int] = {}
        for path in sorted(journals.glob("*.jsonl")):
            case_id = re.sub(r"(\.attempt\d+)?\.requests\.jsonl$", "", path.name)
            lines = sum(1 for line in path.read_text(encoding="utf-8").split("\n") if line.strip())
            counts[case_id] = counts.get(case_id, 0) + lines
        return counts


def _stored_case(record: dict) -> EvalCase:
    raw = record["case"]
    if not isinstance(raw, dict) or set(raw) != set(FIELD_NAMES):
        raise EvaluationError(f"Fall {record.get('id')}: die gespeicherten Falldaten sind unvollständig.")
    return EvalCase(**{**raw, "acceptable": tuple(raw["acceptable"]), "ambiguous_with": tuple(raw["ambiguous_with"])})


def _check_test_gate(campaign: Campaign, freeze_path: Optional[Path]) -> None:
    """The test split may only be read for a test campaign whose arm has its test run recorded in this very folder."""
    if freeze_path is None:
        raise EvaluationError(f"{campaign.dir}: Das ist ein Testlauf. Auswerten braucht --freeze (die Freeze-Datei, in "
                              "der der Testlauf dieses Arms steht); der Testteil wird sonst nicht gelesen.")
    try:
        freeze = eval_runner._read_freeze(freeze_path)
        recorded = eval_runner._test_run(Path(freeze_path), freeze, campaign.arm)
    except eval_runner.FreezeError as exc:
        raise EvaluationError(str(exc)) from None
    if recorded is None or recorded.get("out_dir") != eval_runner._out_key(campaign.dir):
        raise EvaluationError(f"{campaign.dir}: Die Freeze-Datei {freeze_path} hat keinen Testlauf von Arm "
                              f"{campaign.arm} in diesem Ordner; der Testteil wird nicht gelesen.")


def _split_cases(campaign: Campaign, freeze_path: Optional[Path]) -> List[EvalCase]:
    """All cases of the campaign's split, in campaign order; the test split only through the gate."""
    split = campaign.split
    if split not in ("dev", "test"):
        raise EvaluationError(f"{campaign.dir}: Der Split steht weder in {eval_runner.CAMPAIGN_NAME} noch in "
                              f"{eval_runner.SUMMARY_NAME} (gefunden: {split!r}); er wird nicht geraten.")
    if split == "test":
        _check_test_gate(campaign, freeze_path)
        cases = load_testset("test", cases_path=eval_runner.CASES_PATH, manifest_path=eval_runner.MANIFEST_PATH,
                             allow_test=True)
    else:
        cases = load_testset("dev", cases_path=eval_runner.CASES_PATH, manifest_path=eval_runner.MANIFEST_PATH)
    if eval_runner._ids_sha([case.id for case in cases]) != campaign.meta.get("case_ids_sha256"):
        raise EvaluationError(f"{campaign.dir}: Die Fälle des Laufs sind nicht die Fälle des Splits {split} "
                              "(Fall-IDs weichen ab); Auswertung abgelehnt.")
    return cases


def _runs_of(campaign: Campaign, freeze_path: Optional[Path]) -> Tuple[List[CaseRun], List[CaseRun], int]:
    """(runs from the records, runs for the cases that did not run if their cases are known, number not run)."""
    records = campaign.records
    if records and all("case" in record for record in records):  # the case fields are stored: nothing else is read
        runs = [eval_runner._from_record(_stored_case(record), record) for record in records]
        return runs, [], max(campaign.total - len(records), 0)
    cases = _split_cases(campaign, freeze_path)
    by_id = {record["id"]: record for record in records}
    if unknown := sorted(set(by_id) - {case.id for case in cases}):
        raise EvaluationError(f"{campaign.dir}: Ergebnisse zu unbekannten Fällen: {', '.join(unknown[:5])}.")
    runs = [eval_runner._from_record(case, by_id[case.id]) for case in cases if case.id in by_id]
    not_run = [eval_runner._plain_run(case, campaign.arm, eval_runner.NOT_RUN, "nicht gelaufen")
               for case in cases if case.id not in by_id]
    return runs, not_run, len(not_run)


# --- metrics of one campaign ------------------------------------------------------------------------------------------

def _small_metrics(runs: Sequence[CaseRun]) -> Dict[str, Any]:
    answerable = eval_metrics.answerable(runs)
    f1_answered, coverage = eval_metrics.macro_f1_answered(runs) if answerable else (None, None)
    return {"cases": len(runs), "answerable": len(answerable),
            "f1_strict": eval_metrics.macro_f1_strict(runs) if answerable else None,
            "f1_answered": f1_answered, "coverage": coverage}


def _requests(campaign: Campaign, finished: Sequence[CaseRun]) -> Dict[str, Any]:
    by_case = campaign.requests_by_case()
    own = [by_case.get(run.case.id, 0) for run in finished]
    return {"total": sum(by_case.values()), "mean_per_run": sum(own) / len(own) if own else None,
            "max_per_run": max(own) if own else None, "by_case": dict(sorted(by_case.items())),
            "source": "journals" if (campaign.dir / eval_runner.JOURNAL_DIR).is_dir() else "records"}


def evaluate_campaign(campaign: Campaign, freeze_path: Optional[Path]) -> Dict[str, Any]:
    """The report of a b0/b1/b2 campaign."""
    runs, not_run_runs, not_run = _runs_of(campaign, freeze_path)
    not_scored = sum(1 for run in runs if run.outcome == eval_runner.NOT_SCORED)
    finished = [run for run in runs if run.outcome not in NO_RESULT]
    answered = eval_metrics.macro_f1_answered(finished)
    errors = eval_metrics.errors_vs_abstentions(finished)
    cost = eval_metrics.cost_summary(finished)
    source = {**eval_metrics.source_finding(finished)}
    if campaign.arm == "b0":
        source["note"] = ORACLE_NOTE  # B0 is given the gold booklet: finding the source says nothing about it
    metrics = {
        "f1_strict": eval_metrics.macro_f1_strict(finished),
        "f1_answered": answered[0], "coverage": answered[1],
        "wrong_judgements": errors["wrong_judgements"], "abstentions": errors["abstentions"],
        # two different rates: wrong judgements among the answered, and runs with an error among all runs
        "wrong_judgement_rate_answered": errors["error_rate"],
        "technical_error_rate_all_runs": cost["error_rate"],
        "technical_outcomes": sum(1 for run in finished if run.outcome == "technischer_fehler"),
        "evidence_validity": eval_metrics.evidence_validity(finished),
        "source_finding": source,
        "cost": {"tokens": cost["tokens"], "p50_ms": cost["p50_ms"], "p95_ms": cost["p95_ms"],
                 "tool_calls": cost["tool_calls"]},
    }
    novelty: Optional[Dict[str, Any]] = None
    if any(run.case.novelty is not None for run in finished):
        groups: Dict[str, List[CaseRun]] = {}
        for run in finished:
            groups.setdefault(run.case.novelty or NO_NOVELTY, []).append(run)
        novelty = {name: _small_metrics(group) for name, group in sorted(groups.items())}
    outcomes = Counter(run.outcome for run in runs)
    if not_run:
        outcomes[eval_runner.NOT_RUN] = not_run
    return {
        "dir": str(campaign.dir), "arm": campaign.arm, "split": campaign.split, "mock": campaign.mock,
        "cases_total": campaign.total, "finished": len(finished), "not_run": not_run, "not_scored": not_scored,
        "outcomes": dict(sorted(outcomes.items())), "metrics": metrics,
        "f1_strict_with_not_run": (eval_metrics.macro_f1_strict(finished + not_run_runs)
                                   if not_run_runs else None),
        "requests": _requests(campaign, finished), "novelty": novelty,
        "special_cases": [{**row, "acceptable": list(row["acceptable"])}
                          for row in eval_metrics.special_case_table(finished)],
        "errors": [{"id": run.case.id, "outcome": run.outcome, "error": run.error}
                   for run in finished if run.error is not None],
    }


# --- regression pair --------------------------------------------------------------------------------------------------

def _outcomes(campaign: Campaign) -> List[CaseOutcome]:
    try:
        return [outcome_from_record(record) for record in campaign.records]
    except (KeyError, TypeError):
        raise EvaluationError(f"{campaign.dir}: Ergebnisse der Regression sind unvollständig.") from None


def _arm_summary(campaign: Campaign, outcomes: Sequence[CaseOutcome]) -> Dict[str, Any]:
    return {"dir": str(campaign.dir), "cases_total": campaign.total, "finished": len(campaign.records),
            "not_run": max(campaign.total - len(campaign.records), 0), "mock": campaign.mock,
            "technical_errors": sum(1 for outcome in outcomes if outcome.outcome == "technischer_fehler"),
            "requests_total": sum(campaign.requests_by_case().values())}


def evaluate_regression(campaigns: Sequence[Campaign]) -> Dict[str, Any]:
    by_arm: Dict[str, List[Campaign]] = {arm: [c for c in campaigns if c.arm == arm] for arm in REGRESSION_ARMS}
    for arm, found in by_arm.items():
        if len(found) != 1:
            raise EvaluationError(f"Die Regression braucht genau einen Ordner je Arm; für {arm} wurden "
                                  f"{len(found)} übergeben (beide Arme: {', '.join(REGRESSION_ARMS)}).")
    b0, agent = (by_arm[arm][0] for arm in REGRESSION_ARMS)
    if b0.meta.get("case_ids_sha256") != agent.meta.get("case_ids_sha256"):
        raise EvaluationError("Die beiden Regressionsläufe haben nicht dieselben Fälle.")
    left, right = _outcomes(b0), _outcomes(agent)
    labels = {outcome.id: outcome.label for outcome in left + right}
    order = list(dict.fromkeys(outcome.id for outcome in left + right))
    has = ({outcome.id: outcome for outcome in left}, {outcome.id: outcome for outcome in right})

    def side(arm: str, found: Dict[str, CaseOutcome]) -> List[CaseOutcome]:
        # a case the campaign did not run is no result: it is compared as such, never dropped
        return [found.get(case_id) or CaseOutcome(id=case_id, arm=arm, outcome=eval_runner.NOT_RUN,
                                                  label=labels[case_id], error="nicht gelaufen")
                for case_id in order]

    try:
        result = compare_paired(side(REGRESSION_ARMS[0], has[0]), side(REGRESSION_ARMS[1], has[1]))
    except ValueError as exc:
        raise EvaluationError(str(exc)) from None
    return {"cases": result.cases, "cases_total": b0.total, "b0_correct": result.b0_correct,
            "agent_correct": result.agent_correct, "better": result.better, "worse": result.worse,
            "net_worse": result.net_worse, "max_net_worse": MAX_NET_WORSE, "passes": result.passes,
            "changed": [{"id": change.id, "b0": change.b0, "agent": change.agent, "direction": change.direction,
                         "reason": change.reason} for change in result.changed],
            "not_covered": max(b0.total - result.cases, 0),
            "arms": {REGRESSION_ARMS[0]: _arm_summary(b0, left),
                     REGRESSION_ARMS[1]: _arm_summary(agent, right)}}


# --- markdown ---------------------------------------------------------------------------------------------------------

def _cell(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, float):
        return f"{value:.3f}"
    return " ".join(str(value).split()).replace("|", "\\|")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return lines + [""]


def _campaign_markdown(report: Dict[str, Any]) -> List[str]:
    metrics, source, requests = report["metrics"], report["metrics"]["source_finding"], report["requests"]
    title = f"## Arm {report['arm']}, Split {report['split'] or 'unbekannt'}" + (" (Mock)" if report["mock"] else "")
    lines = [title, "", f"Ordner: `{report['dir']}`", "",
             f"Fälle: {report['cases_total']} insgesamt, {report['finished']} ausgewertet, {report['not_run']} nicht "
             f"gelaufen, {report['not_scored']} nicht gewertet (`nicht_gewertet`).", ""]
    if report["not_run"] or report["not_scored"]:
        lines += ["Nicht gelaufene und nicht gewertete Fälle sind aus den Kennzahlen unten herausgenommen"
                  + (f"; Strenger Makro-F1, wenn die {report['not_run']} nicht gelaufenen Fälle als Enthaltung "
                     f"zählen: {_cell(report['f1_strict_with_not_run'])}." if report["not_run"] else "."), ""]
    lines += ["### Kennzahlen", ""]
    lines += _table(["Kennzahl", "Wert"], [
        ("Makro-F1 streng (Enthaltungen und technische Fehler zählen als falsch)", metrics["f1_strict"]),
        ("Makro-F1 nur über beantwortete Fälle", metrics["f1_answered"]),
        ("Abdeckung (beantwortet / beantwortbar)", metrics["coverage"]),
        ("Fehlurteile (beantwortete Fälle)", metrics["wrong_judgements"]),
        ("Enthaltungen (beantwortbare Fälle ohne Urteil)", metrics["abstentions"]),
        ("Fehlurteilquote (beantwortet)", metrics["wrong_judgement_rate_answered"]),
        ("Technische Fehlerquote (alle Läufe)", metrics["technical_error_rate_all_runs"]),
        ("Läufe mit Ausgang technischer_fehler", metrics["technical_outcomes"]),
        ("Beleggültigkeit (geprüfte Urteile)", metrics["evidence_validity"]),
    ])
    lines += ["### Quellenfindung", ""]
    if "note" in source:
        lines += [f"**{source['note']}**: B0 bekommt das Büchlein der richtigen Vorlage vorgegeben.", ""]
    lines += _table(["Zähler", "Anzahl"], [
        ("Richtige Vorlage", source["right_vote"]), ("Berechtigte Rückfragen", source["justified_questions"]),
        ("Unberechtigte Rückfragen", source["unjustified_questions"]),
        ("Falsche Sicherheit (Urteil auf falscher Vorlage)", source["false_confidence"])])
    cost = metrics["cost"]
    lines += ["### Kosten und Anfragen", ""]
    lines += _table(["Grösse", "Wert"], [
        ("Token", cost["tokens"]), ("Werkzeugaufrufe", cost["tool_calls"]), ("Latenz p50 (ms)", cost["p50_ms"]),
        ("Latenz p95 (ms)", cost["p95_ms"]),
        (f"Anfragen insgesamt (aus {'den Journalen' if requests['source'] == 'journals' else 'den Ergebnissen'})",
         requests["total"]),
        ("Anfragen je Lauf (Mittel)", requests["mean_per_run"]), ("Anfragen je Lauf (Höchstwert)",
                                                                  requests["max_per_run"])])
    lines += ["### Ausgänge", ""]
    lines += _table(["Ausgang", "Anzahl"], list(report["outcomes"].items()))
    if report["novelty"]:
        lines += ["### Nach Neuheit der Vorlage", ""]
        lines += _table(["Neuheit", "Fälle", "Beantwortbar", "Makro-F1 streng", "Makro-F1 beantwortet", "Abdeckung"],
                        [(name, group["cases"], group["answerable"], group["f1_strict"], group["f1_answered"],
                          group["coverage"]) for name, group in report["novelty"].items()])
    if report["special_cases"]:
        lines += ["### Spezialfälle", ""]
        lines += _table(["Fall", "Art", "Erwartet", "Akzeptabel", "Ergebnis", "In Ordnung"],
                        [(row["id"], row["kind"], row["expected"], ", ".join(row["acceptable"]), row["got"],
                          row["ok"]) for row in report["special_cases"]])
    if report["errors"]:
        lines += ["### Fälle mit Fehlermeldung", ""]
        lines += _table(["Fall", "Ausgang", "Meldung"], [(e["id"], e["outcome"], e["error"])
                                                           for e in report["errors"]])
    return lines


def _regression_markdown(report: Dict[str, Any]) -> List[str]:
    b0, agent = (report["arms"][arm] for arm in REGRESSION_ARMS)
    verdict = "bestanden" if report["passes"] else "nicht bestanden"
    lines = ["## Regression: B0 gegen Agent", "",
             f"Ordner: B0 `{b0['dir']}`, Agent `{agent['dir']}`", "",
             f"Verglichen: {report['cases']} von {report['cases_total']} Fällen.", ""]
    lines += _table(["", "B0", "Agent"], [
        ("Richtig", report["b0_correct"], report["agent_correct"]),
        ("Nicht gelaufen", b0["not_run"], agent["not_run"]),
        ("Technische Fehler", b0["technical_errors"], agent["technical_errors"]),
        ("Anfragen", b0["requests_total"], agent["requests_total"])])
    lines += [f"Besser: {report['better']}, schlechter: {report['worse']}, netto schlechter: {report['net_worse']} "
              f"(erlaubt: höchstens {report['max_net_worse']}).", "", f"Ergebnis: **{verdict}**.", ""]
    if report["not_covered"]:
        lines += [f"Hinweis: {report['not_covered']} Fälle stehen in keinem der beiden Läufe.", ""]
    if report["changed"]:
        lines += ["### Geänderte Fälle", ""]
        lines += _table(["Fall", "B0", "Agent", "Richtung", "Grund"],
                        [(c["id"], c["b0"], c["agent"], c["direction"], c["reason"]) for c in report["changed"]])
    else:
        lines += ["Kein Fall hat ein anderes Ergebnis.", ""]
    return lines


def render_markdown(data: Dict[str, Any]) -> str:
    lines = ["# Auswertung der Agentenläufe", "", f"Erstellt: {data['generated_at']}", ""]
    for report in data["campaigns"]:
        lines += _campaign_markdown(report)
    if data["regression"]:
        lines += _regression_markdown(data["regression"])
    return "\n".join(lines).rstrip("\n") + "\n"


# --- output -----------------------------------------------------------------------------------------------------------

def _redacted(value: Any) -> Any:
    """`value` with the API key and the key file's path replaced in every string (the records are redacted already)."""
    if isinstance(value, str):
        return eval_runner.redact(value)
    if isinstance(value, dict):
        return {_redacted(key): _redacted(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(item) for item in value]
    return value


def output_paths(prefix: Path) -> Tuple[Path, Path]:
    return Path(f"{prefix}.md"), Path(f"{prefix}.json")


def _check_free(paths: Sequence[Path]) -> None:
    for path in paths:
        if path.exists():
            raise EvaluationError(f"{path} existiert schon. Ein Bericht wird nie überschrieben.")


def _write_new(paths: Sequence[Path], texts: Sequence[str]) -> None:
    created: List[Path] = []
    try:
        for path, text in zip(paths, texts):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                created.append(path)
                stream.write(text)
    except FileExistsError as exists:
        for path in created:  # only what this call created; nothing half-written stays
            path.unlink(missing_ok=True)
        raise EvaluationError(f"{exists.filename} existiert schon. Ein Bericht wird nie überschrieben.") from None


def build_report(run_dirs: Sequence[Path], freeze_path: Optional[Path]) -> Dict[str, Any]:
    campaigns = [Campaign(run_dir) for run_dir in run_dirs]
    if len({campaign.dir.resolve() for campaign in campaigns}) != len(campaigns):
        raise EvaluationError("Ein Ergebnisordner kommt mehrfach vor.")
    regression_runs = [c for c in campaigns if c.arm in REGRESSION_ARMS]
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "campaigns": [evaluate_campaign(c, freeze_path) for c in campaigns if c.arm not in REGRESSION_ARMS],
            "regression": evaluate_regression(regression_runs) if regression_runs else None}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", required=True, nargs="+", type=Path, metavar="DIR",
                        help="Ergebnisordner von run_agent_eval.py (campaign.json und cases.jsonl).")
    parser.add_argument("--out", required=True, type=Path, metavar="PREFIX",
                        help="Schreibt PREFIX.md und PREFIX.json (bestehende Dateien werden nie überschrieben).")
    parser.add_argument("--freeze", type=Path, metavar="FILE",
                        help="Freeze-Datei; nur für Testläufe nötig, deren Falldaten nicht in den Ergebnissen stehen.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    paths = output_paths(args.out)
    try:
        _check_free(paths)
        data = _redacted(build_report(args.runs, args.freeze))
        markdown = eval_runner.redact(render_markdown(data))
        _write_new(paths, [markdown, json.dumps(data, ensure_ascii=False, indent=2) + "\n"])
    except (EvaluationError, eval_runner.FreezeError, ValueError) as refused:
        print(f"Keine Auswertung: {eval_runner.redact(refused)}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"Auswertung geschrieben: {paths[0]} und {paths[1]}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
