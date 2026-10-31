"""Agent settings: defaults, environment overrides and the tool-mode validation."""
import contextlib
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import config

SETTINGS = "src.agent.settings"
AGENT_ENV = ("AGENT_TOOL_MODE", "AGENT_INDEX_DIR", "AGENT_TRACE_DIR")


@contextlib.contextmanager
def settings_with_env(**env):
    """Reload the settings module under a controlled AGENT_* environment, then restore it for later tests."""
    cleaned = {k: v for k, v in os.environ.items() if k not in AGENT_ENV}
    try:
        with patch.dict(os.environ, {**cleaned, **env}, clear=True):
            if SETTINGS in sys.modules:
                yield importlib.reload(sys.modules[SETTINGS])
            else:
                yield importlib.import_module(SETTINGS)
    finally:
        if SETTINGS in sys.modules:
            importlib.reload(sys.modules[SETTINGS])


class AgentSettingsTests(unittest.TestCase):
    def test_defaults(self):
        with settings_with_env() as s:
            self.assertEqual(s.MAX_STEPS, 5)
            self.assertEqual(s.MAX_REQUESTS, 12)
            self.assertEqual(s.MAX_PASSAGES, 10)
            self.assertEqual(s.MAX_CLAIM_CHARS, 1000)
            self.assertEqual(s.LANGUAGES, ("de",))
            self.assertEqual(s.TOOL_MODE, "json")
            self.assertEqual(s.INDEX_DIR, config.REPO_ROOT / ".rag-index" / "agent-sources")
            self.assertEqual(s.TRACE_DIR, config.REPO_ROOT / ".agent-traces")

    def test_directories_and_native_mode_from_environment(self):
        with settings_with_env(AGENT_TOOL_MODE="native", AGENT_INDEX_DIR="/tmp/idx", AGENT_TRACE_DIR="/tmp/trc") as s:
            self.assertEqual(s.TOOL_MODE, "native")
            self.assertEqual(s.INDEX_DIR, Path("/tmp/idx"))
            self.assertEqual(s.TRACE_DIR, Path("/tmp/trc"))

    def test_invalid_tool_mode(self):
        with self.assertRaises(ValueError):
            with settings_with_env(AGENT_TOOL_MODE="xml"):
                pass  # the reload inside the helper raises
        with settings_with_env() as s:  # the helper restored the environment: defaults are back for later tests
            self.assertEqual(s.TOOL_MODE, "json")


if __name__ == "__main__":
    unittest.main()
