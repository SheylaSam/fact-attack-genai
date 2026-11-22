"""The fact-check tab on the agent: live steps, the question inside its step, the answer continuing the same check.

The sessions run over the fakes of test_agent_graph.py (index, engine, preset decider). `make_agent_session`,
`agent_votes` and `agent_available` of src.agent_app are patched: no index is loaded and no request leaves the machine.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest
pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest

from src import agent_ui
from src.agent import settings
from src.agent.source_index import VoteRef
from src.config import BOOKLETS_DIR
from tests.test_agent_graph import (BOOKLETS, CLAIM, CLEAR, CLOSE, FRENCH_CLAIM, HAS_LANGGRAPH, NOT_FOUND, V1, V2,
                                    FakeEngine, FakeIndex, PresetDecider, act, candidate, make_prediction,
                                    search_passages, search_votes, v2_engine)
from tests.test_agent_session import V3

pytestmark = pytest.mark.skipif(not HAS_LANGGRAPH, reason='Install requirements-agent.txt')

APP = Path(__file__).parents[1] / 'app.py'
VOTES = {V1.vote_id: V1, V2.vote_id: V2}
VERIFY = 'Behauptung prüfen'
UPLOADER = 'uploaded_pdf_0'  # the uploader's first key; removing the PDF moves on to uploaded_pdf_1


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
        self.deciders = []  # the decider of every session
        self.answers = []  # every vote id given to a session's `answer`
        self.starts = []  # every claim given to a session's `start`
        self.budgets = []  # the request budget of every session
        self.session_votes = []  # every session vote (an upload) given to `make`

    def make(self, fixed_vote_id, on_event, session_vote=None):
        from src.agent.budget import RequestBudget
        from src.agent.search_backend import LocalBackend
        from src.agent.session import AgentSession
        from src.agent.tools import AgentTools
        from src.agent.trace import Tracer
        from src.agent_app import catalog_sources
        backend = LocalBackend(self.index, self.engine, BOOKLETS, catalog=catalog_sources())  # like the app
        if session_vote is not None:  # like make_agent_session: the vote is registered and fixed
            self.session_votes.append(session_vote)
            backend.add_vote(session_vote)
            fixed_vote_id = session_vote.vote_id
        tools = AgentTools(backend, BOOKLETS, fixed_vote_id=fixed_vote_id)
        decider = PresetDecider(*self.actions)
        self.deciders.append(decider)
        budget = RequestBudget()  # like make_agent_session; the fakes send nothing, so nothing is counted
        self.budgets.append(budget)
        session = AgentSession(tools, decider, self.engine, Tracer(on_event=on_event), budget=budget,
                               nachsuche=False)
        answer, start = session.answer, session.start

        def counted_answer(vote_id):
            self.answers.append(vote_id)
            return answer(vote_id)

        def counted_start(claim, *args, **kwargs):
            self.starts.append(claim)
            return start(claim, *args, **kwargs)

        session.answer, session.start = counted_answer, counted_start
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


def goto(app, page):
    """Opens a page of the sidebar navigation."""
    return app.radio(key='nav').set_value(page).run()


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
    return any('class="verdict-card' in value for value in markdown(app))


def step_texts(app):
    return [value for value in markdown(app) if value.startswith('<div class="agent-step ')]


def test_a_clear_claim_shows_the_steps_and_the_result_card():
    fakes = clear_fakes()
    with agent_app(fakes) as app:
        check(app)
        steps = '\n'.join(step_texts(app))
        for kind in ('votes', 'passages', 'verify', 'evidence'):
            assert f'data-kind="{kind}"' in steps
        assert 'data-kind="ask"' not in steps
        assert 'agent-step-running' not in steps  # the finished list has no running row left from the live one
        assert not options(app)
        assert has_verdict(app)
        card = next(value for value in markdown(app) if 'class="verdict-card' in value)
        assert 'fa-check-circle' in card and 'Vom Büchlein gestützt' in card
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
                        if node.type == 'markdown' and node.value in (agent_ui.TEXT_QUESTION, agent_ui.TEXT_QUESTION_CHOOSE))
        first_option = next(index for index, node in enumerate(nodes)
                            if node.type == 'button' and (node.key or '').startswith('agent_option_'))
        assert ask < question < first_option
        assert not any('**' in node.value and agent_ui.TITLE_QUESTION in node.value
                       for node in nodes if node.type == 'markdown')  # the card says it; no bold repeat below
        assert not any(node.value.startswith('<div class="agent-step ') for node in nodes[first_option:] if node.type == 'markdown')


def test_clicking_an_option_continues_the_check_and_shows_the_result():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        next(button for button in options(app) if button.label.startswith(V2.title)).click().run()
        assert not app.exception
        assert fakes.answers == [V2.vote_id] and len(fakes.sessions) == 1
        steps = '\n'.join(step_texts(app))
        assert 'data-kind="votes"' in steps and 'Du hast gewählt' in steps and 'data-kind="evidence"' in steps
        assert 'agent-step-running' not in steps
        assert not any('Welche Vorlage ist gemeint?' in value for value in markdown(app))
        assert not options(app)
        assert has_verdict(app)
        assert any(V2.title in item.value for item in app.caption)


def no_vote_button(app):
    return [button for button in app.button if (button.key or '').startswith('agent_no_vote_')]


def test_the_question_offers_none_of_these_votes_after_the_options():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        [button] = no_vote_button(app)
        assert button.label == agent_ui.TEXT_NO_VOTE
        keys = [node.key for node in app.main if node.type == 'button']
        assert keys.index(button.key) > max(keys.index(option.key) for option in options(app))


def test_clicking_none_of_these_votes_ends_the_check_without_a_source_and_without_a_check():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        no_vote_button(app)[0].click().run()
        assert not app.exception
        assert fakes.answers == [settings.NO_VOTE_ANSWER] and len(fakes.sessions) == 1
        assert fakes.engine.verify_calls == []
        steps = '\n'.join(step_texts(app))
        assert agent_ui.TITLE_ANSWERED_NO_VOTE in steps
        assert not options(app) and not no_vote_button(app)
        assert not has_verdict(app)
        assert any(agent_ui.TITLE_NO_SOURCE in box.value for box in app.warning)
        # "none" is no vote: it never becomes the "wie zuletzt" button of the next question
        assert 'last_vote_id' not in app.session_state or app.session_state['last_vote_id'] != settings.NO_VOTE_ANSWER


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
        assert any('Quel objet est visé ?' in value for value in step_texts(app))
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


MORE = 'Weitere Möglichkeiten'
MORE_TABS = ['Quelle selbst wählen', 'Beispiel auswählen', 'Eigenes PDF & Suche', 'Mehrere Aussagen',
             'Sprache der Behauptung']
OLD_EXPANDERS = ('Keine eigene Behauptung? Beispiel auswählen', 'Quelle selbst wählen', 'Sprache der Behauptung',
                 'Weitere Optionen: Suche & eigenes PDF', 'Mehrere Aussagen aus einem Text prüfen')
LOCK_HINT = ('Behauptung und Textstellen gehen an Apertus (CSCS). Kein allgemeiner Wahrheitscheck, keine '
             'Abstimmungsempfehlung.')


def outside_expanders(node, inside=False):
    """Every node of the page under `node` with whether an expander encloses it."""
    for child in getattr(node, 'children', {}).values():
        yield child, inside
        yield from outside_expanders(child, inside or child.type == 'expander')


def more_options(app):
    return next(expander for expander in app.main.expander if expander.label == MORE)


def test_agent_page_is_one_column_with_one_more_options_expander():
    with agent_app(clear_fakes()) as app:
        nodes = list(outside_expanders(app.main))
        assert [node.label for node, inside in nodes if node.type == 'expander' and not inside] == [MORE]
        assert not [node for node, inside in nodes if node.type == 'column' and not inside]  # one column
        assert not any(expander.label in OLD_EXPANDERS for expander in app.expander)
        assert [tab.label for tab in more_options(app).tabs] == MORE_TABS
        keys = {getattr(node, 'key', None) for node, _ in nodes}
        for key in ('claim_input', 'ballot', 'vote', 'source_year', 'source_query', 'booklet_lang', 'agent_own_source',
                    'save_history', 'claim_lang', 'enable_vector_rag', 'strategy_option', UPLOADER, 'batch_text',
                    'batch_selected', 'run_batch'):
            assert key in keys, key
        assert any(key.startswith('story_') for key in keys if key)
        assert not more_options(app).proto.expanded
        hints = [value for value in markdown(app) if LOCK_HINT in value]
        assert len(hints) == 1 and '<i class="fas fa-lock tone-grey" aria-hidden="true"></i>' in hints[0]
        assert not any('Beim Prüfen werden' in item.value for item in app.caption)
        assert not any('Keine allgemeine Wahrheitsprüfung' in value for value in markdown(app))
        app.selectbox(key='ui_language').set_value('fr').run()
        [more] = [expander for expander in app.main.expander if expander.label == 'Autres possibilités']
        assert more.tabs[0].label == 'Choisir la source soi-même'
        assert not any(LOCK_HINT in value for value in markdown(app))
        assert any('fa-lock' in value for value in markdown(app))


AGENT_PLACEHOLDER = ('Hier erscheint dein Ergebnis mit der passenden Belegstelle. Tippe eine Behauptung ein und starte '
                     'die Prüfung; die Vorlage sucht der Agent selbst.')


def test_the_empty_result_in_agent_mode_does_not_ask_to_choose_a_source():
    with agent_app(clear_fakes()) as app:
        [placeholder] = [value for value in markdown(app) if 'class="result-placeholder"' in value]
        assert AGENT_PLACEHOLDER in placeholder and 'wähle die Quelle' not in placeholder
        assert not any('Weitere Optionen' in item.value for item in app.caption)  # the tab label says it
        app.selectbox(key='ui_language').set_value('fr').run()
        [placeholder] = [value for value in markdown(app) if 'class="result-placeholder"' in value]
        assert 'l’agent cherche lui-même l’objet.' in placeholder
        app.selectbox(key='ui_language').set_value('it').run()
        [placeholder] = [value for value in markdown(app) if 'class="result-placeholder"' in value]
        assert 'l’oggetto lo cerca l’agente.' in placeholder


def test_more_options_opens_itself_when_the_source_must_be_picked():
    fakes = clear_fakes()
    with agent_app(fakes) as app:
        assert not more_options(app).proto.expanded
        check(app, FRENCH_CLAIM)
        assert any('nur für deutsche Behauptungen' in item.value for item in app.warning)
        assert more_options(app).proto.expanded  # in the same click, not only after the next rerun
        assert more_options(app).tabs[0].label == 'Quelle selbst wählen'  # the first tab is the source


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
        assert more_options(app).tabs[0].label == 'Quelle selbst wählen'
        pick_source(app)
        check(app)
        assert fakes.sessions[0][0] == V1.vote_id
        assert fakes.index.vote_calls == []
        assert not options(app)
        assert not any('Rückfrage' in value for value in step_texts(app))
        assert has_verdict(app)


# --- the app's own wiring in mock mode: scripted decider, mock Apertus client, real engine ------------------------
RENT_PDF = BOOKLETS_DIR / '2024-11-24_de.pdf'
UNTERMIETE = VoteRef('2024-11-24_de#2', '2024-11-24_de', '2024-11-24', 'de',
                     'Änderung des Obligationenrechts (Mietrecht: Untermiete)', (6, 7, *range(24, 34)))
EIGENBEDARF = VoteRef('2024-11-24_de#3', '2024-11-24_de', '2024-11-24', 'de',
                      'Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs)',
                      (8, 9, *range(34, 44)))  # both as in the real index: no "#0" for this booklet
needs_rent_booklet = pytest.mark.skipif(not RENT_PDF.exists(), reason=f'Needs {RENT_PDF}')


def rent_index():
    """The two rent votes of the real index, on par for the guard."""
    return FakeIndex([candidate(UNTERMIETE, 0.873), candidate(EIGENBEDARF, 0.869)], votes=(UNTERMIETE, EIGENBEDARF))


@contextmanager
def mock_mode_app(index, trace_dir):
    """The app with `FACT_ATTACK_AGENT_MOCK=1` and the real `make_agent_session`; only the source index is `index`.
    No request leaves the machine."""
    with patch.dict('os.environ', {'FACT_ATTACK_AGENT_MOCK': '1'}), \
         patch('src.agent.settings.TRACE_DIR', trace_dir), \
         patch('src.agent_app.agent_available', return_value=True), \
         patch('src.agent_app.get_agent_index', return_value=index), \
         patch('src.agent_app.agent_votes', return_value=index.votes), \
         patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No API')) as remote:
        yield AppTest.from_file(APP, default_timeout=50).run()
        remote.assert_not_called()


@needs_rent_booklet
def test_clicking_an_option_in_mock_mode_ends_with_a_verdict_not_a_technical_error(tmp_path):
    index = rent_index()
    with mock_mode_app(index, tmp_path) as app:
        check(app)
        assert [button.label.split(' · ')[0] for button in options(app)] == [UNTERMIETE.title, EIGENBEDARF.title]
        next(button for button in options(app) if button.label.startswith(EIGENBEDARF.title)).click().run()
        assert not app.exception
        assert not [item.value for item in app.error]
        assert has_verdict(app)
        turn = app.session_state['agent_turn']
        assert (turn.vote, turn.error) == (EIGENBEDARF, None)
        steps = '\n'.join(step_texts(app))
        assert 'Du hast gewählt' in steps and 'Kein Zitat zu prüfen' in steps and 'agent-step-error' not in steps


@needs_rent_booklet
def test_picking_a_source_with_automatic_object_runs_the_agent_with_a_whole_booklet_vote(tmp_path):
    import fitz
    from src.agent.source_index import WHOLE_BOOKLET_TITLE
    with fitz.open(RENT_PDF) as document:
        pages = tuple(range(1, document.page_count + 1))
    index = rent_index()
    with mock_mode_app(index, tmp_path) as app:
        pick_source(app, 'Automatisch erkennen')
        check(app)
        assert not [item.value for item in app.error]  # no "Der Agent kann die gewählte Quelle nicht prüfen"
        assert has_verdict(app)
        turn = app.session_state['agent_turn']
        assert turn.vote == VoteRef('2024-11-24_de#0', '2024-11-24_de', '2024-11-24', 'de', WHOLE_BOOKLET_TITLE, pages)
        assert index.vote_calls == [] and index.vector_calls == []  # a given source; no index passages for "#0"
        assert '2024-11-24_de#0' not in index.votes  # registered in the check's own backend only
        assert not options(app) and not any('Rückfrage' in value for value in step_texts(app))


def test_technical_error_is_red_in_its_step_and_there_is_no_verdict(isolated_history):
    from src.check_history import load_checks
    fakes = clear_fakes(engine=FakeEngine(verify_error=RuntimeError('Apertus nicht erreichbar')))
    with agent_app(fakes) as app:
        check(app)
        errors = [item.value for item in app.error]
        assert any('agent-step-error' in value and 'fa-exclamation-triangle' in value and 'Apertus nicht erreichbar' in value
                   and 'role="alert"' in value and 'Schritt fehlgeschlagen' in value
                   for value in step_texts(app))  # the error card carries the escaped error text
        assert any('Die Prüfung konnte nicht abgeschlossen werden' in value for value in errors)
        assert not has_verdict(app) and 'last_check' not in app.session_state
        assert not load_checks(isolated_history)


def test_open_question_survives_a_page_switch():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        keys = [button.key for button in options(app)]
        assert keys
        goto(app, 'Prüfverlauf')
        assert not app.exception and not options(app)
        goto(app, 'Faktencheck')
        assert not app.exception
        assert app.text_area(key='claim_input').value == CLAIM
        assert [button.key for button in options(app)] == keys
        next(button for button in options(app) if button.label.startswith(V2.title)).click().run()
        assert not app.exception
        assert fakes.answers == [V2.vote_id] and len(fakes.sessions) == 1
        assert has_verdict(app)


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
        app.checkbox(key='agent_own_source').check().run()  # in agent mode the multi-check needs a given source
        app.text_area(key='batch_text').set_value('Die Vorlage verändert das Rentenalter. La proposition change les impôts.').run()
        app.multiselect(key='batch_selected').set_value(app.multiselect(key='batch_selected').options).run()
        app.button(key='run_batch').click().run()
        assert not app.exception
        assert verify.call_count == 2 and not fakes.sessions
        assert len(load_checks(isolated_history)) == 2
        reopened = goto(AppTest.from_file(APP, default_timeout=50).run(), 'Prüfverlauf')
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
        assert not any(expander.label in ('Quelle selbst wählen', MORE) for expander in app.expander)
        assert not app.tabs and len(app.main.columns) >= 2  # the two columns with the required source picker
        assert any('Quelle wählen' in value for value in markdown(app))
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
        # a vote of the booklet that the index (VOTES) does not hold: every catalog booklet is checkable as a whole,
        # but a chosen title that names none of the index's votes of an indexed booklet stays unknown
        pick_source(app, 'Bundesbeschluss über den Ausbauschritt 2023 für die Nationalstrassen')
        app.selectbox(key='ui_language').set_value('fr').run()
        app.text_area(key='claim_input').set_value(CLAIM).run()
        next(button for button in app.button if button.label == 'Vérifier l’affirmation').click().run()
        assert not app.exception
        assert any('L’agent ne peut pas vérifier la source choisie' in item.value for item in app.error)
        assert not any('#0' in item.value for item in app.error)
        assert not fakes.sessions


@contextmanager
def uploaded_pdf(contents):
    """The file uploader holds `contents` as the user's PDF. The real `store_uploaded_source` writes it to the booklets
    directory (custom_*.pdf is git-ignored); it is removed afterwards."""
    import hashlib
    from types import SimpleNamespace
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy
    from src import config
    upload = SimpleNamespace(name='mein-dokument.pdf', getbuffer=lambda: memoryview(contents))
    stored = config.BOOKLETS_DIR / f'custom_{hashlib.sha256(contents).hexdigest()}.pdf'
    original_get = SessionStateProxy.get

    def session_get(self, key, default=None):
        return upload if key == UPLOADER else original_get(self, key, default)

    try:
        with patch.object(SessionStateProxy, 'get', session_get):
            yield stored
    finally:
        stored.unlink(missing_ok=True)


@contextmanager
def switchable_upload(contents):
    """Like `uploaded_pdf`, but `held['upload'] = None` empties the uploader, as Streamlit does with the uploader of a
    page that a page switch left undrawn."""
    import hashlib
    from types import SimpleNamespace
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy
    from src import config
    held = {'upload': SimpleNamespace(name='mein-dokument.pdf', getbuffer=lambda: memoryview(contents))}
    stored = config.BOOKLETS_DIR / f'custom_{hashlib.sha256(contents).hexdigest()}.pdf'
    original_get = SessionStateProxy.get

    def session_get(self, key, default=None):
        if key == UPLOADER and held['upload'] is not None:
            return held['upload']
        return original_get(self, key, default)

    try:
        with patch.object(SessionStateProxy, 'get', session_get):
            yield held
    finally:
        stored.unlink(missing_ok=True)


def tiny_pdf(pages=3):
    import fitz
    with fitz.open() as document:
        for number in range(1, pages + 1):
            document.new_page().insert_text((72, 72), f'Eigenes Dokument {pages}-{number}')
        return document.tobytes()


def test_uploaded_pdf_check_has_the_same_steps_and_card(isolated_history):
    from src.check_history import load_checks
    import hashlib
    contents = tiny_pdf()
    upload_id = f'custom_{hashlib.sha256(contents).hexdigest()}#0'  # the vote the app derives from the stored file
    fakes = Fakes([act('suche_passagen', vorlage_id=upload_id), act('pruefen', vorlage_id=upload_id)],
                  index=FakeIndex(CLOSE))
    booklet = {'pages': [{'page_number': number, 'text': f'Seite {number}'} for number in (1, 2, 3)],
               'paragraphs': [], 'full_text': ''}
    with uploaded_pdf(contents) as stored, \
         patch('src.inference.ClaimVerificationEngine._get_booklet_data', return_value=booklet), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', side_effect=AssertionError('old flow')), \
         agent_app(fakes) as app:
        assert not any('ohne Agenten' in item.value for item in app.caption)
        assert not any(item.key == 'agent_own_source' for item in app.checkbox)  # the upload is the source
        app.radio(key='booklet_lang').set_value('FR').run()
        check(app)
        [upload] = fakes.session_votes
        assert upload.vote_id == f'{stored.stem}#0' and (upload.title, upload.language) == ('Eigenes PDF', 'fr')
        assert upload.pages == (1, 2, 3)
        assert fakes.sessions[0][0] == upload.vote_id
        assert fakes.index.vote_calls == []
        steps = '\n'.join(step_texts(app))
        for title in ('data-kind="passages"', 'data-kind="verify"', 'data-kind="evidence"'):
            assert title in steps
        assert 'Rückfrage' not in steps and not options(app)
        assert has_verdict(app)
        assert any('Eigenes PDF' in item.value for item in app.caption)
        last = app.session_state['last_check']
        assert last['source'][0] == stored and last['source'][1] is None and last['source'][2] == 'fr'
        assert [(row['date'], row['language']) for row in load_checks(isolated_history)] == [(None, 'fr')]
        assert not any(item.value.startswith('Die Prüfung konnte nicht') for item in app.error)


def test_an_agent_check_on_an_upload_survives_a_page_switch_until_the_pdf_is_removed():
    import hashlib
    contents = tiny_pdf()
    upload_id = f'custom_{hashlib.sha256(contents).hexdigest()}#0'
    fakes = Fakes([act('suche_passagen', vorlage_id=upload_id), act('pruefen', vorlage_id=upload_id)],
                  index=FakeIndex(CLOSE))
    booklet = {'pages': [{'page_number': number, 'text': f'Seite {number}'} for number in (1, 2, 3)],
               'paragraphs': [], 'full_text': ''}
    with switchable_upload(contents) as held, \
         patch('src.inference.ClaimVerificationEngine._get_booklet_data', return_value=booklet), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', side_effect=AssertionError('old flow')), \
         agent_app(fakes) as app:
        check(app)
        assert has_verdict(app)
        held['upload'] = None  # Streamlit empties the uploader of a page that was not drawn
        goto(goto(app, 'Prüfverlauf'), 'Faktencheck')
        assert not app.exception
        assert has_verdict(app) and step_texts(app)
        assert any('Aktive Quelle: eigenes PDF' in item.value for item in app.info)
        assert len(fakes.sessions) == 1 and 'agent_session' in app.session_state
        app.button(key='remove_upload').click().run()
        assert not app.exception
        assert 'agent_session' not in app.session_state and not has_verdict(app)
        assert not any('Aktive Quelle: eigenes PDF' in item.value for item in app.info)


def test_unreadable_upload_shows_a_translated_message_and_starts_no_check():
    fakes = clear_fakes()
    with uploaded_pdf(b'This is not a PDF.') as stored, agent_app(fakes) as app:
        app.selectbox(key='ui_language').set_value('fr').run()
        app.text_area(key='claim_input').set_value(CLAIM).run()
        next(button for button in app.button if button.label == 'Vérifier l’affirmation').click().run()
        assert not app.exception
        assert any('Ce PDF ne peut pas être lu' in item.value for item in app.error)
        assert not any('custom_' in item.value or 'Traceback' in item.value for item in app.error)
        assert not fakes.sessions and not has_verdict(app)


# --- session memory: the last chosen vote -------------------------------------------------------------------------
LAST = 'wie zuletzt'


def last_buttons(app):
    return [button for button in options(app) if LAST in button.label]


def second_check(app):
    next(button for button in app.button if button.label == VERIFY).click().run()
    assert not app.exception
    return app


def v3_fakes():
    """The guard asks between V1 and V2 for CLAIM; V3 is a vote of the index that is none of the options."""
    return Fakes([search_votes(), search_passages(V1), search_passages(V3), act('pruefen', vorlage_id=V3.vote_id)],
                 index=FakeIndex(CLOSE, votes=(V1, V2, V3)), engine=v2_engine())


def test_no_last_vote_without_a_previous_answer():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        assert len(options(app)) == 2 and not last_buttons(app)
        assert 'last_vote_id' not in app.session_state  # an open question is no answer
        app.text_area(key='claim_input').set_value(CLAIM + ' Und mehr.').run()  # dropped unanswered ...
        second_check(app)
        assert len(options(app)) == 2 and not last_buttons(app)  # ... leaves no memory
        assert 'Zuletzt gewählte' not in fakes.deciders[-1].calls[0]['messages'][1]['content']
    clear = clear_fakes()
    with agent_app(clear) as app:
        check(app)
        assert has_verdict(app)
        assert 'last_vote_id' not in app.session_state  # a check without a question chose nothing


def test_second_question_offers_the_last_chosen_vote_as_extra_button():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        next(button for button in options(app) if button.label.startswith(V2.title)).click().run()
        assert app.session_state['last_vote_id'] == V2.vote_id  # written after the answer
        second_check(app)
        buttons = options(app)
        assert [button.label.split(' · ')[0] for button in buttons] == [V1.title, f'{LAST}: {V2.title}']
        assert len(last_buttons(app)) == 1  # V2 is an option: its button carries the mark, no duplicate
        assert '24. November 2024' in last_buttons(app)[0].label
        app.selectbox(key='ui_language').set_value('fr').run()
        assert not app.exception and len(last_buttons(app)) == 0
        assert [button.label.split(' · ')[0] for button in options(app)][1].startswith('comme la dernière fois')
        assert fakes.answers == [V2.vote_id]  # nothing was answered for the second question


def test_the_extra_button_for_a_vote_outside_the_options_answers_the_check():
    fakes = v3_fakes()
    votes = {**VOTES, V3.vote_id: V3}
    with agent_app(fakes, votes=votes) as app:
        app.session_state['last_vote_id'] = V3.vote_id
        check(app)
        buttons = options(app)
        assert [button.label.split(' · ')[0] for button in buttons] == [V1.title, V2.title, f'{LAST}: {V3.title}']
        assert len(last_buttons(app)) == 1
        last_buttons(app)[0].click().run()
        assert not app.exception
        assert fakes.answers == [V3.vote_id]
        assert has_verdict(app) and any(V3.title in item.value for item in app.caption)
        assert app.session_state['last_vote_id'] == V3.vote_id


def test_last_vote_is_never_chosen_without_a_click():
    fakes = v3_fakes()
    votes = {**VOTES, V3.vote_id: V3}
    with agent_app(fakes, votes=votes) as app:
        app.session_state['last_vote_id'] = V3.vote_id
        check(app)
        session = fakes.sessions[-1][1]
        assert session._tools.fixed_vote_id is None  # the hint did not fix the vote
        assert session.pending is not None and fakes.answers == []
        assert not has_verdict(app) and 'last_check' not in app.session_state
        assert fakes.deciders[-1].calls[0]['messages'][1]['content'].endswith(
            f'Zuletzt gewählte Vorlage der Person: «{V3.title}» ({V3.vote_id}).')
        assert app.session_state['last_vote_id'] == V3.vote_id  # unchanged until the person answers


def test_an_unknown_last_vote_in_the_session_is_dropped_without_an_error():
    fakes = close_fakes()
    with agent_app(fakes) as app:
        app.session_state['last_vote_id'] = 'upload-0#0'  # not in the index (e.g. an own upload of an earlier check)
        check(app)
        assert len(options(app)) == 2 and not last_buttons(app)


# --- final fix wave of stage 4 ------------------------------------------------------------------------------------
DEMO_CASES = json.loads((APP.parent / 'data' / 'demo_cases.json').read_text(encoding='utf-8'))['cases']
JUNE_VOTES = (  # the real index's votes of the 2026-06-14 booklet: German only, like every vote of the index
    VoteRef('2026-06-14_de#1', '2026-06-14_de', '2026-06-14', 'de',
            'Volksinitiative «Keine 10-Millionen-Schweiz! (Nachhaltigkeitsinitiative)»', (4, 5, *range(8, 24))),
    VoteRef('2026-06-14_de#2', '2026-06-14_de', '2026-06-14', 'de', 'Änderung des Zivildienstgesetzes',
            (6, 7, *range(24, 40))))
FR_RENT_PDF = BOOKLETS_DIR / '2024-11-24_fr.pdf'
FR_SUBLET = 'Modification du code des obligations (droit du bail : sous-location)'
MOCK_NOTICE = 'Testmodus, kein Apertus-Urteil'
BATCH_HINT = 'Für die Mehrfachprüfung'
BATCH_TEXT = 'Die Vorlage verändert das Rentenalter. La proposition change les impôts.'


def verify(app):
    next(button for button in app.button if button.label == VERIFY).click().run()
    assert not app.exception
    return app


@pytest.mark.parametrize('case', DEMO_CASES,
                         ids=[f"{case['id']}-{case['claim_language']}-{case['booklet_language']}" for case in DEMO_CASES])
def test_every_demo_case_is_checked_against_its_own_booklet(case, tmp_path):
    """C1: an example's booklet is the given source in every claim/booklet language; FR and IT booklets, which the
    German-only index does not know, are checked as a whole. No dead end, no other booklet, no request."""
    source_id = f"{case['booklet_date']}_{case['booklet_language']}"
    if not (BOOKLETS_DIR / f'{source_id}.pdf').exists():
        pytest.skip(f'Needs {source_id}.pdf')
    index = FakeIndex([], votes=JUNE_VOTES)
    with mock_mode_app(index, tmp_path) as app:
        app.button(key=f"story_{case['id']}").click().run()
        assert app.checkbox(key='agent_own_source').value  # set by the example, not by hand
        verify(app)
        assert not [item.value for item in app.error]  # never "Der Agent kann die gewählte Quelle nicht prüfen"
        assert not any('Quelle selbst wählen' in item.value for item in app.warning)
        assert has_verdict(app)
        turn = app.session_state['agent_turn']
        assert turn.vote.source_id == source_id
        if case['booklet_language'] == 'de':
            assert turn.vote.title == case['vote']  # the index's own vote
        else:
            assert turn.vote.vote_id == f'{source_id}#0'
        assert index.vote_calls == []


@pytest.mark.skipif(not FR_RENT_PDF.exists(), reason=f'Needs {FR_RENT_PDF}')
def test_a_french_booklet_chosen_by_hand_is_checked_as_a_whole_with_a_translated_title(tmp_path):
    """C1 and I5: a French booklet with one of its votes, chosen by hand, runs as the whole booklet; the card names
    it «Gesamtes Büchlein» in the UI language."""
    import fitz
    from src.agent.source_index import WHOLE_BOOKLET_TITLE
    with fitz.open(FR_RENT_PDF) as document:
        pages = tuple(range(1, document.page_count + 1))
    index = rent_index()
    with mock_mode_app(index, tmp_path) as app:
        app.selectbox(key='ballot').set_value('24. November 2024').run()
        app.radio(key='booklet_lang').set_value('FR').run()
        app.selectbox(key='vote').set_value(FR_SUBLET).run()
        app.checkbox(key='agent_own_source').check().run()
        check(app)
        assert not [item.value for item in app.error]
        assert has_verdict(app)
        turn = app.session_state['agent_turn']
        assert turn.vote == VoteRef('2024-11-24_fr#0', '2024-11-24_fr', '2024-11-24', 'fr', WHOLE_BOOKLET_TITLE, pages)
        assert '2024-11-24_fr#0' not in index.votes and index.vote_calls == []
        assert any(f'Geprüfte Vorlage: {WHOLE_BOOKLET_TITLE}' in item.value for item in app.caption)
        app.selectbox(key='ui_language').set_value('fr').run()
        assert not app.exception and has_verdict(app)
        assert any('Objet vérifié : Brochure entière' in item.value for item in app.caption)


def test_agent_available_needs_an_api_key_unless_mock_mode(tmp_path, monkeypatch):
    """C1: without a key the tab keeps the old flow and its saved demo results; mock mode needs no key."""
    from src import agent_app as module
    monkeypatch.delenv('FACT_ATTACK_AGENT_MOCK', raising=False)
    with patch('src.agent.settings.INDEX_DIR', tmp_path):
        with patch('src.config.LLM_API_KEY', None):
            assert module.agent_available() is False
            with patch.dict('os.environ', {'FACT_ATTACK_AGENT_MOCK': '1'}):
                assert module.agent_available() is True
        with patch('src.config.LLM_API_KEY', ''):
            assert module.agent_available() is False
        with patch('src.config.LLM_API_KEY', 'ui-test-placeholder'):
            assert module.agent_available() is True


def slow_answer_fakes():
    """A question between V1 and V2 whose answer blocks in the engine until `gate` is set."""
    entered, gate = threading.Event(), threading.Event()
    engine = v2_engine()
    verify_with_passages = engine.verify_with_passages

    def slow_verify(*args, **kwargs):
        entered.set()
        gate.wait(10)
        return verify_with_passages(*args, **kwargs)

    engine.verify_with_passages = slow_verify
    return close_fakes(engine=engine), entered, gate


def test_a_second_click_on_verify_while_the_check_runs_adopts_it():
    """I1: the first click's worker still runs (its script run was stopped by the second click); the second click
    with the same input waits for it instead of starting a second check."""
    fakes, entered, gate = slow_answer_fakes()
    with agent_app(fakes) as app:
        check(app)
        running = app.session_state['agent_session']
        first = threading.Thread(target=lambda: list(running.run(lambda session: session.answer(V2.vote_id))))
        first.start()
        assert entered.wait(10)
        threading.Timer(0.3, gate.set).start()
        verify(app)
        first.join(10)
        assert fakes.starts == [CLAIM] and len(fakes.sessions) == 1
        assert app.session_state['agent_session'] is running
        assert not fakes.budgets[0].exceeded
        assert has_verdict(app)


def test_changing_the_claim_while_a_check_runs_cancels_the_dropped_checks_budget():
    """I1: the dropped check's worker keeps running, but its next request is refused."""
    from src.agent.budget import BudgetExceeded
    fakes, entered, gate = slow_answer_fakes()
    with agent_app(fakes) as app:
        check(app)
        running = app.session_state['agent_session']
        worker = threading.Thread(target=lambda: list(running.run(lambda session: session.answer(V2.vote_id))))
        worker.start()
        assert entered.wait(10)
        try:
            app.text_area(key='claim_input').set_value(CLAIM + ' Und mehr.').run()
            assert not app.exception
            assert 'agent_session' not in app.session_state
            with pytest.raises(BudgetExceeded):
                fakes.budgets[0].reserve(b'')
        finally:
            gate.set()
            worker.join(10)
        assert len(fakes.sessions) == 1


