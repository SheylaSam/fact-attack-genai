"""Actual Streamlit widgets, with every remote completion explicitly forbidden."""
import re
from pathlib import Path
from unittest.mock import patch
import pytest
pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
from src.ui_strategies import STANDARD,DENSE,FUSED

APP=Path(__file__).parents[1]/'app.py'


def goto(app, page):
    """Opens a page of the sidebar navigation."""
    return app.radio(key='nav').set_value(page).run()


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    from src.check_history import save_check, load_checks, set_removed
    monkeypatch.setenv('FACT_ATTACK_AGENT', '0')  # these tests check the flow without the agent layer
    database = tmp_path / 'history.sqlite3'
    with patch('src.check_history.save_check', side_effect=lambda path, **kwargs: save_check(database, **kwargs)), \
         patch('src.check_history.load_checks', side_effect=lambda path, **kwargs: load_checks(database, **kwargs)), \
         patch('src.check_history.set_removed', side_effect=lambda path, *args, **kwargs: set_removed(database, *args, **kwargs)):
        yield database


def test_strategy_opt_in_switch_and_reset_do_not_infer():
    with patch('src.apertus_client.ApertusClient._request_completion',side_effect=AssertionError('No paid inference in UI tests')) as remote:
        app=AppTest.from_file(APP,default_timeout=50).run()
        assert not app.exception
        assert app.radio(key='strategy_option').value==STANDARD
        assert DENSE not in app.radio(key='strategy_option').options
        app.checkbox(key='enable_vector_rag').check().run()
        assert DENSE in app.radio(key='strategy_option').options
        app.radio(key='strategy_option').set_value(FUSED).run()
        assert app.radio(key='strategy_option').value==FUSED
        app.checkbox(key='enable_vector_rag').uncheck().run()
        assert app.radio(key='strategy_option').value==STANDARD
        remote.assert_not_called()
        import streamlit as st
        with patch('src.inference.ClaimVerificationEngine.close',autospec=True) as released:
            st.cache_resource.clear()
            assert released.call_count>=1


def test_missing_rag_dependencies_give_local_error_without_hybrid_fallback():
    with patch('src.ui_strategies.rag_available',return_value=False), \
         patch('src.apertus_client.ApertusClient._request_completion',side_effect=AssertionError('No network')) as remote:
        app=AppTest.from_file(APP,default_timeout=50).run()
        app.text_area(key='claim_input').set_value('Die Vorlage verändert das Rentenalter.').run()
        app.checkbox(key='enable_vector_rag').check().run()
        app.radio(key='strategy_option').set_value(DENSE).run()
        next(button for button in app.button if button.label=='Behauptung prüfen').click().run()
        assert not app.exception
        assert any('Optionale Abhängigkeiten' in error.value for error in app.error)
        assert 'last_check' not in app.session_state
        remote.assert_not_called()


def test_examples_prepare_inputs_without_starting_inference():
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No automatic inference')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        verify = next(button for button in app.button if button.label == 'Behauptung prüfen')
        assert verify.disabled
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        assert not app.exception
        assert app.text_area(key='claim_input').value.strip()
        assert not next(button for button in app.button if button.label == 'Behauptung prüfen').disabled
        assert 'last_check' not in app.session_state
        remote.assert_not_called()


def test_saved_example_result_remains_readable_when_live_is_unavailable():
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=RuntimeError('Offline UI check')):
        app = AppTest.from_file(APP, default_timeout=50).run()
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception
        assert 'last_check' in app.session_state
        assert app.session_state['last_check']['notice']
        assert any(expander.label == 'Details zur Prüfung' for expander in app.expander)
        assert any('gespeicherte' in item.value for item in app.markdown)
        assert any('Markiert ist der angezeigte Belegauszug' in item.value for item in app.caption)


def test_partial_source_installation_only_offers_available_date_and_language():
    from src.booklet_catalog import load_catalog
    sources = load_catalog(APP.parent / 'data/booklet_catalog.json')
    partial = {'2024-11-24_de.pdf': sources['2024-11-24_de.pdf']}
    with patch('src.booklet_catalog.available_sources', return_value=partial), \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No network')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        assert not app.exception
        assert app.selectbox(key='ballot').options == ['24. November 2024']
        assert app.radio(key='booklet_lang').options == ['DE']
        remote.assert_not_called()


