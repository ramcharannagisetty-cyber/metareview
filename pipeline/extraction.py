"""Extraction validation: every number must sit in a verbatim quote, and every quote must sit in the paper."""
import re
import unicodedata

NUMBER = re.compile(r'[+\-−]?\d+(?:[.,]\d+)*')
DASHES = dict.fromkeys(map(ord, '‐‑‒–—−'), '-')
QUOTES = {0x2018: "'", 0x2019: "'", 0x201c: '"', 0x201d: '"', 0xa0: ' '}

STUDY_FIELDS = ['design', 'country', 'setting', 'n_total', 'population', 'intervention_or_exposure', 'comparator',
                'follow_up', 'funding']
RESULT_SHAPES = {
    'binary': ['events_1', 'n_1', 'events_2', 'n_2'],
    'continuous': ['mean_1', 'sd_1', 'n_1', 'mean_2', 'sd_2', 'n_2'],
    'effect': ['estimate', 'ci_low', 'ci_high'],
    'correlation': ['r', 'n'],
}


def normalize(text):
    text = unicodedata.normalize('NFKC', text or '').translate(DASHES).translate(QUOTES)
    return re.sub(r'\s+', ' ', text).strip().lower()


def quote_in_text(quote, text):
    q, t = normalize(quote), normalize(text)
    return bool(q) and len(q) >= 10 and q in t


def numbers(text):
    return [m.group(0).replace('−', '-').replace(',', '') for m in NUMBER.finditer(text or '')]


def number_in_quote(value, quote):
    """A numeric value counts as present when its digits appear in the quote (1,234 == 1234; 12.50 == 12.5)."""
    if value is None:
        return True
    target = str(value).replace(',', '').lstrip('+')
    try:
        f = float(target)
    except ValueError:
        return target.lower() in normalize(quote)
    for tok in numbers(quote):
        try:
            if abs(float(tok) - f) < 1e-9:
                return True
        except ValueError:
            continue
    # 0.5 may be written as .5 or 50%; allow the percentage form for proportions under 1
    return False


