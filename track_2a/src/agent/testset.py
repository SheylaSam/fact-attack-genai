"""The agent testset's cases: schema, reader, hash and the deterministic validator against the booklet PDFs.

`cases.jsonl` (in `TESTSET_DIR`) has one JSON object per line: every field of `EvalCase` plus `"claim_language": "de"`.
The plan below (which vote belongs to which split) and the counts are fixed in docs/genai/TESTSET.md.

* A case is split by vote ("Vorlage", `vote_id` of the `SourceIndex`), never by claim: paraphrases of one statement can
  not land on both sides.
* `validate` checks each case without any model: the vote exists and belongs to its split, the quote stands verbatim in
  the booklet. "Verbatim" is the agent's own rule (`graph.page_texts`): `official_normalize(quote)` is part of the
  normalized text of the page or of one of its text blocks.
* A booklet that is not local (fresh clone) is reported per case with the command that loads it, never a crash.
* `lock` seals the finished set: it needs Sheyla's blind review of every case (`label_review.jsonl`, one line per
  judgement) and writes `manifest.json` with the hashes. `load_testset` is the only gate to the cases afterwards: dev is
  free, test stays closed (stage 5) and nothing is returned if `cases.jsonl` no longer matches the manifest.
"""
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import typing
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from src import config
from src.agent import settings
from src.agent.graph import Outcome, page_texts
from src.agent.source_index import VoteRef, _booklet_votes
from src.booklet_catalog import available_sources
from src.part_context import claim_part
from src.text_utils import guess_language, official_normalize

OUTCOMES: Tuple[str, ...] = typing.get_args(Outcome)
VERDICTS = ("gestuetzt", "widersprochen", "nicht_belegt")  # what a booklet can say about a claim; = LABEL_OUTCOMES
KINDS = ("mehrdeutig", "falscher_sprecher", "zusammengesetzt", "ohne_abstimmung", "api_ausfall", "prompt_injection")
FAULTS = ("timeout", "http_500")
SPLITS = ("dev", "test")
GROUPS = ("haupt", "spezial")  # the 90 main cases and the 20 special cases

TESTSET_DIR = config.DATA_DIR / "genai" / "testset"
CASES_PATH = TESTSET_DIR / "cases.jsonl"
REVIEW_PATH = TESTSET_DIR / "label_review.jsonl"
MANIFEST_PATH = TESTSET_DIR / "manifest.json"

QUOTE_CHARS = (20, 400)  # length of `official_normalize(quote)`
NOVELTY_BY_DATE = {"2026-11-29": "ungesehen", "2026-09-27": "beobachtet"}  # every other booklet is "bekannt"
FETCH_COMMAND = "scripts/fetch_catalog_booklets.py"

# The vote plan: every vote belongs to exactly one split. Dev main cases: 15 votes x 2, novelty "bekannt".
DEV_VOTES: FrozenSet[str] = frozenset({
    "2020-02-09_de#1",  # Bezahlbare Wohnungen
    "2020-09-27_de#2",  # Jagdgesetz
    "2020-11-29_de#1",  # Konzernverantwortung
    "2021-06-13_de#3",  # Covid-19-Gesetz
    "2021-09-26_de#2",  # Ehe für alle
    "2021-11-28_de#1",  # Pflegeinitiative
    "2022-02-13_de#2",  # Tabakwerbung
    "2022-05-15_de#1",  # Filmgesetz
    "2022-09-25_de#1",  # Massentierhaltung
    "2023-06-18_de#1",  # OECD-Mindeststeuer
    "2024-06-09_de#2",  # Kostenbremse
    "2024-09-22_de#1",  # Biodiversität
    "2025-02-09_de#1",  # Umweltverantwortung
    "2025-11-30_de#1",  # Service citoyen
    "2026-06-14_de#2",  # Zivildienst
})
# Test main cases: 6 each for the votes of the two newest booklets (2 per class), 2 each for the others
_TEST_SIX = (
    "2026-11-29_de#1",  # AHV-Zusatzfinanzierung (ungesehen)
    "2026-11-29_de#2",  # Feuerwerk
    "2026-11-29_de#3",  # Faire Bundessteuern für Ehepaare
    "2026-11-29_de#4",  # Kriegsmaterialgesetz
    "2026-09-27_de#1",  # Neutralitätsinitiative (beobachtet)
    "2026-09-27_de#2",  # Ernährungsinitiative
)
_TEST_TWO = (
    "2020-09-27_de#5",  # Kampfflugzeuge (bekannt)
    "2021-03-07_de#1",  # Verhüllungsverbot
    "2021-06-13_de#4",  # CO2-Gesetz
    "2021-11-28_de#2",  # Justiz-Initiative
    "2022-02-13_de#4",  # Medienpaket
    "2022-05-15_de#2",  # Transplantationsgesetz
    "2023-06-18_de#2",  # Klimaschutzgesetz
    "2024-03-03_de#2",  # Renteninitiative
    "2024-06-09_de#4",  # Stromversorgung
    "2024-11-24_de#1",  # Nationalstrassen
    "2025-11-30_de#2",  # Initiative für eine Zukunft
    "2026-03-08_de#2",  # SRG-Initiative
)
TEST_VOTES: FrozenSet[str] = frozenset(_TEST_SIX + _TEST_TWO)
# Reserved for special cases only (the ambiguous pairs and the wrong-speaker and injection material)
DEV_SPECIAL_VOTES: FrozenSet[str] = frozenset({
    "2024-11-24_de#2",  # Mietrecht Untermiete
    "2024-11-24_de#3",  # Mietrecht Eigenbedarf
    "2024-06-09_de#1",  # Prämien-Entlastung
})
TEST_SPECIAL_VOTES: FrozenSet[str] = frozenset({
    "2021-03-07_de#2",  # E-ID 2021
    "2025-09-28_de#2",  # E-ID 2025
    "2022-09-25_de#2",  # AHV 21
    "2024-03-03_de#1",  # 13. AHV-Rente
    "2026-03-08_de#4",  # Individualbesteuerung
    "2020-11-29_de#2",  # Kriegsgeschäfte
})
HAUPT_PLAN: Dict[str, int] = {**dict.fromkeys(sorted(DEV_VOTES), 2), **dict.fromkeys(_TEST_SIX, 6),
                              **dict.fromkeys(_TEST_TWO, 2)}  # vote_id -> number of main cases
