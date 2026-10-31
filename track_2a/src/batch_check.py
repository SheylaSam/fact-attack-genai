"""Local sentence suggestions and bounded, explicit sequential checks."""
import re

MAX_CHECKS = 5
MAX_TEXT_CHARS = 20000


def suggest_statements(text):
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError('Der Text ist zu lang (maximal 20 000 Zeichen).')
    # Keep numbers such as 3.5 intact. Suggestions are not semantic claim extraction.
    sentences = re.split(r'\n+|(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ«„“])', text.strip())
    found = []
    for sentence in sentences:
        sentence = sentence.strip()
        if sentence and sentence not in found:
            found.append(sentence)
    return found


def run_checks(claims, verify, on_success=None):
    if not 1 <= len(claims) <= MAX_CHECKS:
        raise ValueError('Select one to five statements')
    outcomes = []
    for claim in claims:
        try:
            result = verify(claim)
            if result.error:
                raise RuntimeError(result.error)
        except Exception as error:
            outcomes.append({'claim': claim, 'result': None, 'error': str(error), 'save_error': None})
            continue
        save_error = None
        if on_success:
            try:
                on_success(result)
            except Exception as error:
                save_error = str(error)
        outcomes.append({'claim': claim, 'result': result, 'error': None, 'save_error': save_error})
    return outcomes