@needs_rent_booklet
def test_mock_mode_marks_the_verdict_as_test_mode_and_saves_nothing(tmp_path, isolated_history):
    """I2: a mock verdict carries the test-mode notice, is not saved and its report is not «live»."""
    from src import check_report
    from src.check_history import load_checks
    records = []
    render = check_report.render_check_report

    def recording(given, *args, **kwargs):
        records.extend(given)
        return render(given, *args, **kwargs)

    with patch('src.check_report.render_check_report', side_effect=recording), \
         mock_mode_app(rent_index(), tmp_path) as app:
        pick_source(app, 'Automatisch erkennen')
        check(app)
        assert has_verdict(app)
        last = app.session_state['last_check']
        assert last['notice'] == MOCK_NOTICE
        assert any('notice-box' in value and MOCK_NOTICE in value for value in markdown(app))
        assert not load_checks(isolated_history)
        report = [record for record in records if record.get('source_file') == '2024-11-24_de.pdf']
        assert report and {record['origin'] for record in report} == {'mock'}
        app.selectbox(key='ui_language').set_value('fr').run()
        assert any('Mode test, pas de verdict d’Apertus' in value for value in markdown(app))


def test_multi_check_in_agent_mode_needs_a_given_source():
    """I3: without a given source the multi-check button is off and says why; the old flow is unchanged."""
    with agent_app(clear_fakes()) as app:
        app.text_area(key='batch_text').set_value(BATCH_TEXT).run()
        app.multiselect(key='batch_selected').set_value(app.multiselect(key='batch_selected').options).run()
        assert app.button(key='run_batch').disabled
        assert any(BATCH_HINT in item.value for item in app.caption)
        app.checkbox(key='agent_own_source').check().run()
        assert not app.button(key='run_batch').disabled
        assert not any(BATCH_HINT in item.value for item in app.caption)


