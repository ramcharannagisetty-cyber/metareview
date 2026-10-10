"""Forest, funnel and PRISMA figures as standalone SVG (no dependencies)."""
import math
from xml.sax.saxutils import escape

from . import effects

FONT = "font-family='Helvetica, Arial, sans-serif' font-size='12'"


def _fmt(x):
    return f'{x:.2f}'


def forest(outcome):
    m = outcome['measure']
    ratio = m in effects.RATIO
    studies = outcome['studies']
    fit = outcome.get('fit')
    rows = len(studies) + (1 if fit else 0)
    W, left, right, top, rh = 760, 190, 150, 40, 24
    plot_w = W - left - right
    H = top + rh * (rows + 2) + 50
    vals = [v for s in studies for v in (s['yi'] - 1.96 * s['vi'] ** .5, s['yi'] + 1.96 * s['vi'] ** .5)]
    if fit:
        vals += [fit['ci_lb'][0], fit['ci_ub'][0]] + ([fit['pi_lb'], fit['pi_ub']] if fit.get('pi_lb') is not None else [])
    lo, hi = min(vals), max(vals)
    nullv = 0.0
    lo, hi = min(lo, nullv) - 0.1 * (hi - lo + 1e-9), max(hi, nullv) + 0.1 * (hi - lo + 1e-9)
    x = lambda v: left + (v - lo) / (hi - lo) * plot_w
    out = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' viewBox='0 0 {W} {H}' {FONT}>",
           "<rect width='100%' height='100%' fill='white'/>",
           f"<text x='10' y='22' font-weight='bold'>{escape(outcome['label'])} ({escape(m)})</text>",
           f"<text x='{W - right + 8}' y='{top - 6}' font-size='11'>{escape(m)} [95% CI]   Weight</text>"]
    y0 = top
    maxw = max([s.get('weight', 100) for s in studies] + [1])
    for i, s in enumerate(studies):
        cy = y0 + rh * i + rh / 2
        se = s['vi'] ** .5
        l, u = s['yi'] - 1.96 * se, s['yi'] + 1.96 * se
        size = 4 + 10 * math.sqrt(s.get('weight', 100 / len(studies)) / maxw)
        out.append(f"<text x='10' y='{cy + 4}'>{escape(s['label'])}</text>")
        out.append(f"<line x1='{x(l):.1f}' y1='{cy}' x2='{x(u):.1f}' y2='{cy}' stroke='black'/>")
        out.append(f"<rect x='{x(s['yi']) - size / 2:.1f}' y='{cy - size / 2:.1f}' width='{size:.1f}' height='{size:.1f}' fill='#2c5aa0'/>")
        d = s['display']
        out.append(f"<text x='{W - right + 8}' y='{cy + 4}' font-size='11'>{_fmt(d[0])} [{_fmt(d[1])}, {_fmt(d[2])}]   {s.get('weight', 0):.1f}%</text>")
    if fit:
        cy = y0 + rh * len(studies) + rh / 2 + 6
        b, l, u = fit['beta'][0], fit['ci_lb'][0], fit['ci_ub'][0]
        out.append(f"<text x='10' y='{cy + 4}' font-weight='bold'>Pooled ({fit['method']}{', KH' if fit['test'] == 't' else ''})</text>")
        out.append(f"<polygon points='{x(l):.1f},{cy} {x(b):.1f},{cy - 7} {x(u):.1f},{cy} {x(b):.1f},{cy + 7}' fill='#b22222'/>")
        if fit.get('pi_lb') is not None:
            out.append(f"<line x1='{x(fit['pi_lb']):.1f}' y1='{cy}' x2='{x(fit['pi_ub']):.1f}' y2='{cy}' stroke='#b22222' stroke-dasharray='3,3'/>")
        p = outcome['pooled']
        out.append(f"<text x='{W - right + 8}' y='{cy + 4}' font-size='11' font-weight='bold'>{_fmt(p['estimate'])} [{_fmt(p['ci_lb'])}, {_fmt(p['ci_ub'])}]</text>")
        out.append(f"<text x='10' y='{cy + rh + 4}' font-size='11'>I² = {fit['I2']:.0f}%, τ² = {fit['tau2']:.3f}, Q = {fit['Q']:.1f} (df {fit['Q_df']}, p {fit['Q_p']:.3g})"
                   + (f"; prediction interval {_fmt(p['pi_lb'])} to {_fmt(p['pi_ub'])}" if p.get('pi_lb') is not None else '') + '</text>')
    ax = y0 + rh * rows + rh + 10
    out.append(f"<line x1='{x(nullv):.1f}' y1='{y0 - 5}' x2='{x(nullv):.1f}' y2='{ax}' stroke='gray' stroke-dasharray='4,3'/>")
    out.append(f"<line x1='{left}' y1='{ax}' x2='{left + plot_w}' y2='{ax}' stroke='black'/>")
    ticks = _ticks(lo, hi, ratio)
    for v, lab in ticks:
        out.append(f"<line x1='{x(v):.1f}' y1='{ax}' x2='{x(v):.1f}' y2='{ax + 5}' stroke='black'/>")
        out.append(f"<text x='{x(v):.1f}' y='{ax + 18}' text-anchor='middle' font-size='11'>{lab}</text>")
    out.append(f"<text x='{left + plot_w / 2:.0f}' y='{ax + 36}' text-anchor='middle' font-size='11'>{escape(m)}{' (log scale)' if ratio else ''}</text>")
    out.append('</svg>')
    return '\n'.join(out)


