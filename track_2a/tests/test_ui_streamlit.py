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
        app.checkbox(key='enable_vector_rag').check().run()
        app.radio(key='strategy_option').set_value(DENSE).run()
        next(button for button in app.button if button.label=='Behauptung prüfen').click().run()
        assert not app.exception
        assert any('Optionale Abhängigkeiten' in error.value for error in app.error)
        assert 'last_check' not in app.session_state
        remote.assert_not_called()
