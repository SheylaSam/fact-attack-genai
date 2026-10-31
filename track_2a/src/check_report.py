"""Self-contained printable HTML reports. No remote assets or inference."""
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse
from src.ui_i18n import translate


def batch_report_records(outcomes, context):
    return [{**context, 'claim': item['claim'], 'attempts': item.get('attempts', 1),
             'error': item['error'], 'save_error': item['save_error'], 'origin': 'live',
             'result': item['result'].model_dump(mode='json') if item['result'] is not None else None}
            for item in outcomes]


def render_check_report(records, language='de', generated_at=None):
    language = language if language in ('de', 'fr', 'it') else 'de'
    def t(text):
        return translate(text, language)
    def e(value):
        return escape(str(value if value is not None else t('Nicht verfügbar')))
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    sections = []
    verdicts = {0: 'Vom Abstimmungsbüchlein gestützt', 1: 'Im Abstimmungsbüchlein nicht geklärt',
                2: 'Widerspricht dem Abstimmungsbüchlein'}
    for index, record in enumerate(records, 1):
        result = record.get('result') or {}
        error = record.get('error')
        label = result.get('label')
        title = t('Technischer Fehler – keine Einordnung') if error else t(verdicts.get(label, 'Nicht verfügbar'))
        origin = t('Prüfung fehlgeschlagen') if error else (t('Gespeicherte Demoantwort') if record.get('origin') == 'saved_demo' else t('Gespeicherte Liveantwort'))
        metadata = [('Quelle', record.get('source_file')), ('Quellendatum', record.get('date')),
                    ('Sprache des Büchleins', record.get('language')), ('Vorlage', record.get('vote')),
                    ('Strategie', result.get('strategy') or record.get('strategy')),
                    ('Gespeichert / angezeigt (UTC)', record.get('created_at')),
                    ('SHA-256 der Quelle', record.get('source_sha256'))]
        detail = ''.join(f'<dt>{e(t(key))}</dt><dd>{e(value)}</dd>' for key, value in metadata)
        evidence = ''
        if not error and label != 1:
            for source in result.get('evidence_sources', []):
                evidence += f'<p class="page">{e(t("PDF-Seite"))} {e(source.get("page_number"))}</p><blockquote>{e(source.get("quote"))}</blockquote>'
        if not evidence:
            evidence = f'<p>{e(t("Keine Belegstelle in diesem Bericht."))}</p>'
        url = record.get('source_url') or ''
        parsed = urlparse(url)
        link = f'<p><a href="{escape(url, quote=True)}">{e(t("Offizielle Originalquelle"))}</a></p>' if parsed.scheme == 'https' and parsed.hostname == 'www.bk.admin.ch' else ''
        failure = f'<p class="notice">{e(error)}</p>' if error else ''
        save_error = f'<p class="notice">{e(t("Das Ergebnis konnte nicht im Verlauf gespeichert werden."))}</p>' if record.get('save_error') else ''
        attempts = f'<p>{e(t("Prüfversuche: {n}").format(n=record["attempts"]))}</p>' if record.get('attempts') else ''
        sections.append(f'<article><p class="eyebrow">{index} · {e(origin)}</p><h2>{e(title)}</h2><p class="claim">{e(record.get("claim") or result.get("claim"))}</p>{failure}{save_error}{attempts}<dl>{detail}</dl>{link}<h3>{e(t("Belegstellen"))}</h3>{evidence}</article>')
    html = f'''<!doctype html>
<html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(t('Fact Attack – Prüfbericht'))}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef1f4;color:#182230;font:16px/1.6 system-ui,sans-serif}}main{{max-width:960px;margin:32px auto;padding:32px;background:white}}h1{{font-size:30px}}h2{{font-size:22px}}h3{{font-size:17px}}article{{border-top:2px solid #bd3029;margin-top:32px;padding-top:18px}}.eyebrow,.meta,.page{{color:#526170;font-size:14px}}.claim{{font-size:19px;font-weight:600;white-space:pre-wrap}}.notice{{border-left:4px solid #b86d08;padding:10px;background:#fff6e5}}dl{{display:grid;grid-template-columns:220px minmax(0,1fr);gap:6px 18px}}dt{{font-weight:600}}dd{{margin:0;overflow-wrap:anywhere}}blockquote{{margin:12px 0;padding:14px 18px;border-left:3px solid #697a8a;background:#f3f5f7;white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#204c88}}@media(max-width:600px){{main{{margin:0;padding:20px}}dl{{display:block}}dd{{margin-bottom:12px}}}}@media print{{@page{{size:A4;margin:18mm}}body,main{{background:white}}main{{margin:0;padding:0;max-width:none}}h2,h3,.page{{break-after:avoid}}dl{{font-size:10pt}}blockquote{{font-size:11pt}}article{{break-inside:auto}}.print-help{{display:none}}a{{color:inherit}}}}
</style></head><body><main><header><h1>{e(t('Fact Attack – Prüfbericht'))}</h1>
<p>{e(t('Vergleicht Aussagen mit dem Abstimmungsbüchlein. Keine allgemeine Wahrheitsprüfung und keine Abstimmungsempfehlung.'))}</p>
<p class="meta">{e(t('Bericht erstellt (UTC)'))}: {e(generated_at)} · {len(records)} {e(t('Prüfungen'))}</p>
<p class="print-help">{e(t('Im Browser öffnen und über Drucken ausgeben oder als PDF speichern.'))}</p>
<p>{e(t('Originalzitate bleiben in ihrer Quellensprache. Ein Bericht startet keine neue Prüfung.'))}</p></header>
{''.join(sections)}<footer><p class="meta">{e(t('Historische Momentaufnahme. Demoantworten und technische Fehler sind gesondert gekennzeichnet.'))}</p></footer></main></body></html>'''
    return html.encode('utf-8')
