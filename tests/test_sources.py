"""Source choice, protocol validation, record parsing, cross-source de-duplication and exhaustive PubMed retrieval (offline)."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pipeline import cli, protocol, pubmed, sources as src, store


class SuggestTests(unittest.TestCase):
    def test_biomedical_intervention(self):
        names = [n for n, _ in src.suggest('Does colchicine reduce cardiovascular events in coronary disease?')]
        self.assertEqual(names[:2], ['pubmed', 'europepmc'])
        self.assertIn('clinicaltrials', names)
        self.assertNotIn('openalex', names)

    def test_economics(self):
        names = [n for n, _ in src.suggest('How does a major wildfire alter household income and wage growth?')]
        self.assertEqual(names, ['openalex', 'semanticscholar'])

    def test_mixed(self):
        names = [n for n, _ in src.suggest('Does job loss after a wildfire increase depression in adults?')]
        self.assertIn('pubmed', names)
        self.assertIn('openalex', names)


class ValidationTests(unittest.TestCase):
    def search(self, **kw):
        return {'sources': [{'source': 'pubmed', 'query': 'a', 'rationale': 'x'}, {'source': 'europepmc', 'query': 'b', 'rationale': 'y'}], **kw}

    def test_valid(self):
        self.assertEqual(protocol.validate_sources(self.search()), [])

    def test_single_source_needs_reason(self):
        s = {'sources': [{'source': 'pubmed', 'query': 'a', 'rationale': 'x'}]}
        self.assertTrue(protocol.validate_sources(s))
        self.assertEqual(protocol.validate_sources({**s, 'single_source_reason': 'only database with this content'}), [])

    def test_errors(self):
        s = {'sources': [{'source': 'scopus', 'query': '', 'rationale': ''}, {'source': 'openalex', 'query': 'a, b', 'rationale': 'r'}]}
        errs = ' '.join(protocol.validate_sources(s))
        self.assertIn('unknown source', errs)
        self.assertIn('query is empty', errs)
        self.assertIn('rationale is empty', errs)
        self.assertIn('commas', errs)

    def test_legacy_query(self):
        self.assertEqual(protocol.validate_sources({'query': 'colchicine'}), [])
        self.assertEqual(protocol.search_sources({'query': 'colchicine'})[0]['source'], 'pubmed')


class ParseTests(unittest.TestCase):
    def test_openalex_record(self):
        r = src.OpenAlex.record({'id': 'https://openalex.org/W1', 'doi': 'https://doi.org/10.1/ABC', 'title': 'Fire and wages',
                                 'publication_year': 2020, 'type': 'review', 'ids': {'pmid': 'https://pubmed.ncbi.nlm.nih.gov/123'},
                                 'abstract_inverted_index': {'wages': [1], 'Fire': [0], 'fell': [2]},
                                 'authorships': [{'author': {'display_name': 'Jane Q Doe'}}], 'open_access': {'oa_url': 'http://x/y.pdf'}})
        self.assertEqual(r['pmid'], '123')
        self.assertEqual(r['doi'], '10.1/abc')
        self.assertEqual(r['abstract'], 'Fire wages fell')
        self.assertEqual(r['authors'], ['Doe JQ'])
        self.assertEqual(r['pubtypes'], ['Review'])
        r2 = src.OpenAlex.record({'id': 'https://openalex.org/W9', 'title': 't', 'ids': {}})
        self.assertEqual(r2['pmid'], 'OA-W9')

    def test_europepmc_and_s2_and_registry(self):
        e = src.EuropePMC.record({'id': 'PPR1', 'source': 'PPR', 'title': '<i>Fire</i>', 'abstractText': '<p>Abs</p>', 'pubYear': '2024', 'authorString': 'Doe J, Roe K.'})
        self.assertEqual((e['pmid'], e['title'], e['abstract'], e['authors'], e['preprint']), ('EPMC-PPRPPR1', 'Fire', 'Abs', ['Doe J', 'Roe K'], True))
        s = src.SemanticScholar.record({'title': 'T', 'externalIds': {'CorpusId': 7, 'DOI': '10.2/X'}, 'authors': [{'name': 'Ann Lee'}]})
        self.assertEqual((s['pmid'], s['doi'], s['authors']), ('S2-7', '10.2/x', ['Lee A']))
        c = src.ClinicalTrials.record({'hasResults': True, 'protocolSection': {'identificationModule': {'nctId': 'NCT01', 'briefTitle': 'Trial'},
                                       'statusModule': {'overallStatus': 'COMPLETED', 'startDateStruct': {'date': '2015-03'}},
                                       'designModule': {'studyType': 'INTERVENTIONAL'}, 'descriptionModule': {'briefSummary': 'Sum.'}}})
        self.assertEqual((c['pmid'], c['year'], c['registry']), ('NCT01', '2015', True))
        self.assertIn('results posted: yes', c['abstract'])


class DedupTests(unittest.TestCase):
    def test_merge_across_sources(self):
        recs = [
            {'pmid': 'OA-W1', 'pubmed_id': '5', 'doi': '10.1/a', 'title': 'Wildfire income shock in rural counties', 'abstract': '', 'sources': ['openalex'], 'oa_url': 'u'},
            {'pmid': '5', 'pubmed_id': '5', 'doi': None, 'title': 'Wildfire income shock in rural counties', 'abstract': 'A', 'sources': ['pubmed']},
            {'pmid': 'S2-3', 'pubmed_id': None, 'doi': '10.1/A'.lower(), 'title': 'Different title words entirely here', 'abstract': '', 'sources': ['semanticscholar']},
            {'pmid': 'OA-W2', 'pubmed_id': None, 'doi': None, 'title': 'Wildfire Income Shock in Rural Counties.', 'abstract': '', 'sources': ['openalex']},
            {'pmid': 'OA-W3', 'pubmed_id': None, 'doi': None, 'title': 'Unrelated paper about employment after floods', 'abstract': '', 'sources': ['openalex']},
        ]
        uniq, dups = src.deduplicate(recs)
        self.assertEqual(dups, 3)
        main = next(r for r in uniq if r['pmid'] == '5')
        self.assertEqual(main['sources'], ['pubmed', 'openalex', 'semanticscholar'])
        self.assertEqual((main['doi'], main['oa_url'], main['abstract']), ('10.1/a', 'u', 'A'))


class PubMedSliceTests(unittest.TestCase):
    def test_retrieves_beyond_9999(self):
        years = {y: [f'{y}{i:05d}' for i in range(6000)] for y in (2020, 2021, 2022)}

        def count(q, lo=None, hi=None):
            lo, hi = int(str(lo)[:4]), int(str(hi)[:4])
            return sum(len(v) for y, v in years.items() if lo <= y <= hi), {}

        def search_ids(q, lo=None, hi=None, limit=5000):
            if lo is None:
                return [x for v in years.values() for x in v][:limit], 18000
            lo, hi = int(str(lo)[:4]), int(str(hi)[:4])
            ids = [x for y, v in years.items() if lo <= y <= hi for x in v]
            return ids[:limit], len(ids)

        with mock.patch.object(pubmed, 'count', side_effect=count), mock.patch.object(pubmed, 'search_ids', side_effect=search_ids), \
             mock.patch.object(pubmed, 'fetch', side_effect=lambda ids: [{'pmid': i, 'title': i} for i in ids]):
            recs, total = src.PubMed().fetch_all('q', '2020', '2022')
        self.assertEqual(total, 18000)
        self.assertEqual(len(recs), 18000)
        self.assertEqual(recs[0]['sources'], ['pubmed'])


class MultiSourceSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {'MR_HOME': self.tmp}); env.start(); self.addCleanup(env.stop)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                cli.main(list(argv))
            except SystemExit:
                pass
        return buf.getvalue()

    def test_init_suggests_and_search_merges(self):
        out = self.run_cli('init', 'How does a wildfire change household income?', '--mode', 'sr', '--reviewers', 'single')
        self.assertIn('openalex', out)
        r = store.Review.active()
        p = r.read('protocol.json')
        self.assertEqual([s['source'] for s in p['search']['sources']], ['openalex', 'semanticscholar'])
        p.update({'pico': {'population': 'a', 'intervention_or_exposure': 'b', 'comparator': 'c', 'outcomes': 'd'},
                  'inclusion': [{'id': 'I1', 'text': 'x'}], 'exclusion': [{'id': 'E1', 'text': 'y', 'code': 'DESIGN'}],
                  'outcomes': [{'id': 'inc', 'label': 'income', 'type': 'effect', 'measure': 'beta', 'primary': True, 'lower_is_better': False, 'mid': None}]})
        p['search']['sources'][0]['query'] = 'wildfire AND income'
        p['search']['sources'][1]['query'] = 'wildfire + income'
        r.write('protocol.json', p)
        with mock.patch.object(src.OpenAlex, 'count', return_value=2), mock.patch.object(src.SemanticScholar, 'count', return_value=1):
            out = self.run_cli('protocol', 'check')
        self.assertIn('Protocol locked', out)
        oa = [{'pmid': 'OA-W1', 'pubmed_id': None, 'doi': '10.1/x', 'title': 'Wildfire and household income in California', 'abstract': 'a', 'year': '2021',
               'authors': ['Doe J'], 'journal': 'J', 'pmcid': None, 'pubtypes': ['Article'], 'mesh': [], 'language': 'en', 'sources': ['openalex']},
              {'pmid': 'OA-W2', 'pubmed_id': None, 'doi': None, 'title': 'A review of wildfire economics literature', 'abstract': 'b', 'year': '2020',
               'authors': ['Roe K'], 'journal': 'J', 'pmcid': None, 'pubtypes': ['Review'], 'mesh': [], 'language': 'en', 'sources': ['openalex']}]
        s2 = [{**oa[0], 'pmid': 'S2-9', 'sources': ['semanticscholar']}]
        with mock.patch.object(src.OpenAlex, 'fetch_all', return_value=(oa, 2)), mock.patch.object(src.SemanticScholar, 'fetch_all', return_value=(s2, 1)):
            out = self.run_cli('search')
        self.assertIn('3 identified across 2 sources, 1 duplicates removed, 1 excluded by rule', out)
        summary = r.read('search/summary.json')
        self.assertEqual([x['source'] for x in summary['runs']], ['openalex', 'semanticscholar'])
        self.assertTrue(summary['complete'])
        rec = r.records()['OA-W1']
        self.assertEqual(rec['sources'], ['openalex', 'semanticscholar'])


if __name__ == '__main__':
    unittest.main()
