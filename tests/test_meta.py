import unittest

from pipeline import effects, meta, stats

# dat.bcg (Colditz 1994) as shipped with metafor: tpos tneg cpos cneg
BCG = [(4, 119, 11, 128), (6, 300, 29, 274), (3, 228, 11, 209), (62, 13536, 248, 12619), (33, 5036, 47, 5761),
       (180, 1361, 372, 1079), (8, 2537, 10, 619), (505, 87886, 499, 87892), (29, 7470, 45, 7232), (17, 1699, 65, 1600),
       (186, 50448, 141, 27197), (5, 2493, 3, 2338), (27, 16886, 29, 17825)]


def bcg():
    y, v = [], []
    for a, b, c, d in BCG:
        yi, vi, _ = effects.binary('RR', a, a + b, c, c + d)
        y.append(yi); v.append(vi)
    return y, v


class MetaTests(unittest.TestCase):
    def test_reml_matches_metafor(self):
        y, v = bcg()
        r = meta.rma(y, v, method='REML', knha=False)
        self.assertAlmostEqual(r['beta'][0], -0.7145, 4)
        self.assertAlmostEqual(r['se'][0], 0.1798, 4)
        self.assertAlmostEqual(r['ci_lb'][0], -1.0669, 4)
        self.assertAlmostEqual(r['ci_ub'][0], -0.3622, 4)
        self.assertAlmostEqual(r['tau2'], 0.3132, 4)
        self.assertAlmostEqual(r['I2'], 92.22, 2)
        self.assertAlmostEqual(r['H2'], 12.86, 2)
        self.assertAlmostEqual(r['Q'], 152.2330, 4)
        self.assertTrue(r['converged'])

    def test_dl_and_fe(self):
        y, v = bcg()
        self.assertAlmostEqual(meta.rma(y, v, method='DL', knha=False)['tau2'], 0.3088, 4)
        fe = meta.rma(y, v, method='FE')
        self.assertAlmostEqual(fe['beta'][0], -0.4303, 4)
        self.assertAlmostEqual(fe['se'][0], 0.0405, 4)

    def test_knapp_hartung_widens(self):
        y, v = bcg()
        z = meta.rma(y, v, knha=False); t = meta.rma(y, v, knha=True)
        self.assertEqual(t['test'], 't')
        self.assertLess(t['ci_lb'][0], z['ci_lb'][0])
        self.assertIsNotNone(t['pi_lb'])

    def test_egger_regtest(self):
        y, v = bcg()
        e = meta.egger(y, v, knha=False)
        self.assertAlmostEqual(e['stat'], -0.8033, 3)
        self.assertAlmostEqual(e['pval'], 0.4218, 3)

    def test_leave_one_out_and_subgroups(self):
        y, v = bcg()
        loo = meta.leave_one_out(y, v, [str(i) for i in range(13)], knha=False)
        self.assertEqual(len(loo), 13)
        sg = meta.subgroups(y, v, ['a'] * 6 + ['b'] * 7, knha=False)
        self.assertEqual(set(sg['groups']), {'a', 'b'})
        self.assertIsNotNone(sg['Q_between_p'])

    def test_distributions(self):
        self.assertAlmostEqual(stats.norm_ppf(0.975), 1.959964, 6)
        self.assertAlmostEqual(stats.t_ppf(0.975, 12), 2.178813, 5)
        self.assertAlmostEqual(stats.chi2_sf(3.841459, 1), 0.05, 5)
        self.assertAlmostEqual(stats.t_sf(2.0, 10), 0.036694, 5)

    def test_effects(self):
        yi, vi, note = effects.binary('OR', 0, 10, 5, 10)
        self.assertIn('0.5', note)
        self.assertIsNone(effects.binary('RR', 0, 10, 0, 12)[0])
        g, vg, _ = effects.continuous('SMD', 10, 2, 20, 8, 2, 20)
        self.assertAlmostEqual(g, 0.9803, 3)  # Hedges' g for d = 1 with n = 20 + 20
        yi, vi, _ = effects.generic('HR', 0.8, 0.7, 0.92)
        self.assertAlmostEqual(effects.back('HR', yi), 0.8)
        self.assertAlmostEqual(vi ** .5, (__import__('math').log(0.92) - __import__('math').log(0.7)) / 3.919928, 6)


if __name__ == '__main__':
    unittest.main()