def test_the_request_to_pick_the_source_ticks_the_box_so_a_chosen_booklet_runs():
    """I4: after «Quelle selbst wählen» the box is already ticked; picking a booklet and clicking runs the check."""
    fakes = Fakes([search_passages(V1), act('pruefen', vorlage_id=V1.vote_id)], index=FakeIndex(CLOSE))
    with agent_app(fakes) as app:
        check(app, FRENCH_CLAIM)
        assert any('nur für deutsche Behauptungen' in item.value for item in app.warning)
        assert app.checkbox(key='agent_own_source').value
        assert more_options(app).proto.expanded
        app.selectbox(key='ballot').set_value('24. November 2024').run()
        app.radio(key='booklet_lang').set_value('DE').run()
        app.selectbox(key='vote').set_value(V1.title).run()
        verify(app)
        assert fakes.sessions[-1][0] == V1.vote_id and len(fakes.sessions) == 2
        assert not any('nur für deutsche Behauptungen' in item.value for item in app.warning)
        assert has_verdict(app)


def test_run_texts_in_error_boxes_are_not_rendered_as_markdown():
    """M1: an exception text with Markdown is shown as text."""
    raw = '**fett** [Link](https://example.com) `code` $x$'
    fakes = clear_fakes(engine=FakeEngine(verify_error=RuntimeError(raw)))
    with agent_app(fakes) as app:
        check(app)
        errors = [item.value for item in app.error]
        assert any(r'\*\*fett\*\*' in value for value in errors)
        for value in errors:
            assert '**fett**' not in value and '](https' not in value and '$x$' not in value