KIND_COUNTS: Dict[str, Dict[str, int]] = {
    "dev": {"mehrdeutig": 2, "falscher_sprecher": 1, "zusammengesetzt": 1, "ohne_abstimmung": 1, "api_ausfall": 1,
            "prompt_injection": 1},
    "test": {"mehrdeutig": 4, "falscher_sprecher": 2, "zusammengesetzt": 2, "ohne_abstimmung": 2, "api_ausfall": 1,
             "prompt_injection": 2},
}
CLASS_COUNTS = {"dev": 10, "test": 20}  # main cases per verdict and split
CLASS_PER_SIX_VOTE = 2

_HAUPT_VOTES = {"dev": DEV_VOTES, "test": TEST_VOTES}
_SPECIAL_VOTES = {"dev": DEV_SPECIAL_VOTES, "test": TEST_SPECIAL_VOTES}
_SPLIT_VOTES = {split: _HAUPT_VOTES[split] | _SPECIAL_VOTES[split] for split in SPLITS}
_ID_PREFIX = {("dev", "haupt"): ("dev", 3), ("test", "haupt"): ("test", 3), ("dev", "spezial"): ("sdev", 2),
              ("test", "spezial"): ("stest", 2)}
# what each special kind expects from the agent (a tuple: any of these)
_KIND_EXPECTED = {
    "mehrdeutig": ("rueckfrage",), "zusammengesetzt": ("rueckfrage",), "ohne_abstimmung": ("quelle_fehlt",),
    "api_ausfall": ("technischer_fehler",), "falscher_sprecher": ("widersprochen", "nicht_belegt"),
    "prompt_injection": VERDICTS}


def group_count(split: str, group: str) -> int:
    """How many cases the plan has in this split and group (30 / 60 main, 7 / 13 special)."""
    if group == "haupt":
        return sum(count for vote_id, count in HAUPT_PLAN.items() if vote_id in _HAUPT_VOTES[split])
    return sum(KIND_COUNTS[split].values())


def novelty_for(vote_id: str) -> str:
    """The novelty of a vote by the date of its booklet: 29 Nov 2026 unseen, 27 Sep 2026 observed, else known."""
    return NOVELTY_BY_DATE.get(vote_id.split("_", 1)[0], "bekannt")


@dataclass(frozen=True)
class EvalCase:  # not "TestCase": pytest would collect it
    id: str
    split: str
    group: str
    kind: Optional[str]  # None exactly for "haupt"
    novelty: Optional[str]  # None exactly for "ohne_abstimmung"
    claim: str
    expected: str  # one of OUTCOMES
    acceptable: Tuple[str, ...]  # special cases only: outcomes that are not wrong (includes `expected`)
    vote_id: Optional[str]  # the vote the evidence is from; None exactly for "ohne_abstimmung"
    ambiguous_with: Tuple[str, ...]  # "mehrdeutig": the other votes the claim could mean
    after_answer: Optional[str]  # "mehrdeutig": the verdict once the question is answered with `vote_id`
    page: Optional[int]
    quote: Optional[str]  # copied verbatim from the page text (`check_testset.py --show`)
    fault: Optional[str]  # "api_ausfall": the fault to inject
    note: str


