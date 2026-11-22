"""Per-passage presentation of the evidence: the one sentence to mark and the passage's stance.

The engine quotes whole PDF text blocks. To make a quote quick to scan, the web view marks only the sentence of the
block that best matches the claim (`focus`, a verbatim substring of the quote, so it can also be found on the PDF
page). Each passage also gets a stance: a passage whose best sentence shares (almost) nothing with the claim is shown
as `neutral` (no direct reference), every other passage takes the direction of the verdict (`supports` for
`gestuetzt`, `contradicts` for `widersprochen`). No model is called; the verdict itself is never changed.
"""
import re
from typing import List, Optional, Tuple

from src.text_utils import _stems, normalise

# Short words that end with a period without ending the sentence ("z. B.", "Art. 5", "Mio. Franken").
_ABBREVIATIONS = {"z", "b", "d", "h", "u", "a", "art", "abs", "nr", "bzw", "usw", "ca", "mio", "mrd", "vgl", "bspw",
                  "inkl", "ggf", "evtl", "etc", "s", "st", "dr", "prof", "al", "lit", "ziff", "fr", "chf", "cf",
                  "p", "ex", "env", "mme", "m", "ecc", "sig"}
_BOUNDARY = re.compile(r"[.!?;](?=[»\"')\]]*\s)|\n[ \t]*\n")
MIN_SCORE = 2.0  # below: the passage has no direct reference to the claim
MIN_SENTENCE_CHARS = 15
STANCES = {"gestuetzt": "supports", "widersprochen": "contradicts"}


def sentence_spans(text: str) -> List[Tuple[int, int]]:
    """(start, end) of each sentence of `text`, as offsets into the unchanged text."""
    spans, start = [], 0
    for match in _BOUNDARY.finditer(text):
        end = match.end()
        if match.group().strip() == ".":
            word = re.search(r"(\w+)$", text[start:match.start()])
            if word and (word.group(1).isdigit() or word.group(1).casefold() in _ABBREVIATIONS):
                continue  # "13. AHV-Rente", "Art. 5", "z. B."
        spans.append((start, end))
        start = end
    spans.append((start, len(text)))
    result = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            result.append((s, e))
    return result


def _score(sentence: str, claim_stems: set) -> float:
    return sum(3.0 if hit.isdigit() else 1.0 for hit in claim_stems & _stems(normalise(sentence)))


def focus_sentence(quote: str, claim: str) -> Tuple[Optional[str], float]:
    """The sentence of `quote` that best matches `claim` (verbatim) and its score; (None, 0) without any match."""
    claim_stems = _stems(claim)
    best, best_score = None, 0.0
    for start, end in sentence_spans(quote):
        sentence = quote[start:end]
        if len(normalise(sentence)) < MIN_SENTENCE_CHARS:
            continue
        score = _score(sentence, claim_stems)
        if score > best_score:
            best, best_score = sentence, score
    return best, best_score


def annotate(claim: str, outcome: str, evidence: List[dict]) -> List[dict]:
    """The evidence items with `focus` (str or None) and `stance` ('supports' | 'neutral' | 'contradicts')."""
    direction = STANCES.get(outcome, "neutral")
    found = [focus_sentence(item.get("quote") or "", claim or "") for item in evidence]
    # The verdict rests on these passages: if none reaches the threshold (e.g. a claim in another language than the
    # booklet), the best-matching one still carries the verdict's direction.
    strongest = max(range(len(found)), key=lambda i: found[i][1]) if found else -1
    reached = any(score >= MIN_SCORE for _, score in found)
    return [{**item, "focus": focus if score > 0 else None,
             "stance": direction if score >= MIN_SCORE or (not reached and i == strongest) else "neutral"}
            for i, (item, (focus, score)) in enumerate(zip(evidence, found))]