def test_the_mismatch_question_does_not_say_that_several_votes_fit():
    """M4: the claim clearly fits V1, the action names V2: the question is neutral."""
    fakes = Fakes([search_votes(), search_passages(V2)], index=FakeIndex(CLEAR))
    with agent_app(fakes) as app:
        check(app)
        assert [button.label.split(' · ')[0] for button in options(app)] == [V1.title, V2.title]
        assert not any('Mehrere Vorlagen passen' in value for value in markdown(app))
        assert agent_ui.TEXT_QUESTION_CHOOSE in markdown(app)
        assert not any(value.startswith('**') and 'Welche Vorlage ist gemeint?' in value for value in markdown(app))


# --- stage 5: statements of a compound claim ----------------------------------------------------------------------
PARTS = ['Die Untermiete braucht eine schriftliche Zustimmung.', 'Die Kündigung wegen Eigenbedarfs wird leichter.']


class SessionFakes(Fakes):
    """Like Fakes, but session n gets the n-th action list (the last one repeats)."""

    def __init__(self, *action_lists, **kwargs):
        super().__init__(action_lists[0], **kwargs)
        self.action_lists = list(action_lists)

    def make(self, fixed_vote_id, on_event, session_vote=None):
        self.actions = list(self.action_lists[min(len(self.sessions), len(self.action_lists) - 1)])
        return super().make(fixed_vote_id, on_event, session_vote)


