"""Own PDF uploads as sources of the agent: the vote of an uploaded booklet, its registration in a backend and a
check that runs with it as the fixed vote.

Tiny PDFs are built with PyMuPDF in a temporary directory; the engine and the index are the fakes of
test_agent_graph.py. No network."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.search_backend import LocalBackend
from src.agent.source_index import WHOLE_BOOKLET_TITLE, VoteRef
from src.agent.tools import AgentTools, validate_args
from src.agent.trace import Tracer
from src.agent.uploads import UPLOAD_TITLE, upload_vote
from tests.test_agent_graph import (CLAIM, HAS_LANGGRAPH, NEW_TEXT, NICHT_BELEGT, V1, FakeEngine, FakeIndex,
                                    PresetDecider, act, passage, search_passages, vector_search)

if HAS_LANGGRAPH:
    from src.agent.graph import NO_NEW_PASSAGES_NOTE, run_check
    from src.agent.prompts import system_prompt


def stored_pdf(directory, pages=3, text='Seite'):
    """A PDF of `pages` pages in `directory`, named like `store_uploaded_source` names an upload."""
    import fitz
    with fitz.open() as document:
        for number in range(1, pages + 1):
            document.new_page().insert_text((72, 72), f'{text} {number}')
        contents = document.tobytes()
    path = Path(directory) / f'custom_{hashlib.sha256(contents).hexdigest()}.pdf'
    path.write_bytes(contents)
    return path


class UploadVoteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_upload_vote_is_whole_booklet_with_all_pages(self):
        path = stored_pdf(self.dir, pages=3)
        vote = upload_vote(path, 'fr')
        self.assertEqual(vote, VoteRef(f'{path.stem}#0', path.stem, '', 'fr', 'Eigenes PDF', (1, 2, 3)))
        self.assertEqual(UPLOAD_TITLE, 'Eigenes PDF')
        self.assertEqual(upload_vote(path, 'DE').language, 'de')

    def test_the_id_of_an_upload_passes_the_tools_argument_check(self):
        vote = upload_vote(stored_pdf(self.dir), 'de')
        self.assertEqual(len(vote.vote_id), 73)  # "custom_" + a SHA-256 + "#0"
        self.assertEqual(validate_args('pruefen', {'vorlage_id': vote.vote_id}), {'vorlage_id': vote.vote_id})

    def test_broken_or_empty_pdf_is_refused_with_a_german_message(self):
        good = stored_pdf(self.dir).read_bytes()
        broken = {'leer': b'', 'kein PDF': b'Das ist nur Text, kein PDF.', 'abgeschnitten': good[:60],
                  'nur Kopf': b'%PDF-1.7\n'}
        for name, contents in broken.items():
            with self.subTest(name):
                path = self.dir / f'custom_{hashlib.sha256(contents).hexdigest()}.pdf'
                path.write_bytes(contents)
                with self.assertRaises(ValueError) as caught:
                    upload_vote(path, 'de')
                message = str(caught.exception)
                self.assertIn('PDF', message)
                self.assertNotIn(str(self.dir), message)  # never the path, never a library message
                self.assertNotIn('Traceback', message)

    def test_missing_file_and_foreign_names_are_refused(self):
        with self.assertRaises(ValueError):
            upload_vote(self.dir / 'custom_0000.pdf', 'de')
        catalog = stored_pdf(self.dir)
        renamed = catalog.rename(self.dir / '2024-11-24_de.pdf')
        with self.assertRaises(ValueError):  # only files named like an upload are uploads
            upload_vote(renamed, 'de')


class AddVoteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.index = FakeIndex()
        self.backend = LocalBackend(self.index, FakeEngine(), self.dir)

    def test_add_vote_makes_an_upload_visible_to_this_backend_only(self):
        vote = upload_vote(stored_pdf(self.dir), 'de')
        self.backend.add_vote(vote)
        self.assertEqual(self.backend.votes[vote.vote_id], vote)
        self.assertIn(V1.vote_id, self.backend.votes)  # the catalog's votes stay
        self.assertNotIn(vote.vote_id, self.index.votes)  # the shared index is never touched
        other = LocalBackend(self.index, FakeEngine(), self.dir)
        self.assertNotIn(vote.vote_id, other.votes)
        tools = AgentTools(self.backend, self.dir)
        self.assertEqual(tools.fix_vote(vote.vote_id), vote)

    def test_add_vote_accepts_only_custom_ids(self):
        sha = 'a' * 64
        refused = {
            'catalog id': V1,
            'path in the id': VoteRef('../x#0', '../x', '', 'de', 'Eigenes PDF', (1,)),
            'custom prefix with a path': VoteRef('custom_../x#0', 'custom_../x', '', 'de', 'Eigenes PDF', (1,)),
            'custom prefix with a slash': VoteRef('custom_a/b#0', 'custom_a/b', '', 'de', 'Eigenes PDF', (1,)),
            'absolute path': VoteRef('/etc/passwd#0', '/etc/passwd', '', 'de', 'Eigenes PDF', (1,)),
            'part of a booklet': VoteRef(f'custom_{sha}#1', f'custom_{sha}', '', 'de', 'Eigenes PDF', (1,)),
            'id and source differ': VoteRef(f'custom_{sha}#0', '2024-11-24_de', '', 'de', 'Eigenes PDF', (1,)),
            'no pages': VoteRef(f'custom_{sha}#0', f'custom_{sha}', '', 'de', 'Eigenes PDF', ()),
        }
        before = dict(self.backend.votes)
        for name, vote in refused.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.backend.add_vote(vote)
        self.assertEqual(self.backend.votes, before)

    def test_add_vote_accepts_a_known_sources_whole_booklet_and_nothing_else(self):
        whole = VoteRef('2024-11-24_de#0', '2024-11-24_de', '2024-11-24', 'de', WHOLE_BOOKLET_TITLE, (1, 2, 3, 4, 5))
        accepting = LocalBackend(self.index, FakeEngine(), self.dir)
        accepting.add_vote(whole)
        self.assertEqual(accepting.votes[whole.vote_id], whole)
        self.assertNotIn(whole.vote_id, self.index.votes)  # the shared index is never touched
        self.assertNotIn(whole.vote_id, LocalBackend(self.index, FakeEngine(), self.dir).votes)

        def like_whole(vote_id, source_id, pages=whole.pages):
            return VoteRef(vote_id, source_id, '2024-11-24', 'de', WHOLE_BOOKLET_TITLE, pages)
        refused = {
            'unknown source': like_whole('2099-01-01_de#0', '2099-01-01_de'),
            'part of a known booklet': like_whole('2024-11-24_de#3', '2024-11-24_de'),
            'catalog vote': V1,
            'no pages': like_whole('2024-11-24_de#0', '2024-11-24_de', ()),
            'id and source differ': like_whole('2099-01-01_de#0', '2024-11-24_de'),
            'path before a known source': like_whole('../2024-11-24_de#0', '../2024-11-24_de'),
            'folder before a known source': like_whole('x/2024-11-24_de#0', 'x/2024-11-24_de'),
            'absolute path': like_whole('/booklets/2024-11-24_de#0', '/booklets/2024-11-24_de'),
            'path in the id': like_whole('../x#0', '../x'),
        }
        before = dict(self.backend.votes)
        for name, vote in refused.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.backend.add_vote(vote)
        self.assertEqual(self.backend.votes, before)

    def test_a_catalog_vote_is_never_shadowed_by_the_whole_booklet_vote(self):
        from src import agent_ui
        catalog = VoteRef('2024-11-24_de#0', '2024-11-24_de', '2024-11-24', 'de', WHOLE_BOOKLET_TITLE, (2, 3, 4, 5))
        index = FakeIndex(votes=(catalog, V1))
        stored_pdf(self.dir).rename(self.dir / '2024-11-24_de.pdf')  # three pages, other than the catalog's
        chosen = agent_ui.vote_for_selection(index.votes, '2024-11-24_de', None, booklets_dir=self.dir)
        self.assertIs(chosen, catalog)  # the index's own whole-booklet vote, not one built from the PDF
        backend = LocalBackend(index, FakeEngine(), self.dir)
        with self.assertRaises(ValueError):  # a session vote with the id of a catalog vote is refused
            backend.add_vote(VoteRef(catalog.vote_id, catalog.source_id, catalog.date, catalog.language,
                                     WHOLE_BOOKLET_TITLE, (1, 2, 3)))
        self.assertIs(backend.votes[catalog.vote_id], catalog)

    def test_proven_search_of_an_upload_reads_its_stored_file(self):
        path = stored_pdf(self.dir)
        vote = upload_vote(path, 'de')
        self.backend.add_vote(vote)
        self.backend.proven_passages(vote.vote_id, CLAIM)
        [call] = self.backend.engine.retrieve_calls
        self.assertEqual(call['booklet_pdf'], path)
        self.assertIsNone(call['vote'])  # whole booklet: the title names no vote

    def test_vector_search_for_an_upload_is_empty_and_never_asks_the_index(self):
        index = FakeIndex(vector=[passage(3, NEW_TEXT)])
        backend = LocalBackend(index, FakeEngine(), self.dir)
        vote = upload_vote(stored_pdf(self.dir), 'de')
        backend.add_vote(vote)
        self.assertEqual(backend.vector_passages(vote.vote_id, 'Zustimmung'), [])
        self.assertEqual(index.vector_calls, [])
        self.assertEqual(len(backend.vector_passages(V1.vote_id, 'Zustimmung')), 1)  # a catalog vote still searches


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class UploadCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = stored_pdf(self.dir)
        self.vote = upload_vote(self.path, 'de')

    def run_upload(self, decider, engine=None, index=None):
        engine, index = engine or FakeEngine(), index or FakeIndex()
        backend = LocalBackend(index, engine, self.dir)
        backend.add_vote(self.vote)
        tools = AgentTools(backend, self.dir, fixed_vote_id=self.vote.vote_id)
        result = run_check(CLAIM, tools=tools, decider=decider, engine=engine, tracer=Tracer())
        return result, engine, index

    def test_upload_check_runs_with_the_fixed_vote(self):
        decider = PresetDecider(search_passages(self.vote), act('pruefen', vorlage_id=self.vote.vote_id))
        result, engine, index = self.run_upload(decider)
        self.assertEqual(result.outcome, 'gestuetzt')
        self.assertEqual(result.vote, self.vote)
        self.assertEqual(engine.retrieve_calls[0]['booklet_pdf'], self.path)
        self.assertTrue(engine.retrieve_calls[0]['booklet_pdf'].name.endswith(f'{self.vote.source_id}.pdf'))
        self.assertEqual(engine.verify_calls[0]['booklet_pdf'], self.path)
        self.assertEqual(index.vote_calls, [])  # the vote is given: no search among the catalog's votes
        self.assertNotIn('suche_vorlagen', decider.calls[0]['tools'])

    def test_vector_search_for_an_upload_finds_nothing_and_the_first_result_stands(self):
        decider = PresetDecider(search_passages(self.vote), act('pruefen', vorlage_id=self.vote.vote_id),
                                vector_search(self.vote))
        engine = FakeEngine(predictions=[NICHT_BELEGT])
        index = FakeIndex(vector=[passage(3, NEW_TEXT)])  # the index would find something, if it were asked
        result, engine, index = self.run_upload(decider, engine, index)
        self.assertEqual(index.vector_calls, [])
        self.assertEqual(result.outcome, 'nicht_belegt')
        self.assertEqual(result.note, NO_NEW_PASSAGES_NOTE)
        self.assertEqual((result.checks, len(engine.verify_calls)), (1, 1))
        self.assertIsNone(result.error)

    def test_system_prompt_names_an_upload_without_an_empty_date(self):
        added = system_prompt(self.vote)[len(system_prompt()):].strip()
        self.assertIn(self.vote.vote_id, added)
        self.assertIn('Eigenes PDF', added)
        self.assertNotIn('Abstimmung vom', added)
        self.assertNotIn('()', added)
        self.assertIn('Abstimmung vom 24.11.2024', system_prompt(V1))


class MakeAgentSessionTests(unittest.TestCase):
    @unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
    def test_make_agent_session_registers_the_upload_as_the_fixed_vote(self):
        from src import agent_app
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict('os.environ', {agent_app.AGENT_MOCK: '1'}), \
             patch('src.agent.settings.TRACE_DIR', Path(tmp) / 'traces'), \
             patch('src.config.BOOKLETS_DIR', Path(tmp)), \
             patch('src.agent_app.get_agent_index', return_value=FakeIndex()):
            vote = upload_vote(stored_pdf(tmp), 'de')
            session = agent_app.make_agent_session(None, lambda event: None, session_vote=vote)
            self.assertEqual(session._tools.fixed_vote_id, vote.vote_id)
            self.assertEqual(session._tools.votes[vote.vote_id], vote)
            plain = agent_app.make_agent_session(None, lambda event: None)  # without an upload nothing changes
            self.assertIsNone(plain._tools.fixed_vote_id)
            self.assertNotIn(vote.vote_id, plain._tools.votes)


if __name__ == '__main__':
    unittest.main()
