"""Report: Markdown and a self-contained HTML page, written from stored data only."""
import csv
import html
import io
import re

from . import __version__, effects, plots, screening
from .analysis import label as study_label


def _f(x, nd=2):
    return 'n/a' if x is None else f'{x:.{nd}f}'


def table(headers, rows):
    out = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    out += ['| ' + ' | '.join(str('' if c is None else c).replace('|', '/').replace('\n', ' ') for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def reference(rec):
    authors = rec.get('authors') or []
    a = ', '.join(authors[:6]) + (', et al.' if len(authors) > 6 else '')
    return f"{a} {rec.get('title', '')} {rec.get('journal', '')}. {rec.get('year', '')}." + (f" doi:{rec['doi']}" if rec.get('doi') else '') + f" {idlabel(rec, rec.get('pmid'))}."


def idlabel(rec, key):
    """Human identifier for a record: PMID when it has one, else NCT number, DOI or the internal id."""
    rec = rec or {}
    if rec.get('pubmed_id') or (key and str(key).isdigit()):
        return f"PMID {rec.get('pubmed_id') or key}"
    if rec.get('nct_id'):
        return rec['nct_id']
    if rec.get('doi'):
        return f"doi:{rec['doi']}"
    return str(key)


def build(review):
    protocol = review.read('protocol.json')
    results = review.read('analysis/results.json')
    counts = screening.prisma(review)
    records = review.records()
    search = review.read('search/summary.json', {})
    ta_agr = review.read('screening/ta_agreement.json', {})
    ft_agr = review.read('screening/ft_agreement.json', {})
    settings = review.read('review.json').get('settings', {})
    critic = review.read('report/critic.json', {})
    mode = settings.get('mode', 'full')
    sheet = mode == 'sheet'
    pooled_mode = protocol['analysis'].get('pool', True)
    md = [f"# {protocol['question']}", f"*{'Meta-analysis from a researcher-supplied coding sheet' if sheet else 'Systematic review' + (' with meta-analysis' if pooled_mode else ' (narrative synthesis)')}.*",
          '> **AI-generated draft — not human-verified.** Every judgement below was proposed by an AI system and must be checked by the review team before any use. Numbers were computed by code from the stored extractions.',
          '## Summary']
    primary = next(o for o in results['outcomes'].values() if o['primary'])
    lines = [f"- Question: {protocol['question']}",
             f"- Records identified: {counts['records_identified']}; screened: {counts['records_screened']}; full texts assessed: {counts['reports_assessed']}; studies included: {counts['studies_included']}.",
             f"- Reports not retrieved (no open-access full text; eligibility unknown): {counts['reports_not_retrieved']}."]
    if primary.get('pooled'):
        p, g = primary['pooled'], primary['grade']
        lines.append(f"- Primary outcome ({primary['label']}): {primary['measure']} {_f(p['estimate'])} (95% CI {_f(p['ci_lb'])} to {_f(p['ci_ub'])}), {primary['k']} studies, I² {primary['fit']['I2']:.0f}%. Certainty (draft GRADE): **{g['rating']}** — {g['statement']}.")
    elif primary.get('single'):
        s = primary['single']
        lines.append(f"- Primary outcome ({primary['label']}): one study only; {primary['measure']} {_f(s['display'][0])} ({_f(s['display'][1])} to {_f(s['display'][2])}). No pooling.")
    elif primary.get('studies'):
        lines.append(f"- Primary outcome ({primary['label']}): narrative synthesis of {primary['k']} studies; {primary['narrative']['summary']}")
    else:
        lines.append(f"- Primary outcome ({primary['label']}): no usable data; no pooling.")
    md += ['\n'.join(lines), '## Methods']
    pico = {k: str(v).strip().rstrip('.') for k, v in protocol['pico'].items()}
    md.append(f"Protocol fixed before screening (sha256 {review.read('protocol.lock.json', {}).get('sha256', 'n/a')[:12]}). "
              f"Population: {pico['population']}. Intervention/exposure: {pico['intervention_or_exposure']}. Comparator: {pico['comparator']}. Outcomes: {pico['outcomes']}. "
              f"Designs: {', '.join(protocol['designs'])}.")
    md.append(table(['Id', 'Criterion'], [[c['id'], c['text']] for c in protocol['inclusion']] + [[c['id'], c['text'] + f" ({c['code']})"] for c in protocol['exclusion']]))
    deviations = list(protocol.get('deviations') or [])
    changes = [e for e in review.lines('log.jsonl') if e.get('action') == 'protocol_changed']
    if deviations or changes:
        md.append('**Protocol deviations.** ' + (' '.join(f'({i + 1}) {d}' for i, d in enumerate(deviations)) if deviations else '') +
                  (f" The locked protocol was modified {len(changes)} time(s) after locking (see log.jsonl)." if changes else ''))
    if sheet:
        md.append(f"**Studies.** {search.get('identified', 0)} studies were supplied by the researcher as a coding sheet (`coding_sheet.csv`); no search or screening was performed by the agent. Effect sizes were taken from the sheet as coded.")
    if not sheet:
        runs = search.get('runs') or [{'source': 'pubmed', 'query': protocol['search'].get('query', ''), 'date': search.get('date'),
                                       'hits': search.get('identified'), 'retrieved': search.get('identified'), 'rationale': 'single PubMed search'}]
        md.append(f"**Search (PRISMA-S).** {len(runs)} source(s) chosen for this question and searched exhaustively (every hit retrieved); "
                  f"{search.get('identified', 0)} records identified, {search.get('duplicates_removed', 0)} duplicates removed across sources"
                  + (f"; limits {search.get('limits')}" if search.get('limits') else '') + '.'
                  + (f" Citation chasing added {search.get('chased', 0)} records." if search.get('chased') else ''))
        md.append(table(['Source', 'Searched', 'Hits', 'Retrieved', 'Why chosen'],
                        [[x['source'], x.get('date', ''), x.get('hits', ''), x.get('retrieved', ''), x.get('rationale', '')] for x in runs]))
        md.append('\n'.join(f"Query ({x['source']}):\n```text\n{x['query']}\n```" for x in runs))
    one = settings.get('reviewers') == 'single'
    if one:
        md.append('**Screening.** Rule-based pre-screen removed non-primary publication types only. Title/abstract and full text: a SINGLE AI reviewer '
                  f"(n = {ta_agr.get('n', 0)} and {ft_agr.get('n', 0)}); any unsure record advanced to full text. No second reviewer, so no agreement statistic; "
                  'single screening misses about 5% of eligible studies in human reviews (Waffenschmidt 2019).')
    else:
        md.append('**Screening.** Rule-based pre-screen removed non-primary publication types only. Title/abstract: two independent AI reviewers, blind to each other; any include or unsure advanced to full text. '
                  f"Agreement: {ta_agr.get('percent', 'n/a')}%, kappa {ta_agr.get('kappa', 'n/a')} (n = {ta_agr.get('n', 0)}). Full text: two independent AI reviewers; disagreements adjudicated by a third. "
                  f"Agreement: {ft_agr.get('percent', 'n/a')}%, kappa {ft_agr.get('kappa', 'n/a')} (n = {ft_agr.get('n', 0)}).")
    plan = protocol['analysis']
    md.append(('**Extraction.** A single AI extraction per study; ' if one else '**Extraction.** Two independent AI extractions per study; ') + 'every number had to appear in a verbatim quote found in the paper (checked by code)' + ('. ' if one else '; disagreements resolved field by field. ') +
              '**Risk of bias.** Design-specific tools (RoB 2 for randomised trials; ROBINS-I/ROBINS-E for non-randomised studies; QUIPS for prognostic studies), AI-proposed with per-domain support statements; overall judgement by worst domain. '
              f"**Synthesis.** Random-effects meta-analysis ({plan.get('model', 'REML')} estimator{', Knapp-Hartung adjustment' if plan.get('knapp_hartung', True) else ''}), heterogeneity by τ², I² and the prediction interval; leave-one-out and DerSimonian-Laird sensitivity analyses; funnel plot and Egger test when ≥ 3 studies (interpreted only when ≥ 10). "
              'Certainty by Core GRADE rules of thumb, as a draft for the review team.')
    md += ['## Results', '### Study selection', table(['Step', 'n'], [
        ['Records identified', counts['records_identified']], ['Duplicates removed', counts['duplicates_removed']],
        ['Records screened', counts['records_screened']], ['Excluded at title/abstract', counts['records_excluded']],
        ['Reports sought', counts['reports_sought']], ['Reports not retrieved', counts['reports_not_retrieved']],
        ['Reports assessed', counts['reports_assessed']],
        ['Excluded at full text', str(sum(counts['reports_excluded'].values())) + (' (' + ', '.join(f'{k} {v}' for k, v in sorted(counts['reports_excluded'].items())) + ')' if counts['reports_excluded'] else '')],
        ['Studies included', counts['studies_included']]]), '![PRISMA flow](prisma.svg)']
    if counts['not_retrieved_pmids']:
        md.append('Reports not retrieved (add the PDF or text to `fulltext/uploads/` under the name shown and rerun `fulltext`): ' + ', '.join(f"{study_label({'record': records.get(p, {})})} ({idlabel(records.get(p), p)}; upload as {p}.pdf)" for p in counts['not_retrieved_pmids'][:40]) + (' …' if len(counts['not_retrieved_pmids']) > 40 else ''))
    ft_final = review.read('screening/ft_final.json', {})
    excluded_ft = [(p, d) for p, d in sorted(ft_final.items()) if d['decision'] == 'exclude']
    if excluded_ft:
        md += ['### Studies excluded at full text', table(['Study', 'Reason'], [[f"{study_label({'record': records.get(p, {})})} ({idlabel(records.get(p), p)})", f"{d.get('code')}: {d.get('reason', '')}"] for p, d in excluded_ft])]
    studies = results['studies']
    if studies:
        rows = []
        for s in studies:
            ex = review.read(f"extraction/{s['pmid']}.final.json", {}).get('study', {})
            val = lambda k: (ex.get(k) or {}).get('value')
            rows.append([f"{s['label']} ({idlabel(records.get(s['pmid']), s['pmid'])})", val('design'), val('country'), val('n_total'), val('population'), val('intervention_or_exposure'), val('comparator'), val('follow_up')])
        md += ['### Included studies', table(['Study', 'Design', 'Country', 'N', 'Population', 'Intervention/exposure', 'Comparator', 'Follow-up'], rows)]
        rows = []
        for s in studies:
            r = review.read(f"rob/{s['pmid']}.json", {})
            rows.append([s['label'], r.get('tool') or s['tool'], '; '.join(f"{d['domain']}: {d['judgement']}" for d in r.get('domains', [])) or 'not assessed', r.get('overall') or '—'])
        md += ['### Risk of bias (AI-proposed)', table(['Study', 'Tool', 'Domains', 'Overall'], rows)]
    for oid, o in results['outcomes'].items():
        md.append(f"### {o['label']} ({o['measure']}){' — primary' if o['primary'] else ''}")
        if o.get('excluded'):
            md.append('Not pooled: ' + '; '.join(f"{e['label']} ({e['reason']})" for e in o['excluded']) + '.')
        nar = o.get('narrative') or {}
        if nar.get('rows'):
            md.append('**Narrative synthesis (SWiM, direction of effect).** ' + nar['summary'])
            md.append(table(['Study', o['measure'] + ' [95% CI]', 'Direction', 'CI excludes null', 'Risk of bias'],
                            [[x['label'], f"{_f(x['estimate'])} [{_f(x['ci_lb'])}, {_f(x['ci_ub'])}]", x['direction'], 'yes' if x['ci_excludes_null'] else 'no', x.get('rob') or '—'] for x in nar['rows']]))
        if o.get('pooled'):
            p, fit = o['pooled'], o['fit']
            md.append(f"Pooled {o['measure']} {_f(p['estimate'])} (95% CI {_f(p['ci_lb'])} to {_f(p['ci_ub'])}; {fit['k']} studies; {fit['method']}{', Knapp-Hartung' if fit['test'] == 't' else ''}). "
                      f"Heterogeneity: τ² = {fit['tau2']:.3f}, I² = {fit['I2']:.0f}%, Q = {fit['Q']:.1f} (df = {fit['Q_df']}, p = {fit['Q_p']:.3g})."
                      + (f" Prediction interval {_f(p['pi_lb'])} to {_f(p['pi_ub'])}." if p.get('pi_lb') is not None else ''))
            md.append(f'![Forest plot]({oid}_forest.svg)')
            md.append(table(['Study', o['measure'] + ' [95% CI]', 'Weight', 'Risk of bias', 'Note'],
                            [[s['label'], f"{_f(s['display'][0])} [{_f(s['display'][1])}, {_f(s['display'][2])}]", f"{s.get('weight', 0):.1f}%", s.get('rob') or '—', s.get('note') or ''] for s in o['studies']]))
            sens = []
            if o.get('leave_one_out'):
                ests = [x['display'][0] for x in o['leave_one_out']]
                sens.append(f"leave-one-out estimates ranged {_f(min(ests))} to {_f(max(ests))}")
            if o.get('sensitivity_dl'):
                d = o['sensitivity_dl']
                sens.append(f"DerSimonian-Laird {_f(d['estimate'])} ({_f(d['ci_lb'])} to {_f(d['ci_ub'])})")
            if o.get('sensitivity_z'):
                d = o['sensitivity_z']
                sens.append(f"conventional z-based interval {_f(d['estimate'])} ({_f(d['ci_lb'])} to {_f(d['ci_ub'])}); with only {o['k']} studies the Knapp-Hartung interval has {o['k'] - 1} degree(s) of freedom and is not interpretable")
            if o.get('sensitivity_low_rob'):
                d = o['sensitivity_low_rob']
                sens.append(f"excluding high-risk studies ({d['k']} studies) {_f(d['estimate'])} ({_f(d['ci_lb'])} to {_f(d['ci_ub'])})")
            if sens:
                md.append('Sensitivity: ' + '; '.join(sens) + '.')
            for key, sg in (o.get('subgroups') or {}).items():
                md.append(f"Subgroup by {key}: " + '; '.join(f"{g}: {_f(v['display'][0])} ({_f(v['display'][1])} to {_f(v['display'][2])}), k = {v['k']}" if 'display' in v else f"{g}: {v.get('skipped')}" for g, v in sg['groups'].items())
                          + (f". Test for subgroup differences p = {sg['Q_between_p']:.3f}." if sg.get('Q_between_p') is not None else '.'))
            if o.get('fixed_effect'):
                fe = o['fixed_effect']
                md.append(f"Model: random effects (Hansen et al. 2021, step 7.2: heterogeneity expected). Fixed-effect comparison: {_f(fe['estimate'])} ({_f(fe['ci_lb'])} to {_f(fe['ci_ub'])}).")
            if o.get('influence'):
                flagged = [x for x in o['influence'] if x['outlier'] or x['influential']]
                md.append('Outlier and influence diagnostics (studentized deleted residuals, Cook\'s distance): ' +
                          (('flagged ' + ', '.join(f"{x['label']} (residual {x['rstudent']:.2f}, Cook {x['cook_d']:.2f})" for x in flagged)) if flagged else 'no study flagged') +
                          (f". Without flagged studies ({o['sensitivity_without_outliers']['k']} left): {_f(o['sensitivity_without_outliers']['estimate'])} ({_f(o['sensitivity_without_outliers']['ci_lb'])} to {_f(o['sensitivity_without_outliers']['ci_ub'])})." if o.get('sensitivity_without_outliers') else '.'))
            pb = []
            if o.get('egger'):
                e = o['egger']
                pb.append(f"Egger regression {e['test']} = {e['stat']:.2f}, p = {e['pval']:.3f}")
            if o.get('rank_test'):
                rt = o['rank_test']
                pb.append(f"Begg rank correlation τ = {rt['tau']:.2f}, p = {rt['pval']:.3f}")
            if o.get('trim_and_fill'):
                tf = o['trim_and_fill']
                pb.append(f"trim-and-fill imputed {tf['k0']} studies ({tf['side']} side), adjusted {_f(tf['display'][0])} ({_f(tf['display'][1])} to {_f(tf['display'][2])})")
            if o.get('fail_safe_n'):
                fs = o['fail_safe_n']
                pb.append(f"fail-safe N = {fs['n']} (tolerance 5k+10 = {fs['tolerance_5k_plus_10']})")
            if pb:
                md.append('Publication bias, triangulated: ' + '; '.join(pb) + ('. Fewer than 10 studies: all of these tests have low power.' if o['k'] < 10 else '.') + ' ![Funnel plot](' + oid + '_funnel.svg)')
            if o.get('cumulative'):
                c = o['cumulative']
                md.append('Cumulative meta-analysis by year: ' + ' → '.join(f"{x['added']} {_f(x['display'][0])}" for x in c) + '.')
            for key, mr in (o.get('meta_regression') or {}).items():
                md.append(f"Meta-regression on {key}: slope {mr['slope']:.4f} per unit (95% CI {mr['ci_lb']:.4f} to {mr['ci_ub']:.4f}, p = {mr['pval']:.3f}), residual τ² = {mr['tau2_residual']:.3f}" + (f", R² = {mr['R2']:.0f}%" if mr.get('R2') is not None else '') + (f" ({mr['warning']})" if mr.get('warning') else '') + '.')
            g = o['grade']
            md.append('Certainty of evidence (draft GRADE, starts ' + g['start'] + '): **' + g['rating'] + '**. ' + table(['Domain', 'Change', 'Reason'], [[k.replace('_', ' '), v['change'], v['reason']] for k, v in g['domains'].items()]))
            md.append(f"Plain-language statement: *{g['statement']}*")
        elif o.get('single'):
            s = o['single']
            md.append(f"One study ({s['label']}): {o['measure']} {_f(s['display'][0])} (95% CI {_f(s['display'][1])} to {_f(s['display'][2])}). No pooling.")
        elif not nar.get('rows'):
            md.append('No usable data.')
        for w in o.get('warnings', []):
            md.append(f'- Warning: {w}')
    if any(o.get('pooled') for o in results['outcomes'].values()):
        rows = []
        for o in results['outcomes'].values():
            if o.get('pooled'):
                p = o['pooled']
                rows.append([o['label'], f"{o['measure']} {_f(p['estimate'])} ({_f(p['ci_lb'])} to {_f(p['ci_ub'])})", f"{o['k']} ({sum((s.get('n') or 0) for s in o['studies']) or 'n/a'} participants)", o['grade']['rating'], o['grade']['statement']])
        md += ['### Summary of findings (draft)', table(['Outcome', 'Pooled effect (95% CI)', 'Studies (participants)', 'Certainty', 'What this means'], rows)]
    md += ['## Discussion', '[For the review team: interpretation, comparison with prior reviews, implications. Not AI-written.]', '## Limitations']
    lim = ['Studies were supplied by the researcher; the agent did not search or screen, so completeness depends on the researcher\'s own search.' if sheet else
           (f"Databases searched: {', '.join(x['source'] for x in (search.get('runs') or [{'source': 'pubmed'}]))}. Subscription databases (Embase, Scopus, Web of Science, CENTRAL, EconLit) were not searched, so relevant studies may have been missed."),
           f"{counts['reports_not_retrieved']} reports had no open-access full text and were not assessed." if counts['reports_not_retrieved'] else None,
           'All screening, extraction and risk-of-bias judgements were made by AI and are unverified by humans.' + (' A single AI agent performed every task (no independent second reviewer, and the critique was not independent).' if one else ''),
           'GRADE ratings are rule-based drafts; indirectness was not assessed.' if pooled_mode else 'No quantitative pooling was performed; vote counting by direction does not weight studies by size or precision.',
           'Means and SDs reported as medians or ranges were not converted; such studies were not pooled.']
    md.append('\n'.join('- ' + x for x in lim if x))
    if critic.get('findings'):
        heading = '### Critic findings (unresolved)' + ('' if critic.get('independent', True) else ' — same-agent critique, not independent')
        md += [heading, '\n'.join(f"- [{f.get('severity', 'note')}] {f.get('section', '')}: {f.get('text')}" for f in critic['findings'])]
    md += ['## Human oversight', '- No human sign-off recorded.' if not settings.get('signoff') else f"- Signed off by {settings['signoff']}.",
           '## AI use', ai_use(review, settings),
           '## References']
    inc = [s['pmid'] for s in studies]
    md += [f'{i + 1}. {reference(records.get(p, {"pmid": p}))}' for i, p in enumerate(inc)]
    text = '\n\n'.join(x for x in md if x)
    files = {'report.md': text + '\n', 'prisma.svg': plots.prisma(counts), 'coding_sheet.csv': coding_sheet(results)}
    for oid, o in results['outcomes'].items():
        if o.get('studies'):
            files[f'{oid}_forest.svg'] = plots.forest(o)
            fp = plots.funnel(o)
            if fp:
                files[f'{oid}_funnel.svg'] = fp
    files['index.html'] = to_html(protocol['question'], text, {k: v for k, v in files.items() if k.endswith('.svg')})
    for name, content in files.items():
        review.path('report/' + name).write_text(content, encoding='utf-8')
    review.log('report', {'files': sorted(files)})
    return sorted(files)


def ai_use(review, settings):
    from .llm import usage_totals
    u = usage_totals(review)
    systems = settings.get('ai_model') or 'not recorded (run: settings ai_model <name>)'
    how = ('direct API calls, one fresh request per reviewer, extractor or appraiser task (no shared context between A and B)'
           if settings.get('mode') == 'api' else 'one agent performing every task itself in a single context (single-agent mode)'
           if settings.get('reviewers') == 'single' else 'separate fresh-context subagents under a lead agent')
    text = ('AI made or suggested judgements at: protocol drafting, search building, title/abstract and full-text screening, data extraction, '
            f'risk of bias and critique. Systems: {systems}; orchestration by metareview {__version__} via {how}. '
            'Statistics, validation and counts were computed by code, not by AI. Validation against human reviewers was not performed within this review.')
    if u['calls']:
        text += f" Model calls: {u['calls']}; tokens: {u['input']:,} input ({u['cached']:,} cached), {u['output']:,} output; models: {', '.join(u['models'])}."
    return text


def coding_sheet(results):
    """Open-science export (Hansen et al. 2021, step 8.2): one row per study-outcome with the effect used."""
    out = io.StringIO(newline='')
    w = csv.writer(out)
    w.writerow(['outcome', 'study', 'pmid', 'year', 'design', 'measure', 'yi_analysis_scale', 'vi', 'estimate', 'ci_lb', 'ci_ub', 'weight_pct', 'events', 'n', 'risk_of_bias', 'note'])
    for oid, o in results['outcomes'].items():
        for s in o.get('studies', []):
            w.writerow([oid, s['label'], s['pmid'], s.get('year'), s.get('design'), o['measure'], f"{s['yi']:.6f}", f"{s['vi']:.6f}",
                        f"{s['display'][0]:.4f}", f"{s['display'][1]:.4f}", f"{s['display'][2]:.4f}", f"{s.get('weight', 0):.2f}", s.get('events'), s.get('n'), s.get('rob'), s.get('note')])
    return out.getvalue()


def to_html(title, md, svgs):
    """Minimal Markdown -> HTML: headings, paragraphs, tables, lists, code fences, images (SVG inlined)."""
    out, i, lines = [], 0, md.split('\n')
    while i < len(lines):
        line = lines[i]
        if line.startswith('```'):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                buf.append(lines[i]); i += 1
            out.append('<pre>' + html.escape('\n'.join(buf)) + '</pre>')
        elif line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                rows.append(lines[i]); i += 1
            cells = [[_inline(c.strip()) for c in r.strip('|').split('|')] for r in rows if not re.match(r'^\|(---\|)+$', r.strip())]
            out.append('<table><tr>' + ''.join(f'<th>{c}</th>' for c in cells[0]) + '</tr>' + ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in cells[1:]) + '</table>')
            continue
        elif re.match(r'^#{1,6} ', line):
            n = len(line.split(' ')[0]); out.append(f'<h{n}>{_inline(line[n + 1:])}</h{n}>')
        elif line.startswith('> '):
            out.append(f'<blockquote>{_inline(line[2:])}</blockquote>')
        elif re.match(r'^(- |\d+\. )', line):
            items = []
            tag = 'ul' if line.startswith('- ') else 'ol'
            while i < len(lines) and re.match(r'^(- |\d+\. )', lines[i]):
                items.append(re.sub(r'^(- |\d+\. )', '', lines[i])); i += 1
            out.append(f'<{tag}>' + ''.join(f'<li>{_inline(x)}</li>' for x in items) + f'</{tag}>')
            continue
        elif line.strip():
            out.append(f'<p>{_inline(line)}</p>')
        i += 1
    body = '\n'.join(out)
    for name, svg in svgs.items():
        body = body.replace(f'<img src="{name}">', f'<figure>{svg}</figure>')
    style = ('body{font-family:Helvetica,Arial,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;line-height:1.45;color:#222}'
             'table{border-collapse:collapse;margin:.8rem 0;font-size:.92rem}td,th{border:1px solid #ccd;padding:.3rem .5rem;vertical-align:top;text-align:left}'
             'th{background:#f3f5f9}blockquote{border-left:4px solid #b22222;background:#fff6f6;padding:.5rem .8rem;margin:1rem 0}pre{background:#f6f8fa;padding:.6rem;overflow:auto}'
             'figure{margin:1rem 0;overflow:auto}h1{font-size:1.5rem}h2{border-bottom:1px solid #dde;padding-bottom:.2rem;margin-top:2rem}')
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{style}</style></head><body>{body}</body></html>'


def _inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2">', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text
