"""API mode: drive the whole pipeline with direct model calls. Every reviewer/extractor call is a fresh,
independent request (no shared context), so A and B stay blind to each other exactly as with subagents."""
import io
import json
import re
from contextlib import redirect_stdout
from pathlib import Path

from . import PipelineError, llm, protocol as proto
from .store import Review

ROOT = Path(__file__).resolve().parent.parent
API_NOTE = ('\n\nAPI MODE: you cannot read or write files. Every file you need is included in the message below. '
            'Reply with ONLY the content that would go into the output file (JSON or JSON lines), no prose, no code fences.')


def role_prompt(name):
    text = (ROOT / 'agents' / f'{name}.md').read_text(encoding='utf-8')
    body = re.sub(r'^---.*?---\s*', '', text, count=1, flags=re.S)
    return body.strip() + API_NOTE


def cli(*argv):
    """Run a CLI command in-process; return (output, exit_code)."""
    from . import cli as _cli
    buf = io.StringIO()
    code = 0
    with redirect_stdout(buf):
        try:
            _cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code or 0
    return buf.getvalue(), code


def block(title, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=1, ensure_ascii=False)
    return f'\n\n===== {title} =====\n{content}\n===== END {title} ====='


PROTOCOL_GUIDE = """You are the lead methodologist of a systematic review. Write the protocol as JSON with exactly the
template's keys. Rules:
- pico: one sentence each. designs: subset of rct, cohort, case-control, cross-sectional, diagnostic, prognostic;
  exposure_study true for exposure/risk-factor questions.
- inclusion/exclusion: numbered criteria (I1.., E1..); every exclusion has a code from POPULATION, INTERVENTION,
  COMPARATOR, OUTCOME, DESIGN, NOT_PRIMARY, LANGUAGE, OTHER.
- outcomes: one per outcome, exactly one primary. type binary (RR/OR), continuous (MD/SMD) or effect (HR/RR/OR/MD
  reported with CI; measure must match what such studies report). lower_is_better true/false. mid: number or null.
- search.sources: first choose the databases that publish this kind of evidence (see SOURCES below); at least 2.
  Clinical/biomedical -> pubmed + europepmc (+ clinicaltrials for interventions); economics/social/environment ->
  openalex + semanticscholar; mixed -> pubmed + openalex. Each entry {"source", "query", "rationale"}; the query is
  in that database's own syntax: 2-3 concepts (population, intervention or exposure, optionally design), synonyms
  OR-ed within a concept, concepts AND-ed. No outcome terms. No language limits. Every hit is retrieved, so keep the
  combined total screenable (under ~20000).
- analysis: keep REML, knapp_hartung true, level 95, min_studies_to_pool 2; subgroups: ["design"] when designs mix.
Reply with the JSON object only."""