_LIST_FIELDS = ("acceptable", "ambiguous_with")
_STRING_FIELDS = ("id", "split", "group", "claim", "expected", "note")
_OPTIONAL_STRING_FIELDS = ("kind", "novelty", "vote_id", "after_answer", "quote", "fault")
FIELD_NAMES = tuple(field.name for field in fields(EvalCase))
LANGUAGE_FIELD = "claim_language"  # on every line of cases.jsonl, not part of the case


def _parse_line(line: str, where: str) -> EvalCase:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{where}: kein gültiges JSON ({exc.msg})") from None
    if not isinstance(row, dict):
        raise ValueError(f"{where}: ein JSON-Objekt wird erwartet")
    allowed = set(FIELD_NAMES) | {LANGUAGE_FIELD}
    if unknown := sorted(set(row) - allowed):
        raise ValueError(f"{where}: unbekannte Felder: {', '.join(unknown)}")
    if missing := [name for name in (*FIELD_NAMES, LANGUAGE_FIELD) if name not in row]:
        raise ValueError(f"{where}: Felder fehlen: {', '.join(missing)}")
    if row[LANGUAGE_FIELD] != "de":
        raise ValueError(f"{where}: {LANGUAGE_FIELD} muss \"de\" sein, nicht {row[LANGUAGE_FIELD]!r}")
    for name in _STRING_FIELDS:
        if not isinstance(row[name], str):
            raise ValueError(f"{where}: {name} muss ein Text sein")
    for name in _OPTIONAL_STRING_FIELDS:
        if row[name] is not None and not isinstance(row[name], str):
            raise ValueError(f"{where}: {name} muss ein Text oder null sein")
    if row["page"] is not None and (type(row["page"]) is not int):
        raise ValueError(f"{where}: page muss eine ganze Zahl oder null sein")
    for name in _LIST_FIELDS:
        if not isinstance(row[name], list) or not all(isinstance(item, str) for item in row[name]):
            raise ValueError(f"{where}: {name} muss eine Liste von Texten sein")
    row.pop(LANGUAGE_FIELD)
    return EvalCase(**{**row, **{name: tuple(row[name]) for name in _LIST_FIELDS}})


def _cases_from_text(text: str, name: str) -> List[EvalCase]:
    cases = []
    for number, line in enumerate(text.split("\n"), 1):  # not splitlines: U+2028 is text
        if line.strip():
            cases.append(_parse_line(line, f"{name}, Zeile {number}"))
    return cases


def read_cases(path: Path = CASES_PATH) -> List[EvalCase]:
    """The cases of a cases.jsonl in file order; ValueError (German) names the line of a bad or missing field."""
    path = Path(path)
    return _cases_from_text(path.read_text(encoding="utf-8"), path.name)


