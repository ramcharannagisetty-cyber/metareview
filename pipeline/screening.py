"""Screening: deterministic pre-screen, batches for blind reviewers, merging, agreement (kappa), PRISMA counts."""
from collections import Counter

from . import PipelineError
from .protocol import EXCLUSION_CODES

NON_PRIMARY = {'Review', 'Systematic Review', 'Meta-Analysis', 'Editorial', 'Letter', 'Comment', 'News',
               'Case Reports', 'Guideline', 'Practice Guideline', 'Retracted Publication', 'Retraction of Publication'}
PRIMARY = {'Clinical Trial', 'Randomized Controlled Trial', 'Controlled Clinical Trial', 'Observational Study',
           'Multicenter Study', 'Comparative Study', 'Clinical Study', 'Pragmatic Clinical Trial', 'Cohort Studies'}
DECISIONS = {'include', 'exclude', 'unsure'}


def prescreen(record, protocol):
    """Rule-based exclusion that needs no judgement: non-primary publication types, language limits."""
    types = set(record.get('pubtypes') or [])
    if types & NON_PRIMARY and not types & PRIMARY:
        return {'decision': 'exclude', 'code': 'NOT_PRIMARY', 'reason': 'publication type: ' + ', '.join(sorted(types & NON_PRIMARY))}
    langs = [l.lower() for l in protocol['search'].get('languages') or []]
    if langs and record.get('language') and record['language'].lower() not in langs:
        return {'decision': 'exclude', 'code': 'LANGUAGE', 'reason': 'language ' + record['language']}
    if 'Retracted Publication' in types:
        return {'decision': 'exclude', 'code': 'OTHER', 'reason': 'retracted'}
    return None


def batches(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def validate_decision(row, stage, allowed_ids):
    errors = []
    if row.get('pmid') not in allowed_ids:
        errors.append(f'pmid {row.get("pmid")} is not in this batch')
    if row.get('decision') not in DECISIONS:
        errors.append(f'{row.get("pmid")}: decision must be include, exclude or unsure')
    if row.get('decision') == 'exclude':
        if row.get('code') not in EXCLUSION_CODES:
            errors.append(f'{row.get("pmid")}: exclusion needs a code from {sorted(EXCLUSION_CODES)}')
        if not str(row.get('reason', '')).strip():
            errors.append(f'{row.get("pmid")}: exclusion needs a reason')
    if stage == 'ft' and row.get('decision') == 'unsure':
        errors.append(f'{row.get("pmid")}: full-text decisions must be include or exclude')
    return errors


def merge(a, b, stage):
    """Combine reviewer A and B decisions. Returns (final, conflicts, agreement)."""
    final, conflicts = {}, []
    pmids = sorted(set(a) | set(b))
    for pmid in pmids:
        da, db = a.get(pmid), b.get(pmid)
        if da is None or db is None:
            raise PipelineError(f'{pmid}: missing decision from reviewer {"A" if da is None else "B"}')
        x, y = da['decision'], db['decision']
        if x == y and x != 'unsure':
            final[pmid] = {**da, 'how': 'agreed'}
        elif stage == 'ta':
            # Title/abstract: any include or unsure advances to full text (sensitivity over specificity).
            keep = da if x in ('include', 'unsure') else db
            final[pmid] = {**keep, 'decision': 'include', 'how': 'advanced_on_disagreement'}
        else:
            conflicts.append({'pmid': pmid, 'A': da, 'B': db})
    return final, conflicts, agreement(a, b)


def agreement(a, b):
    """Percent agreement and Cohen's kappa on include-vs-not for the records both reviewers screened."""
    keys = sorted(set(a) & set(b))
    n = len(keys)
    if n == 0:
        return {'n': 0, 'percent': None, 'kappa': None}
    inc = lambda d: d['decision'] == 'include'
    counts = Counter((inc(a[k]), inc(b[k])) for k in keys)
    po = (counts[(True, True)] + counts[(False, False)]) / n
    pa_yes = (counts[(True, True)] + counts[(True, False)]) / n
    pb_yes = (counts[(True, True)] + counts[(False, True)]) / n
    pe = pa_yes * pb_yes + (1 - pa_yes) * (1 - pb_yes)
    kappa = None if pe == 1 else (po - pe) / (1 - pe)
    return {'n': n, 'percent': round(100 * po, 1), 'kappa': None if kappa is None else round(kappa, 3)}


def prisma(review):
    """PRISMA 2020 counts from stored files only."""
    s = review.read('search/summary.json', {})
    ta = review.read('screening/ta_final.json', {})
    ft = review.read('screening/ft_final.json', {})
    retrieval = review.read('fulltext/status.json', {})
    identified = s.get('identified', 0)
    duplicates = s.get('duplicates_removed', 0)
    pre = review.read('screening/prescreen.json', {})
    screened = identified - duplicates
    excluded_ta = sum(1 for v in ta.values() if v['decision'] == 'exclude') + len(pre)
    sought = [k for k, v in ta.items() if v['decision'] == 'include']
    not_retrieved = [k for k in sought if retrieval.get(k, {}).get('status') != 'ok']
    assessed = [k for k in sought if retrieval.get(k, {}).get('status') == 'ok']
    reasons = Counter(v.get('code', 'OTHER') for v in ft.values() if v['decision'] == 'exclude')
    included = [k for k, v in ft.items() if v['decision'] == 'include']
    return {'records_identified': identified, 'duplicates_removed': duplicates, 'records_screened': screened,
            'records_excluded': excluded_ta, 'reports_sought': len(sought), 'reports_not_retrieved': len(not_retrieved),
            'reports_assessed': len(assessed), 'reports_excluded': dict(reasons), 'studies_included': len(included),
            'not_retrieved_pmids': not_retrieved, 'included_pmids': included}
