"""API mode end to end with a fake model: protocol -> search -> screening -> extraction -> RoB -> analysis -> report -> critic."""
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pipeline import cli, llm, pubmed, store
from pipeline.rob import TOOLS
from test_pipeline import PAPERS, extraction_for, fake_records


def section(user, title):
    m = re.search(rf'===== {re.escape(title)} =====\n(.*?)\n===== END {re.escape(title)} =====', user, flags=re.S)
    return m.group(1) if m else None


def fake_model(role, system, user):
    if role == 'protocol':
        p = json.loads(section(user, 'TEMPLATE'))
        p.update({'pico': {'population': 'adults with coronary disease', 'intervention_or_exposure': 'colchicine', 'comparator': 'placebo', 'outcomes': 'MACE'},
                  'inclusion': [{'id': 'I1', 'text': 'RCT of colchicine vs placebo'}], 'exclusion': [{'id': 'E1', 'text': 'not randomised', 'code': 'DESIGN'}],
                  'outcomes': [{'id': 'mace', 'label': 'major adverse cardiovascular events', 'type': 'binary', 'measure': 'RR', 'primary': True, 'lower_is_better': True, 'mid': None, 'timepoint': '12 months'}],
                  'search': {'query': 'colchicine AND coronary', 'date_from': None, 'date_to': None, 'languages': []}})
        return '```json\n' + json.dumps(p) + '\n```'
    if role.startswith('screener'):
        batch = json.loads(section(user, 'BATCH'))
        rows = []
        for rec in batch['records']:
            if batch['stage'] == 'ft' and rec['pmid'] == '3' and role.endswith('B'):
                rows.append({'pmid': rec['pmid'], 'decision': 'exclude', 'code': 'DESIGN', 'reason': 'disagree'})
            else:
                rows.append({'pmid': rec['pmid'], 'decision': 'include'})
        return '\n'.join(json.dumps(r) for r in rows)
    if role == 'adjudicator':
        return json.dumps({'decision': 'include'})
    if role.startswith('extractor'):
        paper = section(user, 'PAPER')
        pmid = next(p for p, t in PAPERS.items() if t in paper)
        rejected = section(user, 'YOUR PREVIOUS ATTEMPT WAS REJECTED FOR THESE REASONS (fix all)')
        if role.endswith('B') and pmid == '3' and not rejected:
            return json.dumps(extraction_for(pmid, e1=99))  # fabricated number -> engine rejects -> retry with feedback
        return json.dumps(extraction_for(pmid, swap=(role.endswith('B') and pmid == '2')))
    if role == 'reconciler':
        paper = section(user, 'PAPER')
        pmid = next(p for p, t in PAPERS.items() if t in paper)
        return json.dumps(extraction_for(pmid))
    if role == 'appraiser':
        return json.dumps({'tool': 'RoB 2', 'outcome': 'mace', 'domains': [{'domain': d, 'judgement': 'low', 'support': 'reported'} for d in TOOLS['RoB 2']['domains']]})
    if role == 'critic':
        return json.dumps({'verdict': 'pass', 'findings': []})
    raise AssertionError('unexpected role ' + role)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {'MR_HOME': self.tmp, 'MR_CONFIG': os.path.join(self.tmp, 'config.json')})
        env.start(); self.addCleanup(env.stop)
        llm.set_fake(fake_model); self.addCleanup(lambda: llm.set_fake(None))

    def test_api_mode_full_run(self):
        buf = io.StringIO()
        with mock.patch.object(pubmed, 'count', return_value=(4, {})), \
             mock.patch.object(pubmed, 'search_ids', return_value=(['1', '2', '3', '9'], 4)), \
             mock.patch.object(pubmed, 'fetch', return_value=fake_records()), \
             mock.patch('pipeline.fulltext.europepmc', side_effect=lambda pmcid, pmid=None: PAPERS.get(pmcid.replace('PMC', '')) * 60), \
             redirect_stdout(buf):
            cli.main(['run', 'Does colchicine reduce MACE in coronary disease?'])
        out = buf.getvalue()
        self.assertIn('DONE', out)
        r = store.Review.active()
        self.assertTrue(r.exists('report/index.html'))
        self.assertTrue(r.exists('report/critic.json'))
        res = r.read('analysis/results.json')['outcomes']['mace']
        self.assertEqual(res['k'], 3)
        self.assertEqual(r.read('extraction/2.final.json')['reconciled'], 'resolved_by_reconciler')
        self.assertEqual(r.read('work/retries.json').get('extract'), 1)
        usage = llm.usage_totals(r)
        self.assertGreater(usage['calls'], 10)
        self.assertEqual(r.read('review.json')['settings']['mode'], 'api')
        self.assertIn('fake', r.text('report/report.md')) or self.assertIn('AI use', r.text('report/report.md'))

    def test_config_round_trip(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(['config', '--provider', 'anthropic', '--model', 'claude-opus-5-5', '--effort', 'medium'])
        self.assertIn('claude-opus-5-5', buf.getvalue())
        self.assertNotIn('sk-', buf.getvalue())
        self.assertEqual(oct(os.stat(os.environ['MR_CONFIG']).st_mode)[-3:], '600')


if __name__ == '__main__':
    unittest.main()
