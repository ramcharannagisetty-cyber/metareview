"""Random-effects meta-analysis in pure Python, matching metafor's rma.uni conventions.

Estimators: FE, DL (DerSimonian-Laird), REML (Fisher scoring). Knapp-Hartung t-based inference.
Heterogeneity: Q, tau^2, I^2, H^2 (Higgins & Thompson). Prediction interval. Egger's regression
test (metafor regtest, predictor = standard error). Leave-one-out. Subgroups with a Q-between test.
"""
import math

from . import PipelineError
from .stats import norm_ppf, t_ppf, chi2_sf, norm_sf, t_sf


def _solve(A, b):
    """Solve A x = b for small dense systems (Gauss-Jordan with partial pivoting)."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-300:
            raise PipelineError('singular design matrix (a moderator is constant or collinear)')
        M[col], M[piv] = M[piv], M[col]
        p = M[col][col]
        M[col] = [v / p for v in M[col]]
        for r in range(n):
            if r != col and M[r][col]:
                f = M[r][col]
                M[r] = [a - f * c for a, c in zip(M[r], M[col])]
    return [M[i][n] for i in range(n)]


def _inv(A):
    n = len(A)
    cols = [_solve(A, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def _wls(y, w, X):
    """Weighted least squares: beta, covariance (X'WX)^-1, fitted values."""
    p = len(X[0])
    XtWX = [[sum(w[i] * X[i][a] * X[i][b] for i in range(len(y))) for b in range(p)] for a in range(p)]
    XtWy = [sum(w[i] * X[i][a] * y[i] for i in range(len(y))) for a in range(p)]
    vb = _inv(XtWX)
    beta = [sum(vb[a][b] * XtWy[b] for b in range(p)) for a in range(p)]
    fit = [sum(X[i][a] * beta[a] for a in range(p)) for i in range(len(y))]
    return beta, vb, fit


def _tau2_dl(y, v, X):
    w = [1 / vi for vi in v]
    beta, vb, fit = _wls(y, w, X)
    k, p = len(y), len(X[0])
    Q = sum(wi * (yi - fi) ** 2 for wi, yi, fi in zip(w, y, fit))
    # trace(P) with P = W - W X (X'WX)^-1 X'W
    W_X = [[w[i] * X[i][a] for a in range(p)] for i in range(k)]
    trP = sum(w) - sum(W_X[i][a] * vb[a][b] * W_X[i][b] for i in range(k) for a in range(p) for b in range(p))
    return max(0.0, (Q - (k - p)) / trP), Q


def _tau2_reml(y, v, X, tol=1e-8, maxit=200):
    """Fisher scoring on the REML log-likelihood (as metafor's rma.uni method='REML')."""
    tau2, Q = _tau2_dl(y, v, X)
    k, p = len(y), len(X[0])
    converged = False
    for _ in range(maxit):
        w = [1 / (vi + tau2) for vi in v]
        beta, vb, fit = _wls(y, w, X)
        # P y = w * residual; trace(P) and trace(P P) via the projection identity
        Py = [wi * (yi - fi) for wi, yi, fi in zip(w, y, fit)]
        W_X = [[w[i] * X[i][a] for a in range(p)] for i in range(k)]
        # P = W - WX (X'WX)^-1 X'W  ->  entries needed for traces
        P_diag_corr = [sum(W_X[i][a] * vb[a][b] * W_X[i][b] for a in range(p) for b in range(p)) for i in range(k)]
        trP = sum(w) - sum(P_diag_corr)
        # trace(P P) = sum_ij P_ij^2 ; compute P fully (k x k, k small)
        P = [[(w[i] if i == j else 0.0) - sum(W_X[i][a] * vb[a][b] * W_X[j][b] for a in range(p) for b in range(p))
              for j in range(k)] for i in range(k)]
        trPP = sum(P[i][j] * P[j][i] for i in range(k) for j in range(k))
        score = -0.5 * trP + 0.5 * sum(x * x for x in Py)
        info = 0.5 * trPP
        step = score / info
        new = max(0.0, tau2 + step)
        if abs(new - tau2) < tol * (1 + tau2):
            tau2 = new
            converged = True
            break
        tau2 = new
    return tau2, Q, converged


def rma(y, v, X=None, method='REML', knha=True, level=95):
    """Fit the model. Returns a dict with beta/se/ci/p (first coefficient = intercept/pooled effect),
    tau2, Q, I2, H2, prediction interval (intercept-only models), weights, fitted residuals."""
    k = len(y)
    if k < 2:
        raise PipelineError('at least two studies are needed to pool')
    if any(vi <= 0 for vi in v):
        raise PipelineError('every sampling variance must be positive')
    X = X or [[1.0] for _ in range(k)]
    p = len(X[0])
    if method == 'FE':
        tau2, Q = 0.0, _tau2_dl(y, v, X)[1]
        converged = True
    elif method == 'DL':
        tau2, Q = _tau2_dl(y, v, X)
        converged = True
    elif method == 'REML':
        tau2, Q, converged = _tau2_reml(y, v, X)
    else:
        raise PipelineError(f'unknown method {method}')
    w = [1 / (vi + tau2) for vi in v]
    beta, vb, fit = _wls(y, w, X)
    df = k - p
    if knha and method != 'FE' and df > 0:
        s2 = sum(wi * (yi - fi) ** 2 for wi, yi, fi in zip(w, y, fit)) / df
        vb = [[c * s2 for c in row] for row in vb]
        crit = t_ppf(1 - (1 - level / 100) / 2, df)
        se = [math.sqrt(vb[a][a]) for a in range(p)]
        stat = [beta[a] / se[a] for a in range(p)]
        pval = [2 * t_sf(abs(s), df) for s in stat]
        test = 't'
    else:
        crit = norm_ppf(1 - (1 - level / 100) / 2)
        se = [math.sqrt(vb[a][a]) for a in range(p)]
        stat = [beta[a] / se[a] for a in range(p)]
        pval = [2 * norm_sf(abs(s)) for s in stat]
        test = 'z'
    ci_lb = [beta[a] - crit * se[a] for a in range(p)]
    ci_ub = [beta[a] + crit * se[a] for a in range(p)]
    # Higgins-Thompson I^2 / H^2 (typical within-study variance s^2 from FE weights)
    wf = [1 / vi for vi in v]
    s2_typ = (k - 1) * sum(wf) / (sum(wf) ** 2 - sum(x * x for x in wf)) if k > 1 else float('nan')
    I2 = 100 * tau2 / (tau2 + s2_typ) if method != 'FE' else max(0.0, 100 * (Q - (k - p)) / Q) if Q > 0 else 0.0
    H2 = tau2 / s2_typ + 1 if method != 'FE' else (Q / (k - p) if k > p else float('nan'))
    out = {'k': k, 'method': method, 'test': test, 'beta': beta, 'se': se, 'stat': stat, 'pval': pval,
           'ci_lb': ci_lb, 'ci_ub': ci_ub, 'tau2': tau2, 'tau': math.sqrt(tau2), 'Q': Q, 'Q_df': df,
           'Q_p': chi2_sf(Q, df) if df > 0 else None, 'I2': I2, 'H2': H2, 'converged': converged,
           'weights': [100 * wi / sum(w) for wi in w], 'residuals': [yi - fi for yi, fi in zip(y, fit)],
           'level': level, 'crit': crit, 'df': df}
    if p == 1 and method != 'FE':
        pi_df = max(1, k - 2)
        pi_crit = t_ppf(1 - (1 - level / 100) / 2, pi_df) if test == 't' else norm_ppf(1 - (1 - level / 100) / 2)
        pi_se = math.sqrt(se[0] ** 2 + tau2)
        out['pi_lb'], out['pi_ub'] = beta[0] - pi_crit * pi_se, beta[0] + pi_crit * pi_se
    return out


def egger(y, v, method='REML', knha=True):
    """Regression test for funnel-plot asymmetry: effect on its standard error (metafor regtest default)."""
    if len(y) < 3 or max(v) - min(v) < 1e-12:
        return None  # identical variances: the predictor is constant and the test is undefined
    X = [[1.0, math.sqrt(vi)] for vi in v]
    fit = rma(y, v, X=X, method=method, knha=knha)
    return {'k': len(y), 'slope': fit['beta'][1], 'se': fit['se'][1], 'stat': fit['stat'][1], 'pval': fit['pval'][1],
            'test': fit['test'], 'limit_estimate': fit['beta'][0],
            'warning': 'fewer than 10 studies: asymmetry tests have low power' if len(y) < 10 else None}


def leave_one_out(y, v, labels, **kw):
    out = []
    if len(y) < 3:
        return out
    for i in range(len(y)):
        yy = y[:i] + y[i + 1:]
        vv = v[:i] + v[i + 1:]
        r = rma(yy, vv, **kw)
        out.append({'omitted': labels[i], 'estimate': r['beta'][0], 'ci_lb': r['ci_lb'][0], 'ci_ub': r['ci_ub'][0],
                    'I2': r['I2'], 'tau2': r['tau2']})
    return out


def subgroups(y, v, groups, **kw):
    """Separate random-effects fits per group plus a Q-between test (mixed-effects, common tau^2 not assumed)."""
    out = {'groups': {}, 'Q_between': None, 'Q_between_df': None, 'Q_between_p': None}
    names = sorted(set(groups))
    fits = {}
    for g in names:
        idx = [i for i, gg in enumerate(groups) if gg == g]
        if len(idx) < 2:
            out['groups'][g] = {'k': len(idx), 'skipped': 'fewer than 2 studies'}
            continue
        r = rma([y[i] for i in idx], [v[i] for i in idx], **kw)
        fits[g] = r
        out['groups'][g] = {'k': r['k'], 'estimate': r['beta'][0], 'ci_lb': r['ci_lb'][0], 'ci_ub': r['ci_ub'][0],
                            'I2': r['I2'], 'tau2': r['tau2']}
    if len(fits) >= 2:
        est = [f['beta'][0] for f in fits.values()]
        wt = [1 / f['se'][0] ** 2 for f in fits.values()]
        pooled = sum(a * b for a, b in zip(est, wt)) / sum(wt)
        qb = sum(wi * (e - pooled) ** 2 for wi, e in zip(wt, est))
        out['Q_between'], out['Q_between_df'] = qb, len(fits) - 1
        out['Q_between_p'] = chi2_sf(qb, len(fits) - 1)
    return out


# ----------------------------------------------------------------------------- publication bias and diagnostics
# (Hansen, Steinmetz & Block 2021, step 7: triangulate several conceptually different procedures)

def rank_test(y, v):
    """Begg & Mazumdar (1994) rank correlation between standardised effects and sampling variances."""
    k = len(y)
    if k < 3:
        return None
    fe = rma(y, v, method='FE')
    b, vb = fe['beta'][0], fe['se'][0] ** 2
    ys = [(yi - b) / math.sqrt(max(vi - vb, 1e-12)) for yi, vi in zip(y, v)]
    conc = disc = 0
    for i in range(k):
        for j in range(i + 1, k):
            s = (ys[i] - ys[j]) * (v[i] - v[j])
            conc += s > 0
            disc += s < 0
    n0 = k * (k - 1) / 2
    tau = (conc - disc) / n0
    z = (conc - disc) / math.sqrt(k * (k - 1) * (2 * k + 5) / 18)
    return {'k': k, 'tau': tau, 'stat': z, 'pval': 2 * norm_sf(abs(z)),
            'warning': 'fewer than 10 studies: rank test has low power' if k < 10 else None}


def trim_and_fill(y, v, method='REML', side=None, maxit=50):
    """Duval & Tweedie (2000) L0 trim-and-fill. Returns the number of imputed studies and the adjusted pooled estimate."""
    k = len(y)
    if k < 3:
        return None
    fit = rma(y, v, method=method, knha=False)
    if side is None:
        eg = egger(y, v, method=method, knha=False)
        if eg:
            side = 'right' if eg['slope'] < 0 else 'left'  # missing studies assumed on the side opposite the asymmetry
        else:
            b = rma(y, v, method=method, knha=False)['beta'][0]
            side = 'right' if sum(yi - b for yi in y) < 0 else 'left'
    sign = -1.0 if side == 'right' else 1.0
    yy = [sign * yi for yi in y]  # flip so missing studies are on the left
    k0 = 0
    for _ in range(maxit):
        keep = sorted(range(k), key=lambda i: yy[i])[:k - k0] if k0 else list(range(k))
        f = rma([yy[i] for i in keep], [v[i] for i in keep], method=method, knha=False)
        b = f['beta'][0]
        dev = [yy[i] - b for i in range(k)]
        ranks = {i: r + 1 for r, i in enumerate(sorted(range(k), key=lambda i: abs(dev[i])))}
        s_r = sum(ranks[i] for i in range(k) if dev[i] > 0)
        new_k0 = min(k - 2, max(0, int(round((4 * s_r - k * (k + 1)) / (2 * k - 1)))))
        if new_k0 == k0:
            break
        k0 = new_k0
    k0 = min(k0, k - 2)
    if k0 == 0:
        return {'k0': 0, 'side': side, 'estimate': fit['beta'][0], 'ci_lb': fit['ci_lb'][0], 'ci_ub': fit['ci_ub'][0], 'filled_y': [], 'filled_v': []}
    keep = sorted(range(k), key=lambda i: yy[i])[:k - k0]
    f = rma([yy[i] for i in keep], [v[i] for i in keep], method=method, knha=False)
    b = f['beta'][0]
    extreme = sorted(range(k), key=lambda i: yy[i])[-k0:]
    fill_y = [sign * (2 * b - yy[i]) for i in extreme]
    fill_v = [v[i] for i in extreme]
    adj = rma(list(y) + fill_y, list(v) + fill_v, method=method, knha=False)
    return {'k0': k0, 'side': side, 'estimate': adj['beta'][0], 'ci_lb': adj['ci_lb'][0], 'ci_ub': adj['ci_ub'][0],
            'filled_y': fill_y, 'filled_v': fill_v}


def fail_safe_n(y, v, alpha=0.05):
    """Rosenthal's fail-safe N: null studies needed to make the combined z non-significant."""
    k = len(y)
    if k < 2:
        return None
    z = [yi / math.sqrt(vi) for yi, vi in zip(y, v)]
    zs = sum(z)
    za = norm_ppf(1 - alpha / 2)
    n = max(0.0, (zs ** 2) / (za ** 2) - k)
    return {'k': k, 'z': zs / math.sqrt(k), 'n': int(math.ceil(n)), 'tolerance_5k_plus_10': 5 * k + 10,
            'robust': n > 5 * k + 10}


def cumulative(y, v, labels, order, **kw):
    """Cumulative meta-analysis in the given order (e.g. by year or by precision)."""
    idx = sorted(range(len(y)), key=lambda i: order[i])
    out = []
    for n in range(2, len(idx) + 1):
        sel = idx[:n]
        r = rma([y[i] for i in sel], [v[i] for i in sel], **kw)
        out.append({'added': labels[idx[n - 1]], 'k': n, 'estimate': r['beta'][0], 'ci_lb': r['ci_lb'][0], 'ci_ub': r['ci_ub'][0]})
    return out


def influence(y, v, labels, method='REML'):
    """Outlier and influence diagnostics (Viechtbauer & Cheung 2010): studentized deleted residuals, hat values,
    Cook's distance from leave-one-out refits. Flags |residual| > 1.96 or Cook's d above the median plus 3 MADs."""
    k = len(y)
    if k < 3:
        return []
    full = rma(y, v, method=method, knha=False)
    w = [1 / (vi + full['tau2']) for vi in v]
    hat = [wi / sum(w) for wi in w]
    rows = []
    for i in range(k):
        yy = y[:i] + y[i + 1:]
        vv = v[:i] + v[i + 1:]
        f = rma(yy, vv, method=method, knha=False)
        pred_se = math.sqrt(v[i] + f['tau2'] + f['se'][0] ** 2)
        rstud = (y[i] - f['beta'][0]) / pred_se
        cook = (full['beta'][0] - f['beta'][0]) ** 2 / full['se'][0] ** 2
        rows.append({'label': labels[i], 'rstudent': rstud, 'hat': hat[i], 'cook_d': cook, 'tau2_del': f['tau2']})
    cooks = sorted(r['cook_d'] for r in rows)
    med = cooks[len(cooks) // 2]
    mad = sorted(abs(c - med) for c in cooks)[len(cooks) // 2]
    for r in rows:
        r['outlier'] = abs(r['rstudent']) > 1.96
        r['influential'] = r['cook_d'] > med + 3 * (mad or 1e-9) and r['cook_d'] > 0.5
    return rows


def metareg(y, v, x, method='REML', knha=True):
    """Mixed-effects meta-regression on one numeric moderator. Returns slope, test, R^2 (tau^2 reduction)."""
    if len(y) < 4:
        return None
    base = rma(y, v, method=method, knha=knha)
    X = [[1.0, float(xi)] for xi in x]
    fit = rma(y, v, X=X, method=method, knha=knha)
    r2 = max(0.0, 100 * (base['tau2'] - fit['tau2']) / base['tau2']) if base['tau2'] > 0 else None
    return {'k': len(y), 'intercept': fit['beta'][0], 'slope': fit['beta'][1], 'se': fit['se'][1], 'stat': fit['stat'][1],
            'pval': fit['pval'][1], 'ci_lb': fit['ci_lb'][1], 'ci_ub': fit['ci_ub'][1], 'test': fit['test'],
            'tau2_residual': fit['tau2'], 'R2': r2, 'warning': 'fewer than 10 studies: meta-regression is exploratory' if len(y) < 10 else None}
