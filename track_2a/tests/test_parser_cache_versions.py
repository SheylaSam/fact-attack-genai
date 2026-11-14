from unittest.mock import Mock,patch
from src.inference import load_parsed_booklet


def test_upgraded_pdf_backend_does_not_reuse_old_extracted_text(tmp_path):
    pdf=tmp_path/'booklet.pdf';pdf.write_bytes(b'cache identity fixture')
    parser=Mock()
    parser.extract_pages.side_effect=[[{'page_number':1,'text':'old extraction'}],[{'page_number':1,'text':'new extraction'}]]
    parser.extract_paragraphs.return_value=[]
    with patch('src.config.BOOKLET_CACHE_DIR',tmp_path/'cache'),patch('src.config.BOOKLET_CACHE_PREBUILT',tmp_path/'prebuilt'), \
         patch('src.config.EVIDENCE_POLICY','legacy'):
        with patch('src.inference.distribution_version',side_effect=lambda name:'old' if name=='pypdf' else 'stable'):
            first=load_parsed_booklet(pdf,parser)
            repeated=load_parsed_booklet(pdf,parser)
            assert first==repeated
            assert parser.extract_pages.call_count==1
        with patch('src.inference.distribution_version',side_effect=lambda name:'new' if name=='pypdf' else 'stable'):
            updated=load_parsed_booklet(pdf,parser)
        assert updated['pages'][0]['text']=='new extraction'
        assert parser.extract_pages.call_count==2
