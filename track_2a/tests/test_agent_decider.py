"""Deciders: native tool calling, JSON action and the scripted policy. Stub chat models; no network, no tiktoken.

The two tests that build a real ChatOpenAI feed it an httpx.MockTransport, so the request and the answer are the real
wire format without a socket.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.agent import settings
from src.agent.tools import ERROR_INVALID_ARGS, TOOL_SPECS, Observation, validate_args


def installed(*packages):
    """True if every package can be imported; an import hook that blocks a package may raise instead of saying no."""
    try:
        return all(importlib.util.find_spec(package) is not None for package in packages)
    except (ImportError, ValueError):
        return False


HAS_HTTPX = installed('httpx')  # requirements-agent.txt; the competition lock (requirements-lock.txt) has no httpx
HAS_LANGCHAIN = HAS_HTTPX and installed('langchain_openai')
if HAS_HTTPX:
    import httpx
    from src.agent import decider as decider_module
    from src.agent.budget import BudgetExceeded, RequestBudget, budget_http_client
    from src.agent.decider import (AgentAction, JsonActionDecider, NativeToolDecider, ScriptedDecider,
                                   make_chat_model, make_decider, parse_json_action)
if HAS_LANGCHAIN:
    import openai
    from langchain_core.messages import AIMessage

CLAIM = 'Die Mietrechtsvorlage stärkt die Vermieter.'
SYSTEM = 'Du prüfst Behauptungen über Schweizer Abstimmungen.'
MESSAGES = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': CLAIM}]
V1, V2 = '2024-11-24_de#1', '2024-11-24_de#2'
THINKING = '<|inner_prefix|>Ich überlege {"tool": "pruefen"} und wähle.<|inner_suffix|>'
SEARCH = {'tool': 'suche_vorlagen', 'args': {'anfrage': 'Miete'}}
CHECK = {'tool': 'pruefen', 'args': {'vorlage_id': '2024-11-24_de#1'}}
USAGE = {'input_tokens': 10, 'output_tokens': 3, 'total_tokens': 13}


def tool_names(specs):
    return [spec['name'] for spec in specs]


def vote_candidate(vote_id, score, title='Änderung des Obligationenrechts (Mietrecht)', date='2024-11-24', hits=10):
    return {'vote_id': vote_id, 'title': title, 'date': date, 'score': score, 'hits': hits, 'best_page': 2}


def votes_observation(*candidates):
    return Observation('suche_vorlagen', 'Gefundene Vorlagen', {'candidates': list(candidates)})


def passages_observation(vote_id=V1, pool_size=3, tool='suche_passagen'):
    return Observation(tool, 'Passagen', {'vote_id': vote_id, 'found': pool_size, 'new': pool_size,
                                          'dropped': 0, 'pool_size': pool_size, 'passages': []})


def error_chain(error):
    """The exception and its causes: the OpenAI SDK wraps some errors of the transport and passes others on."""
    while error is not None:
        yield error
        error = error.__cause__


def fixed_vote_specs():
    return [spec for spec in TOOL_SPECS if spec['name'] not in ('suche_vorlagen', 'rueckfrage')]


class StubChat:
    """Stands in for ChatOpenAI: bind_tools returns itself, invoke records the messages and answers with `reply`."""

    def __init__(self, reply):
        self.reply = reply
        self.bound_tools = None
        self.bind_kwargs = None
        self.calls = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        self.bind_kwargs = kwargs
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return self.reply


@unittest.skipUnless(HAS_HTTPX, 'Install requirements-agent.txt')
class ParseJsonActionTests(unittest.TestCase):
    def test_parse_json_after_thinking(self):
        action = parse_json_action(THINKING + json.dumps(SEARCH), TOOL_SPECS)
        self.assertEqual((action.tool, action.args, action.error), ('suche_vorlagen', {'anfrage': 'Miete'}, None))

    def test_parse_json_in_code_fence(self):
        text = 'Hier meine Wahl:\n```json\n' + json.dumps(SEARCH) + '\n```\n'
        action = parse_json_action(text, TOOL_SPECS)
        self.assertEqual((action.tool, action.args, action.error), ('suche_vorlagen', {'anfrage': 'Miete'}, None))

    def test_parse_takes_the_last_object_with_a_tool(self):
        first = json.dumps({'tool': 'suche_vorlagen', 'args': {'anfrage': 'erste'}})
        last = json.dumps({'tool': 'suche_vorlagen', 'args': {'anfrage': 'letzte'}})
        action = parse_json_action(f'{first} dann besser {last} {{"label": 1}}', TOOL_SPECS)
        self.assertEqual(action.args, {'anfrage': 'letzte'})

    def test_parse_cut_off_thinking_is_no_action(self):
        reasoning = 'Ich wähle vielleicht ' + json.dumps(CHECK) + ', aber das wäre geraten'
        for text in ('<|inner_prefix|>' + reasoning,  # the answer ended inside the thinking
                     THINKING + json.dumps(SEARCH) + '<|inner_prefix|>' + reasoning):  # a second, unended thinking
            with self.subTest(text=text[-40:]):
                action = parse_json_action(text, TOOL_SPECS)
                self.assertIsNone(action.tool)
                self.assertEqual(action.args, {})
                self.assertTrue(action.error.startswith('Denkanteil abgeschnitten'), action.error)
                self.assertNotIn('pruefen', action.raw)  # nothing of the reasoning goes into the history
                self.assertNotIn('geraten', action.raw)
                self.assertNotIn('inner_prefix', action.raw + action.error)
                self.assertLessEqual(len(action.raw), decider_module.RAW_CHARS)

    def test_parse_returns_validated_stripped_args_and_the_object_as_raw(self):
        text = 'Antwort: {"tool": "suche_vorlagen", "args": {"anfrage": "  Miete  "}} Ende'
        action = parse_json_action(text, TOOL_SPECS)
        self.assertEqual(action.args, {'anfrage': 'Miete'})
        self.assertEqual(action.raw, '{"tool": "suche_vorlagen", "args": {"anfrage": "  Miete  "}}')

    def test_parse_unknown_tool_is_error(self):
        action = parse_json_action('{"tool": "loesche_alles", "args": {}}', TOOL_SPECS)
        self.assertIsNone(action.tool)
        self.assertIn('Unbekanntes Werkzeug', action.error)
        self.assertIn('loesche_alles', action.error)

    def test_parse_tool_outside_the_offered_list_is_error(self):
        text = json.dumps({'tool': 'rueckfrage', 'args': {'frage': 'Welche?', 'optionen': [V1, V2]}})
        self.assertEqual(parse_json_action(text, TOOL_SPECS).tool, 'rueckfrage')
        action = parse_json_action(text, fixed_vote_specs())
        self.assertIsNone(action.tool)
        self.assertIn('Unbekanntes Werkzeug', action.error)
        self.assertNotIn('rueckfrage', action.error.split('Verfügbar:')[1])

    def test_parse_missing_arg_is_error(self):
        action = parse_json_action('{"tool": "suche_passagen", "args": {"anfrage": "Miete"}}', TOOL_SPECS)
        self.assertIsNone(action.tool)
        self.assertIn('vorlage_id', action.error)

    def test_parse_missing_args_key_is_error(self):
        action = parse_json_action('{"tool": "suche_vorlagen"}', TOOL_SPECS)
        self.assertIsNone(action.tool)
        self.assertIn('anfrage', action.error)

    def test_parse_args_that_are_not_an_object_is_error(self):
        action = parse_json_action('{"tool": "suche_vorlagen", "args": "Miete"}', TOOL_SPECS)
        self.assertIsNone(action.tool)
        self.assertIn('Objekt', action.error)

    def test_parse_garbage_is_error(self):
        for text in ('Ich weiss es nicht.', '', '{"tool": "suche_vorlagen", "args": {"anfra', '{"label": 1}'):
            with self.subTest(text=text):
                action = parse_json_action(text, TOOL_SPECS)
                self.assertIsNone(action.tool)
                self.assertTrue(action.error)

    def test_parse_error_action_keeps_the_text_for_the_history(self):
        action = parse_json_action('Ich weiss es nicht.', TOOL_SPECS)
        self.assertEqual(action.raw, 'Ich weiss es nicht.')


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class NativeToolDeciderTests(unittest.TestCase):
    def reply(self, *calls, **kwargs):
        return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': call_id}
                                                 for name, args, call_id in calls], **kwargs)

    def test_native_first_tool_call_and_tokens(self):
        chat = StubChat(self.reply(('suche_vorlagen', {'anfrage': 'Miete'}, 'call_a'),
                                   ('pruefen', {'vorlage_id': V1}, 'call_b'), usage_metadata=USAGE))
        action = NativeToolDecider(chat).decide(MESSAGES, TOOL_SPECS, [])
        self.assertEqual((action.tool, action.args, action.call_id, action.error),
                         ('suche_vorlagen', {'anfrage': 'Miete'}, 'call_a', None))
        self.assertEqual((action.tokens_prompt, action.tokens_completion), (10, 3))
        self.assertIn('2 Werkzeugaufrufe', action.raw)
        self.assertEqual(chat.bound_tools, TOOL_SPECS)
        self.assertEqual(chat.calls, [MESSAGES])

    def test_native_single_tool_call_has_no_note_and_missing_usage_is_zero(self):
        action = NativeToolDecider(StubChat(self.reply(('pruefen', {'vorlage_id': V1}, 'c')))).decide(
            MESSAGES, TOOL_SPECS, [])
        self.assertEqual(json.loads(action.raw), {'tool': 'pruefen', 'args': {'vorlage_id': V1}})
        self.assertEqual((action.tokens_prompt, action.tokens_completion), (0, 0))

    def test_native_without_tool_call_is_error(self):
        chat = StubChat(AIMessage(content='Ich antworte direkt.', usage_metadata=USAGE))
        action = NativeToolDecider(chat).decide(MESSAGES, TOOL_SPECS, [])
        self.assertIsNone(action.tool)
        self.assertEqual(action.error, 'Keine Werkzeugwahl')
        self.assertEqual(action.raw, 'Ich antworte direkt.')
        self.assertEqual(action.tokens_prompt, 10)

    def test_native_call_with_broken_json_arguments_is_error(self):
        broken = AIMessage(content='', invalid_tool_calls=[
            {'name': 'suche_vorlagen', 'args': '{"anfrage": ', 'id': 'c', 'error': 'bad json',
             'type': 'invalid_tool_call'}])
        action = NativeToolDecider(StubChat(broken)).decide(MESSAGES, TOOL_SPECS, [])
        self.assertIsNone(action.tool)
        self.assertTrue(action.error.startswith('Keine Werkzeugwahl'))
        self.assertIn('suche_vorlagen', action.error)

    def test_native_invalid_arguments_are_error_but_raw_keeps_the_call(self):
        chat = StubChat(self.reply(('suche_passagen', {'anfrage': 'Miete'}, 'call_a')))
        action = NativeToolDecider(chat).decide(MESSAGES, TOOL_SPECS, [])
        self.assertIsNone(action.tool)
        self.assertIsNone(action.call_id)
        self.assertIn('vorlage_id', action.error)
        self.assertEqual(json.loads(action.raw)['tool'], 'suche_passagen')

    def test_native_unknown_or_hidden_tool_is_error(self):
        for name in ('loesche_alles', 'suche_vorlagen'):
            with self.subTest(name=name):
                chat = StubChat(self.reply((name, {'anfrage': 'Miete'}, 'call_a')))
                action = NativeToolDecider(chat).decide(MESSAGES, fixed_vote_specs(), [])
                self.assertIsNone(action.tool)
                self.assertIn('Unbekanntes Werkzeug', action.error)

    def test_native_observation_messages_carry_call_id(self):
        chat = StubChat(self.reply(('suche_vorlagen', {'anfrage': 'Miete'}, 'call_a')))
        decider = NativeToolDecider(chat)
        action = decider.decide(MESSAGES, TOOL_SPECS, [])
        assistant, tool = decider.observation_messages(action, Observation('suche_vorlagen', 'Ergebnistext'))
        self.assertEqual(assistant['role'], 'assistant')
        (call,) = assistant['tool_calls']
        self.assertEqual((call['id'], call['type'], call['function']['name']), ('call_a', 'function', 'suche_vorlagen'))
        self.assertEqual(json.loads(call['function']['arguments']), {'anfrage': 'Miete'})
        self.assertEqual(tool, {'role': 'tool', 'tool_call_id': 'call_a', 'content': 'Ergebnistext'})

    def test_native_action_without_call_id_falls_back_to_text_messages(self):
        action = AgentAction(tool=None, raw='kaputt', error='Keine Werkzeugwahl')
        assistant, user = NativeToolDecider(StubChat(None)).observation_messages(
            action, Observation('suche_vorlagen', 'Fehler: Keine Werkzeugwahl'))
        self.assertEqual(assistant, {'role': 'assistant', 'content': 'kaputt'})
        self.assertEqual(user, {'role': 'user', 'content': 'Ergebnis von suche_vorlagen: Fehler: Keine Werkzeugwahl'})

    def test_native_decider_requires_a_tool_call(self):
        chat = StubChat(self.reply(('suche_vorlagen', {'anfrage': 'Miete'}, 'call_a')))
        NativeToolDecider(chat).decide(MESSAGES, TOOL_SPECS, [])
        self.assertEqual(chat.bind_kwargs, {'tool_choice': 'required'})

    def test_native_request_body_has_required_and_completion_cap(self):
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={
                'id': 'x', 'object': 'chat.completion', 'created': 1, 'model': 'm',
                'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
                    'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': 'call_1', 'type': 'function',
                         'function': {'name': 'suche_vorlagen', 'arguments': json.dumps({'anfrage': 'Miete'})}}]}}]})

        NativeToolDecider(self.chat_model(RequestBudget(limit=1), handler)).decide(MESSAGES, TOOL_SPECS, [])
        (body,) = sent
        self.assertEqual(body['tool_choice'], 'required')
        self.assertEqual(body['max_completion_tokens'], settings.LLM_MAX_TOKENS)

    def test_native_decider_with_real_chat_model_sends_tools_and_reads_the_call(self):
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={
                'id': 'x', 'object': 'chat.completion', 'created': 1, 'model': 'm',
                'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
                    'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': 'call_9', 'type': 'function',
                         'function': {'name': 'pruefen', 'arguments': json.dumps({'vorlage_id': V1})}}]}}],
                'usage': {'prompt_tokens': 42, 'completion_tokens': 5, 'total_tokens': 47}})

        budget = RequestBudget(limit=2)
        decider = NativeToolDecider(self.chat_model(budget, handler))
        first = AgentAction('suche_vorlagen', {'anfrage': 'Miete'}, call_id='call_0')
        history_messages = MESSAGES + decider.observation_messages(first, Observation('suche_vorlagen', 'Ergebnis'))
        action = decider.decide(history_messages, TOOL_SPECS, [])
        self.assertEqual((action.tool, action.args, action.call_id), ('pruefen', {'vorlage_id': V1}, 'call_9'))
        self.assertEqual((action.tokens_prompt, action.tokens_completion), (42, 5))
        self.assertEqual(budget.used, 1)
        (body,) = sent
        self.assertEqual([tool['function']['name'] for tool in body['tools']], tool_names(TOOL_SPECS))
        assistant, tool = body['messages'][2:]
        self.assertEqual(assistant['tool_calls'][0]['id'], 'call_0')
        self.assertEqual((tool['role'], tool['tool_call_id'], tool['content']), ('tool', 'call_0', 'Ergebnis'))

    @staticmethod
    def chat_model(budget, handler):
        def client(budget_, *args, **kwargs):
            return budget_http_client(budget_, *args, transport=httpx.MockTransport(handler), **kwargs)

        with patch.object(config, 'LLM_API_KEY', 'test-key'), patch.object(decider_module, 'budget_http_client', client):
            return make_chat_model(budget)


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class JsonActionDeciderTests(unittest.TestCase):
    def test_json_decider_parses_the_answer_and_reports_tokens(self):
        chat = StubChat(AIMessage(content=THINKING + json.dumps(SEARCH), usage_metadata=USAGE))
        action = JsonActionDecider(chat).decide(MESSAGES, TOOL_SPECS, [])
        self.assertEqual((action.tool, action.args, action.call_id), ('suche_vorlagen', {'anfrage': 'Miete'}, None))
        self.assertEqual((action.tokens_prompt, action.tokens_completion), (10, 3))
        self.assertIsNone(chat.bound_tools)  # no native tool calling

    def test_json_decider_appends_tool_list_and_format_to_the_system_message(self):
        chat = StubChat(AIMessage(content=json.dumps(SEARCH)))
        original = json.loads(json.dumps(MESSAGES))
        JsonActionDecider(chat).decide(MESSAGES, TOOL_SPECS, [])
        self.assertEqual(MESSAGES, original)  # the caller's list is not changed
        (sent,) = chat.calls
        system, user = sent
        self.assertEqual(user, MESSAGES[1])
        self.assertTrue(system['content'].startswith(SYSTEM))
        self.assertIn('{"tool":', system['content'])
        self.assertIn('"args"', system['content'])
        for spec in TOOL_SPECS:
            self.assertIn(spec['name'], system['content'])
            self.assertIn(spec['description'], system['content'])
        self.assertNotIn(CLAIM, system['content'])

    def test_json_decider_lists_only_the_offered_tools(self):
        chat = StubChat(AIMessage(content=json.dumps(SEARCH)))
        JsonActionDecider(chat).decide(MESSAGES, fixed_vote_specs(), [])
        self.assertNotIn('rueckfrage', chat.calls[0][0]['content'])
        self.assertIn('pruefen', chat.calls[0][0]['content'])

    def test_json_decider_adds_a_system_message_when_there_is_none(self):
        chat = StubChat(AIMessage(content=json.dumps(SEARCH)))
        JsonActionDecider(chat).decide(MESSAGES[1:], TOOL_SPECS, [])
        system, user = chat.calls[0]
        self.assertEqual((system['role'], user), ('system', MESSAGES[1]))
        self.assertIn('suche_vorlagen', system['content'])

    def test_json_decider_garbage_is_error_action(self):
        action = JsonActionDecider(StubChat(AIMessage(content='Keine Ahnung'))).decide(MESSAGES, TOOL_SPECS, [])
        self.assertIsNone(action.tool)
        self.assertTrue(action.error)

    def test_json_cut_off_thinking_keeps_the_reasoning_out_of_the_history(self):
        chat = StubChat(AIMessage(content='<|inner_prefix|>Vielleicht ' + json.dumps(CHECK) + ' geraten',
                                  usage_metadata=USAGE))
        decider = JsonActionDecider(chat)
        action = decider.decide(MESSAGES, TOOL_SPECS, [])
        self.assertIsNone(action.tool)
        self.assertEqual((action.tokens_prompt, action.tokens_completion), (10, 3))
        assistant, user = decider.observation_messages(action, Observation('pruefen', f'Fehler: {action.error}'))
        self.assertNotIn('geraten', assistant['content'])
        self.assertNotIn('pruefen', assistant['content'])
        self.assertIn('Denkanteil abgeschnitten', user['content'])

    def test_json_observation_messages_are_assistant_plus_user(self):
        chat = StubChat(AIMessage(content=THINKING + json.dumps(SEARCH)))
        decider = JsonActionDecider(chat)
        action = decider.decide(MESSAGES, TOOL_SPECS, [])
        messages = decider.observation_messages(action, Observation('suche_vorlagen', 'Ergebnistext'))
        self.assertEqual(messages, [{'role': 'assistant', 'content': action.raw},
                                    {'role': 'user', 'content': 'Ergebnis von suche_vorlagen: Ergebnistext'}])
        self.assertNotIn('inner_prefix', messages[0]['content'])  # the reasoning stays out of the history
        self.assertEqual(json.loads(messages[0]['content']), SEARCH)


@unittest.skipUnless(HAS_HTTPX, 'Install requirements-agent.txt')
class ScriptedDeciderTests(unittest.TestCase):
    def test_scripted_starts_with_the_vote_search(self):
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, [])
        self.assertEqual((action.tool, action.args, action.error), ('suche_vorlagen', {'anfrage': CLAIM}, None))
        self.assertEqual(json.loads(action.raw), {'tool': 'suche_vorlagen', 'args': {'anfrage': CLAIM}})

    def test_scripted_takes_rank_one_and_leaves_the_question_to_the_guard(self):
        history = [votes_observation(vote_candidate(V1, 0.873, 'Untermiete', '2024-11-24'),
                                     vote_candidate(V2, 0.868, 'Eigenbedarf', '2024-11-24'))]
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, history)
        self.assertEqual((action.tool, action.args), ('suche_passagen', {'vorlage_id': V1}))
        self.assertFalse(hasattr(ScriptedDecider(), 'margin'))

    def test_scripted_checks_when_clear(self):
        decider = ScriptedDecider()
        votes = [votes_observation(vote_candidate(V1, 0.90), vote_candidate(V2, 0.85))]
        search = decider.decide(MESSAGES, TOOL_SPECS, votes)
        self.assertEqual((search.tool, search.args), ('suche_passagen', {'vorlage_id': V1}))
        check = decider.decide(MESSAGES, TOOL_SPECS, votes + [passages_observation(V1)])
        self.assertEqual((check.tool, check.args), ('pruefen', {'vorlage_id': V1}))

    def test_scripted_searches_passages_with_the_vote_only(self):
        votes = [votes_observation(vote_candidate(V1, 0.90), vote_candidate(V2, 0.85))]
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, votes)
        self.assertEqual(action.args, {'vorlage_id': '2024-11-24_de#1'})  # the claim goes in through the graph
        self.assertIsNone(action.error)
        fixed = ScriptedDecider(fixed_vote_id=V2).decide(MESSAGES, fixed_vote_specs(), [])
        self.assertEqual(fixed.args, {'vorlage_id': V2})

    def test_scripted_single_candidate_needs_no_question(self):
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, [votes_observation(vote_candidate(V1, 0.5))])
        self.assertEqual((action.tool, action.args['vorlage_id']), ('suche_passagen', V1))

    def test_scripted_no_candidate_means_source_missing(self):
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, [votes_observation()])
        self.assertEqual(action.tool, 'quelle_fehlt')
        self.assertTrue(action.args['begruendung'])

    def test_scripted_with_fixed_vote_skips_vote_search(self):
        decider = ScriptedDecider(fixed_vote_id=V2)
        specs = fixed_vote_specs()
        search = decider.decide(MESSAGES, specs, [])
        self.assertEqual((search.tool, search.args), ('suche_passagen', {'vorlage_id': V2}))
        check = decider.decide(MESSAGES, specs, [passages_observation(V2)])
        self.assertEqual((check.tool, check.args), ('pruefen', {'vorlage_id': V2}))

    @unittest.skipUnless(installed('langgraph'), 'Install requirements-agent.txt')
    def test_scripted_decider_uses_the_vote_the_person_chose_after_the_answer(self):
        from src.agent.graph import ANSWER_PROMPT
        from src.agent.prompts import system_prompt
        from src.agent.source_index import VoteRef
        chosen = VoteRef(V2, '2024-11-24_de', '2024-11-24', 'de', 'Eigenbedarf', (4, 5))
        decider = ScriptedDecider()
        votes = [votes_observation(vote_candidate(V1, 0.873), vote_candidate(V2, 0.869))]
        interrupted = decider.decide(MESSAGES, TOOL_SPECS, votes)  # rank 1; the guard asked instead of running it
        self.assertEqual(interrupted.args, {'vorlage_id': V1})
        # the messages as the graph's ask node writes them: the prompt of the fixed vote, the claim, the answer
        answer = Observation('rueckfrage', ANSWER_PROMPT.format(vote_id=V2, title=chosen.title))
        messages = ([{'role': 'system', 'content': system_prompt(chosen)}, MESSAGES[1]]
                    + decider.observation_messages(interrupted, answer))
        search = decider.decide(messages, fixed_vote_specs(), votes)  # the history still ends with the vote search
        self.assertEqual((search.tool, search.args, search.error), ('suche_passagen', {'vorlage_id': V2}, None))
        check = decider.decide(messages, fixed_vote_specs(), votes + [passages_observation(V2)])
        self.assertEqual((check.tool, check.args), ('pruefen', {'vorlage_id': V2}))
        # a passage search in another vote before the question is no search in the chosen one
        other = decider.decide(messages, fixed_vote_specs(), votes + [passages_observation(V1)])
        self.assertEqual((other.tool, other.args), ('suche_passagen', {'vorlage_id': V2}))

    def test_scripted_passages_by_vector_search_are_checked_too(self):
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, [passages_observation(V1, tool='suche_passagen_vektor')])
        self.assertEqual((action.tool, action.args), ('pruefen', {'vorlage_id': V1}))

    def test_scripted_nachsuche_uses_the_vector_search(self):
        history = [votes_observation(vote_candidate(V1, 0.9)), passages_observation(V2)]
        specs = [spec for spec in TOOL_SPECS if spec['name'] == 'suche_passagen_vektor']
        action = ScriptedDecider().decide(MESSAGES, specs, history)
        self.assertEqual((action.tool, action.args), ('suche_passagen_vektor', {'vorlage_id': V2, 'anfrage': CLAIM}))
        # a rejected step after the passage search does not hide the vote of the last passage search
        rejected = Observation('pruefen', 'Fehler', {'error_kind': ERROR_INVALID_ARGS}, 'Keine Passagen')
        action = ScriptedDecider().decide(MESSAGES, specs, history + [rejected])
        self.assertEqual((action.tool, action.args['vorlage_id']), ('suche_passagen_vektor', V2))
        # without a passage search there is no vote to search in: an invalid step, not a guess
        self.assertIsNone(ScriptedDecider().decide(MESSAGES, specs, history[:1]).tool)

    def test_scripted_empty_pool_means_source_missing(self):
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, [passages_observation(V1, pool_size=0)])
        self.assertEqual(action.tool, 'quelle_fehlt')

    def test_scripted_failed_observation_is_an_invalid_step_not_a_guess(self):
        failed = Observation('suche_passagen', 'Fehler: ...', {'error_kind': ERROR_INVALID_ARGS},
                             'Unbekannte Vorlagen-ID')
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, [failed])
        self.assertIsNone(action.tool)
        self.assertIn('Unbekannte Vorlagen-ID', action.error)

    def test_scripted_repeats_its_own_invalid_step_without_nesting_it(self):
        # the graph records an invalid decision as an observation of "Werkzeugwahl", no tool of the agent
        own = Observation('Werkzeugwahl', 'Fehler: ...', {'error_kind': ERROR_INVALID_ARGS},
                          'Das Werkzeug suche_passagen ist fehlgeschlagen: Unbekannte Vorlagen-ID')
        action = ScriptedDecider().decide(MESSAGES, TOOL_SPECS, [own])
        self.assertEqual((action.tool, action.error), (None, own.error))

    def test_scripted_clips_a_long_claim_to_the_query_limit(self):
        claim = 'Miete ' * 200
        action = ScriptedDecider().decide([MESSAGES[0], {'role': 'user', 'content': claim}], TOOL_SPECS, [])
        self.assertEqual(action.tool, 'suche_vorlagen')
        self.assertEqual(len(action.args['anfrage']), 500)

    def test_scripted_takes_the_claim_from_the_first_user_message(self):
        later = MESSAGES + [{'role': 'assistant', 'content': '{}'},
                            {'role': 'user', 'content': 'Ergebnis von suche_vorlagen: anderer Text'}]
        action = ScriptedDecider().decide(later, TOOL_SPECS, [])
        self.assertEqual(action.args, {'anfrage': CLAIM})

    def test_scripted_without_user_message_is_an_invalid_step(self):
        action = ScriptedDecider().decide(MESSAGES[:1], TOOL_SPECS, [])
        self.assertIsNone(action.tool)
        self.assertTrue(action.error)

    def test_scripted_tool_that_is_not_offered_is_an_invalid_step(self):
        action = ScriptedDecider().decide(MESSAGES, fixed_vote_specs(), [])
        self.assertIsNone(action.tool)
        self.assertIn('suche_vorlagen', action.error)

    def test_scripted_is_deterministic(self):
        history = [votes_observation(vote_candidate(V1, 0.9), vote_candidate(V2, 0.5))]
        decider = ScriptedDecider()
        self.assertEqual(decider.decide(MESSAGES, TOOL_SPECS, history), decider.decide(MESSAGES, TOOL_SPECS, history))

    def test_scripted_observation_messages_are_assistant_plus_user(self):
        decider = ScriptedDecider()
        action = decider.decide(MESSAGES, TOOL_SPECS, [])
        messages = decider.observation_messages(action, Observation('suche_vorlagen', 'Ergebnistext'))
        self.assertEqual(messages, [{'role': 'assistant', 'content': action.raw},
                                    {'role': 'user', 'content': 'Ergebnis von suche_vorlagen: Ergebnistext'}])


@unittest.skipUnless(HAS_HTTPX, 'Install requirements-agent.txt')
class MakeDeciderTests(unittest.TestCase):
    def test_make_decider_scripted_needs_no_chat_model(self):
        with patch.object(decider_module, 'make_chat_model', side_effect=AssertionError('no model')):
            decider = make_decider('scripted', RequestBudget(limit=1), fixed_vote_id=V1)
        self.assertIsInstance(decider, ScriptedDecider)
        self.assertEqual(decider.fixed_vote_id, V1)

    def test_make_decider_native_and_json_get_the_budgeted_chat_model(self):
        budget = RequestBudget(limit=1)
        stub = StubChat(None)
        with patch.object(decider_module, 'make_chat_model', return_value=stub) as make:
            native = make_decider('native', budget)
            json_decider = make_decider('json', budget)
        self.assertIsInstance(native, NativeToolDecider)
        self.assertIsInstance(json_decider, JsonActionDecider)
        self.assertIs(native.chat_model, stub)
        self.assertEqual([call.args for call in make.call_args_list], [(budget,), (budget,)])

    def test_make_decider_unknown_mode_is_error(self):
        with self.assertRaisesRegex(ValueError, 'native'):
            make_decider('magic', RequestBudget(limit=1))


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class MakeChatModelTests(unittest.TestCase):
    ANSWER = {'id': 'x', 'object': 'chat.completion', 'created': 1, 'model': 'm',
              'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'hallo'}}]}

    def make(self, budget, handler):
        def client(budget_, *args, **kwargs):
            return budget_http_client(budget_, *args, transport=httpx.MockTransport(handler), **kwargs)

        with patch.object(config, 'LLM_API_KEY', 'test-key'), \
                patch.object(config, 'LLM_BASE_URL', 'http://llm.invalid/v1'), \
                patch.object(config, 'LLM_NAME', 'test-model'), \
                patch.object(decider_module, 'budget_http_client', client):
            return make_chat_model(budget)

    def test_make_chat_model_settings(self):
        model = self.make(RequestBudget(limit=1), lambda request: httpx.Response(200, json=self.ANSWER))
        self.assertEqual((model.model_name, model.temperature, model.max_retries), ('test-model', 0, 0))
        self.assertEqual(model.openai_api_base, 'http://llm.invalid/v1')

    def test_make_chat_model_caps_the_answer_length(self):
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json=self.ANSWER)

        model = self.make(RequestBudget(limit=1), handler)
        self.assertEqual(model.max_tokens, settings.LLM_MAX_TOKENS)
        model.invoke('hallo')
        # langchain-openai sends the cap under its current OpenAI name
        self.assertEqual(sent[0]['max_completion_tokens'], settings.LLM_MAX_TOKENS)

    def test_make_chat_model_counts_every_request_and_never_retries(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(500, json={'error': {'message': 'kaputt'}})

        budget = RequestBudget(limit=3)
        model = self.make(budget, handler)
        with self.assertRaises(openai.InternalServerError):
            model.invoke('hallo')
        self.assertEqual((budget.used, len(seen)), (1, 1))  # max_retries=0: the SDK does not resend

    def test_make_chat_model_stops_at_the_budget(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=self.ANSWER)

        budget = RequestBudget(limit=1)
        model = self.make(budget, handler)
        self.assertEqual(model.invoke('hallo').content, 'hallo')
        with self.assertRaises(Exception) as refused:
            model.invoke('nochmal')
        self.assertTrue(any(isinstance(error, BudgetExceeded) for error in error_chain(refused.exception)))
        self.assertEqual((budget.used, budget.exceeded, len(seen)), (1, True, 1))

    def test_make_chat_model_sets_a_request_timeout(self):
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            return httpx.Response(200, json=self.ANSWER)

        self.make(RequestBudget(limit=1), handler).invoke('hallo')
        # not None: without it the SDK sends no limit at all; connecting fails fast, a silent host after LLM_TIMEOUT_S
        self.assertEqual(timeouts[0]['read'], settings.LLM_TIMEOUT_S)
        self.assertEqual(timeouts[0]['write'], settings.LLM_TIMEOUT_S)
        self.assertEqual(timeouts[0]['connect'], 10.0)

    def test_make_chat_model_async_path_is_refused(self):
        budget = RequestBudget(limit=3)
        model = self.make(budget, lambda request: httpx.Response(200, json=self.ANSWER))
        with self.assertRaises(RuntimeError):
            asyncio.run(model.http_async_client.post('http://llm.invalid/v1/chat/completions', json={}))
        with self.assertRaises(Exception) as refused:
            asyncio.run(model.ainvoke('hallo'))
        self.assertTrue(any('synchrone' in str(error) for error in error_chain(refused.exception)))
        self.assertEqual(budget.used, 0)  # refused before anything was counted or sent


if __name__ == '__main__':
    unittest.main()