def case_sha(case: EvalCase) -> str:
    """SHA-256 of the case's fields (without `claim_language`); a reviewed verdict is bound to it."""
    text = json.dumps(asdict(case), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class VoteCatalog:
    """The German votes of the catalog's booklets that are available locally, and their PDF text.

    `votes`: vote_id -> VoteRef; `missing`: source_id -> PDF file name of every German catalog booklet that is not a
    local PDF (a fresh clone has none; `scripts/fetch_catalog_booklets.py` loads them).
    """

    def __init__(self, votes: Dict[str, VoteRef], missing: Dict[str, str],
                 parts: Dict[str, Dict[str, Tuple[int, ...]]], read_page: Callable[[VoteRef, int], List[str]]):
        self.votes = votes
        self.missing = missing
        self._parts = parts
        self._read_page = read_page

    def parts(self, vote_id: str) -> Dict[str, Tuple[int, ...]]:
        """Part name -> pages of the vote, in the order of the booklet's contents page; {} for a whole-booklet vote."""
        return dict(self._parts.get(vote_id, {}))

    def page_texts(self, vote_id: str, page: int) -> List[str]:
        """The normalized text of the page and of each of its blocks; [] = the page has no readable text."""
        return self._read_page(self.votes[vote_id], page)


def load_vote_catalog(sources: Dict[str, dict], booklets_dir: Path, engine) -> VoteCatalog:
    """The votes of the German booklets in `sources` (the output of `load_catalog`), read with `engine`.

    `engine` is only used to parse booklets and read pages (`booklet_layout`, `source_page`): build it with a mock
    Apertus client. A booklet that is not a local PDF goes to `missing`, nothing is parsed or downloaded for it.
    """
    booklets_dir = Path(booklets_dir)
    german = {name: entry for name, entry in sorted(sources.items()) if entry["language"] == "de"}
    present = available_sources(booklets_dir, german)
    votes: Dict[str, VoteRef] = {}
    parts: Dict[str, Dict[str, Tuple[int, ...]]] = {}
    missing: Dict[str, str] = {}
    for name, entry in german.items():
        source_id = Path(name).stem
        if name not in present:
            missing[source_id] = name
            continue
        paragraphs, layout = engine.booklet_layout(booklets_dir / name)
        votes.update((ref.vote_id, ref) for ref in _booklet_votes(source_id, entry, paragraphs, layout))
        for k, vote in enumerate(layout or [], 1):  # the k of the vote id; a whole-booklet vote (#0) has no parts
            parts[f"{source_id}#{k}"] = {part: tuple(int(page) for page in pages if page >= 1)
                                         for part, pages in vote["parts"].items()}

    def read_page(ref: VoteRef, page: int) -> List[str]:
        return page_texts(engine, booklets_dir / f"{ref.source_id}.pdf", page)
    return VoteCatalog(votes, missing, parts, read_page)


# --- validation -------------------------------------------------------------------------------------------
# The helpers return messages without the case id; `validate` puts "<id>: " in front.

def _is_vote(vote_id: str, votes) -> bool:
    return vote_id in votes.votes or vote_id.split("#", 1)[0] in votes.missing


def _lookup_problem(vote_id: str, votes) -> Optional[str]:
    if vote_id in votes.votes:
        return None
    source_id = vote_id.split("#", 1)[0]
    if source_id in votes.missing:
        return f"Büchlein fehlt ({votes.missing[source_id]}): {FETCH_COMMAND} ausführen"
    return f"unbekannte Vorlage {vote_id}"


def _split_problem(split: str, vote_id: str) -> Optional[str]:
    if vote_id in _SPLIT_VOTES[split]:
        return None
    other = "test" if split == "dev" else "dev"
    if vote_id in _SPLIT_VOTES[other]:
        return f"Vorlage {vote_id} gehört nicht zu {split}, sondern zu {other}"
    return f"Vorlage {vote_id} steht nicht im Vorlagenplan"


def _field_problems(case: EvalCase) -> List[str]:
    found = []
    if case.split not in SPLITS:
        found.append(f"split {case.split!r} ist weder dev noch test")
    if case.group not in GROUPS:
        found.append(f"group {case.group!r} ist weder haupt noch spezial")
    if (case.split, case.group) in _ID_PREFIX:
        prefix, width = _ID_PREFIX[(case.split, case.group)]
        last = group_count(case.split, case.group)
        match = re.fullmatch(rf"{prefix}-(\d{{{width}}})", case.id)
        if not match or not 1 <= int(match.group(1)) <= last:
            found.append(f"ID passt nicht zu split {case.split} und group {case.group} "
                         f"(erwartet {prefix}-{1:0{width}d} bis {prefix}-{last:0{width}d})")
    if case.group == "haupt" and case.kind is not None:
        found.append(f"kind muss bei haupt None sein, nicht {case.kind!r}")
    if case.group == "spezial" and case.kind not in KINDS:
        found.append(f"kind {case.kind!r} ist keine Art der Spezialfälle ({', '.join(KINDS)})")
    return found


def _claim_problems(case: EvalCase) -> List[str]:
    if not case.claim.strip():
        return ["Behauptung ist leer"]
    found = []
    if len(case.claim) > settings.MAX_CLAIM_CHARS:
        found.append(f"Behauptung hat {len(case.claim)} Zeichen, erlaubt sind höchstens {settings.MAX_CLAIM_CHARS}")
    language = guess_language(case.claim)
    if language != "de":
        found.append(f"Behauptung ist nicht Deutsch (erkannt: {language})")
    part = claim_part(case.claim)
    if part is not None and case.kind != "falscher_sprecher":  # only a wrong-speaker case names a speaker
        found.append(f"Behauptung beginnt mit einem Abschnittssignal ({part}); "
                     "das ist nur bei falscher_sprecher erlaubt")
    return found


def _quote_problems(case: EvalCase, votes) -> List[str]:
    """Page and quote of a case whose vote is known and whose booklet is local."""
    ref = votes.votes[case.vote_id]
    found = []
    if case.page is None:
        found.append("page fehlt")
    elif case.page not in ref.pages:
        found.append(f"S. {case.page} gehört nicht zu {case.vote_id} (Seiten {', '.join(map(str, ref.pages))})")
    if case.quote is None or not case.quote.strip():
        found.append("quote fehlt")
    if found:
        return found
    quote = official_normalize(case.quote)
    low, high = QUOTE_CHARS
    if not low <= len(quote) <= high:
        return [f"Zitat hat {len(quote)} Zeichen, erlaubt sind {low} bis {high} (nach official_normalize)"]
    texts = votes.page_texts(case.vote_id, case.page)
    if not texts:
        return [f"S. {case.page} von {case.vote_id} hat keinen lesbaren Text: das Zitat lässt sich nicht prüfen"]
    if not any(quote in text for text in texts):
        return [f"Zitat steht nicht wörtlich auf S. {case.page} von {case.vote_id} "
                "(weder im Seitentext noch in einem Textblock, geprüft mit official_normalize)"]
    return []


def _plan_problem(case: EvalCase) -> Optional[str]:
    """Why the case's vote is not allowed for its split and group, or None."""
    if case.split not in SPLITS or case.group not in GROUPS:  # reported with the fields
        return None
    if case.group == "spezial":
        return _split_problem(case.split, case.vote_id)
    if case.vote_id in _HAUPT_VOTES[case.split]:
        return None
    if case.vote_id in _SPECIAL_VOTES[case.split]:
        return f"Vorlage {case.vote_id} ist für Spezialfälle reserviert, nicht für Hauptfälle"
    return _split_problem(case.split, case.vote_id)


def _vote_problems(case: EvalCase, votes) -> List[str]:
    if case.kind == "ohne_abstimmung":
        return [] if case.vote_id is None else ["ohne_abstimmung hat keine Vorlage: vote_id muss None sein"]
    if case.vote_id is None:
        return ["vote_id fehlt (None gibt es nur bei ohne_abstimmung)"]
    found = []
    if lookup := _lookup_problem(case.vote_id, votes):
        found.append(lookup)
    if _is_vote(case.vote_id, votes):  # the id is a vote of the catalog, even if its booklet is not local
        if plan := _plan_problem(case):
            found.append(plan)
        if case.novelty != novelty_for(case.vote_id):
            found.append(f"novelty ist {case.novelty!r}, für {case.vote_id} gilt {novelty_for(case.vote_id)!r}")
    if case.vote_id in votes.votes:
        found += _quote_problems(case, votes)
    return found


def _acceptable_problems(case: EvalCase) -> List[str]:
    found = []
    if case.expected not in case.acceptable:
        found.append("acceptable muss expected enthalten")
    if unknown := [value for value in case.acceptable if value not in OUTCOMES]:
        found.append(f"acceptable enthält unbekannte Ergebnisse: {', '.join(unknown)}")
    return found


def _ambiguous_problems(case: EvalCase, votes) -> List[str]:
    if not case.ambiguous_with:
        return ["ambiguous_with darf bei mehrdeutig nicht leer sein"]
    found = []
    for other in dict.fromkeys(case.ambiguous_with):
        if lookup := _lookup_problem(other, votes):
            found.append(lookup)
        if not _is_vote(other, votes):
            continue
        if other == case.vote_id:
            found.append("ambiguous_with enthält die eigene vote_id")
        elif case.split in SPLITS and (problem := _split_problem(case.split, other)):
            found.append(f"ambiguous_with: {problem}")
    return found


def _kind_problems(case: EvalCase, votes) -> List[str]:
    kind = case.kind
    found = []
    if kind in _KIND_EXPECTED and case.expected not in _KIND_EXPECTED[kind]:
        found.append(f"Art {kind} verlangt expected {' oder '.join(_KIND_EXPECTED[kind])}, nicht {case.expected!r}")
    if kind == "mehrdeutig":
        if case.after_answer not in VERDICTS:
            found.append(f"after_answer {case.after_answer!r} muss bei mehrdeutig eines der Urteile "
                         f"{', '.join(VERDICTS)} sein")
        found += _ambiguous_problems(case, votes)
    elif kind == "ohne_abstimmung":
        if case.page is not None or case.quote is not None or case.novelty is not None:
            found.append("ohne_abstimmung hat keine Fundstelle: page, quote und novelty müssen None sein")
    elif kind == "api_ausfall" and case.fault not in FAULTS:
        found.append(f"fault {case.fault!r} muss bei api_ausfall {' oder '.join(FAULTS)} sein")
    return found


def _result_problems(case: EvalCase, votes) -> List[str]:
    found = []
    if case.group == "haupt":
        if case.expected not in VERDICTS:
            found.append(f"expected {case.expected!r} muss bei haupt eines der Urteile {', '.join(VERDICTS)} sein")
        if case.acceptable:
            found.append("acceptable muss bei haupt leer sein")
    elif case.group == "spezial":
        found += _acceptable_problems(case)
    found += _kind_problems(case, votes)
    if case.fault is not None and case.kind != "api_ausfall":
        found.append("fault gibt es nur bei api_ausfall")
    if case.after_answer is not None and case.kind != "mehrdeutig":
        found.append("after_answer gibt es nur bei mehrdeutig")
    if case.ambiguous_with and case.kind != "mehrdeutig":
        found.append("ambiguous_with gibt es nur bei mehrdeutig")
    if ((case.group == "spezial" or "nicht_belegt" in (case.expected, case.after_answer))
            and not case.note.strip()):
        found.append("note fehlt (bei Spezialfällen immer, sonst wenn das Urteil nicht_belegt ist)")
    return found


def _count_problems(cases: List[EvalCase]) -> List[str]:
    found = []
    for split in SPLITS:
        for group in GROUPS:
            have = sum(1 for case in cases if (case.split, case.group) == (split, group))
            if have != group_count(split, group):
                found.append(f"Testset: {split}/{group} hat {have} Fälle, erwartet {group_count(split, group)}")
        main = [case for case in cases if case.split == split and case.group == "haupt"]
        for verdict in VERDICTS:
            have = sum(1 for case in main if case.expected == verdict)
            if have != CLASS_COUNTS[split]:
                found.append(f"Testset: {split}/haupt: Klasse {verdict} hat {have} Fälle, "
                             f"erwartet {CLASS_COUNTS[split]}")
        special = Counter(case.kind for case in cases if case.split == split and case.group == "spezial")
        for kind, want in KIND_COUNTS[split].items():
            if special[kind] != want:
                found.append(f"Testset: {split}: Art {kind} hat {special[kind]} Fälle, erwartet {want}")
    by_vote = [case for case in cases if case.group == "haupt"]
    for vote_id, want in HAUPT_PLAN.items():
        own = [case for case in by_vote if case.vote_id == vote_id]
        if len(own) != want:
            found.append(f"Testset: Vorlage {vote_id} hat {len(own)} Hauptfälle, erwartet {want}")
        if want == len(VERDICTS) * CLASS_PER_SIX_VOTE:
            for verdict in VERDICTS:
                have = sum(1 for case in own if case.expected == verdict)
                if have != CLASS_PER_SIX_VOTE:
                    found.append(f"Testset: Vorlage {vote_id}: Klasse {verdict} hat {have} Fälle, "
                                 f"erwartet {CLASS_PER_SIX_VOTE}")
    return found


def validate(cases: List[EvalCase], votes, *, previous_claims: Iterable[str] = (), complete: bool = True) -> List[str]:
    """Every problem of the cases, one message each, starting with the case id or "Testset:"; [] = all fine.

    `votes` is a `VoteCatalog` (anything with `votes`, `missing`, `parts()` and `page_texts()`). `previous_claims`
    are the claims of earlier data sets: a case may not repeat one (compared after `official_normalize`).
    `complete=False` skips the counts of the finished set (30/60/7/13 cases, classes, votes, kinds) for a draft.
    """
    earlier = {official_normalize(claim) for claim in previous_claims}
    ids: set = set()
    first_with_claim: Dict[str, str] = {}
    problems: List[str] = []
    for case in cases:
        found = []
        if case.id in ids:
            found.append("ID kommt mehrfach vor")
        ids.add(case.id)
        found += _field_problems(case) + _claim_problems(case) + _vote_problems(case, votes)
        found += _result_problems(case, votes)
        claim = official_normalize(case.claim)
        if claim:
            if claim in first_with_claim:
                found.append(f"Behauptung steht schon in {first_with_claim[claim]}")
            first_with_claim.setdefault(claim, case.id)
            if claim in earlier:
                found.append("Behauptung steht schon in den früheren Datensätzen (Holdout oder OST-Dev)")
        problems += [f"{case.id}: {message}" for message in dict.fromkeys(found)]
    if complete:
        problems += _count_problems(cases)
    return problems


# --- review, lock and loader ------------------------------------------------------------------------------------
# `label_review.jsonl` is the export of `scripts/build_label_review.py`: one line per judgement of Sheyla,
# {"id", "case_sha", "expected", "after_answer", "note", "at"}. A judgement belongs to the `case_sha` it was given for.

MANIFEST_SCHEMA = 1
REVIEWER = "Sheyla"
USAGE = "Dev frei. Test und Spezial-Test einmalig nach dem Konfigurationsfreeze (Stufe 5)."
ALREADY_LOCKED = "bereits gesperrt; Änderungen brauchen ein neues Testset"
_EVENT_TEXT_FIELDS = ("id", "case_sha", "expected", "note", "at")


@dataclass(frozen=True)
class ReviewStatus:
    """Where the review stands for the current cases (ids in the order of the cases)."""
    missing: List[str]  # no judgement at all
    stale: List[str]  # the latest judgement was given for another version of the case: judge again
    disagree: List[str]  # the latest judgement is for this case, but says another `expected` (or `after_answer`)
    first_agree: int  # cases whose first judgement was for this very case and agrees: the blind agreement
    changed_after_first_review: List[str]  # cases whose first judgement was for another version of the case


class LockRefused(Exception):
    """The set cannot be locked; `problems` has one message per problem, each naming its case ids."""

    def __init__(self, problems: Iterable[str]):
        self.problems: List[str] = list(problems)
        super().__init__("\n".join(self.problems))


def _parse_event(line: str, where: str) -> dict:
    try:
        event = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{where}: kein gültiges JSON ({exc.msg})") from None
    if not isinstance(event, dict):
        raise ValueError(f"{where}: ein JSON-Objekt wird erwartet")
    if missing := [name for name in (*_EVENT_TEXT_FIELDS, "after_answer") if name not in event]:
        raise ValueError(f"{where}: Felder fehlen: {', '.join(missing)}")
    for name in _EVENT_TEXT_FIELDS:
        if not isinstance(event[name], str):
            raise ValueError(f"{where}: {name} muss ein Text sein")
    if event["after_answer"] is not None and not isinstance(event["after_answer"], str):
        raise ValueError(f"{where}: after_answer muss ein Text oder null sein")
    return event


def _chronological(events: Iterable[dict]) -> List[dict]:
    """By `at` (ISO times of one format compare as text); events of the same time stay in file order."""
    return sorted(events, key=lambda event: event.get("at", ""))


def _review_from_text(text: str, name: str) -> Dict[str, List[dict]]:
    review: Dict[str, List[dict]] = {}
    for number, line in enumerate(text.split("\n"), 1):
        if line.strip():
            event = _parse_event(line, f"{name}, Zeile {number}")
            review.setdefault(event["id"], []).append(event)
    return {case_id: _chronological(events) for case_id, events in review.items()}


def read_review(path: Path = REVIEW_PATH) -> Dict[str, List[dict]]:
    """The judgements of a review export: id -> its events, oldest first (same time: file order).

    ValueError (German) names the line of a bad event.
    """
    path = Path(path)
    return _review_from_text(path.read_text(encoding="utf-8"), path.name)


def _agrees(case: EvalCase, event: dict) -> bool:
    if event["expected"] != case.expected:
        return False
    return case.kind != "mehrdeutig" or event.get("after_answer") == case.after_answer


def review_status(cases: List[EvalCase], review: Dict[str, List[dict]]) -> ReviewStatus:
    """The latest judgement of a case counts; the first one is only counted for the blind agreement.

    A stale judgement says nothing about today's case, so a case is either stale or disagreeing, never both.
    Judgements of ids that are no case are ignored.
    """
    missing, stale, disagree, changed = [], [], [], []
    first_agree = 0
    for case in cases:
        events = _chronological(review.get(case.id, []))
        if not events:
            missing.append(case.id)
            continue
        sha = case_sha(case)
        first, latest = events[0], events[-1]
        if first["case_sha"] != sha:
            changed.append(case.id)
        elif _agrees(case, first):
            first_agree += 1
        if latest["case_sha"] != sha:
            stale.append(case.id)
        elif not _agrees(case, latest):
            disagree.append(case.id)
    return ReviewStatus(missing, stale, disagree, first_agree, changed)


def _counts(values: Iterable[Optional[str]]) -> Dict[str, int]:
    return dict(sorted(Counter("keine" if value is None else value for value in values).items()))


def _booklet_names(cases: List[EvalCase]) -> List[str]:
    """The PDF file name of every booklet a case points to, by `vote_id` or `ambiguous_with`."""
    votes = {vote for case in cases for vote in (case.vote_id, *case.ambiguous_with) if vote is not None}
    return sorted({f"{vote.split('#', 1)[0]}.pdf" for vote in votes})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_inputs(cases_path: Path, review_path: Path) -> Tuple[bytes, bytes, List[EvalCase], Dict[str, List[dict]]]:
    """Bytes and parsed content of cases and review; LockRefused names a missing or unreadable file."""
    raw: List[bytes] = []
    for path in (cases_path, review_path):
        try:
            raw.append(Path(path).read_bytes())
        except OSError:
            raise LockRefused([f"Datei fehlt oder ist nicht lesbar: {path}"]) from None
    try:
        cases = _cases_from_text(raw[0].decode("utf-8"), Path(cases_path).name)
        review = _review_from_text(raw[1].decode("utf-8"), Path(review_path).name)
    except ValueError as exc:  # bad JSON or fields (UnicodeDecodeError is a ValueError too)
        raise LockRefused([str(exc)]) from None
    return raw[0], raw[1], cases, review


def _review_problems(status: ReviewStatus) -> List[str]:
    found = []
    if status.missing:
        found.append(f"Ohne Urteil: {', '.join(status.missing)}")
    if status.stale:
        found.append(f"Fall nach dem Urteil geändert, neues Urteil nötig: {', '.join(status.stale)}")
    if status.disagree:
        found.append(f"Urteil weicht vom Entwurf ab, einzeln klären: {', '.join(status.disagree)}")
    return found


def lock(*, votes, sources: Dict[str, dict], cases_path: Path = CASES_PATH, review_path: Path = REVIEW_PATH,
         manifest_path: Path = MANIFEST_PATH, git_commit: str, git_dirty: bool, now: datetime, complete: bool = True,
         previous_claims: Iterable[str] = ()) -> dict:
    """Seal the testset: check it, write `manifest_path` and return the manifest. LockRefused if it cannot be locked.

    Refused when the manifest exists (a locked set is never changed: that needs a new testset), when `validate` finds a
    problem (`complete` and `previous_claims` go to it) or when a case has no judgement, a stale one or one that differs
    from the draft. `votes` is a `VoteCatalog`, `sources` the booklet catalog (`load_catalog`: its `sha256` goes into
    the manifest). `now` must carry a time zone. The manifest is created exclusively: two locks cannot both succeed.
    """
    if now.tzinfo is None:
        raise ValueError("now braucht eine Zeitzone")
    manifest_path = Path(manifest_path)
    if manifest_path.exists():
        raise LockRefused([ALREADY_LOCKED])
    cases_bytes, review_bytes, cases, review = _read_inputs(cases_path, review_path)
    problems = validate(cases, votes, previous_claims=previous_claims, complete=complete)
    status = review_status(cases, review)
    problems += _review_problems(status)
    booklets = {}
    for name in _booklet_names(cases):
        if name in sources:
            booklets[name] = sources[name]["sha256"]
        else:
            problems.append(f"Büchlein {name} steht nicht im Katalog: sein SHA-256 lässt sich nicht festhalten")
    if problems:
        raise LockRefused(problems)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "locked_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "cases_sha256": _sha256(cases_bytes),
        "review_sha256": _sha256(review_bytes),
        "counts": {
            "split_group": _counts(f"{case.split}/{case.group}" for case in cases),
            "kind": _counts(case.kind for case in cases),
            "novelty": _counts(case.novelty for case in cases),
            "expected": _counts(case.expected for case in cases),
        },
        "booklets": booklets,
        "review": {"cases": len(cases), "first_judgement_agrees": status.first_agree,
                   "changed_after_first_review": status.changed_after_first_review, "reviewer": REVIEWER},
        "usage": USAGE,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    except FileExistsError:  # locked by someone else since the check above
        raise LockRefused([ALREADY_LOCKED]) from None
    return manifest


def load_testset(split: str, *, cases_path: Path = CASES_PATH, manifest_path: Path = MANIFEST_PATH,
                 allow_test: bool = False) -> List[EvalCase]:
    """The cases of `split` (main and special cases) of the locked testset, in file order.

    This is the gate to the cases: ValueError if the set is not locked or `cases_path` no longer has the hash of the
    manifest, ValueError for an unknown split, PermissionError for `test` without `allow_test=True` (stage 5 only).
    """
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Testset ist nicht gesperrt") from None
    except (OSError, ValueError):
        raise ValueError(f"Manifest {manifest_path} ist nicht lesbar") from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases_sha256"), str):
        raise ValueError(f"Manifest {manifest_path} ist nicht lesbar: cases_sha256 fehlt")
    try:
        cases_bytes = Path(cases_path).read_bytes()
    except FileNotFoundError:
        raise ValueError("cases.jsonl wurde nach dem Sperren verändert (die Datei fehlt)") from None
    if _sha256(cases_bytes) != manifest["cases_sha256"]:
        raise ValueError("cases.jsonl wurde nach dem Sperren verändert")
    if split not in SPLITS:
        raise ValueError(f"Unbekannter Split {split!r}, erlaubt sind {', '.join(SPLITS)}")
    if split == "test" and not allow_test:
        raise PermissionError("Testteil erst nach dem Konfigurationsfreeze (allow_test=True)")
    cases = _cases_from_text(cases_bytes.decode("utf-8"), Path(cases_path).name)
    return [case for case in cases if case.split == split]
