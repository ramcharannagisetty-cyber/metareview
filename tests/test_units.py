import unittest
from pathlib import Path

from pipeline import extraction, grade, protocol, pubmed, rob, screening

FIX = Path(__file__).parent / 'fixtures'
PAPER = ('Methods. We randomised 240 patients with stable coronary disease to colchicine 0.5 mg daily or placebo.\n'
         'Results. The primary end point occurred in 12 of 120 patients (10%) in the colchicine group and 24 of 120 (20%) in the placebo group '
         '(hazard ratio 0.48; 95% CI 0.24–0.96).\nTABLE 2\tcolchicine\tplacebo\nDeaths\t3\t5\n')


def proto():
    p = {**protocol.TEMPLATE, 'question': 'q', 'pico': {'population': 'a', 'intervention_or_exposure': 'b', 'comparator': 'c', 'outcomes': 'd'},
         'inclusion': [{'id': 'I1', 'text': 'adults'}], 'exclusion': [{'id': 'E1', 'text': 'animals', 'code': 'POPULATION'}],
         'outcomes': [{'id': 'mace', 'label': 'MACE', 'type': 'binary', 'measure': 'RR', 'primary': True, 'lower_is_better': True, 'mid': None},
                      {'id': 'death', 'label': 'death', 'type': 'binary', 'measure': 'RR', 'primary': False, 'lower_is_better': True, 'mid': None}],
         'search': {'query': 'colchicine AND coronary', 'date_from': None, 'date_to': None, 'languages': []}}
    return p


class ProtocolTests(unittest.TestCase):
    def test_template_invalid_then_valid(self):
        self.assertTrue(protocol.validate(dict(protocol.TEMPLATE)))
        self.assertEqual(protocol.validate(proto()), [])

    def test_rob_tool(self):
        p = proto()
        self.assertEqual(protocol.rob_tool(p, 'rct'), 'RoB 2')
        self.assertEqual(protocol.rob_tool(p, 'cohort'), 'ROBINS-I')
        self.assertEqual(protocol.rob_tool({**p, 'exposure_study': True}, 'cohort'), 'ROBINS-E')


class ExtractionTests(unittest.TestCase):
    def good(self):
        return {'study': {'design': {'value': 'rct', 'quote': 'We randomised 240 patients with stable coronary disease'},
                          'n_total': {'value': 240, 'quote': 'We randomised 240 patients with stable coronary disease'}},
                'results': [{'outcome': 'mace', 'shape': 'binary', 'events_1': 12, 'n_1': 120, 'events_2': 24, 'n_2': 120, 'timepoint': 'end',
                             'quotes': ['The primary end point occurred in 12 of 120 patients (10%) in the colchicine group and 24 of 120 (20%) in the placebo group']},
                            {'outcome': 'death', 'shape': 'binary', 'events_1': 3, 'n_1': 120, 'events_2': 5, 'n_2': 120, 'timepoint': 'end',
                             'quotes': ['Deaths\t3\t5', 'We randomised 240 patients with stable coronary disease to colchicine 0.5 mg daily or placebo', '12 of 120 patients (10%) in the colchicine group and 24 of 120']}]}

    def test_valid_extraction_passes(self):
        errors, warnings = extraction.validate(self.good(), proto(), PAPER)
        self.assertEqual(errors, [])

    def test_number_not_in_quote_fails(self):
        e = self.good(); e['results'][0]['events_1'] = 13
        errors, _ = extraction.validate(e, proto(), PAPER)
        self.assertTrue(any('events_1=13' in x for x in errors))

    def test_fabricated_quote_fails(self):
        e = self.good(); e['results'][0]['quotes'] = ['The primary end point occurred in 12 of 120 patients in the active group']
        errors, _ = extraction.validate(e, proto(), PAPER)
        self.assertTrue(any('not found verbatim' in x for x in errors))

    def test_unicode_dash_and_case_normalised(self):
        self.assertTrue(extraction.quote_in_text('95% ci 0.24-0.96', PAPER))
        self.assertTrue(extraction.number_in_quote(1234, 'n = 1,234 people'))

    def test_effect_shape(self):
        e = self.good()
        e['results'][0] = {'outcome': 'mace', 'shape': 'effect', 'measure': 'HR', 'estimate': 0.48, 'ci_low': 0.24, 'ci_high': 0.96, 'adjusted': False,
                           'quotes': ['hazard ratio 0.48; 95% CI 0.24–0.96']}
        errors, _ = extraction.validate(e, proto(), PAPER)
        self.assertEqual(errors, [])
        e['results'][0]['estimate'] = 1.5
        self.assertTrue(extraction.validate(e, proto(), PAPER)[0])

    def test_compare(self):
        a, b = self.good(), self.good()
        b['results'][0]['events_2'] = 25
        diffs = extraction.compare(a, b)
        self.assertEqual([d['field'] for d in diffs], ['mace.events_2'])


