import re
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