def test_source_filters_no_hits_reset_and_examples_do_not_infer():
    from src.booklet_catalog import load_catalog
    sources = load_catalog(APP.parent / 'data/booklet_catalog.json')
    with patch('src.booklet_catalog.available_sources', return_value=sources), patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No inference')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.text_input(key='source_query').set_value('Mietrecht').run()
        assert app.selectbox(key='ballot').options == ['24. November 2024']
        app.text_area(key='claim_input').set_value('Eine politische Behauptung.').run()
        app.text_input(key='source_query').set_value('xyzkeinetreffer').run()
        assert app.selectbox(key='ballot').disabled
        assert next(button for button in app.button if button.label == 'Behauptung prüfen').disabled
        assert any('Keine passende Quelle' in item.value for item in app.info)
        next(button for button in app.button if button.label == 'Quellenfilter zurücksetzen').click().run()
        assert not app.exception
        assert len(app.selectbox(key='ballot').options) == 22
        app.selectbox(key='source_year').set_value('2020').run()
        assert len(app.selectbox(key='ballot').options) == 3
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        assert app.selectbox(key='source_year').value == 'Alle Jahre'
        assert app.selectbox(key='ballot').value == '14. Juni 2026'
        remote.assert_not_called()


def test_history_reopens_after_app_restart_without_inference(isolated_history):
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=RuntimeError('Offline')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception
        assert isolated_history.exists()
        reopened = goto(AppTest.from_file(APP, default_timeout=50).run(), 'Prüfverlauf')
        assert not reopened.exception
        assert len(reopened.selectbox(key='history_selected').options) == 1
        assert any('Gespeicherte Demoantwort' in info.value for info in reopened.info)
        remote.assert_not_called()


def test_history_opt_out_does_not_persist_a_check(isolated_history):
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=RuntimeError('Offline')):
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.checkbox(key='save_history').uncheck().run()
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception
        assert 'last_check' in app.session_state
        assert not isolated_history.exists()


def test_history_trash_and_restore_survive_new_app_run_without_api(isolated_history):
    from src.check_history import load_checks
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=RuntimeError('Offline')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        goto(app, 'Prüfverlauf')
        app.button(key='history_remove_restore').click().run()
        assert not app.exception
        assert not load_checks(isolated_history)
        app.radio(key='history_view').set_value('Papierkorb').run()
        assert app.button(key='history_remove_restore').label == 'Wiederherstellen'
        app.button(key='history_remove_restore').click().run()
        assert not app.exception
        assert len(load_checks(isolated_history)) == 1
        restarted = goto(AppTest.from_file(APP, default_timeout=50).run(), 'Prüfverlauf')
        assert not restarted.exception
        assert len(restarted.selectbox(key='history_selected').options) == 1
        remote.assert_not_called()


@pytest.mark.parametrize('language,button_label', [('fr', 'Vérifier l’affirmation'), ('it', 'Verifica l’affermazione')])
def test_ui_language_keeps_inputs_and_does_not_infer(language, button_label):
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No network')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.text_area(key='claim_input').set_value('Die Vorlage verändert das Rentenalter.').run()
        before = {key: app.session_state[key] for key in ('claim_input', 'ballot', 'booklet_lang', 'vote', 'strategy_option', 'claim_lang')}
        app.selectbox(key='ui_language').set_value(language).run()
        assert not app.exception
        assert {key: app.session_state[key] for key in before} == before
        assert any(button.label == button_label for button in app.button)
        assert app.selectbox(key='source_year').options[0] == ('Toutes les années' if language == 'fr' else 'Tutti gli anni')
        app.selectbox(key='ui_language').set_value('de').run()
        assert not app.exception
        assert {key: app.session_state[key] for key in before} == before
        remote.assert_not_called()


def test_ui_language_does_not_rewrite_saved_claims_or_evidence(isolated_history):
    from src.check_history import load_checks
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=RuntimeError('Offline')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        original = load_checks(isolated_history)
        app.selectbox(key='ui_language').set_value('fr').run()
        assert not app.exception
        assert load_checks(isolated_history) == original
        assert any('Markiert' not in caption.value and 'surligné' in caption.value for caption in app.caption)
        goto(app, 'Prüfverlauf')
        assert any('Réponse de démonstration enregistrée' in info.value for info in app.info)
        remote.assert_not_called()