def statement_question(parts=PARTS):
    return act('teilaussage_fragen', frage='Welche Teilaussage soll ich prüfen?', teilaussagen=list(parts))


def statements(app):
    return [button for button in app.button if (button.key or '').startswith('agent_statement_')]


def test_clicking_a_statement_starts_a_new_check_with_it_as_the_claim():
    from tests.test_agent_graph import COMPOUND
    fakes = SessionFakes([search_votes(), statement_question()],
                         [search_votes(), search_passages(V1), act('pruefen', vorlage_id=V1.vote_id)])
    with agent_app(fakes) as app:
        check(app, COMPOUND)
        buttons = statements(app)
        assert [button.label.replace('\\', '') for button in buttons] == PARTS  # escaped, see the next test
        assert len({button.key for button in buttons}) == 2
        assert not options(app) and not has_verdict(app) and 'last_check' not in app.session_state
        assert agent_ui.TEXT_STATEMENT in markdown(app)  # under the card: the text only, no bold title repeat
        assert not any(value.startswith('**') and agent_ui.TITLE_STATEMENT in value for value in markdown(app))
        assert agent_ui.TITLE_STATEMENT in '\n'.join(step_texts(app))
        first_check = app.session_state['agent_session']
        buttons[1].click().run()
        assert not app.exception
        assert fakes.starts == [COMPOUND, PARTS[1]]  # a new check with the statement as its claim ...
        assert fakes.answers == []  # ... never an answer to the old one
        assert len(fakes.sessions) == 2 and fakes.sessions[1][1] is not fakes.sessions[0][1]
        assert fakes.sessions[1][1]._tools is not fakes.sessions[0][1]._tools  # new tools, new budget
        assert fakes.budgets[1] is not fakes.budgets[0] and fakes.budgets[0].cancelled
        assert fakes.sessions[1][0] is None  # the agent searches the vote itself, as for a typed claim
        assert app.session_state['agent_session'] is not first_check
        assert app.text_area(key='claim_input').value == PARTS[1]  # the input shows what is checked now
        assert fakes.deciders[1].calls[0]['messages'][1]['content'] == PARTS[1]
        assert not statements(app)
        assert has_verdict(app)
        assert app.session_state['last_check']['result'].label == 0
        app.run()  # the next rerun keeps the new check: its signature is the statement's
        assert not app.exception and len(fakes.sessions) == 2 and has_verdict(app)


