"""The fact-check tab on the agent: live steps, the question inside its step, the answer continuing the same check.

The sessions run over the fakes of test_agent_graph.py (index, engine, preset decider). `make_agent_session`,
`agent_votes` and `agent_available` of src.agent_app are patched: no index is loaded and no request leaves the machine.
"""
from contextlib import contextmanager
from pathlib import Path
import threading
from unittest.mock import patch

import pytest
pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest

from tests.test_agent_graph import (BOOKLETS, CLAIM, CLEAR, CLOSE, FRENCH_CLAIM, HAS_LANGGRAPH, NOT_FOUND, V1, V2,
                                    FakeEngine, FakeIndex, PresetDecider, act, make_prediction, search_passages,
                                    search_votes, v2_engine)

pytestmark = pytest.mark.skipif(not HAS_LANGGRAPH, reason='Install requirements-agent.txt')

APP = Path(__file__).parents[1] / 'app.py'
VOTES = {V1.vote_id: V1, V2.vote_id: V2}
VERIFY = 'Behauptung prüfen'


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    from src.check_history import save_check, load_checks, set_removed
    monkeypatch.delenv('FACT_ATTACK_AGENT', raising=False)
    database = tmp_path / 'history.sqlite3'
    with patch('src.check_history.save_check', side_effect=lambda path, **kwargs: save_check(database, **kwargs)), \
         patch('src.check_history.load_checks', side_effect=lambda path, **kwargs: load_checks(database, **kwargs)), \
         patch('src.check_history.set_removed', side_effect=lambda path, *args, **kwargs: set_removed(database, *args, **kwargs)):
        yield database


class Fakes:
    """Builds one AgentSession per check over the same fake index and engine; every session gets a fresh decider
    with the given actions."""

    def __init__(self, actions, index=None, engine=None):
        self.actions = list(actions)
        self.index = index or FakeIndex(CLEAR)
        self.engine = engine or FakeEngine()
        self.sessions = []  # (fixed_vote_id, session)
        self.answers = []  # every vote id given to a session's `answer`

    def make(self, fixed_vote_id, on_event):
        from src.agent.search_backend import LocalBackend
        from src.agent.session import AgentSession
        from src.agent.tools import AgentTools
        from src.agent.trace import Tracer
        tools = AgentTools(LocalBackend(self.index, self.engine, BOOKLETS), BOOKLETS, fixed_vote_id=fixed_vote_id)
        session = AgentSession(tools, PresetDecider(*self.actions), self.engine, Tracer(on_event=on_event),
                               nachsuche=False)
        answer = session.answer

        def counted_answer(vote_id):
            self.answers.append(vote_id)
            return answer(vote_id)

        session.answer = counted_answer
        self.sessions.append((fixed_vote_id, session))
        return session


def clear_fakes(**kwargs):
    return Fakes([search_votes(), search_passages(V1), act('pruefen', vorlage_id=V1.vote_id)], **kwargs)


def close_fakes(engine=None):
    """The guard asks back between V1 and V2; after the answer V2 is searched and checked."""
    return Fakes([search_votes(), search_passages(V1), search_passages(V2), act('pruefen', vorlage_id=V2.vote_id)],
                 index=FakeIndex(CLOSE), engine=engine or v2_engine())


@contextmanager
def agent_app(fakes, votes=VOTES):
    with patch('src.agent_app.agent_available', return_value=True), \
         patch('src.agent_app.make_agent_session', side_effect=fakes.make), \
         patch('src.agent_app.agent_votes', return_value=votes), \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')) as remote:
        yield AppTest.from_file(APP, default_timeout=50).run()
        remote.assert_not_called()


def check(app, claim=CLAIM):
    app.text_area(key='claim_input').set_value(claim).run()
    next(button for button in app.button if button.label == VERIFY).click().run()
    assert not app.exception
    return app


def options(app):
    return [button for button in app.button if (button.key or '').startswith('agent_option_')]


def markdown(app):
    return [item.value for item in app.markdown]


def has_verdict(app):
    return any('<div class="verdict-container' in value for value in markdown(app))


def step_texts(app):
    return [value for value in markdown(app) if value.startswith('<div class="agent-step ')]


def test_a_clear_claim_shows_the_steps_and_the_result_card():
    fakes = clear_fakes()
    with agent_app(fakes) as app:
        check(app)
        steps = '\n'.join(step_texts(app))
        for title in ('Vorlagen suchen', 'Passagen suchen', 'Prüfung starten', 'Belege prüfen'):
            assert title in steps
        assert 'Rückfrage' not in steps
        assert 'agent-step-running' not in steps  # the finished list has no running row left from the live one
        assert not options(app)
        assert has_verdict(app)
        assert app.session_state['last_check']['result'].label == 0
        assert any('class="conf-bar-fill"' in value for value in markdown(app))
        assert any(V1.title in item.value for item in app.caption)
        assert len(fakes.sessions) == 1 and fakes.sessions[0][0] is None


