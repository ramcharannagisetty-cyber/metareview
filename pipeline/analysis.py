"""Run the pre-specified analysis for every protocol outcome from the final extractions and RoB records.

Always: a study-level table and a direction-based narrative synthesis (SWiM). When analysis.pool is true and
at least min_studies_to_pool studies have usable data: random-effects pooling with heterogeneity, prediction
interval, sensitivity analyses, outlier/influence diagnostics, triangulated publication-bias tests,
subgroups and meta-regression, and a draft GRADE rating.
"""
from . import PipelineError, effects, grade, meta, rob as robmod
from .protocol import rob_tool

Z95 = 1.959964


def study_rows(review, protocol):
    pmids = review.read('screening/ft_final.json')
    rows = []
    records = review.records()
    for pmid, dec in sorted(pmids.items()):
        if dec['decision'] != 'include':
            continue
        final = review.read(f'extraction/{pmid}.final.json', {})
        if not final:
            raise PipelineError(f'extraction/{pmid}.final.json missing: run  extract  first')
        risk = review.read(f'rob/{pmid}.json', {})
        design = ((final.get('study') or {}).get('design') or {}).get('value')
        rows.append({'pmid': pmid, 'record': records.get(pmid, {}), 'extraction': final, 'rob': risk,
                     'design': design, 'tool': rob_tool(protocol, design)})
    return rows


def label(row):
    rec = row['record']
    first = (rec.get('authors') or ['Anon'])[0].split()[0]
    return f"{first} {rec.get('year', '')}".strip()


def _display(m, yi, vi):
    se = vi ** .5
    return [effects.back(m, yi), effects.back(m, yi - Z95 * se), effects.back(m, yi + Z95 * se)]


def narrative(outcome, pooled):
    """SWiM-style vote count by direction of effect, with the caveat that it ignores precision."""
    m = outcome['measure']
    nullv = effects.null(m)
    better = outcome.get('lower_is_better', True)
    rows = []
    for s in pooled:
        est, lo, hi = s['display']
        if m in effects.CORR or m == 'MD' or m == 'SMD' or m == 'beta':
            favours = (est < nullv) == better if est != nullv else None
        else:
            favours = (est < nullv) == better if est != nullv else None
        rows.append({'label': s['label'], 'estimate': est, 'ci_lb': lo, 'ci_ub': hi,
                     'direction': 'favours intervention' if favours else 'favours comparator' if favours is False else 'no difference',
                     'ci_excludes_null': not (lo <= nullv <= hi), 'rob': s.get('rob')})
    n_fav = sum(r['direction'] == 'favours intervention' for r in rows)
    n_against = sum(r['direction'] == 'favours comparator' for r in rows)
    n_sig = sum(r['ci_excludes_null'] for r in rows)
    return {'rows': rows, 'favour': n_fav, 'against': n_against, 'ci_excludes_null': n_sig,
            'summary': (f'{n_fav} of {len(rows)} studies favour the intervention ({n_sig} with a CI excluding the null); '
                        'vote counting by direction ignores study size and precision (SWiM guidance).') if rows else 'no study data'}


