"""Count-consistent cohort forecaster for Exophase's GTA VI PS5 trophy rates.

The page prints, for every trophy, rate = round(100 * E / N, 2), where N is the
players Exophase tracks and E the tracked players holding the trophy.  Both
counts only grow, and the page changes only when Exophase refreshes it.  So the
model works on counts, not on printed rates:

1. Collapse the history into refresh states (identical consecutive reads are
   one stale page) and recover, for every state and trophy, the integer
   interval of E that the 2-decimal rate allows; intersect the intervals under
   E(t) non-decreasing.
2. Forecast N at the target from its arrival intensity: the last two windows of
   arrivals give the decay exponent of a power-law intensity, shrunk to a prior.
3. Explain each trophy's earners above its launch-night base as the tracked
   cohorts (one per refresh) earning it with a log-normal time-to-earn:
   E(t) = B + p * sum_k dN_k * G((t - s_k) / m).  A grid over (m, sigma, p)
   is weighted by a tempered likelihood of the observed counts.
4. Monte Carlo over N scenarios, grid points, model error and count noise, round
   every draw to the page's 2 decimals and write the empirical quantiles, so
   the forecast lands on the values the page can print.

R3 (calibrated 2026-11-20 on the live Exophase reads to 33.6 h after release)
adds two things to this:

5. Removed earners.  The launch-night accounts that held every trophy left the
   page between 2026-11-19 21:44Z and 2026-11-20 09:37Z (the platinum went
   from 0.03% to 0.00%).  E is non-decreasing only after its last drop, and a
   trophy's launch base is cut to what the page still shows.
6. A hierarchical story chain.  Chapter k+1 is reached by chapter k's earners
   after a longer play time: its posterior mixes its own grid with the
   parent's posterior shifted up the m grid (x1.27^0..6, same or wider sigma,
   p x 0.6..1).  Story missions borrow from the deepest chapter at least as
   many players hold.  The chain carries most weight while a step has fewer
   than 10 earners of its own, and is off until a refresh has been observed.

Standard library only, deterministic (seeded per case and trophy).
"""

from __future__ import annotations

import math
import random
import re
from bisect import bisect_left
from datetime import datetime, timezone

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = (-1.6448536, -0.6744898, 0.0, 0.6744898, 1.6448536)