def test_statements_are_escaped():
    raw = ['**fett** <b>Tag</b> [Link](https://example.com) $x$', 'Zweite Aussage mit `code` und _kursiv_.']
    fakes = SessionFakes([statement_question(raw)])
    with agent_app(fakes) as app:
        check(app, 'Die Vorlage ist **fett** und <b>Tag</b>.')
        labels = [button.label for button in statements(app)]
        assert len(labels) == 2
        assert r'\*\*fett\*\*' in labels[0] and r'\<b\>Tag\</b\>' in labels[0]
        for label in labels:  # shown as text: no Markdown, HTML or LaTeX of the model reaches the page
            assert '**fett**' not in label and '](https' not in label and '$x$' not in label
            assert '`code`' not in label and '<b>' not in label
        assert not any('<b>Tag</b>' in value for value in markdown(app))
        statements(app)[0].click().run()
        assert not app.exception
        assert fakes.starts[-1] == raw[0]  # the claim of the new check is the statement as written, not escaped


def batch_prediction(**kwargs):
    from src.inference import PredictionResult
    return PredictionResult(claim=kwargs['claim'], label=1, label_name='Neutral', reasoning='Injected UI test',
                            evidence=[], strategy=kwargs['strategy'], booklet_path=str(kwargs['booklet_pdf']),
                            tokens_prompt=0, tokens_completion=0, tokens_total=0, latency_ms=0)