def run(review, protocol):
    rows = study_rows(review, protocol)
    plan = protocol['analysis']
    pool = plan.get('pool', True)
    model, knha, level = plan.get('model', 'REML'), plan.get('knapp_hartung', True), plan.get('level', 95)
    results = {'mode': 'meta-analysis' if pool else 'systematic review (narrative synthesis)',
               'studies': [{'pmid': r['pmid'], 'label': label(r), 'design': r['design'], 'tool': r['tool'],
                            'rob_overall': (r['rob'] or {}).get('overall')} for r in rows], 'outcomes': {}}
    for outcome in protocol['outcomes']:
        oid, m = outcome['id'], outcome['measure']
        pooled, excluded = [], []
        for r in rows:
            res = next((x for x in r['extraction'].get('results', []) if x.get('outcome') == oid), None)
            if not res or res.get('status') == 'not_reported':
                excluded.append({'pmid': r['pmid'], 'label': label(r), 'reason': 'outcome not reported'})
                continue
            yi, vi, note = effects.from_result(res, outcome)
            if yi is None:
                excluded.append({'pmid': r['pmid'], 'label': label(r), 'reason': note})
                continue
            shape = res.get('shape') or outcome['type']
            events = (res.get('events_1', 0) + res.get('events_2', 0)) if shape == 'binary' else None
            n = (res.get('n_1', 0) + res.get('n_2', 0)) if shape in ('binary', 'continuous') else res.get('n') if shape == 'correlation' else None
            judgement = (r['rob'] or {}).get('overall')
            tool = (r['rob'] or {}).get('tool') or r['tool']
            high = bool(judgement) and (robmod.is_high(tool, judgement) if tool in robmod.TOOLS else judgement in ('high', 'serious', 'critical', 'very high'))
            pooled.append({'pmid': r['pmid'], 'label': label(r), 'yi': yi, 'vi': vi, 'note': note, 'events': events, 'n': n,
                           'display': _display(m, yi, vi), 'rob': judgement, 'rob_high': high, 'design': r['design'],
                           'year': r['record'].get('year'), 'timepoint': res.get('timepoint'), 'adjusted': res.get('adjusted'),
                           'subgroup': {k: (res.get('subgroup') or {}).get(k) for k in plan.get('subgroups', []) + plan.get('meta_regression', [])}})
        out = {'id': oid, 'label': outcome['label'], 'measure': m, 'primary': bool(outcome.get('primary')),
               'studies': pooled, 'excluded': excluded, 'k': len(pooled), 'warnings': [], 'narrative': narrative(outcome, pooled)}
        if len(pooled) == 0:
            out['warnings'].append('no study provided usable data for this outcome')
        elif not pool:
            out['warnings'].append('quantitative pooling not requested (systematic review mode): narrative synthesis only')
        elif len(pooled) < max(2, plan.get('min_studies_to_pool', 2)):
            out['warnings'].append('only one study: no pooling; the single estimate is shown')
            out['single'] = pooled[0]
        else:
            _pool(out, pooled, outcome, plan, model, knha, level)
        results['outcomes'][oid] = out
    review.write('analysis/results.json', results)
    review.log('analyze', {'outcomes': {k: v['k'] for k, v in results['outcomes'].items()}, 'pooled': pool})
    return results


