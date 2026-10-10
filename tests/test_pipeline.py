"""Offline end-to-end run: fake PubMed, fake full texts, scripted reviewer and extractor answers."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipeline import cli, pubmed, store

FIX = Path(__file__).parent / 'fixtures'

PAPERS = {
    '1': 'Methods. We randomised 200 adults with coronary disease to colchicine or placebo.\nResults. MACE occurred in 10 of 100 colchicine patients and 20 of 100 placebo patients at 12 months.\nFunding: none.',
    '2': 'Methods. We randomised 300 adults with coronary disease to colchicine or placebo.\nResults. MACE occurred in 18 of 150 colchicine patients and 30 of 150 placebo patients at 12 months.\nFunding: none.',
    '3': 'Methods. We randomised 400 adults with coronary disease to colchicine or placebo.\nResults. MACE occurred in 24 of 200 colchicine patients and 36 of 200 placebo patients at 12 months.\nFunding: none.',
}


def fake_records():
    return [{'pmid': p, 'title': f'Trial {p} of colchicine', 'abstract': 'RCT of colchicine in coronary disease.', 'year': '202' + p, 'authors': [f'Author{p} A'],
             'journal': 'J', 'doi': None, 'pmcid': 'PMC' + p, 'pubtypes': ['Randomized Controlled Trial'], 'mesh': [], 'language': 'eng'} for p in PAPERS] + \
           [{'pmid': '9', 'title': 'Colchicine: a review', 'abstract': 'review', 'year': '2020', 'authors': ['Rev R'], 'journal': 'J', 'doi': None, 'pmcid': None,
             'pubtypes': ['Review'], 'mesh': [], 'language': 'eng'}]


def extraction_for(pmid, e1=None, swap=False):
    text = PAPERS[pmid]
    line = text.split('\n')[1]
    import re
    e1_, n1, e2, n2 = map(int, re.findall(r'(\d+) of (\d+) colchicine patients and (\d+) of (\d+)', line)[0])
    return {'study': {'design': {'value': 'rct', 'quote': text.split('\n')[0].replace('Methods. ', '')},
                      'n_total': {'value': int(text.split()[3]), 'quote': text.split('\n')[0].replace('Methods. ', '')},
                      'country': {'value': None, 'status': 'not_reported'}, 'setting': {'value': None, 'status': 'not_reported'},
                      'population': {'value': 'adults with coronary disease', 'quote': 'adults with coronary disease to colchicine or placebo'},
                      'intervention_or_exposure': {'value': 'colchicine', 'quote': 'adults with coronary disease to colchicine or placebo'},
                      'comparator': {'value': 'placebo', 'quote': 'adults with coronary disease to colchicine or placebo'},
                      'follow_up': {'value': '12 months', 'quote': 'placebo patients at 12 months'},
                      'funding': {'value': 'none', 'quote': 'Funding: none.'}},
            'results': [{'outcome': 'mace', 'shape': 'binary', 'events_1': e1 if e1 is not None else (e2 if swap else e1_), 'n_1': n1, 'events_2': e1_ if swap else e2, 'n_2': n2,
                         'timepoint': '12 months', 'quotes': [line.replace('Results. ', '')], 'subgroup': {'design': 'rct'}}]}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {'MR_HOME': self.tmp})
        self.env.start(); self.addCleanup(self.env.stop)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                cli.main(list(argv))
            except SystemExit as exc:
                if exc.code:
                    return buf.getvalue() + f'\n[exit {exc.code}]'
        return buf.getvalue()

    def review(self):
        return store.Review.active()

    def test_full_run(self):
        out = self.run_cli('init', 'Does colchicine reduce MACE in coronary disease?')
        self.assertIn('NEXT:', out)
        r = self.review()
        p = r.read('protocol.json')
        p.update({'pico': {'population': 'adults with coronary disease', 'intervention_or_exposure': 'colchicine', 'comparator': 'placebo', 'outcomes': 'MACE'},
                  'inclusion': [{'id': 'I1', 'text': 'RCT of colchicine vs placebo'}], 'exclusion': [{'id': 'E1', 'text': 'not randomised', 'code': 'DESIGN'}],
                  'outcomes': [{'id': 'mace', 'label': 'major adverse cardiovascular events', 'type': 'binary', 'measure': 'RR', 'primary': True, 'lower_is_better': True, 'mid': None, 'timepoint': '12 months'}],
                  'search': {'query': 'colchicine AND coronary', 'date_from': None, 'date_to': None, 'languages': []},
                  'analysis': {'model': 'REML', 'knapp_hartung': True, 'level': 95, 'min_studies_to_pool': 2, 'subgroups': ['design']}})
        r.write('protocol.json', p)
        out = self.run_cli('protocol', 'check', '--offline')
        self.assertIn('Protocol locked', out)
        with mock.patch.object(pubmed, 'search_ids', return_value=(['1', '2', '3', '9'], 4)), mock.patch.object(pubmed, 'fetch', return_value=fake_records()):
            out = self.run_cli('search')
        self.assertIn('1 excluded by rule', out)
        self.assertIn('batch-01', out)
        # title/abstract screening by two "reviewers"
        for who in ('A', 'B'):
            lines = [json.dumps({'pmid': p, 'decision': 'include' if p != '3' or who == 'A' else 'unsure'}) for p in PAPERS]
            r.path(f'screening/ta/{who}-01.jsonl').write_text('\n'.join(lines) + '\n')
        out = self.run_cli('screen', 'ta')
        self.assertIn('3 included', out)
        # full texts
        with mock.patch('pipeline.fulltext.europepmc', side_effect=lambda pmcid, pmid=None: PAPERS.get(pmcid.replace('PMC', '')) * 60):
            out = self.run_cli('fulltext')
        self.assertIn('3 retrieved', out)
        for who in ('A', 'B'):
            lines = [json.dumps({'pmid': p, 'decision': 'include' if not (p == '3' and who == 'B') else 'exclude', 'code': 'DESIGN', 'reason': 'test'}) for p in PAPERS]
            r.path(f'screening/ft/{who}-01.jsonl').write_text('\n'.join(lines) + '\n')
        out = self.run_cli('screen', 'ft')
        self.assertIn('1 full-text disagreements', out)
        r.path('screening/ft/adjudication.jsonl').write_text(json.dumps({'pmid': '3', 'decision': 'include'}) + '\n')
        out = self.run_cli('screen', 'ft')
        self.assertIn('3 included', out)
        self.assertIn('extractor A', self.run_cli('next'))
        # extraction: B swaps the arms for study 2 (a valid-looking disagreement a reconciler must settle); a fabricated number is rejected for study 3
        for p in PAPERS:
            r.write(f'extraction/{p}.A.json', extraction_for(p))
            r.write(f'extraction/{p}.B.json', extraction_for(p, swap=(p == '2')))
        r.write('extraction/3.B.json', extraction_for('3', e1=99))
        out = self.run_cli('extract')
        self.assertIn('REJECTED', out)
        self.assertIn('events_1=99', out)
        self.assertTrue(r.exists('extraction/1.final.json'))
        self.assertTrue(r.exists('extraction/2.conflicts.json'))
        r.write('extraction/2.R.json', extraction_for('2'))
        r.write('extraction/3.B.json', extraction_for('3'))
        out = self.run_cli('extract')
        self.assertIn('Final extractions: 3/3', out)
        self.assertIn('appraiser', self.run_cli('next'))
        from pipeline.rob import TOOLS
        for p in PAPERS:
            r.write(f'rob/{p}.json', {'tool': 'RoB 2', 'domains': [{'domain': d, 'judgement': 'high' if (p == '3' and d.startswith('missing')) else 'low', 'support': 'x'} for d in TOOLS['RoB 2']['domains']]})
        out = self.run_cli('rob')
        self.assertEqual(r.read('rob/3.json')['overall'], 'high')
        out = self.run_cli('analyze')
        self.assertIn('mace: RR', out)
        res = r.read('analysis/results.json')['outcomes']['mace']
        self.assertEqual(res['k'], 3)
        self.assertLess(res['pooled']['estimate'], 1)
        self.assertIn('rating', res['grade'])
        self.assertIn('sensitivity_low_rob', res)
        out = self.run_cli('report')
        self.assertTrue(r.exists('report/index.html'))
        md = r.text('report/report.md')
        self.assertIn('AI-generated draft', md)
        self.assertIn('Pooled RR', md)
        self.assertIn('| Records identified | 4 |', md)
        self.assertIn('critic', self.run_cli('next'))
        r.write('report/critic.json', {'verdict': 'pass', 'findings': []})
        self.assertIn('DONE', self.run_cli('next'))
        prisma = json.loads(self.run_cli('prisma'))
        self.assertEqual(prisma['studies_included'], 3)
        self.assertEqual(prisma['records_screened'], prisma['records_excluded'] + prisma['reports_sought'])

    def test_single_agent_mode(self):
        self.run_cli('init', 'Single mode colchicine', '--reviewers', 'single')
        r = self.review()
        p = r.read('protocol.json')
        p.update({'pico': {'population': 'a', 'intervention_or_exposure': 'b', 'comparator': 'c', 'outcomes': 'd'},
                  'inclusion': [{'id': 'I1', 'text': 'rct'}], 'exclusion': [{'id': 'E1', 'text': 'x', 'code': 'DESIGN'}],
                  'outcomes': [{'id': 'mace', 'label': 'MACE', 'type': 'binary', 'measure': 'RR', 'primary': True, 'lower_is_better': True, 'mid': None}],
                  'search': {'query': 'q', 'date_from': None, 'date_to': None, 'languages': []}})
        r.write('protocol.json', p)
        self.run_cli('protocol', 'check', '--offline')
        with mock.patch.object(pubmed, 'search_ids', return_value=(['1', '2', '3', '9'], 4)), mock.patch.object(pubmed, 'fetch', return_value=fake_records()):
            self.run_cli('search')
        nxt = self.run_cli('next')
        self.assertIn('YOURSELF', nxt)
        self.assertNotIn('reviewer B', nxt)
        r.path('screening/ta/A-01.jsonl').write_text('\n'.join(json.dumps({'pmid': p, 'decision': 'unsure' if p == '3' else 'include'}) for p in PAPERS) + '\n')
        out = self.run_cli('screen', 'ta')
        self.assertIn('3 included', out)
        with mock.patch('pipeline.fulltext.europepmc', side_effect=lambda pmcid, pmid=None: PAPERS.get(pmcid.replace('PMC', '')) * 60):
            self.run_cli('fulltext')
        r.path('screening/ft/A-01.jsonl').write_text('\n'.join(json.dumps({'pmid': p, 'decision': 'include'}) for p in PAPERS) + '\n')
        self.assertIn('3 included', self.run_cli('screen', 'ft'))
        for p in PAPERS:
            r.write(f'extraction/{p}.A.json', extraction_for(p))
        self.assertIn('Final extractions: 3/3', self.run_cli('extract'))
        self.assertEqual(r.read('extraction/1.final.json')['reconciled'], 'single_extractor')
        from pipeline.rob import TOOLS
        for p in PAPERS:
            r.write(f'rob/{p}.json', {'tool': 'RoB 2', 'domains': [{'domain': d, 'judgement': 'low', 'support': 'x'} for d in TOOLS['RoB 2']['domains']]})
        self.run_cli('rob'); self.run_cli('analyze'); self.run_cli('report')
        md = r.text('report/report.md')
        self.assertIn('SINGLE AI reviewer', md)
        self.assertIn('single-agent mode', md)

    def test_invalid_protocol_blocks(self):
        self.run_cli('init', 'q')
        out = self.run_cli('protocol', 'check', '--offline')
        self.assertIn('PROTOCOL INVALID', out)
        self.assertIn('[exit 2]', out)


if __name__ == '__main__':
    unittest.main()
