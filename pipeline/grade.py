"""A first-draft GRADE certainty rating per outcome, following Core GRADE (BMJ 2025) rules of thumb.

Rules are explicit so a human can overrule each one. Start: randomised trials high, observational low.
Rate down for risk of bias, inconsistency, imprecision (CI against the MID or the null; optimal
information size), publication bias. Indirectness is a reviewer judgement and starts at 0.
"""
import math

from . import effects

LEVELS = ['very low', 'low', 'moderate', 'high']
STATEMENT = {'high': '{x}', 'moderate': 'probably {x}', 'low': 'may {x}', 'very low': 'very uncertain whether {x}'}


def rate(outcome, fit, studies, egger, designs, rob_high_weight):
    """outcome: protocol outcome; fit: meta.rma result; studies: pooled study rows; designs: list of designs."""
    m = outcome['measure']
    nullv = effects.null(m)
    lo, hi = effects.back(m, fit['ci_lb'][0]), effects.back(m, fit['ci_ub'][0])
    est = effects.back(m, fit['beta'][0])
    rct = all((d or '').lower() == 'rct' for d in designs)
    start = 3 if rct else 1
    domains = {}
    # risk of bias
    if rob_high_weight is None:
        domains['risk_of_bias'] = (0, 'no risk-of-bias assessments: rate manually')
    elif rob_high_weight > 50:
        domains['risk_of_bias'] = (-1, f'{rob_high_weight:.0f}% of the weight comes from studies at high/serious risk of bias')
    else:
        domains['risk_of_bias'] = (0, f'{rob_high_weight:.0f}% of the weight from high-risk studies')
    # inconsistency
    I2 = fit['I2']
    pi_cross = fit.get('pi_lb') is not None and fit['pi_lb'] < math.log(nullv) if m in effects.RATIO else (fit.get('pi_lb') is not None and fit['pi_lb'] < 0 < fit['pi_ub'])
    if m in effects.RATIO and fit.get('pi_lb') is not None:
        pi_cross = fit['pi_lb'] < 0 < fit['pi_ub']
    if fit['k'] < 2:
        domains['inconsistency'] = (0, 'single study')
    elif I2 >= 75 and pi_cross:
        domains['inconsistency'] = (-2, f'I² = {I2:.0f}% and the prediction interval includes both benefit and harm')
    elif I2 >= 50:
        domains['inconsistency'] = (-1, f'substantial heterogeneity (I² = {I2:.0f}%)')
    else:
        domains['inconsistency'] = (0, f'I² = {I2:.0f}%')
    domains['indirectness'] = (0, 'not rated down; reviewers must confirm the studies match the PICO')
    # imprecision (Core GRADE 2): threshold = MID when given, else the null
    mid = outcome.get('mid')
    down, why = 0, []
    if mid is not None:
        if m in effects.RATIO:
            t_benefit, t_harm = (mid, 1 / mid) if mid < 1 else (1 / mid, mid)
        else:
            t_benefit, t_harm = (-abs(mid), abs(mid))
        crosses_b = lo < t_benefit < hi or lo < t_benefit and hi > t_benefit and not (lo > t_benefit)
        crosses_b = lo <= t_benefit <= hi
        crosses_h = lo <= t_harm <= hi
        if crosses_b and crosses_h:
            down, why = 2, ['CI includes both an important benefit and an important harm (MID threshold)']
        elif crosses_b or crosses_h:
            down, why = 1, ['CI crosses the minimal important difference']
    else:
        if lo <= nullv <= hi:
            down, why = 1, ['CI includes no effect']
    events = sum((s.get('events') or 0) for s in studies)
    n = sum((s.get('n') or 0) for s in studies)
    small = (outcome['type'] == 'binary' and 0 < events < 300) or (outcome['type'] != 'binary' and 0 < n < 400)
    if small:
        size = f'only {events} events (< 300)' if outcome['type'] == 'binary' else f'only {n} participants (< 400)'
        if down == 1:
            down, why = 2, why + [f'{size}: optimal information size not met, so the CI spans appreciable benefit and harm']
        elif down == 0:
            down, why = 1, [f'{size}: optimal information size not met']
    domains['imprecision'] = (-down, '; '.join(why) or 'adequately precise')
    # publication bias
    if fit['k'] < 10 or not egger:
        domains['publication_bias'] = (0, f'{fit["k"]} studies: asymmetry tests unreliable; undetected (judge from search completeness)')
    elif egger['pval'] < 0.10:
        domains['publication_bias'] = (-1, f'Egger test p = {egger["pval"]:.3f}')
    else:
        domains['publication_bias'] = (0, f'Egger test p = {egger["pval"]:.2f}')
    total = sum(d[0] for d in domains.values())
    level = LEVELS[max(0, min(3, start + total))]
    better = outcome.get('lower_is_better', True)
    reduces = est < nullv
    improves = (reduces and better) or (not reduces and not better)
    verb = ('reduces' if reduces else 'increases') + ' ' + outcome['label']
    if lo <= nullv <= hi and mid is None:
        verb = 'has little or no effect on ' + outcome['label'] if abs(math.log(est) if m in effects.RATIO else est) < 1e-9 else verb
        statement = {'high': f'{outcome["label"]}: no clear effect (CI includes the null)',
                     'moderate': f'probably little or no difference in {outcome["label"]} (CI includes the null)',
                     'low': f'may make little or no difference to {outcome["label"]}',
                     'very low': f'very uncertain about the effect on {outcome["label"]}'}[level]
    else:
        statement = STATEMENT[level].format(x=verb)
    return {'start': LEVELS[start], 'rating': level, 'domains': {k: {'change': v[0], 'reason': v[1]} for k, v in domains.items()},
            'statement': statement, 'favourable': improves, 'confirmed_by_human': False}
