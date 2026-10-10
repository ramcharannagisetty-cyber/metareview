"""Meta-analysis features: publication-bias triangulation, influence, meta-regression, correlations, coding-sheet import, SR mode."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pipeline import cli, effects, meta, pubmed, store
from test_meta import bcg
from test_pipeline import PAPERS, extraction_for, fake_records

LATITUDE = [44, 55, 42, 52, 13, 44, 19, 13, 27, 42, 18, 33, 33]  # dat.bcg ablat, metafor order


class MetaExtras(unittest.TestCase):
    def test_rank_test_bcg(self):
        y, v = bcg()
        r = meta.rank_test(y, v)
        self.assertAlmostEqual(r['tau'], 0.0256, 3)  # metafor ranktest(dat.bcg): tau = 0.0256, p = 0.90
        self.assertGreater(r['pval'], 0.8)

    def test_metareg_latitude_matches_metafor(self):
        y, v = bcg()
        mr = meta.metareg(y, v, LATITUDE, knha=False)
        self.assertAlmostEqual(mr['slope'], -0.0291, 3)  # metafor rma(yi, vi, mods=~ablat, data=dat.bcg): -0.0291, intercept 0.2515, tau2 0.0764
        self.assertAlmostEqual(mr['intercept'], 0.2515, 3)
        self.assertAlmostEqual(mr['tau2_residual'], 0.0764, 3)

    def test_trim_and_fill_symmetric_and_asymmetric(self):
        sym_y = [-0.4, -0.2, 0.0, 0.2, 0.4, -0.1, 0.1]
        sym_v = [0.05] * 7
        self.assertEqual(meta.trim_and_fill(sym_y, sym_v)['k0'], 0)
        asym_y = [0.9, 0.8, 0.7, 0.6, 0.3, 0.25, 0.2]          # small studies all positive, large ones smaller: classic funnel asymmetry
        asym_v = [0.30, 0.25, 0.20, 0.15, 0.02, 0.015, 0.01]
        tf = meta.trim_and_fill(asym_y, asym_v)
        self.assertGreater(tf['k0'], 0)
        self.assertLess(tf['estimate'], meta.rma(asym_y, asym_v, knha=False)['beta'][0])

    def test_fail_safe_and_cumulative_and_influence(self):
        y, v = bcg()
        fs = meta.fail_safe_n(y, v)
        self.assertTrue(fs['robust'])
        cum = meta.cumulative(y, v, [str(i) for i in range(13)], list(range(13)), knha=False)
        self.assertEqual(cum[-1]['k'], 13)
        self.assertAlmostEqual(cum[-1]['estimate'], -0.7145, 3)
        inf = meta.influence(y, v, [str(i) for i in range(13)])
        self.assertEqual(len(inf), 13)
        self.assertTrue(all(r['cook_d'] >= 0 for r in inf))
        yy, vv = y + [3.0], v + [0.05]  # one wild study
        flagged = [r for r in meta.influence(yy, vv, [str(i) for i in range(14)]) if r['outlier']]
        self.assertTrue(any(r['label'] == '13' for r in flagged))

    def test_correlation_effects(self):
        z, var, _ = effects.correlation(0.5, 53)
        self.assertAlmostEqual(z, 0.5493, 3)
        self.assertAlmostEqual(var, 0.02, 6)
        self.assertAlmostEqual(effects.back('r', z), 0.5, 6)


class CodingSheetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {'MR_HOME': self.tmp}); env.start(); self.addCleanup(env.stop)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                cli.main(list(argv))
            except SystemExit as exc:
                if exc.code:
                    return buf.getvalue() + f'\n[exit {exc.code}]'
        return buf.getvalue()

    def test_binary_sheet_end_to_end(self):
        rows = ['study,year,events_1,n_1,events_2,n_2,rob,setting,latitude']
        for i, (a, b, c, d) in enumerate([(4, 123, 11, 139), (6, 306, 29, 303), (3, 231, 11, 220), (62, 13598, 248, 12867), (33, 5069, 47, 5808), (180, 1541, 372, 1451)]):
            rows.append(f'Trial{i},{1948 + i},{a},{b},{c},{d},{"low" if i % 2 else "high"},{"north" if i < 3 else "south"},{LATITUDE[i]}')
        path = os.path.join(self.tmp, 'sheet.csv')
        open(path, 'w').write('\n'.join(rows) + '\n')
        out = self.run_cli('ma', 'import', path, '--measure', 'RR', '--question', 'BCG and TB')
        self.assertIn('Imported 6 studies (binary)', out)
        self.assertIn('setting', out); self.assertIn('latitude', out)
        out = self.run_cli('analyze')
        self.assertIn('effect: RR', out)
        r = store.Review.active()
        res = r.read('analysis/results.json')['outcomes']['effect']
        self.assertEqual(res['k'], 6)
        self.assertIn('rank_test', res); self.assertIn('trim_and_fill', res); self.assertIn('fail_safe_n', res)
        self.assertIn('latitude', res['meta_regression'])
        self.assertIn('setting', res['subgroups'])
        self.assertTrue(res['influence'])
        self.assertIn('sensitivity_low_rob', res)
        out = self.run_cli('report')
        md = r.text('report/report.md')
        self.assertIn('coding sheet', md)
        self.assertIn('Publication bias, triangulated', md)
        self.assertIn('Meta-regression on latitude', md)
        self.assertTrue(r.exists('report/coding_sheet.csv'))
        self.assertIn('critic', self.run_cli('next'))
        r.write('report/critic.json', {'verdict': 'pass', 'findings': []})
        self.assertIn('DONE', self.run_cli('next'))

    def test_correlation_sheet(self):
        path = os.path.join(self.tmp, 'corr.csv')
        open(path, 'w').write('study,r,n\nA,0.3,100\nB,0.25,200\nC,0.4,50\n')
        self.assertIn('Imported 3 studies (correlation)', self.run_cli('ma', 'import', path, '--measure', 'r', '--higher-is-better'))
        self.run_cli('analyze')
        res = store.Review.active().read('analysis/results.json')['outcomes']['effect']
        self.assertAlmostEqual(res['pooled']['estimate'], 0.29, 1)

    def test_bad_sheet(self):
        path = os.path.join(self.tmp, 'bad.csv')
        open(path, 'w').write('study,events_1,n_1\nA,1,2\n')
        self.assertIn('ERROR', self.run_cli('ma', 'import', path, '--measure', 'RR'))


class SystematicReviewModeTest(unittest.TestCase):
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

    def test_sr_mode_narrative_only(self):
        self.run_cli('init', 'SR mode colchicine', '--mode', 'sr', '--reviewers', 'single')
        r = store.Review.active()
        p = r.read('protocol.json')
        self.assertFalse(p['analysis']['pool'])
        p.update({'pico': {'population': 'a', 'intervention_or_exposure': 'b', 'comparator': 'c', 'outcomes': 'd'},
                  'inclusion': [{'id': 'I1', 'text': 'rct'}], 'exclusion': [{'id': 'E1', 'text': 'x', 'code': 'DESIGN'}],
                  'outcomes': [{'id': 'mace', 'label': 'MACE', 'type': 'binary', 'measure': 'RR', 'primary': True, 'lower_is_better': True, 'mid': None}],
                  'search': {'query': 'q', 'date_from': None, 'date_to': None, 'languages': []}})
        r.write('protocol.json', p)
        self.run_cli('protocol', 'check', '--offline')
        with mock.patch.object(pubmed, 'search_ids', return_value=(['1', '2', '3', '9'], 4)), mock.patch.object(pubmed, 'fetch', return_value=fake_records()):
            self.run_cli('search')
        r.path('screening/ta/A-01.jsonl').write_text('\n'.join(json.dumps({'pmid': p, 'decision': 'include'}) for p in PAPERS) + '\n')
        self.run_cli('screen', 'ta')
        with mock.patch('pipeline.fulltext.europepmc', side_effect=lambda pmcid, pmid=None: PAPERS.get(pmcid.replace('PMC', '')) * 60):
            self.run_cli('fulltext')
        r.path('screening/ft/A-01.jsonl').write_text('\n'.join(json.dumps({'pmid': p, 'decision': 'include'}) for p in PAPERS) + '\n')
        self.run_cli('screen', 'ft')
        for p in PAPERS:
            r.write(f'extraction/{p}.A.json', extraction_for(p))
        self.run_cli('extract')
        from pipeline.rob import TOOLS
        for p in PAPERS:
            r.write(f'rob/{p}.json', {'tool': 'RoB 2', 'domains': [{'domain': d, 'judgement': 'low', 'support': 'x'} for d in TOOLS['RoB 2']['domains']]})
        self.run_cli('rob'); self.run_cli('analyze'); self.run_cli('report')
        md = r.text('report/report.md')
        self.assertIn('Systematic review (narrative synthesis)', md)
        self.assertIn('Narrative synthesis (SWiM', md)
        self.assertIn('3 of 3 studies favour the intervention', md)
        self.assertNotIn('Pooled RR', md)


if __name__ == '__main__':
    unittest.main()
