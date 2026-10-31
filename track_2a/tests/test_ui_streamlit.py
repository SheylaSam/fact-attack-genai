"""Actual Streamlit widgets, with every remote completion explicitly forbidden."""
from pathlib import Path
from unittest.mock import patch
import pytest
pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
from src.ui_strategies import STANDARD,DENSE,FUSED

APP=Path(__file__).parents[1]/'app.py'


@pytest.fixture(autouse=True)
def isolated_history(tmp_path):
    from src.check_history import save_check, load_checks, set_removed
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
        assert len(app.selectbox(key='ballot').options) == 20
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
        reopened = AppTest.from_file(APP, default_timeout=50).run()
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
        app.button(key='history_remove_restore').click().run()
        assert not app.exception
        assert not load_checks(isolated_history)
        app.radio(key='history_view').set_value('Papierkorb').run()
        assert app.button(key='history_remove_restore').label == 'Wiederherstellen'
        app.button(key='history_remove_restore').click().run()
        assert not app.exception
        assert len(load_checks(isolated_history)) == 1
        restarted = AppTest.from_file(APP, default_timeout=50).run()
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
        assert any('Réponse de démonstration enregistrée' in info.value for info in app.info)
        assert any('Markiert' not in caption.value and 'surligné' in caption.value for caption in app.caption)
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