def test_batch_selection_is_local_and_requires_explicit_start():
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.text_area(key='batch_text').set_value('Die Vorlage verändert das Rentenalter. La proposition change les impôts.').run()
        assert not app.exception
        choices = app.multiselect(key='batch_selected').options
        assert len(choices) == 2
        assert app.button(key='run_batch').disabled
        app.multiselect(key='batch_selected').set_value(choices).run()
        assert not app.button(key='run_batch').disabled
        app.selectbox(key='ui_language').set_value('fr').run()
        assert not app.exception
        assert app.multiselect(key='batch_selected').value == choices
        remote.assert_not_called()


def test_batch_results_are_separate_persisted_and_not_rechecked_on_rerun(isolated_history):
    import streamlit as st
    from src.inference import PredictionResult
    from src.check_history import load_checks
    st.cache_resource.clear()
    def prediction(**kwargs):
        return PredictionResult(claim=kwargs['claim'], label=1, label_name='Neutral', reasoning='Injected UI test',
                                evidence=[], strategy=kwargs['strategy'], booklet_path=str(kwargs['booklet_pdf']),
                                tokens_prompt=0, tokens_completion=0, tokens_total=0, latency_ms=0)
    with patch('src.config.LLM_API_KEY', 'ui-test-placeholder'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', side_effect=prediction) as verify, \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No actual API')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.text_area(key='batch_text').set_value('Die Vorlage verändert das Rentenalter. La proposition change les impôts.').run()
        app.multiselect(key='batch_selected').set_value(app.multiselect(key='batch_selected').options).run()
        app.button(key='run_batch').click().run()
        assert not app.exception
        assert verify.call_count == 2
        assert len(load_checks(isolated_history)) == 2
        app.run()
        assert verify.call_count == 2
        app.multiselect(key='batch_selected').set_value([]).run()
        assert any('bisherigen Mehrfachergebnisse' in info.value for info in app.info)
        assert verify.call_count == 2
        remote.assert_not_called()
    st.cache_resource.clear()


def test_history_search_filters_dates_labels_and_reset_without_api(isolated_history):
    import json
    from datetime import datetime, timezone, timedelta
    from src.check_history import save_check, load_checks
    sample = json.loads((APP.parent / 'data/demo_cases.json').read_text())['cases'][0]
    for claim, label in [('Mietrecht alpha', 0), ('Rente beta', 2)]:
        result = {**sample['cached_result'], 'claim': claim, 'label': label}
        save_check(isolated_history, result=result, source_path=APP.parent / 'data/booklets/2026-06-14_fr.pdf',
                   date='2026-06-14', language='fr', vote=None, notice='saved demo')
    before = load_checks(isolated_history)
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')) as remote:
        app = goto(AppTest.from_file(APP, default_timeout=50).run(), 'Prüfverlauf')
        assert len(app.selectbox(key='history_selected').options) == 2
        app.text_input(key='history_query').set_value('Mietrecht').run()
        assert not app.exception
        assert len(app.selectbox(key='history_selected').options) == 1
        app.selectbox(key='history_label').set_value('widersprochen').run()
        assert any('Keine Prüfung passt' in info.value for info in app.info)
        assert not any(widget.key == 'history_selected' for widget in app.selectbox)
        app.button(key='reset_history_filters').click().run()
        assert len(app.selectbox(key='history_selected').options) == 2
        app.date_input(key='history_start').set_value(datetime.now(timezone.utc).date() + timedelta(days=1)).run()
        assert any('Keine Prüfung passt' in info.value for info in app.info)
        app.button(key='reset_history_filters').click().run()
        assert not app.exception
        assert len(app.selectbox(key='history_selected').options) == 2
        assert load_checks(isolated_history) == before
        remote.assert_not_called()