class ScreeningTests(unittest.TestCase):
    def test_prescreen_rules(self):
        p = proto()
        self.assertEqual(screening.prescreen({'pubtypes': ['Review']}, p)['code'], 'NOT_PRIMARY')
        self.assertIsNone(screening.prescreen({'pubtypes': ['Review', 'Randomized Controlled Trial']}, p))
        self.assertIsNone(screening.prescreen({'pubtypes': ['Journal Article']}, p))

    def test_merge_ta_advances_disagreements(self):
        a = {'1': {'pmid': '1', 'decision': 'include'}, '2': {'pmid': '2', 'decision': 'exclude', 'code': 'DESIGN', 'reason': 'x'}, '3': {'pmid': '3', 'decision': 'unsure'}}
        b = {'1': {'pmid': '1', 'decision': 'exclude', 'code': 'DESIGN', 'reason': 'y'}, '2': {'pmid': '2', 'decision': 'exclude', 'code': 'DESIGN', 'reason': 'x'}, '3': {'pmid': '3', 'decision': 'exclude', 'code': 'OUTCOME', 'reason': 'z'}}
        final, conflicts, agree = screening.merge(a, b, 'ta')
        self.assertEqual({k: v['decision'] for k, v in final.items()}, {'1': 'include', '2': 'exclude', '3': 'include'})
        self.assertEqual(conflicts, [])
        self.assertEqual(agree['n'], 3)

    def test_merge_ft_conflicts_and_kappa(self):
        a = {'1': {'pmid': '1', 'decision': 'include'}, '2': {'pmid': '2', 'decision': 'include'}}
        b = {'1': {'pmid': '1', 'decision': 'include'}, '2': {'pmid': '2', 'decision': 'exclude', 'code': 'DESIGN', 'reason': 'y'}}
        final, conflicts, agree = screening.merge(a, b, 'ft')
        self.assertEqual(list(final), ['1'])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(agree['percent'], 50.0)

    def test_kappa_known_value(self):
        # 20 records: 12 both include, 5 both exclude, 2 A-only, 1 B-only -> po = .85, pe = (.7*.65)+(.3*.35)=.56
        a, b = {}, {}
        for i in range(20):
            a[str(i)] = {'decision': 'include' if i < 14 else 'exclude'}
            b[str(i)] = {'decision': 'include' if i < 12 or i == 19 else 'exclude'}
        k = screening.agreement(a, b)['kappa']
        self.assertAlmostEqual(k, (0.85 - 0.56) / (1 - 0.56), 3)


class RobTests(unittest.TestCase):
    def test_validate_and_overall(self):
        rec = {'tool': 'RoB 2', 'domains': [{'domain': d, 'judgement': 'low', 'support': 's'} for d in rob.TOOLS['RoB 2']['domains']]}
        self.assertEqual(rob.validate(rec, 'RoB 2'), [])
        self.assertEqual(rob.overall(rec, 'RoB 2')[0], 'low')
        rec['domains'][2]['judgement'] = 'high'
        self.assertEqual(rob.overall(rec, 'RoB 2')[0], 'high')
        self.assertTrue(rob.validate({'tool': 'RoB 2', 'domains': []}, 'RoB 2'))
        self.assertTrue(rob.validate(rec, 'ROBINS-I'))


class GradeTests(unittest.TestCase):
    def fit(self, lb, ub, I2=10, k=5):
        import math
        b = (math.log(lb) + math.log(ub)) / 2
        return {'k': k, 'beta': [b], 'ci_lb': [math.log(lb)], 'ci_ub': [math.log(ub)], 'I2': I2, 'pi_lb': math.log(lb) - .1, 'pi_ub': math.log(ub) + .1, 'tau2': 0.01}

    def test_rct_precise_high(self):
        o = {'id': 'x', 'label': 'death', 'measure': 'RR', 'type': 'binary', 'lower_is_better': True, 'mid': None}
        g = grade.rate(o, self.fit(0.6, 0.8), [{'events': 400, 'n': 4000}], None, ['rct'] * 5, 10)
        self.assertEqual(g['rating'], 'high')
        self.assertIn('reduces death', g['statement'])

    def test_observational_imprecise_very_low(self):
        o = {'id': 'x', 'label': 'death', 'measure': 'RR', 'type': 'binary', 'lower_is_better': True, 'mid': None}
        g = grade.rate(o, self.fit(0.7, 1.2, I2=80), [{'events': 50, 'n': 500}], None, ['cohort'] * 5, 60)
        self.assertEqual(g['rating'], 'very low')

    def test_mid_threshold(self):
        o = {'id': 'x', 'label': 'death', 'measure': 'RR', 'type': 'binary', 'lower_is_better': True, 'mid': 0.9}
        g = grade.rate(o, self.fit(0.85, 0.95), [{'events': 400, 'n': 4000}], None, ['rct'] * 5, 0)
        self.assertEqual(g['domains']['imprecision']['change'], -1)


class PubmedParseTests(unittest.TestCase):
    def test_parse_fixture(self):
        recs = pubmed.parse((FIX / 'pubmed.xml').read_bytes())
        self.assertEqual(len(recs), 2)
        r = recs[0]
        self.assertEqual(r['pmid'], '100001')
        self.assertIn('colchicine', r['title'].lower())
        self.assertIn('Randomized Controlled Trial', r['pubtypes'])
        self.assertEqual(r['pmcid'], 'PMC9000001')
        self.assertEqual(r['year'], '2020')
        self.assertTrue(r['abstract'].startswith('BACKGROUND:'))


if __name__ == '__main__':
    unittest.main()
