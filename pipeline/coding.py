"""Coding sheet import (Hansen et al. 2021, step 6): a wide-format CSV, one row per study, becomes a review project.

Required columns: study (label) and the numbers for one shape:
  binary       events_1, n_1, events_2, n_2
  continuous   mean_1, sd_1, n_1, mean_2, sd_2, n_2
  effect       estimate, ci_low, ci_high   (or estimate, se)
  correlation  r, n
Optional: year, outcome (defaults to the single protocol outcome), timepoint, rob (low|some concerns|high|...),
and any other column, which is kept as a moderator (subgroup or meta-regression variable).
"""
import csv
import io

from . import PipelineError
from .store import Review, home, now

SHAPES = {
    'binary': ['events_1', 'n_1', 'events_2', 'n_2'],
    'continuous': ['mean_1', 'sd_1', 'n_1', 'mean_2', 'sd_2', 'n_2'],
    'effect_ci': ['estimate', 'ci_low', 'ci_high'],
    'effect_se': ['estimate', 'se'],
    'correlation': ['r', 'n'],
}
RESERVED = {'study', 'year', 'outcome', 'timepoint', 'rob', 'authors', 'title', 'pmid', 'doi', 'journal', 'measure', 'ci_level', 'adjusted'}


def _num(value):
    if value is None or str(value).strip() == '':
        return None
    text = str(value).strip().replace(',', '')
    try:
        return int(text) if text.lstrip('-').isdigit() else float(text)
    except ValueError:
        return text