def test_batch_retry_only_failed_keeps_history_and_survives_language_change(isolated_history):
    import streamlit as st
    from src.inference import PredictionResult
    from src.check_history import load_checks
    st.cache_resource.clear()
    statements = ['Die Vorlage verändert das Rentenalter.', 'La proposition change les impôts.']
    calls = []
    def prediction(**kwargs):
        claim = kwargs['claim']
        calls.append(claim)
        if claim == statements[1] and calls.count(claim) == 1:
            raise RuntimeError('Injected failure')
        return PredictionResult(claim=claim, label=1, label_name='Neutral', reasoning='Injected UI test',
                                evidence=[], strategy=kwargs['strategy'], booklet_path=str(kwargs['booklet_pdf']),
                                tokens_prompt=0, tokens_completion=0, tokens_total=0, latency_ms=0)
    with patch('src.config.LLM_API_KEY', 'ui-test-placeholder'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', side_effect=prediction), \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No actual API')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.text_area(key='batch_text').set_value(' '.join(statements)).run()
        app.multiselect(key='batch_selected').set_value(statements).run()
        app.button(key='run_batch').click().run()
        assert not app.exception
        assert len(load_checks(isolated_history)) == 1
        assert app.button(key='retry_batch')
        app.selectbox(key='ui_language').set_value('fr').run()
        assert calls == statements
        app.button(key='retry_batch').click().run()
        assert not app.exception
        assert calls == [*statements, statements[1]]
        assert len(load_checks(isolated_history)) == 2
        assert not any(button.key == 'retry_batch' for button in app.button)
        assert app.session_state['batch_check']['outcomes'][0]['attempts'] == 1
        assert app.session_state['batch_check']['outcomes'][1]['attempts'] == 2
        assert next(metric for metric in app.metric if metric.label == 'En échec').value == '0'
        app.run()
        assert calls == [*statements, statements[1]]
        remote.assert_not_called()
    st.cache_resource.clear()


def test_history_origin_sort_and_localized_report_use_filtered_records(isolated_history):
    import json
    from src.check_history import save_check
    from src.check_report import render_check_report
    sample = json.loads((APP.parent / 'data/demo_cases.json').read_text())['cases'][0]
    for claim, notice in [('Alpha Demo', 'saved demo'), ('Beta Live', None)]:
        save_check(isolated_history, result={**sample['cached_result'], 'claim': claim},
                   source_path=APP.parent / 'data/booklets/2026-06-14_fr.pdf',
                   date='2026-06-14', language='fr', vote=None, notice=notice)
    with patch('src.check_report.render_check_report', wraps=render_check_report) as reports, \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')) as remote:
        app = goto(AppTest.from_file(APP, default_timeout=50).run(), 'Prüfverlauf')
        app.selectbox(key='history_order').set_value('Älteste zuerst').run()
        assert not app.exception
        assert app.selectbox(key='history_selected').options[0].endswith('Alpha Demo')
        reports.reset_mock()
        app.selectbox(key='history_origin').set_value('Liveantworten').run()
        assert len(app.selectbox(key='history_selected').options) == 1
        assert all(record['origin'] == 'live' for call in reports.call_args_list for record in call.args[0])
        app.selectbox(key='ui_language').set_value('it').run()
        assert not app.exception
        assert reports.call_args.args[1] == 'it'
        assert app.selectbox(key='history_origin').value == 'Liveantworten'
        app.button(key='reset_history_filters').click().run()
        assert len(app.selectbox(key='history_selected').options) == 2
        assert app.selectbox(key='history_origin').value == 'Alle Antwortarten'
        remote.assert_not_called()


def test_failed_recheck_does_not_show_previous_success(isolated_history):
    import streamlit as st
    from src.inference import PredictionResult
    from src.check_history import load_checks
    st.cache_resource.clear()
    result = PredictionResult(claim='Eine eigene Aussage.', label=1, label_name='Neutral', reasoning='Injected',
                              evidence=[], strategy='hybrid', booklet_path='', tokens_prompt=0,
                              tokens_completion=0, tokens_total=0, latency_ms=0)
    with patch('src.config.LLM_API_KEY', 'ui-test'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', side_effect=[result, RuntimeError('Offline')]), \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')):
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.text_area(key='claim_input').set_value(result.claim).run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert 'last_check' in app.session_state
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception and 'last_check' not in app.session_state
        assert app.error and not any('<div class="verdict-card' in item.value for item in app.markdown)
        assert len(load_checks(isolated_history)) == 1
    st.cache_resource.clear()


