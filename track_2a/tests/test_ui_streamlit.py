"""Actual Streamlit widgets, with every remote completion explicitly forbidden."""
from pathlib import Path
from unittest.mock import patch
import pytest
pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
from src.ui_strategies import STANDARD,DENSE,FUSED

APP=Path(__file__).parents[1]/'app.py'


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
