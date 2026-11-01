"""Agent tools: validated arguments, German observations and the per-run passage pool; fake index and engine."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent import settings
from src.agent.source_index import VoteCandidate, VoteRef
from src.agent.tools import (ERROR_INVALID_ARGS, ERROR_TOOL_FAILURE, TERMINAL_TOOLS, TOOL_SPECS, AgentTools,
                             Observation, validate_args)


def installed(*packages):
    """True if every package can be imported; an import hook that blocks a package may raise instead of saying no."""
    try:
        return all(importlib.util.find_spec(package) is not None for package in packages)
    except (ImportError, ValueError):
        return False


BOOKLETS = Path('/booklets')
V1 = VoteRef('2024-11-24_de#1', '2024-11-24_de', '2024-11-24', 'de',
             'Änderung des Obligationenrechts (Mietrecht: Untermiete)', (2, 3))
V2 = VoteRef('2024-11-24_de#2', '2024-11-24_de', '2024-11-24', 'de',
             'Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs)', (4, 5))
V3 = VoteRef('2026-06-14_de#1', '2026-06-14_de', '2026-06-14', 'de', 'Änderung des Zivildienstgesetzes', (2, 3))
WRAPPED = VoteRef('2025-09-28_de#1', '2025-09-28_de', '2025-09-28', 'de',
                  'Volksinitiativ e «Mehr bezahlbare Wohnungen»', (2,))
WHOLE = VoteRef('2030-01-01_de#0', '2030-01-01_de', '2030-01-01', 'de', 'Gesamtes Büchlein', (1, 2))  # no contents
BAD_IDS = ('2024-11-24_de#7', '../../.env', 'https://x')


def candidate(vote, score=0.873, hits=12, page=2):
    return VoteCandidate(vote=vote, score=score, hits=hits, best_page=page,
                         snippet=f'Anfang der besten Textstelle zu {vote.vote_id}')


def passage(page, text=None, section='COM'):
    return {'page_number': page, 'text': text or f'Originaltext von Seite {page}.', 'section': section}


def key(p):
    return p['page_number'], hashlib.sha256(p['text'].encode('utf-8')).hexdigest()


class FakeIndex:
    def __init__(self, votes=(V1, V2, V3), candidates=None, passages=None, error=None):
        self.votes = {vote.vote_id: vote for vote in votes}
        self.candidates = [candidate(V1), candidate(V2, 0.861, 7, 4)] if candidates is None else candidates
        self.passages = passages if passages is not None else []
        self.error = error
        self.vote_calls, self.passage_calls = [], []

    def search_votes(self, query, k=5):
        self.vote_calls.append((query, k))
        if self.error:
            raise self.error
        return list(self.candidates)

    def search_passages(self, vote_id, query, k=8):
        self.passage_calls.append((vote_id, query, k))
        if self.error:
            raise self.error
        return [dict(p, vote_id=vote_id, retrieval_score=0.9) for p in self.passages]


class FakeEngine:
    def __init__(self, passages=(), error=None):
        self.passages = list(passages)
        self.error = error
        self.calls = []

    def retrieve_passages(self, claim, booklet_pdf, vote=None, claim_language=None, booklet_language=None, top_k=8):
        self.calls.append({'claim': claim, 'booklet_pdf': booklet_pdf, 'vote': vote,
                           'claim_language': claim_language, 'booklet_language': booklet_language, 'top_k': top_k})
        if self.error:
            raise self.error
        return [dict(p) for p in self.passages]


def make_tools(index=None, engine=None, **kwargs):
    index = index or FakeIndex()
    engine = engine or FakeEngine()
    return AgentTools(index, engine, BOOKLETS, **kwargs), index, engine


class SearchToolTests(unittest.TestCase):
    def test_suche_vorlagen_lists_ids_titles_dates(self):
        tools, index, _ = make_tools()
        obs = tools.run('suche_vorlagen', {'anfrage': 'Untermiete'})
        self.assertIsInstance(obs, Observation)
        self.assertEqual(obs.tool, 'suche_vorlagen')
        self.assertIsNone(obs.error)
        self.assertIn('2024-11-24_de#1', obs.text)
        self.assertIn('2024-11-24_de#2', obs.text)
        self.assertIn('24.11.2024', obs.text)
        self.assertIn(V1.title, obs.text)
        self.assertEqual(index.vote_calls, [('Untermiete', 5)])

    def test_suche_vorlagen_shows_score_and_hits_best_first(self):
        tools, _, _ = make_tools()
        obs = tools.run('suche_vorlagen', {'anfrage': 'Untermiete'})
        self.assertIn('Score 0.873', obs.text)
        self.assertIn('Treffer 12', obs.text)
        self.assertIn('Score 0.861', obs.text)
        self.assertIn('Treffer 7', obs.text)
        self.assertLess(obs.text.index('2024-11-24_de#1'), obs.text.index('2024-11-24_de#2'))
        self.assertEqual([c['vote_id'] for c in obs.data['candidates']], [V1.vote_id, V2.vote_id])
        self.assertEqual(obs.data['candidates'][0]['score'], 0.873)
        self.assertEqual(obs.data['candidates'][0]['hits'], 12)

    def test_suche_vorlagen_shows_catalog_titles_as_they_are(self):
        tools, _, _ = make_tools(FakeIndex(votes=(WRAPPED,), candidates=[candidate(WRAPPED)]))
        obs = tools.run('suche_vorlagen', {'anfrage': 'Wohnungen'})
        self.assertIn('Volksinitiativ e «Mehr bezahlbare Wohnungen»', obs.text)
        self.assertIn('28.09.2025', obs.text)

    def test_suche_vorlagen_without_candidates_is_no_error(self):
        tools, _, _ = make_tools(FakeIndex(candidates=[]))
        obs = tools.run('suche_vorlagen', {'anfrage': 'Euro'})
        self.assertIsNone(obs.error)
        self.assertIn('Keine Vorlage', obs.text)
        self.assertEqual(obs.data['candidates'], [])

    def test_suche_passagen_calls_proven_retrieval_with_vote_title(self):
        tools, _, engine = make_tools(engine=FakeEngine([passage(2), passage(3)]))
        obs = tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'Mieter brauchen Zustimmung'})
        self.assertIsNone(obs.error)
        self.assertEqual(len(engine.calls), 1)
        call = engine.calls[0]
        self.assertEqual(call['claim'], 'Mieter brauchen Zustimmung')
        self.assertEqual(call['vote'], V1.title)
        self.assertEqual(call['booklet_pdf'], BOOKLETS / '2024-11-24_de.pdf')
        self.assertEqual(call['booklet_language'], 'de')
        self.assertIn('Seite 2', obs.text)
        self.assertIn('Originaltext von Seite 3.', obs.text)

    def test_suche_passagen_says_truthfully_that_it_searches_the_whole_booklet(self):
        spec = next(spec for spec in TOOL_SPECS if spec['name'] == 'suche_passagen')
        self.assertIn('ganzen Abstimmungsbüchlein der gewählten Vorlage', spec['description'])
        self.assertIn('Titel der Vorlage', spec['description'])  # the proven search prioritises the vote's title
        self.assertIn('anderer Vorlagen', spec['description'])  # and may return pages of other votes
        self.assertNotIn('in einer gewählten Vorlage', spec['description'])
        tools, _, _ = make_tools(engine=FakeEngine([passage(2)]))
        head = tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'Miete'}).text.splitlines()[0]
        self.assertIn('ganzen Büchlein 2024-11-24_de', head)
        self.assertIn('Titel der Vorlage', head)
        self.assertIn('anderer Vorlagen', head)
        self.assertNotIn(f'Passagen aus {V1.vote_id}', head)
        vector = make_tools(index=FakeIndex(passages=[passage(4)]))[0].run(
            'suche_passagen_vektor', {'vorlage_id': V1.vote_id, 'anfrage': 'Miete'})
        self.assertTrue(vector.text.startswith(f'Passagen aus {V1.vote_id} (Vektorsuche)'))  # filtered by vote

    def test_suche_passagen_for_the_whole_booklet_passes_no_vote_title(self):
        tools, _, engine = make_tools(index=FakeIndex(votes=(V1, WHOLE)), engine=FakeEngine([passage(2)]))
        obs = tools.run('suche_passagen', {'vorlage_id': WHOLE.vote_id, 'anfrage': 'Miete'})
        self.assertIsNone(obs.error)
        self.assertIsNone(engine.calls[0]['vote'])  # "Gesamtes Büchlein" is a placeholder, not a vote of the booklet
        self.assertEqual(engine.calls[0]['booklet_pdf'], BOOKLETS / '2030-01-01_de.pdf')
        self.assertNotIn('Titel der Vorlage', obs.text.splitlines()[0])
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'Miete'})
        self.assertEqual(engine.calls[1]['vote'], V1.title)

    def test_suche_passagen_vektor_searches_the_index_for_that_vote(self):
        index = FakeIndex(passages=[passage(4), passage(5)])
        tools, _, engine = make_tools(index)
        obs = tools.run('suche_passagen_vektor', {'vorlage_id': V2.vote_id, 'anfrage': 'Kündigung'})
        self.assertIsNone(obs.error)
        self.assertEqual(index.passage_calls, [(V2.vote_id, 'Kündigung', 8)])
        self.assertEqual(engine.calls, [])
        self.assertEqual([p['page_number'] for p in tools.passages_for(V2.vote_id)], [4, 5])

    def test_passage_observation_marks_new_and_known_passages(self):
        tools, _, engine = make_tools(engine=FakeEngine([passage(2), passage(3)]))
        first = tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        self.assertEqual(first.data['found'], 2)
        self.assertEqual(first.data['new'], 2)
        engine.passages = [passage(3), passage(4)]
        second = tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'b'})
        self.assertEqual((second.data['found'], second.data['new'], second.data['pool_size']), (2, 1, 3))
        self.assertIn('schon im Pool', second.text)
        self.assertIn('neu', second.text)

    def test_passage_observation_shortens_long_text_for_display_only(self):
        long_text = 'Wort ' * 200
        tools, _, _ = make_tools(engine=FakeEngine([passage(2, long_text)]))
        obs = tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        self.assertLess(len(obs.text), len(long_text))
        self.assertEqual(tools.passages_for(V1.vote_id)[0]['text'], long_text)


class PassagePoolTests(unittest.TestCase):
    def test_passages_are_pooled_deduplicated_and_capped(self):
        first = [passage(page) for page in range(1, 9)]  # pages 1-8
        second = [passage(page) for page in range(5, 15)]  # pages 5-8 repeat, 9-14 are new
        tools, index, engine = make_tools(index=FakeIndex(passages=second), engine=FakeEngine(first))
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        tools.run('suche_passagen_vektor', {'vorlage_id': V1.vote_id, 'anfrage': 'b'})
        pooled = tools.passages_for(V1.vote_id)
        self.assertEqual(len(pooled), settings.MAX_PASSAGES)
        self.assertEqual(len({key(p) for p in pooled}), len(pooled))
        self.assertEqual([p['page_number'] for p in pooled], list(range(1, 11)))  # first found first
        self.assertEqual(pooled[0]['text'], 'Originaltext von Seite 1.')  # the PDF text, untouched

    def test_overlap_alone_adds_nothing(self):
        same = [passage(2), passage(3)]
        tools, _, _ = make_tools(index=FakeIndex(passages=same), engine=FakeEngine(same))
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        obs = tools.run('suche_passagen_vektor', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        self.assertEqual(len(tools.passages_for(V1.vote_id)), 2)
        self.assertEqual(obs.data['new'], 0)

    def test_pool_key_is_page_and_text_hash(self):
        found = [passage(2, 'Gleicher Text.'), passage(3, 'Gleicher Text.'),  # other page: distinct
                 passage(2, 'Anderer Text.'),  # same page, other text: distinct
                 passage(2, 'Gleicher Text.')]  # exact repeat: dropped
        tools, _, _ = make_tools(engine=FakeEngine(found))
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        self.assertEqual([key(p) for p in tools.passages_for(V1.vote_id)], [key(p) for p in found[:3]])

    def test_pool_is_kept_per_vote(self):
        tools, _, engine = make_tools(engine=FakeEngine([passage(2)]))
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        engine.passages = [passage(4)]
        tools.run('suche_passagen', {'vorlage_id': V2.vote_id, 'anfrage': 'a'})
        self.assertEqual([p['page_number'] for p in tools.passages_for(V1.vote_id)], [2])
        self.assertEqual([p['page_number'] for p in tools.passages_for(V2.vote_id)], [4])
        self.assertEqual(tools.passages_for(V3.vote_id), [])

    def test_passages_for_returns_copies(self):
        tools, _, _ = make_tools(engine=FakeEngine([passage(2)]))
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        tools.passages_for(V1.vote_id)[0]['text'] = 'verändert'
        tools.passages_for(V1.vote_id).clear()
        self.assertEqual(tools.passages_for(V1.vote_id)[0]['text'], 'Originaltext von Seite 2.')

    def test_full_pool_says_so_and_drops_the_rest(self):
        tools, _, engine = make_tools(engine=FakeEngine([passage(page) for page in range(1, 9)]))
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        engine.passages = [passage(page) for page in range(9, 15)]
        obs = tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'b'})
        self.assertEqual((obs.data['found'], obs.data['new'], obs.data['dropped']), (6, 2, 4))
        self.assertEqual(obs.data['pool_size'], settings.MAX_PASSAGES)
        self.assertIn(f'{settings.MAX_PASSAGES} von höchstens {settings.MAX_PASSAGES}', obs.text)
        self.assertEqual([p['page_number'] for p in tools.passages_for(V1.vote_id)], list(range(1, 11)))


class VoteIdTests(unittest.TestCase):
    def test_invalid_vote_ids_rejected(self):
        for tool in ('suche_passagen', 'suche_passagen_vektor'):
            for bad in BAD_IDS:
                with self.subTest(tool=tool, vote_id=bad):
                    tools, index, engine = make_tools(index=FakeIndex(passages=[passage(2)]),
                                                      engine=FakeEngine([passage(2)]))
                    tools.run('suche_vorlagen', {'anfrage': 'Miete'})
                    obs = tools.run(tool, {'vorlage_id': bad, 'anfrage': 'Miete'})
                    self.assertIsInstance(obs, Observation)
                    self.assertTrue(obs.error)
                    self.assertIn('Fehler', obs.text)
                    self.assertIn(V1.vote_id, obs.error)  # the valid ids of the last search come back
                    self.assertIn(V2.vote_id, obs.error)
                    self.assertEqual(engine.calls, [])
                    self.assertEqual(index.passage_calls, [])
                    self.assertEqual(tools.passages_for(V1.vote_id), [])

    def test_check_vote_id_returns_the_vote_and_lists_recent_ids(self):
        tools, _, _ = make_tools()
        with self.assertRaises(ValueError) as before:
            tools.check_vote_id('2024-11-24_de#7')
        self.assertIn('suche_vorlagen', str(before.exception))  # nothing found yet: where to get ids
        tools.run('suche_vorlagen', {'anfrage': 'Miete'})
        self.assertEqual(tools.check_vote_id(V3.vote_id), V3)  # in the index, though not among the last candidates
        with self.assertRaises(ValueError) as after:
            tools.check_vote_id('2024-11-24_de#7')
        self.assertIn(V1.vote_id, str(after.exception))
        self.assertIn(V2.vote_id, str(after.exception))
        self.assertNotIn(V3.vote_id, str(after.exception))

    def test_check_vote_id_rejects_non_text(self):
        tools, _, _ = make_tools()
        for bad in (None, 7, ['2024-11-24_de#1']):
            with self.subTest(vote_id=bad), self.assertRaises(ValueError):
                tools.check_vote_id(bad)

    def test_fixed_vote_hides_suche_vorlagen_and_rejects_other_votes(self):
        tools, index, engine = make_tools(engine=FakeEngine([passage(2)]), fixed_vote_id=V1.vote_id)
        names = [spec['name'] for spec in tools.available_specs()]
        self.assertNotIn('suche_vorlagen', names)
        self.assertNotIn('rueckfrage', names)  # a fixed vote leaves nothing to ask about: it could never be valid
        self.assertIn('quelle_fehlt', names)
        self.assertEqual(names, ['suche_passagen', 'suche_passagen_vektor', 'pruefen', 'quelle_fehlt'])
        hidden = tools.run('suche_vorlagen', {'anfrage': 'Miete'})
        self.assertTrue(hidden.error)
        self.assertEqual(index.vote_calls, [])
        other = tools.run('suche_passagen', {'vorlage_id': V2.vote_id, 'anfrage': 'Miete'})
        self.assertTrue(other.error)
        self.assertIn(V1.vote_id, other.error)
        self.assertEqual(engine.calls, [])
        with self.assertRaises(ValueError):
            tools.check_vote_id(V2.vote_id)
        self.assertEqual(tools.check_vote_id(V1.vote_id), V1)
        self.assertIsNone(tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'Miete'}).error)
        self.assertEqual(len(engine.calls), 1)

    def test_fixed_vote_must_exist_in_the_index(self):
        with self.assertRaises(ValueError):
            make_tools(fixed_vote_id='2024-11-24_de#7')

    def test_all_tools_are_available_without_a_fixed_vote(self):
        tools, _, _ = make_tools()
        self.assertEqual([spec['name'] for spec in tools.available_specs()], [spec['name'] for spec in TOOL_SPECS])
        tools.available_specs()[0]['name'] = 'verändert'  # a copy: the module's specs stay intact
        self.assertEqual(TOOL_SPECS[0]['name'], 'suche_vorlagen')


class ArgumentValidationTests(unittest.TestCase):
    def test_anfrage_length_validated(self):
        for tool, extra in (('suche_vorlagen', {}), ('suche_passagen', {'vorlage_id': V1.vote_id}),
                            ('suche_passagen_vektor', {'vorlage_id': V1.vote_id})):
            for bad in ('', '   ', 'a' * 501):
                with self.subTest(tool=tool, length=len(bad)):
                    tools, index, engine = make_tools(index=FakeIndex(passages=[passage(2)]))
                    obs = tools.run(tool, dict(extra, anfrage=bad))
                    self.assertTrue(obs.error)
                    self.assertEqual((engine.calls, index.vote_calls, index.passage_calls), ([], [], []))
            with self.subTest(tool=tool, length=500):
                tools, _, _ = make_tools(index=FakeIndex(passages=[passage(2)]), engine=FakeEngine([passage(2)]))
                self.assertIsNone(tools.run(tool, dict(extra, anfrage='a' * 500)).error)

    def test_anfrage_is_stripped(self):
        self.assertEqual(validate_args('suche_vorlagen', {'anfrage': '  Miete \n'}), {'anfrage': 'Miete'})

    def test_missing_wrong_type_and_extra_arguments_are_rejected_in_german(self):
        cases = (
            ('suche_passagen', {'anfrage': 'Miete'}, 'vorlage_id'),  # missing
            ('suche_vorlagen', {'anfrage': 7}, 'Text'),  # wrong type
            ('suche_vorlagen', {'anfrage': 'Miete', 'datei': '../.env'}, 'datei'),  # not an argument of the tool
            ('suche_vorlagen', 'Miete', 'Objekt'),  # not an object
        )
        for tool, args, fragment in cases:
            with self.subTest(args=args), self.assertRaises(ValueError) as caught:
                validate_args(tool, args)
            self.assertIn(fragment, str(caught.exception))

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            validate_args('lösche_alles', {})
        self.assertIn('suche_vorlagen', str(caught.exception))  # the known tools are listed

    def test_validate_args_returns_only_the_declared_arguments(self):
        self.assertEqual(validate_args('pruefen', {'vorlage_id': ' 2024-11-24_de#1 '}),
                         {'vorlage_id': '2024-11-24_de#1'})
        self.assertEqual(validate_args('quelle_fehlt', {'begruendung': 'Euro steht in keinem Büchlein.'}),
                         {'begruendung': 'Euro steht in keinem Büchlein.'})
        self.assertEqual(validate_args('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'Miete'}),
                         {'vorlage_id': V1.vote_id, 'anfrage': 'Miete'})

    def test_vote_id_must_be_non_empty_short_text(self):
        for bad in ('', '   ', 'x' * 65, None, 3):
            with self.subTest(vote_id=bad), self.assertRaises(ValueError):
                validate_args('pruefen', {'vorlage_id': bad})

    def test_rueckfrage_requires_two_to_four_known_options(self):
        ids = [V1.vote_id, V2.vote_id, V3.vote_id, WRAPPED.vote_id, '2020-01-01_de#1']
        question = 'Welche Mietrechtsvorlage meinst du?'
        for count in (0, 1, 5):
            with self.subTest(count=count), self.assertRaises(ValueError) as caught:
                validate_args('rueckfrage', {'frage': question, 'optionen': ids[:count]})
            self.assertIn('optionen', str(caught.exception))
        for count in (2, 3, 4):
            with self.subTest(count=count):
                clean = validate_args('rueckfrage', {'frage': question, 'optionen': ids[:count]})
                self.assertEqual(clean, {'frage': question, 'optionen': ids[:count]})
        for bad in ([V1.vote_id, V1.vote_id], 'a#1, b#1', [V1.vote_id, 7], [V1.vote_id, '']):
            with self.subTest(options=bad), self.assertRaises(ValueError):
                validate_args('rueckfrage', {'frage': question, 'optionen': bad})
        with self.assertRaises(ValueError):
            validate_args('rueckfrage', {'frage': '', 'optionen': ids[:2]})
        tools, _, _ = make_tools()  # whether the options exist is checked against the index
        self.assertEqual([tools.check_vote_id(i) for i in ids[:3]], [V1, V2, V3])
        for unknown in ids[3:]:
            with self.assertRaises(ValueError):
                tools.check_vote_id(unknown)


class RunTests(unittest.TestCase):
    def test_terminal_and_unknown_tools_are_not_run(self):
        tools, index, engine = make_tools()
        for tool, args in (('pruefen', {'vorlage_id': V1.vote_id}), ('quelle_fehlt', {'begruendung': 'x'}),
                           ('rueckfrage', {'frage': 'f', 'optionen': [V1.vote_id, V2.vote_id]}),
                           ('lösche_alles', {})):
            with self.subTest(tool=tool):
                obs = tools.run(tool, args)
                self.assertEqual(obs.tool, tool)
                self.assertTrue(obs.error)
        self.assertEqual((engine.calls, index.vote_calls, index.passage_calls), ([], [], []))

    def test_engine_and_index_exceptions_become_observation_errors(self):
        secret = '/Users/sheyla/booklets/2024-11-24_de.pdf'
        tools, _, _ = make_tools(engine=FakeEngine(error=FileNotFoundError(secret)))
        obs = tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'Miete'})
        self.assertIn('FileNotFoundError', obs.error)
        self.assertIn('FileNotFoundError', obs.text)
        self.assertNotIn(secret, obs.text)  # what the LLM sees carries no local paths
        self.assertEqual(obs.data['error_kind'], ERROR_TOOL_FAILURE)
        self.assertEqual(tools.passages_for(V1.vote_id), [])
        for tool, args in (('suche_vorlagen', {'anfrage': 'Miete'}),
                           ('suche_passagen_vektor', {'vorlage_id': V1.vote_id, 'anfrage': 'Miete'})):
            with self.subTest(tool=tool):
                tools, _, _ = make_tools(index=FakeIndex(error=RuntimeError('Qdrant ist zu')))
                obs = tools.run(tool, args)
                self.assertIn('RuntimeError', obs.error)
                self.assertEqual(obs.data['error_kind'], ERROR_TOOL_FAILURE)

    def test_rejected_calls_are_marked_as_invalid_not_as_failures(self):
        tools, _, _ = make_tools()
        obs = tools.run('suche_passagen', {'vorlage_id': BAD_IDS[1], 'anfrage': 'Miete'})
        self.assertEqual(obs.data['error_kind'], ERROR_INVALID_ARGS)
        self.assertEqual(tools.run('suche_vorlagen', {'anfrage': ''}).data['error_kind'], ERROR_INVALID_ARGS)

    def test_error_kinds_are_the_documented_values(self):
        self.assertEqual((ERROR_INVALID_ARGS, ERROR_TOOL_FAILURE), ('invalid_args', 'tool_failure'))

    def test_a_failed_search_leaves_the_pool_and_recent_ids_untouched(self):
        tools, index, engine = make_tools(engine=FakeEngine([passage(2)]))
        tools.run('suche_vorlagen', {'anfrage': 'Miete'})
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'a'})
        index.error = RuntimeError('weg')
        engine.error = RuntimeError('weg')
        tools.run('suche_vorlagen', {'anfrage': 'b'})
        tools.run('suche_passagen', {'vorlage_id': V1.vote_id, 'anfrage': 'b'})
        self.assertEqual([p['page_number'] for p in tools.passages_for(V1.vote_id)], [2])
        with self.assertRaises(ValueError) as caught:
            tools.check_vote_id('nope')
        self.assertIn(V1.vote_id, str(caught.exception))


class ToolSpecTests(unittest.TestCase):
    EXPECTED = {
        'suche_vorlagen': ['anfrage'], 'suche_passagen': ['vorlage_id', 'anfrage'],
        'suche_passagen_vektor': ['vorlage_id', 'anfrage'], 'pruefen': ['vorlage_id'],
        'rueckfrage': ['frage', 'optionen'], 'quelle_fehlt': ['begruendung'],
    }

    def test_specs_name_all_six_tools_in_german(self):
        self.assertEqual({spec['name']: spec['parameters']['required'] for spec in TOOL_SPECS}, self.EXPECTED)
        self.assertEqual(len(TOOL_SPECS), 6)
        for spec in TOOL_SPECS:
            with self.subTest(tool=spec['name']):
                self.assertTrue(spec['description'].strip())
                self.assertEqual(spec['parameters']['type'], 'object')
                self.assertEqual(set(spec['parameters']['properties']), set(spec['parameters']['required']))
                self.assertFalse(spec['parameters']['additionalProperties'])
        json.dumps(TOOL_SPECS)  # plain JSON: it goes to the API as it is

    def test_terminal_tools(self):
        self.assertEqual(set(TERMINAL_TOOLS), {'pruefen', 'rueckfrage', 'quelle_fehlt'})

    def test_schema_limits_match_the_validation(self):
        by_name = {spec['name']: spec['parameters']['properties'] for spec in TOOL_SPECS}
        for tool in ('suche_vorlagen', 'suche_passagen', 'suche_passagen_vektor'):
            self.assertEqual(by_name[tool]['anfrage']['minLength'], 1)
            self.assertEqual(by_name[tool]['anfrage']['maxLength'], 500)
        options = by_name['rueckfrage']['optionen']
        self.assertEqual((options['minItems'], options['maxItems']), (2, 4))
        for name, properties in by_name.items():
            for field, schema in properties.items():
                with self.subTest(tool=name, field=field):
                    self.assertNotIn('title', schema)
                    self.assertTrue(schema['description'].strip())

    @unittest.skipUnless(installed('langchain_core'), 'Install requirements-agent.txt')
    def test_specs_bind_as_openai_tools(self):
        from langchain_core.utils.function_calling import convert_to_openai_tool
        for spec in TOOL_SPECS:
            converted = convert_to_openai_tool(spec)
            self.assertEqual(converted['type'], 'function')
            self.assertEqual(converted['function']['name'], spec['name'])
            self.assertEqual(converted['function']['parameters'], spec['parameters'])


if __name__ == '__main__':
    unittest.main()
