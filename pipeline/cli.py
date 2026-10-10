"""Command line: one command per pipeline step, and `next`, which always says exactly what to do now."""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from . import PipelineError, __version__, analysis, extraction, fulltext as ft, protocol as proto, pubmed, report, rob as robmod, screening
from .store import Review, home

ROOT_AGENTS = Path(__file__).resolve().parent.parent / 'agents'

TA_BATCH, FT_BATCH = 25, 4


def single(review):
    return review.read('review.json').get('settings', {}).get('reviewers') == 'single'


def reviewers(review):
    return ('A',) if single(review) else ('A', 'B')


def who_does(review, role):
    """How the NEXT text names the worker: a subagent (dual mode) or the lead itself (single-agent mode)."""
    if single(review):
        return f'YOURSELF (read {ROOT_AGENTS}/{role}.md once, then do the task in this context and write the output file)'
    return f'a fresh metareview:{role} subagent'


def say(*parts):
    print(*parts)


def sha(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ----------------------------------------------------------------------------- stage detection

def stage(review):
    """Return (stage_name, instruction text). The instruction is complete: file paths and the subagent to use."""
    root = review.root
    if review.read('review.json').get('settings', {}).get('mode') == 'sheet':
        if not review.exists('analysis/results.json'):
            return 'analyze', 'Run:  analyze'
        if not review.exists('report/report.md'):
            return 'report', 'Run:  report'
        if not review.exists('report/critic.json'):
            return 'critic', (f'Critique: {who_does(review, "critic")} with: report {root / "report/report.md"}, results {root / "analysis/results.json"}, '
                              f'protocol {root / "protocol.json"}, output {root / "report/critic.json"}. Then run:  report   and you are DONE.')
        return 'done', f'DONE. Report: {root / "report/index.html"} (and report.md). Tell the researcher it is an unverified AI draft.'
    if not review.exists('protocol.lock.json'):
        return 'protocol', (f'Write the protocol to {root / "protocol.json"} (template already there; fill every field, keep the JSON shape), '
                            f'then run:  protocol check')
    if not review.exists('search/summary.json'):
        return 'search', 'Run:  search'
    if not review.exists('screening/ta_final.json'):
        missing = _missing_batches(review, 'ta')
        if missing:
            return 'screen_ta', _batch_instruction(review, 'ta', missing)
        return 'screen_ta', 'Run:  screen ta'
    if not review.exists('fulltext/status.json'):
        return 'fulltext', 'Run:  fulltext'
    if not review.exists('screening/ft_final.json'):
        missing = _missing_batches(review, 'ft')
        if missing:
            return 'screen_ft', _batch_instruction(review, 'ft', missing)
        conflicts = review.read('screening/ft/conflicts.json', {})
        if conflicts and not review.exists('screening/ft/adjudication.jsonl'):
            return 'screen_ft', (f'{len(conflicts)} full-text disagreements need a third reviewer: {who_does(review, "screener")} '
                                 f'(role: adjudicator) with: protocol {root / "protocol.json"}, conflicts {root / "screening/ft/conflicts.json"}, '
                                 f'output {root / "screening/ft/adjudication.jsonl"}. Then run:  screen ft')
        return 'screen_ft', 'Run:  screen ft'
    todo = _extraction_todo(review)
    if todo:
        return 'extract', todo
    todo = _rob_todo(review)
    if todo:
        return 'rob', todo
    if not review.exists('analysis/results.json'):
        return 'analyze', 'Run:  analyze'
    if not review.exists('report/report.md'):
        return 'report', 'Run:  report'
    if not review.exists('report/critic.json'):
        return 'critic', (f'Critique: {who_does(review, "critic")} with: report {root / "report/report.md"}, results {root / "analysis/results.json"}, '
                          f'protocol {root / "protocol.json"}, output {root / "report/critic.json"}. Then run:  report   (findings are appended) and you are DONE.')
    return 'done', f'DONE. Report: {root / "report/index.html"} (and report.md). Tell the researcher it is an unverified AI draft.'


def _missing_batches(review, stage_name):
    batches = sorted(review.path(f'screening/{stage_name}').glob('batch-*.json'))
    missing = []
    for b in batches:
        n = b.stem.split('-')[1]
        for who in reviewers(review):
            if not review.exists(f'screening/{stage_name}/{who}-{n}.jsonl'):
                missing.append((n, who))
    return missing


def _batch_instruction(review, stage_name, missing):
    root = review.root
    lines = [f'{len(missing)} screening runs outstanding for stage {stage_name.upper()}. For EACH line below: {who_does(review, "screener")}'
             + ('.' if single(review) else ' (run A and B in parallel, never the same subagent for both). Give it exactly: role, stage, protocol path, batch path, output path.')]
    for n, who in missing:
        lines.append(f'- reviewer {who}, stage {stage_name}, protocol {root / "protocol.json"}, batch {root / f"screening/{stage_name}/batch-{n}.json"}, output {root / f"screening/{stage_name}/{who}-{n}.jsonl"}')
    lines.append(f'When all outputs exist run:  screen {stage_name}')
    return '\n'.join(lines)


def _included(review):
    return sorted(p for p, d in review.read('screening/ft_final.json', {}).items() if d['decision'] == 'include')


def _extraction_todo(review):
    root = review.root
    lines = []
    for pmid in _included(review):
        if review.exists(f'extraction/{pmid}.final.json'):
            continue
        text = root / f'fulltext/{pmid}.txt'
        for who in reviewers(review):
            if not review.exists(f'extraction/{pmid}.{who}.json'):
                lines.append(f'- extractor {who}: protocol {root / "protocol.json"}, paper {text}, output {root / f"extraction/{pmid}.{who}.json"}')
        if review.exists(f'extraction/{pmid}.conflicts.json') and not review.exists(f'extraction/{pmid}.R.json'):
            lines.append(f'- reconciler: protocol {root / "protocol.json"}, paper {text}, A {root / f"extraction/{pmid}.A.json"}, B {root / f"extraction/{pmid}.B.json"}, '
                         f'conflicts {root / f"extraction/{pmid}.conflicts.json"}, output {root / f"extraction/{pmid}.R.json"}')
    if lines:
        return (f'Extraction, one line at a time: {who_does(review, "extractor")}'
                + ('' if single(review) else ' (A and B in parallel; a reconciler only after a conflicts file exists)')
                + '. Then run:  extract\n' + '\n'.join(lines))
    if any(not review.exists(f'extraction/{p}.final.json') for p in _included(review)):
        return 'Run:  extract'
    return None


def _rob_todo(review):
    root = review.root
    protocol = review.read('protocol.json')
    lines = []
    for pmid in _included(review):
        rec = review.read(f'rob/{pmid}.json', {})
        if rec.get('overall'):
            continue
        design = ((review.read(f'extraction/{pmid}.final.json', {}).get('study') or {}).get('design') or {}).get('value')
        tool = proto.rob_tool(protocol, design)
        if not review.exists(f'rob/{pmid}.json'):
            lines.append(f'- appraiser: tool "{tool}", protocol {root / "protocol.json"}, paper {root / f"fulltext/{pmid}.txt"}, extraction {root / f"extraction/{pmid}.final.json"}, output {root / f"rob/{pmid}.json"}')
    if lines:
        return f'Risk of bias, one line at a time: {who_does(review, "appraiser")}. Then run:  rob\n' + '\n'.join(lines)
    if any(not review.read(f'rob/{p}.json', {}).get('overall') for p in _included(review)):
        return 'Run:  rob'
    return None


# ----------------------------------------------------------------------------- commands

def cmd_init(args):
    review = Review.create(args.question)
    data = review.read('review.json')
    data['settings']['reviewers'] = args.reviewers
    data['settings']['mode'] = args.mode
    review.write('review.json', data)
    template = json.loads(json.dumps(proto.TEMPLATE))
    template['question'] = args.question
    template['analysis']['pool'] = args.mode != 'sr' or args.with_meta_analysis
    review.write('protocol.json', template)
    say(f'Review created: {review.root}')
    say('NEXT:', stage(review)[1])


def cmd_use(args):
    if not (home() / args.slug).is_dir():
        raise PipelineError(f'no review named {args.slug} under {home()}')
    (home() / '.active').write_text(args.slug)
    say('active review:', args.slug)


def cmd_next(args):
    review = Review.active()
    name, text = stage(review)
    say(f'[{review.slug}] stage: {name}')
    say('NEXT:', text)


def cmd_protocol(args):
    review = Review.active()
    p = review.read('protocol.json')
    errors = proto.validate(p)
    if errors:
        say('PROTOCOL INVALID:')
        for e in errors:
            say(' -', e)
        say('Fix protocol.json and run:  protocol check')
        sys.exit(2)
    if review.exists('protocol.lock.json') and review.read('protocol.lock.json')['sha256'] != sha(p):
        say('WARNING: protocol changed after it was locked. Deviations must be reported; the lock is updated and logged.')
        review.log('protocol_changed', {'old': review.read('protocol.lock.json')['sha256'], 'new': sha(p)})
    if not args.offline:
        n, errs = pubmed.count(p['search']['query'], p['search'].get('date_from'), p['search'].get('date_to'))
        if errs:
            say('PubMed reported problems with the query:', json.dumps(errs)[:500])
            say('Fix search.query and run:  protocol check')
            sys.exit(2)
        say(f'PubMed count for the query: {n}')
        if n == 0:
            say('Zero hits: broaden the query (synonyms, MeSH plus free text, no outcome terms). Then run:  protocol check')
            sys.exit(2)
        if n > args.max_hits:
            say(f'{n} hits exceed the limit of {args.max_hits}. Narrow the question/query (specific population or exposure; keep sensitivity), or rerun with --max-hits N. Then run:  protocol check')
            sys.exit(2)
        p['search']['expected_count'] = n
        review.write('protocol.json', p)
    review.write('protocol.lock.json', {'sha256': sha(p), 'locked': time.strftime('%Y-%m-%d')})
    review.log('protocol_locked', {'sha256': sha(p)})
    say('Protocol locked.')
    say('NEXT:', stage(review)[1])


def cmd_search(args):
    review = Review.active()
    if not review.exists('protocol.lock.json'):
        raise PipelineError('lock the protocol first:  protocol check')
    p = review.read('protocol.json')
    q = p['search']
    ids, total = pubmed.search_ids(q['query'], q.get('date_from'), q.get('date_to'), limit=args.limit)
    say(f'PubMed: {total} hits, retrieving {len(ids)}')
    records = pubmed.fetch(ids)
    chased = 0
    if args.chase and args.chase_pmids:
        seeds = [s.strip() for s in args.chase_pmids.split(',') if s.strip()]
        extra = set(pubmed.linked(seeds, 'pubmed_pubmed_citedin')) | set(pubmed.linked(seeds, 'pubmed_pubmed_refs'))
        extra -= {r['pmid'] for r in records}
        chased = len(extra)
        records += [{**r, 'source': 'citation_chase'} for r in pubmed.fetch(sorted(extra))]
    seen_titles, unique, dups = {}, [], 0
    for r in records:
        key = re.sub(r'[^a-z0-9]', '', r['title'].lower())[:80]
        if key and key in seen_titles:
            dups += 1
            continue
        seen_titles[key] = r['pmid']
        unique.append(r)
    review.path('search/records.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in unique), encoding='utf-8')
    pre = {}
    for r in unique:
        rule = screening.prescreen(r, p)
        if rule:
            pre[r['pmid']] = rule
    review.write('screening/prescreen.json', pre)
    to_screen = [r for r in unique if r['pmid'] not in pre]
    for i, batch in enumerate(screening.batches(to_screen, TA_BATCH), 1):
        review.write(f'screening/ta/batch-{i:02d}.json', {'stage': 'ta', 'batch': i, 'records': [
            {'pmid': r['pmid'], 'title': r['title'], 'abstract': r['abstract'], 'year': r['year'], 'pubtypes': r['pubtypes']} for r in batch]})
    summary = {'date': time.strftime('%Y-%m-%d'), 'identified': len(records), 'duplicates_removed': dups, 'prescreen_excluded': len(pre),
               'to_screen': len(to_screen), 'batches': (len(to_screen) + TA_BATCH - 1) // TA_BATCH, 'chased': chased,
               'complete': len(ids) >= total, 'limits': {k: v for k, v in q.items() if k in ('date_from', 'date_to', 'languages') and v}}
    review.write('search/summary.json', summary)
    review.log('search', summary)
    say(f'Records: {len(records)} identified, {dups} duplicates, {len(pre)} excluded by rule (publication type/language), {len(to_screen)} to screen in {summary["batches"]} batches.')
    if not summary['complete']:
        say(f'WARNING: only {len(ids)} of {total} hits retrieved (limit). Raise --limit for a complete review.')
    say('NEXT:', stage(review)[1])


def _load_decisions(review, stage_name, who, n, allowed):
    rows = review.lines(f'screening/{stage_name}/{who}-{n}.jsonl')
    errors, out = [], {}
    for row in rows:
        errors += screening.validate_decision(row, stage_name, allowed)
        out[str(row.get('pmid'))] = row
    missing = allowed - set(out)
    if missing:
        errors.append(f'{who}-{n}: no decision for {sorted(missing)[:10]}{"…" if len(missing) > 10 else ""}')
    return out, errors


def cmd_screen(args):
    review = Review.active()
    stage_name = args.stage
    batches = sorted(review.path(f'screening/{stage_name}').glob('batch-*.json'))
    if not batches:
        raise PipelineError(f'no {stage_name} batches yet')
    A, B, errors = {}, {}, []
    for b in batches:
        n = b.stem.split('-')[1]
        allowed = {str(r['pmid']) for r in review.read(f'screening/{stage_name}/{b.name}')['records']}
        for who, target in [('A', A), ('B', B)][:len(reviewers(review))]:
            if not review.exists(f'screening/{stage_name}/{who}-{n}.jsonl'):
                errors.append(f'missing screening/{stage_name}/{who}-{n}.jsonl')
                continue
            rows, errs = _load_decisions(review, stage_name, who, n, allowed)
            errors += errs
            target.update(rows)
    if errors:
        say('SCREENING FILES INVALID (fix or re-run the reviewer for that batch):')
        for e in errors[:40]:
            say(' -', e)
        sys.exit(2)
    if single(review):
        final = {k: {**v, 'decision': 'include' if v['decision'] == 'unsure' else v['decision'], 'how': 'single_reviewer'} for k, v in A.items()}
        conflicts, agree = [], {'n': len(A), 'percent': None, 'kappa': None, 'mode': 'single'}
    else:
        final, conflicts, agree = screening.merge(A, B, stage_name)
    if stage_name == 'ft' and not single(review):
        adj = {str(r['pmid']): r for r in review.lines('screening/ft/adjudication.jsonl')}
        unresolved = []
        for c in conflicts:
            row = adj.get(c['pmid'])
            if row and not screening.validate_decision(row, 'ft', {c['pmid']}):
                final[c['pmid']] = {**row, 'how': 'adjudicated'}
            else:
                unresolved.append(c)
        review.write('screening/ft/conflicts.json', {c['pmid']: c for c in unresolved})
        if unresolved:
            say(f'{len(unresolved)} full-text disagreements need adjudication.')
            say('NEXT:', stage(review)[1])
            return
    review.write(f'screening/{stage_name}_final.json', final)
    review.write(f'screening/{stage_name}_agreement.json', agree)
    inc = sum(1 for v in final.values() if v['decision'] == 'include')
    review.log(f'screen_{stage_name}', {'n': len(final), 'included': inc, 'agreement': agree})
    say(f'{stage_name.upper()} screening merged: {len(final)} records, {inc} included; agreement {agree["percent"]}%, kappa {agree["kappa"]}.')
    say('NEXT:', stage(review)[1])


def cmd_fulltext(args):
    review = Review.active()
    ta = review.read('screening/ta_final.json')
    records = review.records()
    status = review.read('fulltext/status.json', {})
    for pmid, d in sorted(ta.items()):
        if d['decision'] != 'include':
            continue
        if status.get(pmid, {}).get('status') == 'ok' and review.exists(f'fulltext/{pmid}.txt'):
            continue
        text, err = ft.uploaded(review, pmid)
        source = 'upload'
        if text is None and not err:
            text = ft.europepmc(records.get(pmid, {}).get('pmcid'), pmid)
            source = 'europepmc'
        if text:
            review.path(f'fulltext/{pmid}.txt').write_text(text, encoding='utf-8')
            status[pmid] = {'status': 'ok', 'source': source, 'chars': len(text)}
        else:
            status[pmid] = {'status': 'missing', 'error': err or 'no open-access full text found'}
    review.write('fulltext/status.json', status)
    ok = [p for p, s in status.items() if s['status'] == 'ok']
    missing = [p for p, s in status.items() if s['status'] != 'ok']
    for i, batch in enumerate(screening.batches(sorted(ok), FT_BATCH), 1):
        review.write(f'screening/ft/batch-{i:02d}.json', {'stage': 'ft', 'batch': i, 'records': [
            {'pmid': p, 'title': records.get(p, {}).get('title'), 'file': str(review.path(f'fulltext/{p}.txt'))} for p in batch]})
    review.log('fulltext', {'ok': len(ok), 'missing': len(missing)})
    say(f'Full texts: {len(ok)} retrieved, {len(missing)} missing.')
    if missing:
        say(f'Missing (drop <pmid>.pdf or <pmid>.txt into {review.path("fulltext/uploads")} and rerun  fulltext ): ' + ', '.join(missing[:30]) + (' …' if len(missing) > 30 else ''))
    if not ok:
        say('No full text available for any record: the review cannot proceed past title/abstract. Ask the researcher for PDFs.')
        return
    say('NEXT:', stage(review)[1])


def cmd_extract(args):
    review = Review.active()
    protocol = review.read('protocol.json')
    problems = []
    for pmid in _included(review):
        if review.exists(f'extraction/{pmid}.final.json'):
            continue
        text = review.text(f'fulltext/{pmid}.txt')
        if review.exists(f'extraction/{pmid}.R.json'):
            r = review.read(f'extraction/{pmid}.R.json')
            errs, warns = extraction.validate(r, protocol, text)
            if errs:
                problems.append((f'extraction/{pmid}.R.json', errs))
                continue
            review.write(f'extraction/{pmid}.final.json', {**r, 'reconciled': 'resolved_by_reconciler', 'warnings': warns})
            continue
        ok = {}
        for who in reviewers(review):
            if not review.exists(f'extraction/{pmid}.{who}.json'):
                continue
            data = review.read(f'extraction/{pmid}.{who}.json')
            errs, warns = extraction.validate(data, protocol, text)
            if errs:
                problems.append((f'extraction/{pmid}.{who}.json', errs))
            else:
                ok[who] = data
        if single(review) and 'A' in ok:
            review.write(f'extraction/{pmid}.final.json', {**ok['A'], 'reconciled': 'single_extractor'})
        elif len(ok) == 2:
            diffs = extraction.compare(ok['A'], ok['B'])
            if diffs:
                review.write(f'extraction/{pmid}.conflicts.json', {'pmid': pmid, 'differences': diffs})
            else:
                review.write(f'extraction/{pmid}.final.json', {**ok['A'], 'reconciled': 'identical'})
    if problems:
        say('EXTRACTIONS REJECTED (delete the file and re-run that extractor with these errors in its prompt):')
        for name, errs in problems:
            say(f' {name}:')
            for e in errs[:12]:
                say('   -', e)
    done = sum(review.exists(f'extraction/{p}.final.json') for p in _included(review))
    say(f'Final extractions: {done}/{len(_included(review))}.')
    say('NEXT:', stage(review)[1])


def cmd_rob(args):
    review = Review.active()
    protocol = review.read('protocol.json')
    problems = []
    for pmid in _included(review):
        if not review.exists(f'rob/{pmid}.json'):
            continue
        rec = review.read(f'rob/{pmid}.json')
        design = ((review.read(f'extraction/{pmid}.final.json', {}).get('study') or {}).get('design') or {}).get('value')
        tool = proto.rob_tool(protocol, design)
        errs = robmod.validate(rec, tool)
        if errs:
            problems.append((pmid, errs))
            continue
        overall, how = robmod.overall(rec, tool)
        review.write(f'rob/{pmid}.json', {**rec, 'overall': overall, 'overall_rule': how})
    if problems:
        say('RISK-OF-BIAS FILES REJECTED (delete and re-run the appraiser with these errors):')
        for pmid, errs in problems:
            say(f' rob/{pmid}.json:')
            for e in errs[:10]:
                say('   -', e)
    say('NEXT:', stage(review)[1])


def cmd_analyze(args):
    review = Review.active()
    protocol = review.read('protocol.json')
    results = analysis.run(review, protocol)
    for oid, o in results['outcomes'].items():
        if o.get('pooled'):
            p = o['pooled']
            say(f"{oid}: {o['measure']} {p['estimate']:.2f} ({p['ci_lb']:.2f} to {p['ci_ub']:.2f}), k={o['k']}, I²={o['fit']['I2']:.0f}%, GRADE {o['grade']['rating']}")
        else:
            say(f"{oid}: k={o['k']} — {'; '.join(o['warnings'])}")
    say('NEXT:', stage(review)[1])


def cmd_report(args):
    review = Review.active()
    files = report.build(review)
    say('Report written:', ', '.join(str(review.path('report/' + f)) for f in files if f.endswith(('.md', '.html'))))
    say('NEXT:', stage(review)[1])


def cmd_ma(args):
    from . import coding
    path = Path(args.csv)
    if not path.is_file():
        raise PipelineError(f'coding sheet not found: {path}')
    question = args.question or f'Meta-analysis of {path.stem}'
    review, n, shape, mods = coding.create_project(question, path.read_text(encoding='utf-8-sig'), args.measure, args.outcome_label,
                                                   lower_is_better=not args.higher_is_better, model=args.model)
    say(f'Imported {n} studies ({shape}); moderators: {", ".join(mods) or "none"}. Project: {review.root}')
    say('NEXT:', stage(review)[1])


def cmd_settings(args):
    review = Review.active()
    data = review.read('review.json')
    data.setdefault('settings', {})[args.key] = args.value
    review.write('review.json', data)
    review.log('settings', {args.key: args.value})
    say(f'{args.key} = {args.value}')


def cmd_prisma(args):
    say(json.dumps(screening.prisma(Review.active()), indent=1))


def cmd_run(args):
    from . import llm, runner
    if not args.question and not (home() / '.active').is_file():
        raise PipelineError('give a question:  run "<question>"')
    say('Model:', llm.describe())
    review = runner.run(args.question, log=say, max_steps=args.max_steps)
    say('Usage:', json.dumps(llm.usage_totals(review)))


def cmd_config(args):
    from . import llm
    values = {'provider': args.provider, 'model': args.model, 'effort': args.effort,
              'anthropic_api_key': args.anthropic_key, 'openai_api_key': args.openai_key}
    if any(v is not None for v in values.values()):
        llm.save_config(**values)
        say('saved to', llm.config_path())
    cfg = llm.load_config()
    say('provider:', cfg['provider'], '| model:', cfg['model'], '| effort:', cfg.get('effort') or 'default')
    say('anthropic key:', 'set' if cfg.get('anthropic_api_key') else 'not set', '| openai key:', 'set' if cfg.get('openai_api_key') else 'not set')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='metareview', description=f'metareview {__version__}: scripted systematic review + meta-analysis pipeline')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('init', help='start a review from a question'); s.add_argument('question')
    s.add_argument('--reviewers', choices=['single', 'dual'], default='dual', help='single: one agent does every task itself; dual: blind A/B reviewers')
    s.add_argument('--mode', choices=['sr', 'ma', 'full'], default='full', help='sr: systematic review with narrative synthesis; ma/full: with meta-analysis')
    s.add_argument('--with-meta-analysis', action='store_true', help='in sr mode, also pool when data allow'); s.set_defaults(fn=cmd_init)
    s = sub.add_parser('use', help='switch the active review'); s.add_argument('slug'); s.set_defaults(fn=cmd_use)
    for name in ('next', 'status'):
        sub.add_parser(name, help='what to do now').set_defaults(fn=cmd_next)
    s = sub.add_parser('protocol'); s.add_argument('action', choices=['check']); s.add_argument('--offline', action='store_true')
    s.add_argument('--max-hits', type=int, default=3000); s.set_defaults(fn=cmd_protocol)
    s = sub.add_parser('search'); s.add_argument('--limit', type=int, default=3000); s.add_argument('--chase', action='store_true')
    s.add_argument('--chase-pmids', default=''); s.set_defaults(fn=cmd_search)
    s = sub.add_parser('screen'); s.add_argument('stage', choices=['ta', 'ft']); s.set_defaults(fn=cmd_screen)
    sub.add_parser('fulltext').set_defaults(fn=cmd_fulltext)
    sub.add_parser('extract').set_defaults(fn=cmd_extract)
    sub.add_parser('rob').set_defaults(fn=cmd_rob)
    sub.add_parser('analyze').set_defaults(fn=cmd_analyze)
    sub.add_parser('report').set_defaults(fn=cmd_report)
    sub.add_parser('prisma').set_defaults(fn=cmd_prisma)
    s = sub.add_parser('run', help='API mode: run the whole review with your own API key'); s.add_argument('question', nargs='?')
    s.add_argument('--max-steps', type=int, default=400); s.set_defaults(fn=cmd_run)
    s = sub.add_parser('config', help='API mode settings: provider, model, effort, keys (stored in ~/.config/metareview/config.json)')
    s.add_argument('--provider', choices=['anthropic', 'openai']); s.add_argument('--model'); s.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    s.add_argument('--anthropic-key'); s.add_argument('--openai-key'); s.set_defaults(fn=cmd_config)
    s = sub.add_parser('ma', help='meta-analysis from a researcher-supplied coding sheet'); s.add_argument('action', choices=['import']); s.add_argument('csv')
    s.add_argument('--measure', required=True, choices=['RR', 'OR', 'HR', 'MD', 'SMD', 'r', 'beta']); s.add_argument('--question'); s.add_argument('--outcome-label')
    s.add_argument('--higher-is-better', action='store_true'); s.add_argument('--model', choices=['REML', 'DL', 'FE'], default='REML'); s.set_defaults(fn=cmd_ma)
    s = sub.add_parser('settings', help='settings ai_model "<name>" | signoff "<human name>"'); s.add_argument('key', choices=['ai_model', 'signoff', 'reviewers']); s.add_argument('value'); s.set_defaults(fn=cmd_settings)
    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except PipelineError as exc:
        say('ERROR:', exc)
        sys.exit(1)
