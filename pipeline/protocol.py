"""Protocol validation: PICO, eligibility criteria, outcomes and the analysis plan, fixed before screening."""
from . import PipelineError

MEASURES = {'binary': {'RR', 'OR'}, 'continuous': {'MD', 'SMD'}, 'effect': {'HR', 'RR', 'OR', 'MD', 'SMD', 'beta'}, 'correlation': {'r'}}
RATIO = {'RR', 'OR', 'HR'}
DESIGNS = {'rct', 'cohort', 'case-control', 'cross-sectional', 'diagnostic', 'prognostic', 'any'}
TOOLS_BY_DESIGN = {'rct': 'RoB 2', 'cohort': 'ROBINS-I', 'case-control': 'ROBINS-I', 'cross-sectional': 'ROBINS-I',
                   'diagnostic': 'QUADAS-2', 'prognostic': 'QUIPS'}

TEMPLATE = {
    'question': '<the plain-language question>',
    'pico': {'population': '', 'intervention_or_exposure': '', 'comparator': '', 'outcomes': ''},
    'designs': ['rct'],
    'exposure_study': False,
    'inclusion': [{'id': 'I1', 'text': ''}],
    'exclusion': [{'id': 'E1', 'text': '', 'code': 'POPULATION'}],
    'outcomes': [{'id': 'primary', 'label': '', 'type': 'binary', 'measure': 'RR', 'primary': True,
                  'lower_is_better': True, 'mid': None, 'timepoint': ''}],
    'search': {'sources': [{'source': 'pubmed', 'query': '', 'rationale': ''}],
               'date_from': None, 'date_to': None, 'languages': []},
    'analysis': {'pool': True, 'model': 'REML', 'knapp_hartung': True, 'level': 95, 'min_studies_to_pool': 2,
                 'subgroups': [], 'meta_regression': [], 'outlier_diagnostics': True},
}

EXCLUSION_CODES = {'POPULATION', 'INTERVENTION', 'COMPARATOR', 'OUTCOME', 'DESIGN', 'DUPLICATE', 'LANGUAGE',
                   'NOT_PRIMARY', 'OTHER'}


def validate(p):
    errors = []
    if not isinstance(p, dict):
        return ['protocol must be a JSON object']
    for key in ('question', 'pico', 'designs', 'inclusion', 'exclusion', 'outcomes', 'search', 'analysis'):
        if key not in p:
            errors.append(f'missing key: {key}')
    if errors:
        return errors
    pico = p['pico']
    for key in ('population', 'intervention_or_exposure', 'comparator', 'outcomes'):
        if not str(pico.get(key, '')).strip():
            errors.append(f'pico.{key} is empty')
    bad = [d for d in p['designs'] if d not in DESIGNS]
    if bad:
        errors.append(f'unknown designs {bad}; allowed: {sorted(DESIGNS)}')
    ids = set()
    for kind in ('inclusion', 'exclusion'):
        if not p[kind]:
            errors.append(f'{kind} criteria list is empty')
        for c in p[kind]:
            if not c.get('id') or not str(c.get('text', '')).strip():
                errors.append(f'{kind} criterion needs id and text: {c}')
            if c.get('id') in ids:
                errors.append(f'duplicate criterion id {c.get("id")}')
            ids.add(c.get('id'))
            if kind == 'exclusion' and c.get('code') not in EXCLUSION_CODES:
                errors.append(f'exclusion {c.get("id")} needs a code from {sorted(EXCLUSION_CODES)}')
    if not p['outcomes']:
        errors.append('outcomes list is empty')
    primaries = 0
    for o in p['outcomes']:
        if not o.get('id') or not o.get('label'):
            errors.append(f'outcome needs id and label: {o}')
        t = o.get('type')
        if t not in MEASURES:
            errors.append(f'outcome {o.get("id")}: type must be one of {sorted(MEASURES)}')
        elif o.get('measure') not in MEASURES[t]:
            errors.append(f'outcome {o.get("id")}: measure {o.get("measure")} not valid for type {t}; allowed {sorted(MEASURES[t])}')
        if 'lower_is_better' not in o:
            errors.append(f'outcome {o.get("id")}: lower_is_better (true/false) is required')
        if o.get('mid') is not None and not isinstance(o['mid'], (int, float)):
            errors.append(f'outcome {o.get("id")}: mid must be a number or null')
        primaries += bool(o.get('primary'))
    if primaries != 1:
        errors.append('exactly one outcome must have "primary": true')
    errors += validate_sources(p['search'])
    a = p['analysis']
    if a.get('model') not in ('REML', 'DL', 'FE'):
        errors.append('analysis.model must be REML, DL or FE')
    if a.get('level') not in (90, 95, 99):
        errors.append('analysis.level must be 90, 95 or 99')
    return errors


def search_sources(search):
    """The protocol's sources; legacy protocols with a single search.query are PubMed-only."""
    if search.get('sources'):
        return search['sources']
    if str(search.get('query', '')).strip():
        return [{'source': 'pubmed', 'query': search['query'], 'rationale': 'legacy single-source protocol'}]
    return []


def validate_sources(search):
    from .sources import SOURCES
    errors = []
    if search.get('sources') is None:
        if not str(search.get('query', '')).strip():
            errors.append('search.sources is missing: list the databases you chose, each with its own query and rationale (run  sources  for the catalog)')
        return errors
    srcs = search['sources']
    names = [s.get('source') for s in srcs]
    for s in srcs:
        if s.get('source') not in SOURCES:
            errors.append(f"unknown source {s.get('source')!r}; choose from {sorted(SOURCES)}")
        if not str(s.get('query', '')).strip():
            errors.append(f"source {s.get('source')}: query is empty (write it in that database's own syntax)")
        if not str(s.get('rationale', '')).strip():
            errors.append(f"source {s.get('source')}: rationale is empty (say why this database fits the question)")
        if s.get('source') == 'openalex' and ',' in str(s.get('query', '')):
            errors.append('source openalex: remove commas from the query (use AND/OR)')
    if len(set(names)) != len(names):
        errors.append('each source may appear only once')
    if len(set(names)) < 2 and not str(search.get('single_source_reason', '')).strip():
        errors.append('search at least 2 databases (systematic-review standard), or give search.single_source_reason')
    return errors


def rob_tool(protocol, design):
    """The risk-of-bias tool for a study design under this protocol."""
    design = (design or '').lower()
    if protocol.get('exposure_study') and design != 'rct':
        return 'ROBINS-E'
    return TOOLS_BY_DESIGN.get(design, 'ROBINS-I')


def outcome(protocol, outcome_id):
    for o in protocol['outcomes']:
        if o['id'] == outcome_id:
            return o
    raise PipelineError(f'unknown outcome id {outcome_id}; protocol outcomes: {[o["id"] for o in protocol["outcomes"]]}')