class Runner:
    def __init__(self, review, log=print, max_steps=400):
        self.review, self.log, self.max_steps = review, log, max_steps
        self.steps = 0

    def ask(self, role, system, parts, max_tokens=16000):
        self.steps += 1
        if self.steps > self.max_steps:
            raise PipelineError(f'stopped after {self.max_steps} model calls (raise --max-steps to continue)')
        user = ''.join(block(t, c) for t, c in parts)
        return llm.complete(role, system, user, review=self.review, max_tokens=max_tokens)

    # ------------------------------------------------------------------ stages
    def protocol(self):
        r = self.review
        question = r.read('review.json')['question']
        errors, text = [], ''
        for attempt in range(4):
            from . import sources as src
            catalog = {n: {k: v for k, v in i.items()} for n, i in src.CATALOG.items()}
            parts = [('QUESTION', question), ('TEMPLATE', proto.TEMPLATE), ('SOURCES', catalog),
                     ('SUGGESTED SOURCES FOR THIS QUESTION', [{'source': n, 'why': w} for n, w in src.suggest(question)])]
            if errors:
                parts.append(('PROBLEMS WITH YOUR PREVIOUS PROTOCOL (fix all)', '\n'.join(errors)))
            reply = self.ask('protocol', PROTOCOL_GUIDE, parts)
            try:
                p = llm.parse_json(reply)
            except PipelineError as exc:
                errors = [str(exc)]
                continue
            if isinstance(p, dict):
                p['question'] = question
            errors = proto.validate(p) if isinstance(p, dict) else ['reply must be one JSON object']
            if errors:
                continue
            r.write('protocol.json', p)
            out, code = cli('protocol', 'check')
            self.log(out.strip())
            if code == 0:
                return
            errors = [line.strip() for line in out.splitlines() if line.strip() and not line.startswith('NEXT')]
        raise PipelineError('could not produce a valid protocol after 4 attempts:\n' + '\n'.join(errors))

    def screen(self, stage_name):
        r = self.review
        system = role_prompt('screener')
        protocol = r.read('protocol.json')
        for b in sorted(r.path(f'screening/{stage_name}').glob('batch-*.json')):
            n = b.stem.split('-')[1]
            batch = r.read(f'screening/{stage_name}/{b.name}')
            records = batch['records']
            if stage_name == 'ft':
                records = [{**rec, 'full_text': r.text(f"fulltext/{rec['pmid']}.txt")} for rec in records]
            for who in ('A', 'B'):
                target = f'screening/{stage_name}/{who}-{n}.jsonl'
                if r.exists(target):
                    continue
                reply = self.ask(f'screener_{who}', system, [('ROLE', f'reviewer {who}, stage {stage_name}'), ('PROTOCOL', protocol), ('BATCH', {'stage': stage_name, 'records': records})])
                rows = llm.parse_json(reply)
                rows = rows if isinstance(rows, list) else [rows]
                r.path(target).write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows if isinstance(x, dict)), encoding='utf-8')
                self.log(f'{stage_name} batch {n} reviewer {who}: {len(rows)} decisions')
        out, code = cli('screen', stage_name)
        if code:
            bad = re.findall(r'screening/(ta|ft)/([AB])-(\d+)\.jsonl', out)
            if not bad:
                bad = re.findall(r'^ - ([AB])-(\d+):', out, flags=re.M)
                bad = [(stage_name, w, n) for w, n in bad]
            for _, who, n in set(bad):
                r.path(f'screening/{stage_name}/{who}-{n}.jsonl').unlink(missing_ok=True)
            if not bad:
                raise PipelineError('screening files invalid and could not identify which:\n' + out)
            self.log(f'{len(set(bad))} screening file(s) invalid; re-running those reviewers')
            return self.screen(stage_name) if self._retry(f'screen_{stage_name}') else None
        self.log(out.strip())
        if stage_name == 'ft' and r.exists('screening/ft/conflicts.json'):
            conflicts = r.read('screening/ft/conflicts.json')
            if conflicts and not r.exists('screening/ft/adjudication.jsonl'):
                rows = []
                for pmid, c in conflicts.items():
                    reply = self.ask('adjudicator', system, [('ROLE', 'adjudicator, stage ft'), ('PROTOCOL', protocol),
                                                             ('CONFLICT', c), ('FULL TEXT', r.text(f'fulltext/{pmid}.txt'))])
                    row = llm.parse_json(reply)
                    row = row[0] if isinstance(row, list) else row
                    row['pmid'] = pmid
                    rows.append(row)
                r.path('screening/ft/adjudication.jsonl').write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows), encoding='utf-8')
                out, code = cli('screen', 'ft')
                self.log(out.strip())

    def extract(self):
        r = self.review
        system = role_prompt('extractor')
        protocol = r.read('protocol.json')
        included = [p for p, d in r.read('screening/ft_final.json').items() if d['decision'] == 'include']
        feedback = r.read('work/extract_feedback.json', {})
        for pmid in sorted(included):
            if r.exists(f'extraction/{pmid}.final.json'):
                continue
            paper = r.text(f'fulltext/{pmid}.txt')
            if r.exists(f'extraction/{pmid}.conflicts.json') and not r.exists(f'extraction/{pmid}.R.json'):
                reply = self.ask('reconciler', system, [('ROLE', 'reconciler'), ('PROTOCOL', protocol), ('PAPER', paper),
                                                        ('EXTRACTION A', r.read(f'extraction/{pmid}.A.json')), ('EXTRACTION B', r.read(f'extraction/{pmid}.B.json')),
                                                        ('CONFLICTS', r.read(f'extraction/{pmid}.conflicts.json'))])
                r.write(f'extraction/{pmid}.R.json', llm.parse_json(reply))
                self.log(f'{pmid}: reconciled')
                continue
            for who in ('A', 'B'):
                target = f'extraction/{pmid}.{who}.json'
                if r.exists(target):
                    continue
                parts = [('ROLE', f'extractor {who}'), ('PROTOCOL', protocol), ('PAPER', paper)]
                if feedback.get(target):
                    parts.append(('YOUR PREVIOUS ATTEMPT WAS REJECTED FOR THESE REASONS (fix all)', '\n'.join(feedback[target])))
                reply = self.ask(f'extractor_{who}', system, parts, max_tokens=32000)
                r.write(target, llm.parse_json(reply))
                self.log(f'{pmid}: extractor {who} done')
        out, _ = cli('extract')
        self.log(out.strip())
        rejected = {}
        current = None
        for line in out.splitlines():
            m = re.match(r'^ (extraction/\S+\.json):$', line)
            if m:
                current = m.group(1); rejected[current] = []
            elif current and line.startswith('   - '):
                rejected[current].append(line[5:])
            elif not line.startswith(' '):
                current = None
        if rejected:
            for name, errs in rejected.items():
                r.path(name).unlink(missing_ok=True)
                feedback[name] = errs
            r.write('work/extract_feedback.json', feedback)
            if self._retry('extract'):
                return self.extract()
            raise PipelineError('extractions kept failing validation:\n' + json.dumps(rejected, indent=1))

    def rob(self):
        r = self.review
        system = role_prompt('appraiser')
        protocol = r.read('protocol.json')
        included = [p for p, d in r.read('screening/ft_final.json').items() if d['decision'] == 'include']
        feedback = r.read('work/rob_feedback.json', {})
        for pmid in sorted(included):
            if r.read(f'rob/{pmid}.json', {}).get('overall'):
                continue
            if r.exists(f'rob/{pmid}.json'):
                continue
            final = r.read(f'extraction/{pmid}.final.json')
            design = ((final.get('study') or {}).get('design') or {}).get('value')
            tool = proto.rob_tool(protocol, design)
            parts = [('ROLE', f'appraiser, tool "{tool}"'), ('PROTOCOL', protocol), ('EXTRACTION', final), ('PAPER', r.text(f'fulltext/{pmid}.txt'))]
            if feedback.get(pmid):
                parts.append(('YOUR PREVIOUS ATTEMPT WAS REJECTED FOR THESE REASONS (fix all)', '\n'.join(feedback[pmid])))
            reply = self.ask('appraiser', system, parts)
            r.write(f'rob/{pmid}.json', llm.parse_json(reply))
            self.log(f'{pmid}: risk of bias ({tool}) done')
        out, _ = cli('rob')
        self.log(out.strip())
        rejected = {}
        current = None
        for line in out.splitlines():
            m = re.match(r'^ rob/(\S+)\.json:$', line)
            if m:
                current = m.group(1); rejected[current] = []
            elif current and line.startswith('   - '):
                rejected[current].append(line[5:])
        if rejected:
            for pmid, errs in rejected.items():
                r.path(f'rob/{pmid}.json').unlink(missing_ok=True)
                feedback[pmid] = errs
            r.write('work/rob_feedback.json', feedback)
            if self._retry('rob'):
                return self.rob()
            raise PipelineError('risk-of-bias files kept failing validation:\n' + json.dumps(rejected, indent=1))

    def critic(self):
        r = self.review
        reply = self.ask('critic', role_prompt('critic'), [('PROTOCOL', r.read('protocol.json')), ('RESULTS', r.read('analysis/results.json')),
                                                           ('REPORT', r.text('report/report.md'))])
        r.write('report/critic.json', llm.parse_json(reply))
        out, _ = cli('report')
        self.log(out.strip())

    def _retry(self, key):
        counts = self.review.read('work/retries.json', {})
        counts[key] = counts.get(key, 0) + 1
        self.review.write('work/retries.json', counts)
        return counts[key] <= 3

    # ------------------------------------------------------------------ loop
    def run(self):
        from .cli import stage
        r = self.review
        r.write('review.json', {**r.read('review.json'), 'settings': {**r.read('review.json').get('settings', {}), 'ai_model': llm.describe(), 'mode': 'api'}})
        last, same = None, 0
        while True:
            name, text = stage(r)
            same = same + 1 if name == last else 0
            last = name
            if same >= 4:
                raise PipelineError(f'stuck at stage {name}:\n{text}')
            self.log(f'[{r.slug}] stage: {name}')
            if name == 'done':
                self.log(text)
                return
            if name == 'protocol':
                self.protocol()
            elif name in ('search', 'fulltext', 'analyze', 'report'):
                out, code = cli(name)
                self.log(out.strip())
                if code:
                    raise PipelineError(f'{name} failed:\n{out}')
                if name == 'fulltext' and 'No full text available' in out:
                    raise PipelineError('no full texts: add PDFs to ' + str(r.path('fulltext/uploads')) + ' and run again')
            elif name == 'screen_ta':
                self.screen('ta')
            elif name == 'screen_ft':
                self.screen('ft')
            elif name == 'extract':
                self.extract()
            elif name == 'rob':
                self.rob()
            elif name == 'critic':
                self.critic()
            else:
                raise PipelineError(f'unknown stage {name}')


def run(question=None, log=print, max_steps=400):
    if question:
        review = Review.create(question)
    else:
        review = Review.active()
    Runner(review, log=log, max_steps=max_steps).run()
    return review
