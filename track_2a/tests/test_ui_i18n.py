import ast
from pathlib import Path
import re
from src import agent_ui
from src.ui_i18n import TRANSLATIONS, translate, translate_static_html, date_label


def test_translations_keep_template_parameters_and_have_both_languages():
    for source, translations in TRANSLATIONS.items():
        assert set(translations) == {'fr', 'it'}
        expected = set(re.findall(r'\{\w+\}', source))
        for value in translations.values():
            assert value and set(re.findall(r'\{\w+\}', value)) == expected
    assert translate('### Dein Prüfverlauf', 'fr') == '### Votre historique'
    assert translate('An unchanged original quote.', 'it') == 'An unchanged original quote.'
    assert translate_static_html('<h1>Was steht wirklich im Büchlein?</h1>', 'fr') == '<h1>Que dit vraiment la brochure ?</h1>'
    assert 'juin' in date_label('14. Juni 2026', 'fr')


def _agent_ui_text_keys():
    """Every German text of agent_ui.py: the module-level text constants (strings, also inside dicts and tuples) and
    each literal given to t(...). Found by reading the source, so a new text cannot slip past the translations."""
    source = Path(agent_ui.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = node.value
            parts = value.values if isinstance(value, ast.Dict) else [value]  # dict keys are tool names, not texts
            for part in parts:
                found.update(child.value for child in ast.walk(part)
                             if isinstance(child, ast.Constant) and isinstance(child.value, str))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 't':
            found.update(arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
    return found


def test_every_literal_text_of_agent_ui_has_a_translation():
    keys = _agent_ui_text_keys()
    assert keys, 'no texts found: the AST scan is broken'
    assert set(agent_ui.TEXT_KEYS) == keys  # TEXT_KEYS lists exactly the module's texts
    for key in keys:
        assert set(TRANSLATIONS.get(key, {})) == {'fr', 'it'}, key
        for language in ('fr', 'it'):
            assert translate(key, language) != key, (language, key)


def test_new_rows_keep_placeholders():
    for key in agent_ui.TEXT_KEYS:
        expected = set(re.findall(r'\{\w+\}', key))
        for language, value in TRANSLATIONS[key].items():
            assert set(re.findall(r'\{\w+\}', value)) == expected, (language, key)
            assert '|' not in key and '|' not in value