def validate(extraction, protocol, fulltext):
    """Return (errors, warnings). Errors block acceptance; warnings are reported."""
    errors, warnings = [], []
    if not isinstance(extraction, dict):
        return ['extraction must be a JSON object'], []
    study = extraction.get('study') or {}
    for key in STUDY_FIELDS:
        row = study.get(key)
        if row is None:
            warnings.append(f'study.{key} missing (use {{"value": null, "status": "not_reported"}} if the paper does not say)')
            continue
        if not isinstance(row, dict) or 'value' not in row:
            errors.append(f'study.{key} must be {{"value": ..., "quote": "..."}}')
            continue
        if row.get('value') is not None:
            if not quote_in_text(row.get('quote', ''), fulltext):
                errors.append(f'study.{key}: quote not found verbatim in the paper (quote at least 10 characters, copied exactly)')
            elif key == 'n_total' and not number_in_quote(row['value'], row['quote']):
                errors.append(f'study.{key}: value {row["value"]} does not appear in its quote')
    results = extraction.get('results')
    if not isinstance(results, list) or not results:
        errors.append('results must be a non-empty list (one entry per protocol outcome, or status not_reported)')
        return errors, warnings
    ids = {o['id']: o for o in protocol['outcomes']}
    seen = set()
    for r in results:
        oid = r.get('outcome')
        if oid not in ids:
            errors.append(f'result outcome {oid!r} is not a protocol outcome {sorted(ids)}')
            continue
        if oid in seen:
            errors.append(f'outcome {oid} appears twice; keep one entry per outcome (choose the protocol timepoint)')
        seen.add(oid)
        if r.get('status') == 'not_reported':
            continue
        shape = r.get('shape') or ids[oid]['type']
        if shape not in RESULT_SHAPES:
            errors.append(f'{oid}: shape must be binary, continuous, effect or correlation')
            continue
        quotes = r.get('quotes') or ([r['quote']] if r.get('quote') else [])
        if not quotes:
            errors.append(f'{oid}: quotes list is required (verbatim sentences or table rows containing every number)')
            continue
        bad_q = [q for q in quotes if not quote_in_text(q, fulltext)]
        if bad_q:
            errors.append(f'{oid}: {len(bad_q)} quote(s) not found verbatim in the paper: ' + ' | '.join(q[:60] for q in bad_q))
            continue
        joined = ' '.join(quotes)
        keys = ['estimate', 'se'] if shape == 'effect' and r.get('se') is not None and r.get('ci_low') is None else RESULT_SHAPES[shape]
        for key in keys:
            if key not in r:
                errors.append(f'{oid}: missing {key} for shape {shape}')
            elif r[key] is None:
                errors.append(f'{oid}: {key} is null; use status "not_reported" if the paper gives no usable numbers')
            elif not number_in_quote(r[key], joined):
                errors.append(f'{oid}: {key}={r[key]} does not appear in the quotes')
        if shape == 'effect':
            if r.get('measure') not in ('HR', 'RR', 'OR', 'MD', 'SMD', 'beta'):
                errors.append(f'{oid}: effect results need measure HR, RR, OR, MD, SMD or beta')
            if isinstance(r.get('ci_low'), (int, float)) and isinstance(r.get('ci_high'), (int, float)) and isinstance(r.get('estimate'), (int, float)):
                if not (r['ci_low'] <= r['estimate'] <= r['ci_high']):
                    errors.append(f'{oid}: estimate must lie inside its confidence interval')
            if r.get('ci_level') not in (None, 90, 95, 99):
                errors.append(f'{oid}: ci_level must be 90, 95 or 99')
            if 'adjusted' not in r:
                warnings.append(f'{oid}: say whether the effect estimate is adjusted (adjusted: true/false)')
        if shape == 'binary':
            for e, n in (('events_1', 'n_1'), ('events_2', 'n_2')):
                if isinstance(r.get(e), (int, float)) and isinstance(r.get(n), (int, float)) and r[e] > r[n]:
                    errors.append(f'{oid}: {e} exceeds {n}')
        if not r.get('timepoint'):
            warnings.append(f'{oid}: timepoint not stated')
    missing = sorted(set(ids) - seen)
    if missing:
        errors.append(f'no result entry for outcomes {missing} (add status "not_reported" when the paper lacks them)')
    return errors, warnings


def compare(a, b):
    """Field-by-field differences between two independent extractions of the same study."""
    diffs = []
    sa, sb = a.get('study') or {}, b.get('study') or {}
    for key in STUDY_FIELDS:
        va, vb = (sa.get(key) or {}).get('value'), (sb.get(key) or {}).get('value')
        if _differs(va, vb):
            diffs.append({'field': f'study.{key}', 'A': va, 'B': vb})
    ra = {r.get('outcome'): r for r in a.get('results', [])}
    rb = {r.get('outcome'): r for r in b.get('results', [])}
    for oid in sorted(set(ra) | set(rb)):
        x, y = ra.get(oid, {}), rb.get(oid, {})
        if x.get('status') != y.get('status') or x.get('shape') != y.get('shape'):
            diffs.append({'field': f'{oid}.status/shape', 'A': [x.get('status'), x.get('shape')], 'B': [y.get('status'), y.get('shape')]})
            continue
        for key in RESULT_SHAPES.get(x.get('shape') or '', []) + ['measure', 'timepoint', 'adjusted']:
            if _differs(x.get(key), y.get(key)):
                diffs.append({'field': f'{oid}.{key}', 'A': x.get(key), 'B': y.get(key)})
    return diffs


def _differs(x, y):
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return abs(x - y) > 1e-9
    if isinstance(x, str) and isinstance(y, str):
        return normalize(x) != normalize(y)
    return x != y