def test_changed_pdf_invalidates_single_result_and_demo_fallback(tmp_path):
    import shutil
    import streamlit as st
    from src.inference import PredictionResult
    source = tmp_path / '2026-06-14_fr.pdf'
    shutil.copyfile(APP.parent / 'data/booklets/2026-06-14_fr.pdf', source)
    st.cache_resource.clear()
    with patch('src.config.BOOKLETS_DIR', tmp_path), \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception and 'last_check' in app.session_state
        with source.open('ab') as stream:
            stream.write(b'\n% test content change\n')
        app.run()
        assert not any('<div class="verdict-card' in item.value for item in app.markdown)
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception and 'last_check' not in app.session_state
        assert app.error
        remote.assert_not_called()
    st.cache_resource.clear()


def test_displayed_evidence_comes_from_selected_quote_not_other_page_text():
    import streamlit as st
    from src.inference import PredictionResult, EvidenceSource
    st.cache_resource.clear()
    quote = 'L’Assemblea federale raccomanda al Popolo e ai Cantoni di respingere l’iniziativa.'
    distractor = 'Die Vorlage kostet hundert Milliarden Franken und wird sofort umgesetzt.'
    result = PredictionResult(claim=distractor, label=0, label_name='Entailment', reasoning='Injected UI fixture',
                              evidence=[quote], evidence_sources=[EvidenceSource(page_number=22, quote=quote)],
                              strategy='hybrid', booklet_path='', tokens_prompt=0, tokens_completion=0,
                              tokens_total=0, latency_ms=0)
    booklet = {'pages': [{'page_number': 22, 'text': quote + ' ' + distractor}],
               'paragraphs': [], 'full_text': quote + ' ' + distractor}
    with patch('src.config.LLM_API_KEY', 'ui-test'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', return_value=result), \
         patch('src.inference.ClaimVerificationEngine._get_booklet_data', return_value=booklet), \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')):
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.radio(key='booklet_lang').set_value('IT').run()
        app.text_area(key='claim_input').set_value(distractor).run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception
        quotes = [item.value for item in app.markdown if 'class="quote"' in item.value]
        assert quotes and quote in quotes[0] and distractor not in quotes[0]
    st.cache_resource.clear()


def test_uploaded_pdf_has_no_official_date_in_history_or_report(tmp_path, isolated_history):
    import hashlib
    import streamlit as st
    from types import SimpleNamespace
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy
    from src.inference import PredictionResult
    from src.check_history import load_checks
    from src.check_report import render_check_report
    pdf = (APP.parent / 'data/booklets/2026-06-14_fr.pdf').read_bytes()
    source = tmp_path / f'custom_{hashlib.sha256(pdf).hexdigest()}.pdf'
    source.write_bytes(pdf)
    upload = SimpleNamespace(name='mein-dokument.pdf', getbuffer=lambda: memoryview(pdf))
    original_get = SessionStateProxy.get
    def session_get(self, key, default=None):
        return upload if key == 'uploaded_pdf' else original_get(self, key, default)
    result = PredictionResult(claim='Une affirmation personnelle.', label=1, label_name='Neutral', reasoning='Injected',
                              evidence=[], strategy='hybrid', booklet_path=str(source), tokens_prompt=0,
                              tokens_completion=0, tokens_total=0, latency_ms=0)
    st.cache_resource.clear()
    with patch.object(SessionStateProxy, 'get', session_get), \
         patch('src.ui_strategies.store_uploaded_source', return_value=source), \
         patch('src.config.LLM_API_KEY', 'ui-test'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', return_value=result), \
         patch('src.check_report.render_check_report', wraps=render_check_report) as reports, \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')):
        app = AppTest.from_file(APP, default_timeout=50).run()
        assert not app.exception and app.selectbox(key='ballot').disabled
        assert any('Aktive Quelle: eigenes PDF' in item.value for item in app.info)
        app.text_area(key='claim_input').set_value(result.claim).run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception
        assert load_checks(isolated_history)[0]['date'] is None
        assert all(record['date'] is None for call in reports.call_args_list for record in call.args[0])
    st.cache_resource.clear()


def test_dark_toggle_switches_the_palette():
    app = AppTest.from_file(APP, default_timeout=50).run()
    assert not app.exception
    assert any('#F3F4F8' in item.value for item in app.markdown)
    app.toggle(key='ui_dark').set_value(True).run()
    assert not app.exception
    assert any('#121419' in item.value for item in app.markdown)


