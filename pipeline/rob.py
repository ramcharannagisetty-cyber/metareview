"""Risk-of-bias tools: domains, allowed judgements, and the overall judgement rule (worst domain)."""
from . import PipelineError

TOOLS = {
    'RoB 2': {
        'levels': ['low', 'some concerns', 'high'],
        'domains': ['randomization process', 'deviations from intended interventions', 'missing outcome data',
                    'measurement of the outcome', 'selection of the reported result'],
        'high': {'high'}},
    'ROBINS-I': {
        'levels': ['low', 'moderate', 'serious', 'critical', 'no information'],
        'domains': ['confounding', 'selection of participants', 'classification of interventions',
                    'deviations from intended interventions', 'missing data', 'measurement of outcomes',
                    'selection of the reported result'],
        'high': {'serious', 'critical'}},
    'ROBINS-E': {
        'levels': ['low', 'some concerns', 'high', 'very high', 'no information'],
        'domains': ['confounding', 'measurement of the exposure', 'selection of participants',
                    'post-exposure interventions', 'missing data', 'measurement of the outcome',
                    'selection of the reported result'],
        'high': {'high', 'very high'}},
    'QUIPS': {
        'levels': ['low', 'moderate', 'high'],
        'domains': ['study participation', 'study attrition', 'prognostic factor measurement', 'outcome measurement',
                    'study confounding', 'statistical analysis and reporting'],
        'high': {'high'}},
    'QUADAS-2': {
        'levels': ['low', 'high', 'unclear'],
        'domains': ['patient selection', 'index test', 'reference standard', 'flow and timing'],
        'high': {'high'}},
}


def validate(record, tool):
    spec = TOOLS.get(tool)
    if not spec:
        raise PipelineError(f'unknown tool {tool}; known: {sorted(TOOLS)}')
    errors = []
    if record.get('tool') != tool:
        errors.append(f'tool must be {tool!r} for this study (protocol design rule)')
    got = {d.get('domain'): d for d in record.get('domains', [])}
    for name in spec['domains']:
        d = got.get(name)
        if not d:
            errors.append(f'missing domain: {name}')
            continue
        if d.get('judgement') not in spec['levels']:
            errors.append(f'{name}: judgement must be one of {spec["levels"]}')
        if not str(d.get('support', '')).strip():
            errors.append(f'{name}: give a one-sentence support statement citing what the paper says')
    extra = set(got) - set(spec['domains'])
    if extra:
        errors.append(f'unknown domains for {tool}: {sorted(extra)}')
    return errors


def overall(record, tool):
    """Worst-domain rule. RoB 2: any 'high' -> high; any 'some concerns' -> some concerns; else low."""
    spec = TOOLS[tool]
    order = {lvl: i for i, lvl in enumerate(spec['levels'])}
    worst = max((d['judgement'] for d in record['domains']), key=lambda j: order.get(j, -1))
    if tool == 'RoB 2' and sum(d['judgement'] == 'some concerns' for d in record['domains']) >= 3 and worst != 'high':
        return 'high', 'several domains with some concerns (RoB 2 guidance: may be judged high)'
    return worst, 'worst domain'


def is_high(tool, judgement):
    return judgement in TOOLS[tool]['high']
