"""Local sentence suggestions and bounded, explicit sequential checks."""
import re
import json
import csv
import io

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


def run_checks(claims, verify, on_success=None, on_progress=None):
    if not 1 <= len(claims) <= MAX_CHECKS:
        raise ValueError('Select one to five statements')
    outcomes = []
    for index, claim in enumerate(claims):
        if on_progress:
            on_progress(index, len(claims), claim)
        try:
            result = verify(claim)
            if result.error:
                raise RuntimeError(result.error)
        except Exception as error:
            outcomes.append({'claim': claim, 'result': None, 'error': str(error) or type(error).__name__, 'save_error': None, 'attempts': 1})
        else:
            save_error = None
            if on_success:
                try:
                    on_success(result)
                except Exception as error:
                    save_error = str(error) or type(error).__name__
            outcomes.append({'claim': claim, 'result': result, 'error': None, 'save_error': save_error, 'attempts': 1})
        if on_progress:
            on_progress(index + 1, len(claims), claim)
    return outcomes


def retry_failed(outcomes, verify, on_success=None, on_progress=None):
    if not 1 <= len(outcomes) <= MAX_CHECKS:
        raise ValueError('Invalid batch size')
    failed = [index for index, outcome in enumerate(outcomes) if outcome['error']]
    if not failed:
        return list(outcomes)
    updated = list(outcomes)
    retried = run_checks([outcomes[index]['claim'] for index in failed], verify, on_success, on_progress)
    for index, outcome in zip(failed, retried):
        updated[index] = {**outcome, 'attempts': outcomes[index].get('attempts', 1) + 1}
    return updated


def batch_summary(outcomes):
    return {'total': len(outcomes), 'successful': sum(item['result'] is not None and not item['error'] for item in outcomes),
            'failed': sum(bool(item['error']) for item in outcomes),
            'save_failed': sum(bool(item['save_error']) for item in outcomes)}


def export_batch(outcomes, context, format='json'):
    # Keep only presentation-safe source context and result fields; no absolute paths/debug metadata.
    context = {key: context.get(key) for key in ('source_file', 'source_sha256', 'date', 'language', 'vote', 'strategy')}
    rows = []
    fields = ('label', 'label_name', 'reasoning', 'evidence', 'evidence_sources',
              'tokens_prompt', 'tokens_completion', 'latency_ms', 'context_pages')
    for outcome in outcomes:
        result = outcome['result'].model_dump(mode='json') if outcome['result'] is not None else None
        rows.append({'claim': outcome['claim'], 'status': 'failed' if outcome['error'] else 'successful',
                     'attempts': outcome.get('attempts', 1), 'error': outcome['error'],
                     'save_error': outcome['save_error'],
                     'result': {key: result[key] for key in fields if key in result} if result else None})
    if format == 'json':
        return (json.dumps({'schema_version': 1, 'kind': 'batch_check_export', 'source': context,
                            'summary': batch_summary(outcomes), 'outcomes': rows}, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if format != 'csv':
        raise ValueError('Unsupported format')
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Behauptung', 'Status', 'Ergebnis', 'Versuche', 'Fehler', 'Speicherfehler', 'Quellendatum', 'Sprache', 'Strategie'])
    for row in rows:
        cells = [row['claim'], row['status'], (row['result'] or {}).get('label_name'), row['attempts'],
                 row['error'], row['save_error'], context['date'], context['language'], context['strategy']]
        safe = []
        for cell in cells:
            text = '' if cell is None else str(cell)
            if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
                text = "'" + text
            safe.append(text)
        writer.writerow(safe)
    return output.getvalue().encode('utf-8-sig')