def test_stylesheet_is_not_rendered_as_page_text():
    app = AppTest.from_file(APP, default_timeout=50).run()
    assert not app.exception
    sheets = [item.value for item in app.markdown if '<style>' in item.value]
    assert len(sheets) == 1 and all(line.strip() for line in sheets[0].splitlines())
    assert not any('data-testid=' in item.value for item in app.markdown if '<style>' not in item.value)


def test_dark_switch_lives_in_the_sidebar_and_sidebar_starts_open():
    app = AppTest.from_file(APP, default_timeout=50).run()
    assert [t.key for t in app.sidebar.toggle] == ['ui_dark']
    assert 'initial_sidebar_state="expanded"' in APP.read_text()


def test_no_old_terms_or_emoji_in_the_rendered_page():
    import re
    import streamlit as st
    st.cache_resource.clear()
    bad = re.compile('Nicht geklärt|nicht geklärt|Widerspruch(?!s)|[\U0001F300-\U0001FAFF\u2139\u2705\u274C]')
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')):
        app = AppTest.from_file(APP, default_timeout=50).run()
        next(button for button in app.button if button.key.startswith('story_')).click().run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception
        texts = []
        for page in ('Prüfverlauf', 'Ergebnisse & Vergleiche', 'So funktioniert’s', 'Faktencheck'):  # every page once
            texts += [item.value for item in list(app.markdown) + list(app.caption) + list(app.info)]
            goto(app, page)
            assert not app.exception
        assert any('verdict-card' in value for value in texts)
        assert not [value[:80] for value in texts if bad.search(value) and 'Zweitprüfung' not in value and 'Gegenargumente' not in value]  # methodology notes quote past experiments
        goto(app, 'Prüfverlauf')
        assert app.selectbox(key='history_label').options == ['Alle Ergebnisse', 'gestützt', 'nicht belegt', 'widersprochen']
    st.cache_resource.clear()


def test_an_old_history_filter_value_resets_instead_of_crashing():
    app = AppTest.from_file(APP, default_timeout=50)
    app.session_state['history_label'] = 'Widerspruch'
    goto(app.run(), 'Prüfverlauf')
    assert not app.exception
    assert app.selectbox(key='history_label').value == 'Alle Ergebnisse'


def test_a_multi_paragraph_quote_keeps_the_verdict_card_in_one_html_block():
    import streamlit as st
    from src.inference import PredictionResult, EvidenceSource
    st.cache_resource.clear()
    quote = 'Erster Absatz der Belegstelle.\n\nZweiter Absatz der Belegstelle.'
    result = PredictionResult(claim='Erster Absatz der Belegstelle.', label=0, label_name='Entailment', reasoning='x',
                              evidence=[quote], evidence_sources=[EvidenceSource(page_number=22, quote=quote)],
                              strategy='hybrid', booklet_path='', tokens_prompt=0, tokens_completion=0,
                              tokens_total=0, latency_ms=0)
    booklet = {'pages': [{'page_number': 22, 'text': quote}], 'paragraphs': [], 'full_text': quote}
    with patch('src.config.LLM_API_KEY', 'ui-test'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', return_value=result), \
         patch('src.inference.ClaimVerificationEngine._get_booklet_data', return_value=booklet), \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')):
        app = AppTest.from_file(APP, default_timeout=50).run()
        app.text_area(key='claim_input').set_value(result.claim).run()
        next(button for button in app.button if button.label == 'Behauptung prüfen').click().run()
        assert not app.exception
        card = next(item.value for item in app.markdown if 'class="verdict-card' in item.value)
        assert not re.search(r'\n[ \t]*\n', card) and 'Erster Absatz' in card and 'Zweiter Absatz' in card
    st.cache_resource.clear()


def test_the_legend_uses_icons_and_the_stylesheet_has_no_dead_verdict_rules():
    css = (APP.parent / 'src/ui_theme.css').read_text(encoding='utf-8')
    assert 'verdict-container' not in css and 'outcome-dot' not in css and 'icon-entailment' not in css
    app = AppTest.from_file(APP, default_timeout=50).run()
    legend = next(item.value for item in app.markdown if 'class="outcome-row"' in item.value)
    assert 'outcome-dot' not in legend
    for icon in ('fa-check-circle', 'fa-minus-circle', 'fa-times-circle'):
        assert icon in legend