def parse(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise PipelineError('the coding sheet is empty')
    cols = {c.strip().lower(): c for c in rows[0].keys() if c}
    if 'study' not in cols:
        raise PipelineError('the coding sheet needs a "study" column')
    shape = next((name for name, keys in SHAPES.items() if all(k in cols for k in keys)), None)
    if not shape:
        raise PipelineError('no complete set of effect columns found; need one of: ' + '; '.join(f'{k}: {", ".join(v)}' for k, v in SHAPES.items()))
    moderators = [c for c in cols if c not in RESERVED and c not in sum(SHAPES.values(), [])]
    out = []
    for i, raw in enumerate(rows, 1):
        row = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
        if not row.get('study'):
            raise PipelineError(f'row {i}: study label missing')
        numbers = {k: _num(row.get(k)) for k in SHAPES[shape]}
        missing = [k for k, v in numbers.items() if v is None or isinstance(v, str)]
        if missing:
            raise PipelineError(f'row {i} ({row["study"]}): non-numeric or missing {missing}')
        out.append({'study': row['study'], 'year': _num(row.get('year')), 'outcome': row.get('outcome') or None,
                    'timepoint': row.get('timepoint') or None, 'rob': (row.get('rob') or '').lower() or None,
                    'measure': row.get('measure') or None, 'ci_level': _num(row.get('ci_level')),
                    'adjusted': str(row.get('adjusted', '')).lower() in ('1', 'true', 'yes'),
                    'pmid': row.get('pmid') or None, 'doi': row.get('doi') or None, 'title': row.get('title') or None,
                    'shape': 'effect' if shape.startswith('effect') else shape, 'numbers': numbers,
                    'moderators': {m: _num(row.get(m)) for m in moderators}})
    return out, shape, moderators


def create_project(question, sheet_text, measure, outcome_label=None, lower_is_better=True, model='REML'):
    rows, shape, moderators = parse(sheet_text)
    review = Review.create(question)
    data = review.read('review.json')
    data['settings'].update({'mode': 'sheet', 'reviewers': 'single'})
    review.write('review.json', data)
    outcome_ids = sorted({r['outcome'] for r in rows if r['outcome']}) or ['effect']
    kind = {'binary': 'binary', 'continuous': 'continuous', 'correlation': 'correlation'}.get(shape.split('_')[0], 'effect')
    protocol = {
        'question': question,
        'pico': {'population': 'as coded by the researcher', 'intervention_or_exposure': 'as coded by the researcher',
                 'comparator': 'as coded by the researcher', 'outcomes': outcome_label or ', '.join(outcome_ids)},
        'designs': ['any'], 'exposure_study': False,
        'inclusion': [{'id': 'I1', 'text': 'Studies listed in the researcher-supplied coding sheet.'}],
        'exclusion': [{'id': 'E1', 'text': 'Rows without usable effect-size numbers.', 'code': 'OTHER'}],
        'outcomes': [{'id': oid, 'label': outcome_label or oid, 'type': kind, 'measure': measure, 'primary': i == 0,
                      'lower_is_better': lower_is_better, 'mid': None, 'timepoint': ''} for i, oid in enumerate(outcome_ids)],
        'search': {'query': 'not applicable: studies supplied as a coding sheet', 'date_from': None, 'date_to': None, 'languages': []},
        'analysis': {'pool': True, 'model': model, 'knapp_hartung': True, 'level': 95, 'min_studies_to_pool': 2,
                     'subgroups': [m for m in moderators if any(isinstance(r['moderators'].get(m), str) for r in rows)],
                     'meta_regression': [m for m in moderators if all(isinstance(r['moderators'].get(m), (int, float)) for r in rows)],
                     'outlier_diagnostics': True},
    }
    review.write('protocol.json', protocol)
    review.path('coding_sheet.csv').write_text(sheet_text, encoding='utf-8')
    # Studies become records + final extractions so the analysis and report run unchanged.
    records, ta, ft = [], {}, {}
    for i, r in enumerate(rows, 1):
        key = r['pmid'] or f'S{i:03d}'
        label = r['study']
        records.append({'pmid': key, 'title': r['title'] or label, 'abstract': '', 'year': str(r['year'] or ''),
                        'authors': [label], 'journal': '', 'doi': r['doi'], 'pmcid': None, 'pubtypes': [], 'mesh': [], 'language': ''})
        ta[key] = {'pmid': key, 'decision': 'include', 'how': 'coding_sheet'}
        ft[key] = {'pmid': key, 'decision': 'include', 'how': 'coding_sheet'}
        result = {'outcome': r['outcome'] or outcome_ids[0], 'shape': r['shape'], 'timepoint': r['timepoint'],
                  'quotes': ['coding sheet row ' + str(i)], 'subgroup': r['moderators'], **r['numbers']}
        if r['shape'] == 'effect':
            result['measure'] = r['measure'] or measure
            if r['ci_level']:
                result['ci_level'] = r['ci_level']
            result['adjusted'] = r['adjusted']
        extraction = {'study': {'design': {'value': r['moderators'].get('design'), 'quote': 'coding sheet'}},
                      'results': [result], 'reconciled': 'coding_sheet', 'source': 'coding sheet'}
        review.write(f'extraction/{key}.final.json', extraction)
        if r['rob']:
            review.write(f'rob/{key}.json', {'tool': 'researcher-supplied', 'domains': [], 'overall': r['rob'], 'overall_rule': 'coding sheet'})
    review.path('search/records.jsonl').write_text(''.join(__import__('json').dumps(x) + '\n' for x in records), encoding='utf-8')
    review.write('search/summary.json', {'date': now()[:10], 'identified': len(records), 'duplicates_removed': 0, 'prescreen_excluded': 0,
                                         'to_screen': 0, 'batches': 0, 'chased': 0, 'complete': True, 'limits': {}, 'source': 'coding sheet'})
    review.write('screening/prescreen.json', {})
    review.write('screening/ta_final.json', ta)
    review.write('screening/ta_agreement.json', {'n': 0, 'percent': None, 'kappa': None})
    review.write('fulltext/status.json', {k: {'status': 'ok', 'source': 'coding sheet'} for k in ta})
    review.write('screening/ft_final.json', ft)
    review.write('screening/ft_agreement.json', {'n': 0, 'percent': None, 'kappa': None})
    review.write('protocol.lock.json', {'sha256': 'coding-sheet', 'locked': now()[:10]})
    review.log('coding_sheet_import', {'rows': len(rows), 'shape': shape, 'moderators': moderators})
    return review, len(rows), shape, moderators