def test_a_multi_check_drops_the_agent_check_above_it():
    """M7: the steps of an earlier agent check do not stay above the multi-check's results."""
    import streamlit as st
    st.cache_resource.clear()
    fakes = Fakes([search_passages(V1), act('pruefen', vorlage_id=V1.vote_id)], index=FakeIndex(CLOSE))
    with patch('src.config.LLM_API_KEY', 'ui-test-placeholder'), patch('src.config.MOCK_APERTUS', False), \
         patch('src.inference.ClaimVerificationEngine.verify_claim', side_effect=batch_prediction), \
         agent_app(fakes) as app:
        pick_source(app)
        check(app)
        assert step_texts(app) and has_verdict(app)
        app.text_area(key='batch_text').set_value(BATCH_TEXT).run()
        app.multiselect(key='batch_selected').set_value(app.multiselect(key='batch_selected').options).run()
        app.button(key='run_batch').click().run()
        assert not app.exception
        assert 'agent_session' not in app.session_state
        assert not step_texts(app)
        assert any('Ergebnisse der Mehrfachprüfung' in value for value in markdown(app))
    st.cache_resource.clear()


BLOCK_AGENT_DEPENDENCIES = '''
import sys
class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("langgraph", "httpx"):
            raise ImportError(f"blocked for this test: {name}")
        return None
sys.meta_path.insert(0, Blocked())
'''