def test_sidebar_has_navigation_language_and_dark_switch():
    app = AppTest.from_file(APP, default_timeout=50).run()
    assert not app.exception
    keyed = [node.key for node in app.sidebar if getattr(node, 'key', None) in ('nav', 'ui_language', 'ui_dark')]
    assert keyed == ['nav', 'ui_language', 'ui_dark']
    nav = app.sidebar.radio(key='nav')
    assert nav.options == ['Faktencheck', 'Prüfverlauf', 'Ergebnisse & Vergleiche', 'So funktioniert’s']
    assert nav.value == 'Faktencheck'
    assert any('Fact Attack' in item.value for item in app.sidebar.markdown)
    language = app.sidebar.selectbox(key='ui_language')
    assert language.label and 'Oberfläche' in language.help
    assert not app.tabs
    assert not any('topbar' in item.value for item in app.markdown)
    assert not any('Die Sprache der Oberfläche' in item.value for item in app.caption)
    app.selectbox(key='ui_language').set_value('fr').run()
    assert app.radio(key='nav').options == ['Vérifier', 'Historique', 'Résultats et comparaisons', 'Fonctionnement']
    assert app.radio(key='nav').value == 'Faktencheck'


def _save_checks(database, claims):
    import json
    import time
    from src.check_history import save_check, load_checks
    sample = json.loads((APP.parent / 'data/demo_cases.json').read_text())['cases'][0]
    for claim, label in claims:
        save_check(database, result={**sample['cached_result'], 'claim': claim, 'label': label},
                   source_path=APP.parent / 'data/booklets/2026-06-14_fr.pdf',
                   date='2026-06-14', language='fr', vote=None, notice='saved demo')
        time.sleep(0.01)  # distinct timestamps keep the order stable
    return load_checks(database)  # newest first


def _recent(app):
    return [button for button in app.sidebar.button if (button.key or '').startswith('recent_')]


def test_recent_checks_are_collapsed_and_open_the_saved_result(isolated_history):
    long_claim = 'Die Vorlage verändert das Rentenalter für alle Frauen und Männer in der ganzen Schweiz.'
    saved = _save_checks(isolated_history, [(long_claim, 2), ('Mietrecht alpha', 0)])
    newer, older = saved
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')) as remote:
        app = AppTest.from_file(APP, default_timeout=50).run()
        assert not app.exception
        recent = next(expander for expander in app.sidebar.expander if expander.label == 'Zuletzt geprüft')
        assert not recent.proto.expanded
        assert [button.key for button in _recent(app)] == [f"recent_{newer['id']}", f"recent_{older['id']}"]
        button = app.button(key=f"recent_{older['id']}")
        short, word = button.label.rsplit(' · ', 1)
        assert len(short) <= 42 and short.endswith('…') and long_claim.startswith(short[:-2])
        assert word == 'widersprochen'
        assert any('fa-times-circle' in item.value and 'tone-red' in item.value and 'aria-hidden="true"' in item.value
                   for item in app.sidebar.markdown)
        goto(app, 'Prüfverlauf')
        app.text_input(key='history_query').set_value('xyz').run()
        assert not any(widget.key == 'history_selected' for widget in app.selectbox)
        goto(app, 'Faktencheck')
        app.button(key=f"recent_{older['id']}").click().run()
        assert not app.exception
        assert app.radio(key='nav').value == 'Prüfverlauf'
        assert app.text_input(key='history_query').value == ''
        assert app.radio(key='history_view').value == 'Aktive Prüfungen'
        assert app.selectbox(key='history_selected').value == older['id']
        app.selectbox(key='ui_language').set_value('fr').run()
        assert app.button(key=f"recent_{older['id']}").label.endswith(' · contredite')
        remote.assert_not_called()


def test_recent_checks_show_the_five_newest_active_ones(isolated_history):
    from src.check_history import set_removed
    saved = _save_checks(isolated_history, [(f'Aussage {number}', 1) for number in range(7)])
    set_removed(isolated_history, saved[1]['id'])
    app = AppTest.from_file(APP, default_timeout=50).run()
    assert not app.exception
    assert [button.key for button in _recent(app)] == [f"recent_{record['id']}" for record in [saved[0], *saved[2:6]]]


def test_recent_section_is_absent_without_history(isolated_history):
    app = AppTest.from_file(APP, default_timeout=50).run()
    assert not app.exception
    assert not any(expander.label == 'Zuletzt geprüft' for expander in app.expander)
    assert not _recent(app)