# ---- tunables (set from the simulated-week backtest, see README) ----------
N_C = 2.0            # hour offset of the arrival power law
ALPHA0 = 0.6         # prior decay exponent of the arrival intensity
ALPHA_SD = 0.45      # its prior sd
LAM_SD = 0.18        # sd of the recent arrival rate (weekday/weekend swings)
LAUNCH_BETA = 0.95   # N ~ (h + 1) ** beta when only one state is known
LAUNCH_BETA_SD = 0.15
TEMPER = 0.5         # likelihood tempering (cumulative counts are autocorrelated)
KAPPA = 0.12         # proportional observation noise of a count
PHI = 1.5            # Poisson over-dispersion of a count
MIS = 0.35           # model error of the excess, per unit of log-time ahead
MIS0 = 0.10
N_SAMPLES = 800
M_LO = 12.0
M_HI = 5000.0
M_N = 26
M_GRID = tuple(math.exp(math.log(M_LO) + k * (math.log(M_HI) - math.log(M_LO)) / (M_N - 1)) for k in range(M_N))
S_GRID = (0.5, 0.9, 1.4)
P_GRID = (0.03, 0.08, 0.18, 0.35, 0.6, 0.85, 1.0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def logit_rate(pct):
    p = clamp(float(pct), 0.01, 99.99) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit_rate(z):
    z = clamp(z, -40.0, 40.0)
    return 100.0 / (1.0 + math.exp(-z))


def parse_ts(value):
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        s += "T00:00:00+00:00"
    if s[-1] in "Zz":
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def round2(x):
    """Round half up to 2 decimals, as a printed percentage."""
    return math.floor(x * 100.0 + 0.5 + 1e-9) / 100.0


def norm_cdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def wquantiles(values, weights, levels):
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    out = []
    for lv in levels:
        target = lv * total
        acc = 0.0
        chosen = pairs[-1][0]
        for v, w in pairs:
            acc += w
            if acc >= target - 1e-12:
                chosen = v
                break
        out.append(chosen)
    return out


# ---------------------------------------------------------------------------
# reading the case
# ---------------------------------------------------------------------------

class State:
    """One refresh of the page: the counts it shows and when they were taken."""

    __slots__ = ("h", "h_seen", "n", "rates", "lo", "hi")

    def __init__(self, h, n, rates):
        self.h = h          # estimated refresh time (hours from release)
        self.h_seen = h     # last read that still showed it
        self.n = n
        self.rates = rates
        self.lo = []
        self.hi = []


def read_case(case):
    trophies = case.get("trophies") or []
    m = len(trophies)
    release = parse_ts((case.get("game") or {}).get("release_date"))
    at = parse_ts(case.get("at"))
    target = parse_ts(case.get("target"))
    horizon = case.get("horizon_minutes")

    reads = []
    for rec in case.get("history") or []:
        if not isinstance(rec, dict):
            continue
        t = parse_ts(rec.get("at")) or parse_ts(rec.get("slot"))
        n = rec.get("players_tracked")
        if t is None or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0:
            continue
        raw = rec.get("rates") if isinstance(rec.get("rates"), list) else []
        rates = []
        for i in range(m):
            v = raw[i] if i < len(raw) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                rates.append(clamp(float(v), 0.0, 100.0))
            else:
                rates.append(None)
        reads.append((t, float(n), rates))
    reads.sort(key=lambda r: r[0])
    # a read whose players-tracked falls below an earlier read's is an old
    # copy of the page or a glitch, unless the drop persists for 3 more reads
    kept = []
    top = 0.0
    for k, rd in enumerate(reads):
        if rd[1] < top:
            nxt = reads[k + 1:k + 4]
            if len(nxt) < 3 or any(r[1] > rd[1] for r in nxt):
                continue
        top = max(top, rd[1]) if rd[1] >= top else rd[1]
        kept.append(rd)
    reads = kept

    if release is None:
        release = reads[0][0] if reads else (at or 0.0)
    if target is None:
        if at is not None and isinstance(horizon, (int, float)) and math.isfinite(horizon):
            target = at + 60.0 * horizon
        elif reads:
            target = reads[-1][0] + 3600.0
        else:
            target = release
    if at is None:
        at = reads[-1][0] if reads else target

    def hrs(t):
        return (t - release) / 3600.0

    def same_page(st, n, rates):
        if st.n != n:
            return False
        return all(a is None or b is None or a == b for a, b in zip(st.rates, rates))

    states = []
    prev_t = None
    for t, n, rates in reads:
        h = hrs(t)
        if states and same_page(states[-1], n, rates):
            st = states[-1]
            st.h_seen = h
            st.rates = [b if a is None else a for a, b in zip(st.rates, rates)]
        else:
            gap = (h - hrs(prev_t)) if prev_t is not None else 0.0
            st = State(h - 0.5 * clamp(gap, 0.0, 1.0), n, rates)
            st.h_seen = h
            states.append(st)
        prev_t = t
    return trophies, states, hrs(at), hrs(target)


def earner_intervals(states, m):
    """Integer E interval per state and trophy, tightened by monotonicity."""
    for st in states:
        st.lo, st.hi = [], []
        for i in range(m):
            r = st.rates[i]
            if r is None:
                st.lo.append(0)
                st.hi.append(int(st.n))
                continue
            lo = math.ceil((r - 0.005) * st.n / 100.0 - 1e-7)
            hi = math.floor((r + 0.005) * st.n / 100.0 + 1e-7)
            if r <= 0.0:
                lo = 0
            if r >= 100.0:
                hi = int(st.n)
            lo = int(clamp(lo, 0, st.n))
            hi = int(clamp(hi, lo, st.n))
            st.lo.append(lo)
            st.hi.append(hi)
    k = len(states)
    env_lo = [[0] * m for _ in range(k)]
    env_hi = [[0] * m for _ in range(k)]
    for i in range(m):
        run = 0
        for j in range(k):
            run = max(run, states[j].lo[i])
            env_lo[j][i] = run
        run = None
        for j in range(k - 1, -1, -1):
            v = states[j].hi[i]
            run = v if run is None else min(run, v)
            env_hi[j][i] = run
        ok = all(env_lo[j][i] <= env_hi[j][i] for j in range(k))
        if not ok:
            # earners removed (the launch-night test accounts that held the
            # platinum left the page between 2026-11-19 21:44Z and 11-20 09:37Z):
            # E is non-decreasing only after the last drop, so tighten from there
            cut = 0
            for j in range(1, k):
                if states[j].hi[i] < max(states[jj].lo[i] for jj in range(j)):
                    cut = j
            for j in range(k):
                env_lo[j][i] = states[j].lo[i]
                env_hi[j][i] = states[j].hi[i]
            run = 0
            for j in range(cut, k):
                run = max(run, states[j].lo[i])
                env_lo[j][i] = run
            run = None
            for j in range(k - 1, cut - 1, -1):
                v = states[j].hi[i]
                run = v if run is None else min(run, v)
                env_hi[j][i] = max(run, env_lo[j][i])
    return env_lo, env_hi


def removal_base(states, env_lo, env_hi, i, b0):
    """Launch base still on the page, and the first state that shows it.

    When earners were removed the base drops to what the page still shows
    from the first state below the old base on; earlier states keep the old
    base, so the removed accounts never pass for real earners."""
    cut = len(states)
    b_new = b0
    for j in range(1, len(states)):
        if env_hi[j][i] < b_new:
            cut = min(cut, j)
            b_new = float(env_hi[j][i])
    return b_new, cut, b0


# ---------------------------------------------------------------------------
# players tracked
# ---------------------------------------------------------------------------

def n_at(states, h):
    n = states[0].n
    for st in states:
        if st.h <= h + 1e-9:
            n = st.n
        else:
            break
    return n


def gauss_nodes(k):
    """Equal-weight normal quantile nodes."""
    out = []
    for j in range(k):
        u = (j + 0.5) / k
        out.append(_norm_ppf(u))
    return out


def _norm_ppf(u):
    lo, hi = -9.0, 9.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < u:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def n_scenarios(states, h_eff):
    """Scenarios of future arrivals: list of (weight, [(h_mid, dN), ...], N_T)."""
    last = states[-1]
    h0 = last.h
    n_last = last.n
    span = h0 - states[0].h
    horizon = max(0.0, h_eff - h0)
    scen = []
    if horizon <= 1e-9:
        return [(1.0, [], n_last)]

    if len(states) < 2 or span < 2.0:
        # launch backlog: N ~ (h + 1) ** beta from the only known state
        base = max(h0 + 1.0, 0.25)
        for zb in gauss_nodes(9):
            beta = LAUNCH_BETA + LAUNCH_BETA_SD * zb
            n_t = n_last * ((h_eff + 1.0) / base) ** beta
            scen.append((1.0, _spread(h0, h_eff, n_t - n_last, 1.0 - beta), n_t))
        return scen

    w = min(24.0, span / 2.0) if span >= 6.0 else span
    a1 = h0 - w
    lam1 = max(n_last - n_at(states, a1), 0.0) / w
    mid1 = h0 - 0.5 * w
    alpha_hat = None
    if span >= 2.0 * w - 1e-6 and w >= 3.0:
        a0 = h0 - 2.0 * w
        lam0 = max(n_at(states, a1) - n_at(states, a0), 0.0) / w
        mid0 = h0 - 1.5 * w
        if lam0 > 0 and lam1 > 0:
            alpha_hat = math.log(lam0 / lam1) / math.log((mid1 + N_C) / (mid0 + N_C))
    if alpha_hat is not None:
        wd = 1.0 * (w / 24.0)
        alpha = (wd * clamp(alpha_hat, -1.0, 3.0) + ALPHA0) / (wd + 1.0)
        a_sd = ALPHA_SD / math.sqrt(1.0 + wd)
    else:
        alpha, a_sd = ALPHA0, ALPHA_SD
    lam_sd = LAM_SD + 0.25 * (1.0 - min(w, 24.0) / 24.0)
    if lam1 <= 0:
        lam1 = max(n_last, 1.0) * 0.002
        lam_sd = 0.8
    for za in gauss_nodes(7):
        al = alpha + a_sd * za
        for zl in (-1.0, 0.0, 1.0):
            # 3-point Gauss-Hermite rule for a normal: nodes 0, +-sqrt(3)
            l1 = lam1 * math.exp(lam_sd * zl * math.sqrt(3.0))
            wgt = 2.0 / 3.0 if zl == 0.0 else 1.0 / 6.0
            total = _intensity_integral(l1, al, mid1, h0, h_eff)
            scen.append((wgt, _spread(h0, h_eff, total, al, mid1), n_last + total))
    return scen


def _intensity_integral(l1, al, mid, a, b):
    c = N_C
    x0 = (a + c) / (mid + c)
    x1 = (b + c) / (mid + c)
    if abs(1.0 - al) < 1e-6:
        return l1 * (mid + c) * math.log(x1 / x0)
    return l1 * (mid + c) * (x1 ** (1.0 - al) - x0 ** (1.0 - al)) / (1.0 - al)


def _spread(a, b, total, al, mid=None):
    """Split `total` arrivals over [a, b] in up to 16 bins along the intensity."""
    if b <= a or total <= 0:
        return []
    nb = int(clamp(math.ceil((b - a) / 6.0), 1, 16))
    edges = [a + (b - a) * k / nb for k in range(nb + 1)]
    ref = mid if mid is not None else a
    ws = []
    for k in range(nb):
        ws.append(_intensity_integral(1.0, al, ref, edges[k], edges[k + 1]) if ref + N_C > 0 else 1.0)
    sw = sum(ws) or 1.0
    return [(0.5 * (edges[k] + edges[k + 1]), total * ws[k] / sw) for k in range(nb)]


# ---------------------------------------------------------------------------
# cohort earning model
# ---------------------------------------------------------------------------

def past_cohorts(states):
    """(arrival time, players) per refresh, merged into bins of <= 2 h."""
    out = []
    prev_h = states[0].h - 0.25
    prev_n = 0.0
    for st in states:
        dn = st.n - prev_n
        if dn > 0:
            s = 0.5 * (prev_h + st.h) if prev_n > 0 else st.h - 0.25
            if out and s - out[-1][2] < 2.0:
                h_, n_, s0 = out[-1]
                out[-1] = ((h_ * n_ + s * dn) / (n_ + dn), n_ + dn, s0)
            else:
                out.append((s, dn, s))
        prev_n = max(prev_n, st.n)
        prev_h = st.h
    return [(h_, n_) for h_, n_, _ in out]


def g_cdf(tau, m, sig):
    if tau <= 0.0:
        return 0.0
    return norm_cdf((math.log(tau) - math.log(m)) / sig)


def basis(cohorts, t, m, sig):
    tot = 0.0
    for s, dn in cohorts:
        if t > s:
            tot += dn * g_cdf(t - s, m, sig)
    return tot


def trophy_kind(tr):
    tier = str(tr.get("tier") or "").lower()
    text = (str(tr.get("name") or "") + " " + str(tr.get("description") or "")).lower()
    if tier == "platinum":
        return "platinum"
    if "100% completion" in text:
        return "completion"
    desc = str(tr.get("description") or "").strip().lower()
    if desc.startswith("complete chapter") or desc == "complete the final mission.":
        return "story"
    if desc.startswith("complete ") and " for " not in desc and tr.get("secret"):
        return "story"
    if _GRIND.search(desc):
        return "grind"
    return "side"


# collection / completion wording: every spot, all challenges, 250 stunts ...
_GRIND = re.compile(r"\b(all|every|each)\b|(?<![$,\d])\b([2-9]\d|\d{3,})\b(?!,)")


P_PRIOR = {
    "platinum": (0.30, 0.30, 0.20, 0.12, 0.05, 0.02, 0.01),
    "completion": (0.30, 0.30, 0.20, 0.12, 0.05, 0.02, 0.01),
    "story": (0.02, 0.03, 0.05, 0.10, 0.20, 0.30, 0.30),
    "side": (0.10, 0.14, 0.18, 0.18, 0.16, 0.14, 0.10),
    "grind": (0.22, 0.24, 0.20, 0.15, 0.10, 0.06, 0.03),
}


# the platinum and 100% completion take 150-200 hours of play: nobody who
# started at launch can hold them inside a week
M_FLOOR = {"platinum": 250.0, "completion": 250.0, "grind": 30.0}


# hierarchical story chain (R3) ---------------------------------------------
CHAIN_W = 0.75                      # prior weight of "this step follows its parent"
CHAIN_W_DATA = 0.10                 # ... once the step has CHAIN_DATA_X earners of its own
CHAIN_DATA_X = 10.0
CHAIN_S = ((0.6, 0.25), (0.8, 0.35), (1.0, 0.40))   # p scale and its prior
# (m-grid shift, sigma-grid shift): chapter k+1 takes m x 1.27^0 .. 1.27^6 of
# chapter k's time, with the same or a wider spread of reach times
CHAIN_SHIFT = tuple((j, d) for j in range(0, 7) for d in (0, 1))
MISSION_SHIFT = tuple((j, d) for j in range(-4, 7) for d in (0, 1))
TOP_K = 60


def _chapter_no(tr):
    d = str(tr.get("description") or "").strip().lower()
    for n in range(1, 10):
        if d == "complete chapter %d." % n:
            return n
    if d == "complete the final mission.":
        return 99
    return None


def story_parents(trophies, last_rates):
    """i -> (parent index, allowed m-grid shifts) for the story trophies."""
    chain = sorted((_chapter_no(t), i) for i, t in enumerate(trophies) if _chapter_no(t) is not None)
    par = {}
    for (_, a), (_, b) in zip(chain, chain[1:]):
        par[b] = (a, CHAIN_SHIFT)
    for i, t in enumerate(trophies):
        if i in par or _chapter_no(t) is not None or trophy_kind(t) != "story":
            continue
        r = last_rates[i] if i < len(last_rates) else None
        if r is None:
            continue
        # the deepest chapter that at least as many players hold (at a tie on
        # the launch-night base that is the latest one: the simulated-week
        # backtest preferred it to the deepest chapter more players hold)
        cand = [c for _, c in chain if last_rates[c] is not None and last_rates[c] >= r]
        if cand:
            par[i] = (cand[-1], MISSION_SHIFT)
    return par


def _depth(parents, i):
    d = 0
    while i in parents and d < 50:
        i = parents[i][0]
        d += 1
    return d


def _top(post, wts):
    """The posterior's heaviest points (99% of its weight, at most TOP_K)."""
    tot = sum(wts) or 1.0
    idx = sorted(range(len(post)), key=lambda k: (-wts[k], k))
    keep, acc = [], 0.0
    for k in idx:
        keep.append(k)
        acc += wts[k]
        if acc >= 0.99 * tot or len(keep) >= TOP_K:
            break
    z = sum(wts[k] for k in keep) or 1.0
    return [post[k] for k in keep], [wts[k] / z for k in keep]


# ---------------------------------------------------------------------------
# the forecast
# ---------------------------------------------------------------------------

def forecast_case(case):
    trophies, states, h_at, h_target = read_case(case)
    m = len(trophies)
    if m == 0:
        return []
    if not states:
        raise ValueError("no usable history")
    env_lo, env_hi = earner_intervals(states, m)
    last = states[-1]
    h_last_read = last.h_seen

    # refresh cadence -> how stale the answering page is, and whether it moves
    gaps = [states[j].h - states[j - 1].h for j in range(1, len(states))]
    gaps = [g for g in gaps if g > 0]
    tau = clamp(sum(gaps[-12:]) / len(gaps[-12:]), 0.17, 8.0) if gaps else None
    dt = max(0.0, h_target - h_last_read)
    if tau is None:
        backlog = (last.h_seen - last.h) >= 2.0 and h_target < 36.0
        p_none = 0.0 if backlog else math.exp(-dt / 1.0)
        # a launch backlog clears and the page then refreshes about hourly,
        # so the answering page is from the last hour before the target
        h_eff_lo, h_eff_hi = max(h_last_read, h_target - 1.0), h_target
    else:
        p_none = math.exp(-dt / tau)
        h_eff_lo, h_eff_hi = max(h_last_read, h_target - tau), h_target
    h_eff = 0.5 * (h_eff_lo + h_eff_hi)
    h_eff = max(h_eff, last.h)

    scen = n_scenarios(states, h_eff)
    sw = sum(s[0] for s in scen)
    scen = [(w / sw, f, n) for w, f, n in scen]
    cohorts = past_cohorts(states)

    # observation points (refresh states after the first), thinned
    obs_idx = [j for j in range(1, len(states)) if states[j].h > states[0].h + 0.5]
    if len(obs_idx) > 40:
        recent = [j for j in obs_idx if states[j].h >= last.h - 24.0]
        older = [j for j in obs_idx if states[j].h < last.h - 24.0]
        pick = _thin(recent, 26) + _thin(older, 14)
        obs_idx = sorted(set(pick))

    grid = [(mm, ss) for mm in M_GRID for ss in S_GRID]
    b_obs = [[basis(cohorts, states[j].h, mm, ss) for j in obs_idx] for mm, ss in grid]
    b_past_t = [basis(cohorts, h_eff, mm, ss) for mm, ss in grid]
    b_fut = [[basis(f, h_eff, mm, ss) for mm, ss in grid] for _, f, _ in scen]

    ahead = math.log((h_eff + N_C + 4.0) / (last.h + N_C + 4.0))
    mis = MIS0 + MIS * ahead
    seed_base = _case_seed(case)
    rows = []
    qrows = [None] * m
    parents = story_parents(trophies, last.rates)
    posts = {}
    # parents before children, so a story step can borrow its parent's posterior
    order = sorted(range(m), key=lambda i: (_depth(parents, i), i))
    for i in order:
        tr = trophies[i]
        b0, j_cut, b_old = removal_base(states, env_lo, env_hi, i, 0.5 * (env_lo[0][i] + env_hi[0][i]))
        e_lo_last = env_lo[-1][i]
        e_hi_last = env_hi[-1][i]
        e_last = 0.5 * (e_lo_last + e_hi_last)
        rng = random.Random(seed_base * 1009 + i * 7919 + 17)
        r_last = last.rates[i]
        if r_last is None:
            r_last = next((st.rates[i] for st in reversed(states) if st.rates[i] is not None), None)

        if r_last is not None and r_last >= 95.0:
            vals = _complement(rng, scen, last, e_last)
        else:
            kind = trophy_kind(tr)
            pp = P_PRIOR[kind]
            m_floor = M_FLOOR.get(kind, 0.0)
            xs = []
            for j in obs_idx:
                lo, hi = env_lo[j][i], env_hi[j][i]
                xs.append((0.5 * (lo + hi) - (b0 if j >= j_cut else b_old), (hi - lo) ** 2 / 12.0))

            def chi_of(g, p):
                chi = 0.0
                for (x, rv), b in zip(xs, b_obs[g]):
                    mu = p * b
                    v = PHI * (mu if mu > 1.0 else 1.0) + (KAPPA * mu) ** 2 + rv
                    d = x - mu
                    chi += d * d / v
                return chi

            indep = []
            for g in range(len(grid)):
                if grid[g][0] < m_floor:
                    continue
                for k, p in enumerate(P_GRID):
                    indep.append((g, p, chi_of(g, p), pp[k]))
            post = indep
            par = parents.get(i)
            # only once a refresh has been observed is the parent's posterior
            # learned from data; before that it is just the prior again
            if par is not None and par[0] in posts and obs_idx:
                # hierarchical story chain: this step is reached by the
                # parent's earners after a longer play time (m shifted up the
                # log grid) and by no more of them (p scaled down)
                ppost, pw = posts[par[0]]
                chain = []
                cache = {}
                for (g, p, _, _), w in zip(ppost, pw):
                    for jsh, dsg in par[1]:
                        si = g % 3 + dsg
                        g2 = g - g % 3 + 3 * jsh + si
                        if si > 2 or g2 < 0 or g2 >= len(grid) or grid[g2][0] < m_floor:
                            continue
                        for s_, ws in CHAIN_S:
                            key = (g2, p * s_)
                            if key not in cache:
                                cache[key] = chi_of(g2, p * s_)
                            chain.append((g2, p * s_, cache[key], w * ws / len(par[1])))
                if chain:
                    # the chain is a prior for the data-poor steps: a step whose
                    # own earners already tell its curve leans on them instead
                    cw = CHAIN_W if e_last - b0 < CHAIN_DATA_X else CHAIN_W_DATA
                    zi = sum(e[3] for e in indep) or 1.0
                    zc = sum(e[3] for e in chain) or 1.0
                    post = [(g, p, c, (1.0 - cw) * pr / zi) for g, p, c, pr in indep] + \
                           [(g, p, c, cw * pr / zc) for g, p, c, pr in chain]
            cmin = min(c for _, _, c, _ in post)
            wts = [pr * math.exp(-0.5 * TEMPER * (c - cmin)) for _, _, c, pr in post]
            posts[i] = _top(post, wts)
            vals = _sample_trophy(rng, post, wts, scen, b_past_t, b_fut, b0, e_lo_last, mis)
        if p_none > 1e-6 and r_last is not None:
            # with probability p_none the page has not refreshed by the target
            k_none = int(round(p_none * len(vals)))
            if k_none > 0:
                vals = vals[: len(vals) - k_none] + [r_last] * k_none
        q = wquantiles(vals, [1.0] * len(vals), LEVELS)
        qrows[i] = [clamp(v, 0.0, 100.0) for v in q]

    _monotone_pools(trophies, qrows)
    for tr, q in zip(trophies, qrows):
        prev = 0.0
        out = []
        for v in q:
            # the scorer clamps to 0.01..99.99 before its logit, so keeping
            # every quantile strictly inside (0, 100) costs nothing
            v = round(clamp(max(v, prev), 0.01, 99.99), 4)
            out.append(v)
            prev = v
        rows.append({"id": tr.get("id"), "q": out})
    return rows


def _thin(idx, k):
    if len(idx) <= k:
        return list(idx)
    return [idx[int(round(j * (len(idx) - 1) / (k - 1)))] for j in range(k)]


def _case_seed(case):
    s = str(case.get("id") or "") + "|" + str(case.get("at") or "")
    h = 2166136261
    for ch in s.encode("utf-8"):
        h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
    return h


def _pick(rng, cum):
    return min(bisect_left(cum, rng.random() * cum[-1]), len(cum) - 1)


def _sample_trophy(rng, post, wts, scen, b_past_t, b_fut, b0, e_floor, mis):
    cum_p = []
    acc = 0.0
    for w in wts:
        acc += w
        cum_p.append(acc)
    cum_s = []
    acc = 0.0
    for w, _, _ in scen:
        acc += w
        cum_s.append(acc)
    vals = []
    for _ in range(N_SAMPLES):
        g, p, _, _ = post[_pick(rng, cum_p)]
        si = _pick(rng, cum_s)
        n_t = scen[si][2]
        x = p * (b_past_t[g] + b_fut[si][g])
        # model error on the logit of the earning share, so it never passes 100%
        room = max(n_t - b0, 1.0)
        f = clamp(x / room, 1e-9, 1.0 - 1e-9)
        lf = math.log(f / (1.0 - f)) + mis * rng.gauss(0.0, 1.0)
        x = room / (1.0 + math.exp(-clamp(lf, -40.0, 40.0)))
        x += math.sqrt(PHI * max(x, 0.0)) * rng.gauss(0.0, 1.0)
        e = b0 + max(0.0, x)
        e = clamp(round(e), e_floor, n_t)
        vals.append(round2(100.0 * e / n_t))
    return vals


def _complement(rng, scen, last, e_last):
    """A trophy nearly everyone holds: forecast the few who lack it."""
    c_last = max(0.0, last.n - e_last)
    cum_s = []
    acc = 0.0
    for w, _, _ in scen:
        acc += w
        cum_s.append(acc)
    vals = []
    for _ in range(N_SAMPLES):
        n_t = scen[_pick(rng, cum_s)][2]
        c = c_last * (n_t / last.n) * math.exp(0.35 * rng.gauss(0.0, 1.0))
        c += math.sqrt(max(c, 0.0)) * rng.gauss(0.0, 1.0)
        c = clamp(round(c), 0, n_t)
        vals.append(round2(100.0 * (n_t - c) / n_t))
    return vals


def _monotone_pools(trophies, q):
    """Chapter order, 100% under the final mission, platinum under everything."""
    idx = {}
    for i, tr in enumerate(trophies):
        d = str(tr.get("description") or "").strip().lower()
        for n in range(1, 10):
            if d == "complete chapter %d." % n:
                idx[n] = i
        if d == "complete the final mission.":
            idx[99] = i
    chain = [idx[k] for k in sorted(idx)]
    for a, b in zip(chain, chain[1:]):
        q[b] = [min(x, y) for x, y in zip(q[a], q[b])]
    comp = [i for i, tr in enumerate(trophies) if trophy_kind(tr) == "completion"]
    if comp and 99 in idx:
        c = comp[0]
        q[c] = [min(x, y) for x, y in zip(q[c], q[idx[99]])]
    plat = [i for i, tr in enumerate(trophies) if trophy_kind(tr) == "platinum"]
    if plat:
        pi = plat[0]
        for j in range(len(q)):
            if j != pi:
                q[pi] = [min(x, y) for x, y in zip(q[pi], q[j])]