def test_the_app_and_its_agent_modules_load_without_langgraph_and_httpx(tmp_path):
    """M9 (Ruling A): with langgraph and httpx unimportable the app keeps the old flow; nothing is patched in the
    app's modules, the import itself is blocked in a fresh interpreter."""
    script = BLOCK_AGENT_DEPENDENCIES + '''
from pathlib import Path
from src import agent_app, agent_ui
from src.agent import settings
settings.INDEX_DIR = Path(sys.argv[2])  # an index directory exists: only the missing packages turn the agent off
assert agent_app.agent_available() is False
from streamlit.testing.v1 import AppTest
app = AppTest.from_file(sys.argv[1], default_timeout=50).run()
assert not app.exception, [item.value for item in app.exception]
assert any("Agentenschicht ist nicht verfügbar" in item.value for item in app.caption)
assert not any(name.split(".")[0] in ("langgraph", "httpx") for name in sys.modules)
print("loaded without the agent layer")
'''
    environment = {key: value for key, value in os.environ.items() if key != 'FACT_ATTACK_AGENT'}
    environment['FACT_ATTACK_AGENT_MOCK'] = '1'  # past the key check: the imports are what is tested
    result = subprocess.run([sys.executable, '-c', script, str(APP), str(tmp_path)], cwd=APP.parent, env=environment,
                            capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr[-3000:]
    assert 'loaded without the agent layer' in result.stdout


def test_step_cards_escape_titles_and_agent_text():
    import dataclasses
    evil = dataclasses.replace(V2, title='<b>Gesetz</b> & «Test»')
    fakes = Fakes([search_votes(), search_passages(V1), search_passages(evil), act('pruefen', vorlage_id=evil.vote_id)],
                  index=FakeIndex([candidate(V1, 0.873), candidate(evil, 0.869)], votes=(V1, evil)), engine=v2_engine())
    with agent_app(fakes, votes={V1.vote_id: V1, evil.vote_id: evil}) as app:
        check(app)
        next(button for button in options(app) if button.label.startswith('<b>Gesetz')).click().run()
        assert not app.exception
        steps = '\n'.join(step_texts(app))
        assert '&lt;b&gt;Gesetz&lt;/b&gt; &amp;' in steps and '<b>Gesetz' not in steps


def test_live_step_list_is_one_element():
    from streamlit.delta_generator import DeltaGenerator
    original = DeltaGenerator.markdown
    live = []

    def spy(self, body, *args, **kwargs):
        if 'agent-steps-live' in str(body):
            live.append(str(body))
        return original(self, body, *args, **kwargs)

    with agent_app(clear_fakes()) as app, patch.object(DeltaGenerator, 'markdown', spy):
        check(app)
        assert len(live) >= 3  # one redraw per event, all through the one placeholder element
        assert all(body.count('class="agent-steps-live"') == 1 for body in live)
        assert 'class="fas ' in live[-1]
        assert not any('agent-steps-live' in value for value in markdown(app))


def test_cards_use_icons_not_marks():
    with agent_app(clear_fakes()) as app:
        check(app)
        cards = step_texts(app)
        assert cards
        for card in cards:
            assert 'class="fas ' in card and '<i class="fas ' in card and 'aria-hidden="true"' in card
            text = card.split('</i>', 1)[1].lstrip()
            assert text[:1] not in ('✓', '✕', '…', '?')
        assert 'So prüft der Agent' in '\n'.join(markdown(app))
        assert not any('Schritte der Prüfung' in value for value in markdown(app))


def test_recent_checks_list_the_new_entry_right_after_the_verdict(isolated_history):
    """The sidebar list is filled after the page saved the check, so the same run already shows it."""
    from src.check_history import load_checks
    fakes = close_fakes()
    with agent_app(fakes) as app:
        check(app)
        recent = lambda: [b.key for b in app.sidebar.button if (b.key or '').startswith('recent_')]
        assert recent() == []
        next(b for b in options(app) if b.label.startswith(V2.title)).click().run()
        saved = load_checks(isolated_history)
        assert len(saved) == 1
        assert recent() == [f"recent_{saved[0]['id']}"]  # no further interaction needed
        assert any(e.label == 'Zuletzt geprüft' for e in app.sidebar.expander)
