"""Study-level effect sizes and sampling variances (Cochrane Handbook chapter 6; metafor escalc conventions)."""
import math

from . import PipelineError

Z = {90: 1.6448536269514722, 95: 1.959963984540054, 99: 2.5758293035489004}
RATIO = {'RR', 'OR', 'HR'}
CORR = {'r'}


def binary(measure, e1, n1, e2, n2):
    """log RR or log OR from 2x2 counts. Adds 0.5 to every cell of a study with a zero cell; double zero -> None."""
    if e1 == 0 and e2 == 0 or e1 == n1 and e2 == n2:
        return None, None, 'no events in either arm (or events in all): no information on the ratio; study not pooled'
    note = None
    a, b, c, d = e1, n1 - e1, e2, n2 - e2
    if 0 in (a, b, c, d):
        a, b, c, d = a + .5, b + .5, c + .5, d + .5
        note = 'zero cell: 0.5 added to every cell'
    if measure == 'RR':
        yi = math.log((a / (a + b)) / (c / (c + d)))
        vi = 1 / a - 1 / (a + b) + 1 / c - 1 / (c + d)
    elif measure == 'OR':
        yi = math.log(a * d / (b * c))
        vi = 1 / a + 1 / b + 1 / c + 1 / d
    else:
        raise PipelineError(f'binary data cannot give measure {measure}')
    return yi, vi, note


def continuous(measure, m1, sd1, n1, m2, sd2, n2):
    if min(n1, n2) < 2 or sd1 <= 0 or sd2 <= 0:
        return None, None, 'need n >= 2 and positive SDs in both arms'
    if measure == 'MD':
        return m1 - m2, sd1 ** 2 / n1 + sd2 ** 2 / n2, None
    if measure == 'SMD':
        m = n1 + n2 - 2
        sp = math.sqrt(((n1 - 1) * sd1 ** 2 + (n2 - 1) * sd2 ** 2) / m)
        j = 1 - 3 / (4 * m - 1)
        g = j * (m1 - m2) / sp
        vi = 1 / n1 + 1 / n2 + (1 - (m - 2) / (m * j ** 2)) * g ** 2
        return g, vi, "Hedges' g (small-sample corrected)"
    raise PipelineError(f'continuous data cannot give measure {measure}')


def generic(measure, est, lo, hi, level=95):
    """Reported effect with its CI: ratios are pooled on the log scale."""
    if measure in RATIO:
        if min(est, lo, hi) <= 0:
            return None, None, 'ratio and CI must be positive'
        yi, lo, hi = math.log(est), math.log(lo), math.log(hi)
    else:
        yi = est
    se = (hi - lo) / (2 * Z[level or 95])
    if se <= 0:
        return None, None, 'confidence interval has zero or negative width'
    return yi, se ** 2, 'from reported estimate and CI'


def correlation(r, n):
    """Fisher z transform of a correlation; variance 1/(n-3)."""
    if n is None or n <= 3 or not -1 < r < 1:
        return None, None, 'need n > 3 and -1 < r < 1'
    return math.atanh(r), 1 / (n - 3), "Fisher's z"


def from_result(r, outcome):
    """Effect (yi, vi, note, scale) for one extracted result under the protocol outcome's measure."""
    shape = r.get('shape') or outcome['type']
    measure = outcome['measure']
    if shape == 'binary':
        if measure not in ('RR', 'OR'):
            return None, None, f'binary counts cannot be pooled as {measure}; extract a reported {measure} with CI instead'
        yi, vi, note = binary(measure, r['events_1'], r['n_1'], r['events_2'], r['n_2'])
    elif shape == 'continuous':
        if measure not in ('MD', 'SMD'):
            return None, None, f'means cannot be pooled as {measure}'
        yi, vi, note = continuous(measure, r['mean_1'], r['sd_1'], r['n_1'], r['mean_2'], r['sd_2'], r['n_2'])
    elif shape == 'correlation':
        if measure != 'r':
            return None, None, f'correlations cannot be pooled as {measure}'
        yi, vi, note = correlation(r['r'], r['n'])
    elif shape == 'effect':
        if r.get('measure') != measure:
            return None, None, f'reported {r.get("measure")} cannot be pooled with protocol measure {measure}'
        if r.get('se') is not None and r.get('ci_low') is None:
            yi = math.log(r['estimate']) if measure in RATIO else r['estimate']
            vi, note = float(r['se']) ** 2, 'from reported estimate and SE'
            if measure in RATIO:
                note += ' (SE taken on the log scale)'
        else:
            yi, vi, note = generic(measure, r['estimate'], r['ci_low'], r['ci_high'], r.get('ci_level') or 95)
    else:
        return None, None, f'unknown shape {shape}'
    return yi, vi, note


def back(measure, value):
    if measure in RATIO:
        return math.exp(value)
    if measure in CORR:
        return math.tanh(value)
    return value


def null(measure):
    return 1.0 if measure in RATIO else 0.0