def _pool(out, pooled, outcome, plan, model, knha, level):
    m = outcome['measure']
    y, v = [s['yi'] for s in pooled], [s['vi'] for s in pooled]
    labels = [s['label'] for s in pooled]
    kw = dict(method=model, knha=knha, level=level)
    fit = meta.rma(y, v, **kw)
    for s, w in zip(pooled, fit['weights']):
        s['weight'] = w
    out['fit'] = fit
    back = lambda x: effects.back(m, x)
    out['pooled'] = {'estimate': back(fit['beta'][0]), 'ci_lb': back(fit['ci_lb'][0]), 'ci_ub': back(fit['ci_ub'][0]),
                     'pi_lb': back(fit['pi_lb']) if fit.get('pi_lb') is not None else None,
                     'pi_ub': back(fit['pi_ub']) if fit.get('pi_ub') is not None else None}
    if len(pooled) == 2:
        out['warnings'].append('two studies: between-study variance is very poorly estimated; treat as illustrative')
    if len(pooled) <= 3 and fit['test'] == 't':
        zf = meta.rma(y, v, method=model, knha=False, level=level)
        out['sensitivity_z'] = {'estimate': back(zf['beta'][0]), 'ci_lb': back(zf['ci_lb'][0]), 'ci_ub': back(zf['ci_ub'][0])}
    if not fit['converged']:
        out['warnings'].append('REML did not converge; last iterate used')
    fe = meta.rma(y, v, method='FE', level=level)
    out['fixed_effect'] = {'estimate': back(fe['beta'][0]), 'ci_lb': back(fe['ci_lb'][0]), 'ci_ub': back(fe['ci_ub'][0])}
    out['leave_one_out'] = [{**x, 'display': [back(x['estimate']), back(x['ci_lb']), back(x['ci_ub'])]}
                            for x in meta.leave_one_out(y, v, labels, **kw)]
    if model != 'DL':
        dl = meta.rma(y, v, method='DL', knha=knha, level=level)
        out['sensitivity_dl'] = {'estimate': back(dl['beta'][0]), 'ci_lb': back(dl['ci_lb'][0]), 'ci_ub': back(dl['ci_ub'][0])}
    low = [s for s in pooled if not s['rob_high']]
    if 2 <= len(low) < len(pooled):
        hf = meta.rma([s['yi'] for s in low], [s['vi'] for s in low], **kw)
        out['sensitivity_low_rob'] = {'k': len(low), 'estimate': back(hf['beta'][0]), 'ci_lb': back(hf['ci_lb'][0]), 'ci_ub': back(hf['ci_ub'][0])}
    # outliers and influence (step 7.1)
    if plan.get('outlier_diagnostics', True):
        inf = meta.influence(y, v, labels, method=model)
        out['influence'] = inf
        flagged = [i for i, r in enumerate(inf) if r['outlier'] or r['influential']]
        if flagged and len(pooled) - len(flagged) >= 2:
            keep = [i for i in range(len(pooled)) if i not in flagged]
            wf = meta.rma([y[i] for i in keep], [v[i] for i in keep], **kw)
            out['sensitivity_without_outliers'] = {'removed': [labels[i] for i in flagged], 'k': len(keep),
                                                   'estimate': back(wf['beta'][0]), 'ci_lb': back(wf['ci_lb'][0]), 'ci_ub': back(wf['ci_ub'][0])}
    # publication bias triangulation (step 7.1)
    out['egger'] = meta.egger(y, v, method=model, knha=knha)
    out['rank_test'] = meta.rank_test(y, v)
    tf = meta.trim_and_fill(y, v, method=model)
    if tf:
        out['trim_and_fill'] = {**tf, 'display': [back(tf['estimate']), back(tf['ci_lb']), back(tf['ci_ub'])]}
    out['fail_safe_n'] = meta.fail_safe_n(y, v)
    years = [s.get('year') for s in pooled]
    if all(str(yv or '').isdigit() for yv in years) and len(pooled) >= 3:
        out['cumulative'] = [{**c, 'display': [back(c['estimate']), back(c['ci_lb']), back(c['ci_ub'])]}
                             for c in meta.cumulative(y, v, labels, [int(yv) for yv in years], **kw)]
    # moderators (steps 4 and 6)
    out['subgroups'] = {}
    for key in plan.get('subgroups', []):
        groups = [str(s['subgroup'].get(key)) for s in pooled]
        if len(set(groups)) >= 2 and all(g != 'None' for g in groups):
            sg = meta.subgroups(y, v, groups, **kw)
            for g in sg['groups'].values():
                if 'estimate' in g:
                    g['display'] = [back(g['estimate']), back(g['ci_lb']), back(g['ci_ub'])]
            out['subgroups'][key] = sg
    out['meta_regression'] = {}
    for key in plan.get('meta_regression', []):
        xs = [s['subgroup'].get(key) for s in pooled]
        if all(isinstance(x, (int, float)) for x in xs) and len(set(xs)) >= 3:
            mr = meta.metareg(y, v, xs, method=model, knha=knha)
            if mr:
                out['meta_regression'][key] = mr
    rob_known = [s for s in pooled if s['rob']]
    rob_w = sum(s['weight'] for s in pooled if s['rob_high']) if rob_known else None
    out['grade'] = grade.rate(outcome, fit, pooled, out['egger'], [s['design'] for s in pooled], rob_w)
