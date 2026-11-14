import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('analyze_rag_pairs', Path(__file__).parents[1]/'scripts/analyze_rag_pairs.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row(cid, variant, value, cluster='source'):
    return dict(id=cid, variant=variant, reference_5gram_recall=value, reference_cluster=cluster,
                crosslingual=True, pair='de->fr')


def test_requires_real_pairs_instead_of_silently_dropping_missing_cases():
    with pytest.raises(ValueError, match='identical nonempty'):
        module.paired({'rows': [row('a','vector',.5),row('b','bm25_chunks',.5)]}, 'vector')


def test_cluster_mean_does_not_overweight_repeated_references():
    rows=[]
    for cid, value, cluster in [('a',1,'shared'),('b',1,'shared'),('c',0,'other')]:
        rows.extend([row(cid,'vector',value,cluster),row(cid,'bm25_chunks',.5,cluster)])
    result=module.paired({'rows':rows},'vector')
    assert result['paired_case_count']==3 and result['reference_cluster_count']==2
    assert result['lexical_recall_delta_mean']==pytest.approx(1/6)
    assert result['cluster_mean_delta']==0
    assert result['higher']==2 and result['lower']==1