def test_a_close_claim_shows_the_question_with_one_button_per_option_in_the_ask_step():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        buttons = options(app)
        assert [button.label.split(' · ')[0] for button in buttons] == [V1.title, V2.title]
        assert not has_verdict(app) and 'last_check' not in app.session_state
        nodes = list(app.main)
        ask = next(index for index, node in enumerate(nodes)
                   if node.type == 'markdown' and node.value.startswith('<div class="agent-step agent-step-ask'))
        question = next(index for index, node in enumerate(nodes)
                        if node.type == 'markdown' and 'Welche Vorlage ist gemeint?' in node.value)
        first_option = next(index for index, node in enumerate(nodes)
                            if node.type == 'button' and (node.key or '').startswith('agent_option_'))
        assert ask < question < first_option
        assert not any(node.value.startswith('<div class="agent-step ') for node in nodes[first_option:] if node.type == 'markdown')


def test_clicking_an_option_continues_the_check_and_shows_the_result():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        next(button for button in options(app) if button.label.startswith(V2.title)).click().run()
        assert not app.exception
        assert fakes.answers == [V2.vote_id] and len(fakes.sessions) == 1
        steps = '\n'.join(step_texts(app))
        assert 'Vorlagen suchen' in steps and 'Rückfrage' in steps and 'Belege prüfen' in steps
        assert 'agent-step-running' not in steps
        assert not any('Welche Vorlage ist gemeint?' in value for value in markdown(app))
        assert not options(app)
        assert has_verdict(app)
        assert any(V2.title in item.value for item in app.caption)


def test_changing_the_claim_with_an_open_question_discards_it_and_starts_fresh():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        assert options(app)
        app.text_area(key='claim_input').set_value(CLAIM + ' Und mehr.').run()
        assert not app.exception
        assert not options(app) and not step_texts(app)
        assert 'agent_session' not in app.session_state
        next(button for button in app.button if button.label == VERIFY).click().run()
        assert len(fakes.sessions) == 2
        assert options(app)


def test_changing_the_ui_language_keeps_the_open_question_and_translates_the_buttons():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        keys = [button.key for button in options(app)]
        assert all('24. November 2024' in button.label for button in options(app))
        app.selectbox(key='ui_language').set_value('fr').run()
        assert not app.exception
        assert [button.key for button in options(app)] == keys
        assert all('24 novembre 2024' in button.label for button in options(app))
        assert any('Quel objet est visé ?' in value for value in markdown(app))
        assert any('Question' in value for value in step_texts(app))
        assert len(fakes.sessions) == 1
        next(button for button in options(app) if button.label.startswith(V2.title)).click().run()
        assert not app.exception and has_verdict(app)


def test_french_claim_without_source_asks_to_pick_the_source():
    fakes = clear_fakes()
    with agent_app(fakes) as app:
        check(app, FRENCH_CLAIM)
        assert any('nur für deutsche Behauptungen' in item.value for item in app.warning)
        assert not has_verdict(app) and 'last_check' not in app.session_state
        assert fakes.index.vote_calls == [] and fakes.engine.verify_calls == []


def pick_source(app, vote_title=V1.title):
    app.selectbox(key='ballot').set_value('24. November 2024').run()
    app.radio(key='booklet_lang').set_value('DE').run()
    app.selectbox(key='vote').set_value(vote_title).run()
    app.checkbox(key='agent_own_source').check().run()
    assert not app.exception
    return app


def test_picking_the_source_yourself_runs_with_the_fixed_vote_and_has_no_question_step():
    fakes = Fakes([search_passages(V1), act('pruefen', vorlage_id=V1.vote_id)], index=FakeIndex(CLOSE))
    with agent_app(fakes) as app:
        assert any(expander.label == 'Quelle selbst wählen' for expander in app.expander)
        pick_source(app)
        check(app)
        assert fakes.sessions[0][0] == V1.vote_id
        assert fakes.index.vote_calls == []
        assert not options(app)
        assert not any('Rückfrage' in value for value in step_texts(app))
        assert has_verdict(app)


def test_technical_error_is_red_in_its_step_and_there_is_no_verdict(isolated_history):
    from src.check_history import load_checks
    fakes = clear_fakes(engine=FakeEngine(verify_error=RuntimeError('Apertus nicht erreichbar')))
    with agent_app(fakes) as app:
        check(app)
        errors = [item.value for item in app.error]
        assert any('Urteil von Apertus' in value and 'Apertus nicht erreichbar' in value for value in errors)
        assert any('Die Prüfung konnte nicht abgeschlossen werden' in value for value in errors)
        assert not has_verdict(app) and 'last_check' not in app.session_state
        assert not load_checks(isolated_history)


def test_history_saves_verdicts_but_not_questions(isolated_history):
    from src.check_history import load_checks
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        assert options(app)
        assert not load_checks(isolated_history)
        next(button for button in options(app) if button.label.startswith(V2.title)).click().run()
        saved = load_checks(isolated_history)
        assert len(saved) == 1
        assert (saved[0]['vote'], saved[0]['date'], saved[0]['language']) == (V2.title, V2.date, V2.language)