def _ticks(lo, hi, ratio):
    if ratio:
        cands = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20]
        return [(math.log(c), str(c)) for c in cands if lo <= math.log(c) <= hi]
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / 4)) if span > 0 else 1
    for mult in (1, 2, 5, 10):
        if span / (step * mult) <= 6:
            step *= mult
            break
    start = math.ceil(lo / step) * step
    ticks, v = [], start
    while v <= hi + 1e-9:
        ticks.append((v, f'{v:g}'))
        v += step
    return ticks


def funnel(outcome):
    fit = outcome.get('fit')
    if not fit:
        return None
    studies = outcome['studies']
    W, H, left, top = 520, 400, 60, 30
    pw, ph = W - left - 20, H - top - 50
    ses = [s['vi'] ** .5 for s in studies]
    semax = max(ses) * 1.1
    b = fit['beta'][0]
    span = max(abs(s['yi'] - b) for s in studies) + 1.96 * semax
    lo, hi = b - span * 1.05, b + span * 1.05
    x = lambda v: left + (v - lo) / (hi - lo) * pw
    y = lambda se: top + se / semax * ph
    out = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' viewBox='0 0 {W} {H}' {FONT}>",
           "<rect width='100%' height='100%' fill='white'/>",
           f"<text x='10' y='18' font-weight='bold'>Funnel plot: {escape(outcome['label'])}</text>",
           f"<polygon points='{x(b):.1f},{top} {x(b - 1.96 * semax):.1f},{y(semax):.1f} {x(b + 1.96 * semax):.1f},{y(semax):.1f}' fill='#eef3fb' stroke='#99a'/>",
           f"<line x1='{x(b):.1f}' y1='{top}' x2='{x(b):.1f}' y2='{y(semax):.1f}' stroke='#b22222' stroke-dasharray='4,3'/>"]
    for s in studies:
        out.append(f"<circle cx='{x(s['yi']):.1f}' cy='{y(s['vi'] ** .5):.1f}' r='4' fill='#2c5aa0'/>")
    out.append(f"<line x1='{left}' y1='{top}' x2='{left}' y2='{y(semax):.1f}' stroke='black'/>")
    out.append(f"<line x1='{left}' y1='{y(semax):.1f}' x2='{left + pw}' y2='{y(semax):.1f}' stroke='black'/>")
    out.append(f"<text x='{left + pw / 2:.0f}' y='{H - 12}' text-anchor='middle' font-size='11'>effect ({'log ' if outcome['measure'] in effects.RATIO else ''}{escape(outcome['measure'])})</text>")
    out.append(f"<text x='14' y='{top + ph / 2:.0f}' text-anchor='middle' font-size='11' transform='rotate(-90 14 {top + ph / 2:.0f})'>standard error</text>")
    eg = outcome.get('egger')
    if eg:
        out.append(f"<text x='{left + 6}' y='{top + 14}' font-size='11'>Egger {eg['test']} = {eg['stat']:.2f}, p = {eg['pval']:.3f}{' (low power, k<10)' if eg.get('warning') else ''}</text>")
    out.append('</svg>')
    return '\n'.join(out)


def prisma(c):
    boxes = [
        ('Records identified (databases, registers)', f"n = {c['records_identified']}"),
        ('Duplicates removed', f"n = {c['duplicates_removed']}"),
        ('Records screened (title/abstract)', f"n = {c['records_screened']}"),
        ('Records excluded', f"n = {c['records_excluded']}"),
        ('Reports sought for retrieval', f"n = {c['reports_sought']}"),
        ('Reports not retrieved', f"n = {c['reports_not_retrieved']}"),
        ('Reports assessed for eligibility', f"n = {c['reports_assessed']}"),
        ('Reports excluded', 'n = ' + str(sum(c['reports_excluded'].values())) + ('; ' + ', '.join(f'{k} {v}' for k, v in sorted(c['reports_excluded'].items())) if c['reports_excluded'] else '')),
        ('Studies included in review', f"n = {c['studies_included']}"),
    ]
    W, bw, bh, gap = 720, 300, 50, 22
    out = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{(bh + gap) * 5 + 40}' {FONT}>", "<rect width='100%' height='100%' fill='white'/>"]
    main = [0, 2, 4, 6, 8]
    side = {2: 3, 4: 5, 6: 7}
    y = 20
    pos = {}
    for i in main:
        pos[i] = (40, y)
        y += bh + gap
    pos[1] = (380, pos[0][1] + (bh + gap) // 2)
    for k, v in side.items():
        pos[v] = (380, pos[k][1])
    for i, (title, n) in enumerate(boxes):
        bx, by = pos[i]
        out.append(f"<rect x='{bx}' y='{by}' width='{bw}' height='{bh}' fill='#f7f9fc' stroke='#334'/>")
        out.append(f"<text x='{bx + 10}' y='{by + 20}' font-size='12'>{escape(title)}</text>")
        out.append(f"<text x='{bx + 10}' y='{by + 38}' font-size='12' font-weight='bold'>{escape(n)[:48]}</text>")
    for a, b in zip(main, main[1:]):
        out.append(f"<line x1='{40 + bw // 2}' y1='{pos[a][1] + bh}' x2='{40 + bw // 2}' y2='{pos[b][1]}' stroke='#334' marker-end='url(#m)'/>")
    for k, v in list(side.items()) + [(0, 1)]:
        out.append(f"<line x1='{40 + bw}' y1='{pos[k][1] + bh // 2}' x2='380' y2='{pos[v][1] + bh // 2}' stroke='#334'/>")
    out.insert(2, "<defs><marker id='m' markerWidth='8' markerHeight='8' refX='4' refY='4' orient='auto'><path d='M0,0 L8,4 L0,8 z' fill='#334'/></marker></defs>")
    out.append('</svg>')
    return '\n'.join(out)
