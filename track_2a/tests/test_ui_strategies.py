import pytest
from src.ui_strategies import STANDARD,DENSE,FUSED,strategy_options,strategy_from_label


def test_ui_default_and_explicit_vector_opt_in():
    default=strategy_options()
    assert default[0]==STANDARD and strategy_from_label(default[0])=='hybrid'
    assert DENSE not in default and FUSED not in default
    assert strategy_from_label(DENSE)=='vector'
    assert strategy_from_label(FUSED)=='vector_hybrid'
    assert DENSE in strategy_options(True) and FUSED in strategy_options(True)
    with pytest.raises(ValueError):strategy_from_label('typo')


def test_upload_identity_is_content_based_not_a_reused_filename(tmp_path):
    from src.ui_strategies import uploaded_source_path
    first=uploaded_source_path(tmp_path,b'first PDF')
    changed=uploaded_source_path(tmp_path,b'changed PDF')
    assert first!=changed
    assert first==uploaded_source_path(tmp_path,b'first PDF')
    assert first.parent==tmp_path and first.suffix=='.pdf'


def test_partial_upload_is_repaired_and_never_reused_as_valid(tmp_path):
    from src.ui_strategies import uploaded_source_path,store_uploaded_source
    content=b'complete source PDF fixture'
    path=uploaded_source_path(tmp_path,content)
    path.write_bytes(b'partial')
    assert store_uploaded_source(tmp_path,content)==path
    assert path.read_bytes()==content
    assert not list(tmp_path.glob('.upload-*.tmp'))