def test_multi_check_and_history_tabs_work_as_before(isolated_history):
    import streamlit as st
    from src.check_history import load_checks
    from src.inference import PredictionResult

    def prediction(**kwargs):
        return PredictionResult(claim=kwargs['claim'], label=1, label_name='Neutral', reasoning='Injected UI test',
                                evidence=[], strategy=kwargs['strategy'], booklet_path=str(kwargs['booklet_pdf']),
                                tokens_prompt=0, tokens_completion=0, tokens_total=0, latency_ms=0)
    st.cache_resource.clear()
    fakes = clear_fakes()
    with patch('src.config.LLM_API_KEY', 'ui-test-placeholder'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', side_effect=prediction) as verify, \
         agent_app(fakes) as app:
        app.text_area(key='batch_text').set_value('Die Vorlage verändert das Rentenalter. La proposition change les impôts.').run()
        app.multiselect(key='batch_selected').set_value(app.multiselect(key='batch_selected').options).run()
        app.button(key='run_batch').click().run()
        assert not app.exception
        assert verify.call_count == 2 and not fakes.sessions
        assert len(load_checks(isolated_history)) == 2
        reopened = AppTest.from_file(APP, default_timeout=50).run()
        assert not reopened.exception
        assert len(reopened.selectbox(key='history_selected').options) == 2
    st.cache_resource.clear()


def test_without_the_agent_layer_the_tab_keeps_the_old_flow():
    import streamlit as st
    from src.inference import PredictionResult
    result = PredictionResult(claim=CLAIM, label=1, label_name='Neutral', reasoning='Injected', evidence=[],
                              strategy='hybrid', booklet_path='', tokens_prompt=0, tokens_completion=0,
                              tokens_total=0, latency_ms=0)
    st.cache_resource.clear()
    with patch('src.agent_app.agent_available', return_value=False), \
         patch('src.agent_app.make_agent_session', side_effect=AssertionError('no agent')) as make, \
         patch('src.config.LLM_API_KEY', 'ui-test'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', return_value=result) as verify, \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')):
        app = AppTest.from_file(APP, default_timeout=50).run()
        assert any('Agentenschicht ist nicht verfügbar' in item.value for item in app.caption)
        assert not any(expander.label == 'Quelle selbst wählen' for expander in app.expander)
        check(app)
        assert verify.call_count == 1 and not make.called
        assert app.session_state['last_check']['result'] == result
        assert not step_texts(app)
    st.cache_resource.clear()


def test_double_click_on_an_option_continues_once():
    """The first click's script run was stopped by the second click while its worker still runs the answer: the
    second run waits for that worker, shows its result and does not answer again."""
    entered, gate = threading.Event(), threading.Event()
    engine = v2_engine()
    verify = engine.verify_with_passages

    def slow_verify(*args, **kwargs):
        entered.set()
        gate.wait(10)
        return verify(*args, **kwargs)

    engine.verify_with_passages = slow_verify
    fakes = close_fakes(engine=engine)
    with agent_app(fakes) as app:
        check(app)
        running = app.session_state['agent_session']
        first = threading.Thread(target=lambda: list(running.run(lambda session: session.answer(V2.vote_id))))
        first.start()  # the first click's worker, whose script run is gone
        assert entered.wait(10)
        threading.Timer(0.3, gate.set).start()
        next(button for button in options(app) if button.label.startswith(V2.title)).click().run()
        first.join(10)
        assert not app.exception
        assert fakes.answers == [V2.vote_id]
        assert len(engine.verify_calls) == 1
        assert has_verdict(app)
        assert app.session_state['last_check']['result'].label == 0


def test_a_discarded_verdict_card_has_no_probability_bars():
    discarded = make_prediction(0, [(2, NOT_FOUND)]).model_copy(update={'numerical_conflict': 'Zahl passt nicht'})
    fakes = clear_fakes(engine=FakeEngine(prediction=discarded))
    with agent_app(fakes) as app:
        check(app)
        assert has_verdict(app)
        assert app.session_state['last_check']['result'].label == 1
        assert not any('class="conf-bar-fill"' in value for value in markdown(app))
        assert not any('Zahl passt nicht' in value for value in markdown(app))


def test_unknown_source_selection_shows_a_translated_message():
    fakes = clear_fakes()
    with agent_app(fakes) as app:
        app.checkbox(key='agent_own_source').check().run()  # the default source 2026-06-14_de is not in VOTES
        app.selectbox(key='ui_language').set_value('fr').run()
        app.text_area(key='claim_input').set_value(CLAIM).run()
        next(button for button in app.button if button.label == 'Vérifier l’affirmation').click().run()
        assert not app.exception
        assert any('L’agent ne peut pas vérifier la source choisie' in item.value for item in app.error)
        assert not any('#0' in item.value for item in app.error)
        assert not fakes.sessions
