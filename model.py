"""Trophy-rarity forecaster for make.fun's GTA VI launch-week bounty.

The model works in three coupled layers:

* a players-tracked trajectory N(t) fitted to genuine Exophase refresh
  snapshots only (stale plateau reads are discarded);
* a per-trophy earner trajectory E_i(t) = N(t) * r_i(t) / 100, which is
  physically non-decreasing, fitted as log E_i against log N with
  hierarchical shrinkage toward the trophy's structural archetype;
* a logit-scale predictive distribution that blends earner accumulation,
  a damped recency-weighted logit trend and the archetype prior, then
  applies the story / platinum monotonicity pool.

Standard library only.  Everything is deterministic.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
# Student-t (df 7) quantiles: a little heavier than Gaussian so the
# 5%/95% tails do not explode a pinball loss on launch-week surprises.
Z = (-1.895, -0.711, 0.0, 0.711, 1.895)

LN2 = math.log(2.0)

# Tunable blend weights (earner, trend, archetype, persistence).  These
# are overwritten only by the local backtest harness, never at run time.
W_MULTI = (0.30, 0.50, 0.10, 0.10)
W_SINGLE = (0.52, 0.08, 0.26, 0.14)
W_LAUNCH = (0.78, 0.00, 0.16, 0.06)
N_BLEND = (0.20, 0.50, 0.20, 0.10)
SIG_SCALE = 0.82


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------

def clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def sigmoid(z: float) -> float:
    if z >= 0.0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def logit_rate(pct: float) -> float:
    """Logit of a rate in percent, clamped to the scoring range."""
    p = clamp(float(pct), 0.01, 99.99) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit_rate(z: float) -> float:
    """Inverse logit, back in percent, stable for large |z|."""
    return 100.0 * sigmoid(clamp(z, -40.0, 40.0))


def parse_ts(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    s = value.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n & 1:
        return s[n // 2]
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


def weighted_line(xs: list[float], ys: list[float], ws: list[float]):
    """Weighted least squares y = a + b x.  Returns (a, b, residual_sd).

    residual_sd is a weighted root-mean-square residual, in y units.
    """
    sw = sum(ws)
    if sw <= 0.0 or len(xs) < 2:
        return 0.0, 0.0, 0.0
    mx = sum(w * x for x, w in zip(xs, ws)) / sw
    my = sum(w * y for y, w in zip(ys, ws)) / sw
    sxx = sum(w * (x - mx) ** 2 for x, w in zip(xs, ws))
    if sxx <= 1e-12:
        return my, 0.0, 0.0
    sxy = sum(w * (x - mx) * (y - my) for x, y, w in zip(xs, ys, ws))
    b = sxy / sxx
    a = my - b * mx
    ss = sum(w * (y - (a + b * x)) ** 2 for x, y, w in zip(xs, ys, ws))
    sd = math.sqrt(max(ss / sw, 0.0))
    return a, b, sd


# ---------------------------------------------------------------------------
# trophy archetypes
# ---------------------------------------------------------------------------

ARCH_BETA = {
    "open": 1.00,
    "story_ch1": 0.90,
    "story_ch2": 0.96,
    "story_ch3": 1.00,
    "story_ch4": 1.04,
    "story_final": 1.16,
    "story_mission": 0.98,
    "completion_gold": 1.50,
    "platinum": 1.55,
    "grind_collect": 0.80,
    "activity": 0.88,
    "action": 0.88,
    "other": 0.94,
}

# A launch-night refresh is different from a normal mid-week refresh: the
# newly tracked accounts have just started, so their trophy count is far
# below the reviewers' while N jumps several-fold.
LAUNCH_BETA = {
    "open": 1.00,
    "story_ch1": 0.42,
    "story_ch2": 0.28,
    "story_ch3": 0.22,
    "story_ch4": 0.18,
    "story_final": 0.12,
    "story_mission": 0.22,
    "completion_gold": 0.10,
    "platinum": 0.10,
    "grind_collect": 0.10,
    "activity": 0.14,
    "action": 0.16,
    "other": 0.18,
}


def archetype(trophy: dict) -> str:
    name = str(trophy.get("name") or "")
    desc = str(trophy.get("description") or "")
    tier = str(trophy.get("tier") or "").lower()
    text = (name + " " + desc).lower()

    if tier == "platinum":
        return "platinum"
    if "100% completion" in text or "attain 100%" in text:
        return "completion_gold"
    if "complete gone fishing" in text:
        return "open"
    if "complete chapter 1" in text:
        return "story_ch1"
    if "complete chapter 2" in text:
        return "story_ch2"
    if "complete chapter 3" in text:
        return "story_ch3"
    if "complete chapter 4" in text:
        return "story_ch4"
    if "complete the final mission" in text or "final mission." in text:
        return "story_final"
    if desc.lower().startswith("complete "):
        return "story_mission"

    grind_words = (
        "100 vehicles", "250 stunts", "50 challenges", "every spot",
        "every location", "all garage", "all shooting", "collectible card",
        "all shops", "each gang", "8 mission strands", "3 collectible",
    )
    if any(w in text for w in grind_words):
        return "grind_collect"

    activity_words = (
        "race", "activity", "shooting range", "arcade", "meditate",
        "hunt", "fishing challenge", "drone", "bowl", "card set",
    )
    if any(w in text for w in activity_words):
        return "activity"

    action_words = (
        "rob", "wanted level", "combat focus", "take down", "discover",
        "spend", "earn $", "register", "smash", "cocktail", "explore",
        "departure", "jewelry", "armored truck", "vault", "shops",
    )
    if any(w in text for w in action_words):
        return "action"

    return "other"


# ---------------------------------------------------------------------------
# case normalisation
# ---------------------------------------------------------------------------

class Read:
    __slots__ = ("t", "n", "earned", "rates", "sig")

    def __init__(self, t, n, earned, rates):
        self.t = t
        self.n = n
        self.earned = earned
        self.rates = rates
        self.sig = tuple(-1.0 if r is None else round(float(r), 2) for r in rates)


def normalise_case(case: dict):
    trophies = case.get("trophies") or []
    m = len(trophies)
    history = case.get("history") or []

    raw = []
    for rec in history:
        if not isinstance(rec, dict):
            continue
        t = parse_ts(rec.get("at"))
        if t is None:
            t = parse_ts(rec.get("slot"))
        if t is None:
            continue
        try:
            n = float(rec.get("players_tracked") or 0.0)
        except (TypeError, ValueError):
            n = 0.0
        try:
            earned = float(rec.get("trophies_earned") or 0.0)
        except (TypeError, ValueError):
            earned = 0.0
        rates_in = rec.get("rates")
        if not isinstance(rates_in, list):
            rates_in = []
        rates = []
        for i in range(m):
            v = rates_in[i] if i < len(rates_in) else None
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                rates.append(clamp(float(v), 0.0, 100.0))
            else:
                rates.append(None)
        raw.append(Read(t, max(n, 0.0), max(earned, 0.0), rates))

    raw.sort(key=lambda r: r.t)
    # Make sure an exactly duplicated first/second read never matters.
    deduped = []
    for rd in raw:
        if deduped and rd.t == deduped[-1].t and rd.n == deduped[-1].n and rd.sig == deduped[-1].sig:
            continue
        deduped.append(rd)
    raw = deduped

    at_epoch = parse_ts(case.get("at"))
    target_epoch = parse_ts(case.get("target"))
    horizon_min = case.get("horizon_minutes")
    if not isinstance(horizon_min, (int, float)) or not math.isfinite(horizon_min):
        if at_epoch is not None and target_epoch is not None:
            horizon_min = (target_epoch - at_epoch) / 60.0
        else:
            horizon_min = 0.0
    horizon_min = max(0.0, float(horizon_min))

    release = (case.get("game") or {}).get("release_date")
    launch_epoch = parse_ts(release)
    if launch_epoch is None:
        launch_epoch = parse_ts("2026-11-19T00:00:00Z")
    if not isinstance(launch_epoch, (int, float)):
        launch_epoch = 0.0

    return trophies, raw, at_epoch, target_epoch, horizon_min, launch_epoch


# ---------------------------------------------------------------------------
# players-tracked trajectory
# ---------------------------------------------------------------------------

def n_points(reads: list[Read]):
    """Genuine new-N snapshots, time-ordered, non-decreasing N."""
    pts = []
    for rd in reads:
        if rd.n <= 0:
            continue
        if not pts or rd.n > pts[-1][1]:
            pts.append((rd.t, rd.n))
        elif not pts:
            pts.append((rd.t, rd.n))
    if not pts:
        # fall back to the first usable read
        for rd in reads:
            if rd.n > 0:
                pts.append((rd.t, rd.n))
                break
    return pts


def forecast_n(pts, t_last, h_last, h_target, span_h, gap_first_last):
    """Return (N_median, log_sigma_N, ratio_median, mode).

    mode is 'launch', 'flat', or 'multi' so the caller can pick the right
    earner prior.
    """
    horizon_h = max(0.0, h_target - h_last)
    distinct = len(pts)

    if distinct == 0:
        return 1.0, 0.55, 1.0, "launch"
    if distinct == 1 or (distinct >= 2 and pts[-1][0] - pts[0][0] < 1200 and span_h < 3.0):
        # Unrefreshed launch-night plateau.  Exophase refreshes hours
        # apart; a launch-night checkpoint has usually gone stale for 3+
        # hours, and the next refresh multiplies tracked players 4-8x.
        if h_last <= 10.0 and gap_first_last >= 2.0:
            base = 6.0
            if horizon_h < 0.5:
                base = 2.4
            elif horizon_h < 1.5:
                base = 5.2
        elif h_last <= 24.0:
            base = 3.2
        else:
            base = max(1.05, 1.0 + 0.30 * math.log1p(horizon_h / 24.0))
        log_ratio = math.log(max(base, 1.0))
        return pts[-1][1] * math.exp(log_ratio), 0.55, math.exp(log_ratio), "launch"

    n_last = pts[-1][1]
    if n_last <= 0:
        return 1.0, 0.5, 1.0, "flat"

    # ---- candidate A: damped log-log slope in ln(1+h) -----------------
    pts_rel = [(t, n) for t, n in pts if n > 0]
    # recent points matter much more; the launch-night jump has huge
    # leverage and should not set a week-ahead slope.
    tau_w = 30.0 * 3600.0
    xs, ys, ws = [], [], []
    for t, n in pts_rel:
        # reconstruct h from the caller's two anchors: t_last maps to h_last
        h = h_last + (t - t_last) / 3600.0
        if h < 0.0:
            h = 0.0
        xs.append(math.log1p(h))
        ys.append(math.log(n))
        ws.append(math.exp(-max(0.0, (t_last - t) / tau_w)))
    _, b_ll, resid_ll = weighted_line(xs, ys, ws)
    u_last = math.log1p(max(0.0, h_last))
    u_target = math.log1p(max(0.0, h_target))
    du = max(0.0, u_target - u_last)
    b_ll = clamp(b_ll, 0.0, 1.8)
    if len(pts_rel) < 3:
        b_ll = 0.45
    # shrink toward a structural slope and damp the extrapolation
    b_eff = 0.55 * b_ll + 0.45 * 0.42
    b_eff *= 0.78
    log_ratio_a = b_eff * du

    # ---- candidate B: exponentially decaying absolute growth rate -----
    g_last = _local_growth(pts_rel, t_last, 24.0)
    g_prev = _local_growth(pts_rel, t_last - 86400.0, 24.0)
    if g_last is None:
        g_last = 0.0
    g_last = clamp(g_last, 0.0, 0.030)
    decay = 0.76
    if g_prev is not None and g_prev > 1e-6 and g_last > 0.0:
        decay = clamp(g_last / g_prev, 0.66, 0.95)
    k = -math.log(decay) / 24.0  # per hour
    if k > 1e-9:
        log_ratio_b = g_last * (1.0 - math.exp(-k * horizon_h)) / k
    else:
        log_ratio_b = g_last * horizon_h
    log_ratio_b = clamp(log_ratio_b, 0.0, math.log(4.0))

    # ---- candidate C: structural week-one prior ------------------------
    log_ratio_c = 0.34 * math.log1p(horizon_h / 24.0)
    log_ratio_c = clamp(log_ratio_c, 0.0, math.log(3.0))

    # ---- candidate D: saturating cohort-arrival curve ------------------
    log_ratio_d = _saturating_ratio(pts_rel, t_last, h_last, h_target, n_last)

    log_ratio = (
        N_BLEND[0] * log_ratio_a
        + N_BLEND[1] * log_ratio_b
        + N_BLEND[2] * log_ratio_c
        + N_BLEND[3] * log_ratio_d
    )
    log_ratio = clamp(log_ratio, 0.0, math.log(5.0))

    # N uncertainty grows with horizon and with how little the recent
    # growth is constrained by data.
    span = max(1.0, (t_last - pts_rel[0][0]) / 3600.0)
    sig_n = 0.16 + 0.10 * math.sqrt(horizon_h / 24.0) + 0.20 * math.exp(-span / 30.0)
    if len(pts_rel) >= 3:
        sig_n += min(0.10, resid_ll * 0.10)
    sig_n = clamp(sig_n, 0.14, 0.52)

    return n_last * math.exp(log_ratio), sig_n, math.exp(log_ratio), "multi"



def _saturating_ratio(pts, t_last, h_last, h_target, n_last):
    """Grid-fit N(t) = N0 + A (1 - exp(-(t/tau)^alpha)), read at target."""
    if len(pts) < 4 or n_last <= 0:
        return 0.0
    n0 = pts[0][1]
    rel = []
    for t, n in pts:
        h = h_last + (t - t_last) / 3600.0
        if h > 0.0 and n > 0.0:
            rel.append((h, n))
    if len(rel) < 3:
        return 0.0
    best = None
    for tau in (40.0, 60.0, 90.0, 120.0, 160.0, 220.0, 300.0, 450.0):
        for alpha in (0.7, 0.9, 1.1, 1.3, 1.6, 2.0, 2.4):
            num = 0.0
            den = 0.0
            for h, n in rel:
                f = 1.0 - math.exp(-((h / tau) ** alpha))
                w = math.exp(-max(0.0, (h_last - h) / 30.0))
                num += w * f * max(n - n0, 0.0)
                den += w * f * f
            if den <= 1e-12:
                continue
            a = num / den
            if a <= 0.0:
                continue
            sse = 0.0
            sw = 0.0
            for h, n in rel:
                f = 1.0 - math.exp(-((h / tau) ** alpha))
                pred = max(n0 + a * f, 1.0)
                w = math.exp(-max(0.0, (h_last - h) / 30.0))
                sse += w * (math.log(pred) - math.log(n)) ** 2
                sw += w
            rmse = math.sqrt(max(sse / sw, 0.0))
            # mild prior keeps a short, steep sample from running away
            pen = 0.03 * (math.log(tau / 180.0) ** 2) + 0.08 * ((alpha - 1.4) ** 2)
            score = rmse + pen
            pred_t = n0 + a * (1.0 - math.exp(-((max(h_target, 0.0) / tau) ** alpha)))
            if best is None or score < best[0]:
                best = (score, pred_t)
    if best is None or best[1] <= 0.0:
        return 0.0
    ratio = best[1] / n_last
    if not math.isfinite(ratio):
        return 0.0
    return clamp(math.log(max(ratio, 1.0)), 0.0, math.log(10.0))


def _local_growth(pts, t_center, window_h):
    """Slope of ln N per hour over [t_center-window, t_center]."""
    lo = t_center - window_h * 3600.0
    sel = [(t, n) for t, n in pts if lo <= t <= t_center + 1e-6]
    if len(sel) < 2:
        return None
    xs = [(t - t_center) / 3600.0 for t, _ in sel]
    ys = [math.log(n) for _, n in sel]
    ws = [1.0] * len(sel)
    _, b, _ = weighted_line(xs, ys, ws)
    return b


# ---------------------------------------------------------------------------
# per-trophy earner trajectories
# ---------------------------------------------------------------------------

def _series(reads: list[Read], i: int):
    """(t, N, E, r) points for trophy i, with E made monotone."""
    out = []
    prev_e = None
    prev_n = None
    prev_r = None
    for rd in reads:
        r = rd.rates[i]
        if r is None or rd.n <= 0:
            continue
        e = rd.n * r / 100.0
        if prev_e is not None and e < prev_e:
            e = prev_e
        if prev_e is None or e != prev_e or rd.n != prev_n or r != prev_r:
            out.append((rd.t, rd.n, e, r))
            prev_e, prev_n, prev_r = e, rd.n, r
    return out


def _beta_fit(series, t_last, beta_prior):
    """Weighted log E ~ log N slope, shrunk toward beta_prior."""
    points = [(t, n, e) for t, n, e, _ in series if n > 0 and e > 0]
    # Prefer the post-launch / recent regime; the initial 638 -> 3868
    # reviewer-to-public jump is not representative of week-one growth.
    recent = [p for p in points if p[0] >= t_last - 48 * 3600.0]
    work = recent if len(recent) >= 4 else points
    if len(work) < 2:
        return beta_prior, 0.0, 0.0

    xs = [math.log(n) for _, n, _ in work]
    ys = [math.log(e) for _, _, e in work]
    ws = []
    for t, n, e in work:
        rec = math.exp(-max(0.0, (t_last - t) / (30.0 * 3600.0)))
        rel = clamp(e / 20.0, 0.06, 1.0)
        ws.append(rec * rel)

    _, b, resid = weighted_line(xs, ys, ws)
    sw = sum(ws)
    sw2 = sum(w * w for w in ws)
    n_eff = (sw * sw / sw2) if sw2 > 0 else 0.0
    spread_x = max(xs) - min(xs) if xs else 0.0

    b = clamp(b, -0.4, 3.2)
    if spread_x < 0.06 or n_eff < 1.5:
        return beta_prior, 0.0, n_eff

    # shrinkage: require a real log-N range and several effective points
    w_data = (n_eff / (n_eff + 4.0)) * clamp(spread_x / 0.35, 0.0, 1.0)
    beta = w_data * b + (1.0 - w_data) * beta_prior
    beta = clamp(beta, -0.2, 2.8)
    return beta, w_data, n_eff


def _trend_fit(series, t_last, h_last):
    """Damped recency-weighted logit(r) ~ ln(1+h) slope."""
    pts = []
    for t, n, e, r in series:
        if r is None or r <= 0.0 or r >= 100.0 or n <= 0:
            continue
        h = h_last + (t - t_last) / 3600.0
        pts.append((t, math.log1p(max(0.0, h)), logit_rate(r), e))
    if len(pts) < 4:
        return 0.0, 0.0, 0.0
    recent = [p for p in pts if p[0] >= t_last - 72 * 3600.0]
    work = recent if len(recent) >= 4 else pts
    # one point per distinct rate value is enough; duplicates carry no
    # slope information and only inflate confidence.
    seen = set()
    uniq = []
    for p in reversed(work):
        key = round(p[2], 6)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    uniq.reverse()
    if len(uniq) < 3:
        return 0.0, 0.0, 0.0
    xs = [p[1] for p in uniq]
    ys = [p[2] for p in uniq]
    ws = [math.exp(-max(0.0, (t_last - p[0]) / (36.0 * 3600.0))) * clamp(p[3] / 20.0, 0.1, 1.0) for p in uniq]
    _, b, resid = weighted_line(xs, ys, ws)
    b = clamp(b, -1.6, 1.6)
    if max(xs) - min(xs) < 0.08:
        return 0.0, 0.0, 0.0
    return b, resid, len(uniq)


# ---------------------------------------------------------------------------
# main case forecaster
# ---------------------------------------------------------------------------

def forecast_case(case: dict):
    trophies, reads, at_epoch, target_epoch, horizon_min, launch_epoch = normalise_case(case)
    m = len(trophies)
    if m == 0:
        return []

    if reads:
        t_last = reads[-1].t
        n_last = reads[-1].n
    else:
        t_last = at_epoch if at_epoch is not None else launch_epoch
        n_last = 0.0

    h_last = max(0.0, (t_last - launch_epoch) / 3600.0)
    if target_epoch is None:
        target_epoch = t_last + horizon_min * 60.0
    h_target = max(0.0, (target_epoch - launch_epoch) / 3600.0)
    horizon_h = max(0.0, h_target - h_last)
    horizon_days = horizon_h / 24.0
    if horizon_min <= 0.0 and target_epoch is not None and at_epoch is not None:
        horizon_days = max(0.0, (target_epoch - at_epoch) / 86400.0)

    span_h = (reads[-1].t - reads[0].t) / 3600.0 if len(reads) >= 2 else 0.0
    gap_first_last = span_h

    n_pts = n_points(reads)
    n_med, sig_n, n_ratio, n_mode = forecast_n(
        n_pts, t_last, h_last, h_target, span_h, gap_first_last
    )
    if n_last > 0:
        # anchor: the last observed N is certain, the forecast is not
        n_med = max(n_med, n_last * 1.0000001)
    n_hi = n_med * math.exp(1.645 * sig_n)
    ln_ratio_n = math.log(max(n_ratio, 1.0))

    # ---- aggregate earners, used as a robust beta prior ----------------
    agg_pts = []
    for rd in reads:
        if rd.n > 0 and rd.earned > 0:
            agg_pts.append((rd.t, rd.n, rd.earned))
    beta_agg = None
    if len(agg_pts) >= 2:
        recent = [p for p in agg_pts if p[0] >= t_last - 48 * 3600.0]
        work = recent if len(recent) >= 3 else agg_pts
        if len(work) >= 2:
            xs = [math.log(p[1]) for p in work]
            ys = [math.log(p[2]) for p in work]
            ws = [math.exp(-max(0.0, (t_last - p[0]) / (30.0 * 3600.0))) for p in work]
            _, b, _ = weighted_line(xs, ys, ws)
            if max(xs) - min(xs) > 0.04:
                beta_agg = clamp(b, -0.2, 2.8)

    # ---- per-trophy parameters ----------------------------------------
    params = []
    for i, tr in enumerate(trophies):
        arch = archetype(tr)
        series = _series(reads, i)

        r_last = None
        for rd in reversed(reads):
            r = rd.rates[i]
            if r is not None:
                r_last = r
                break
        if r_last is None:
            r_last = 1.0
        e_last = n_last * r_last / 100.0 if n_last > 0 else 0.0

        beta_arch = ARCH_BETA.get(arch, ARCH_BETA["other"])
        if n_mode == "launch":
            beta_prior = LAUNCH_BETA.get(arch, LAUNCH_BETA["other"])
        else:
            if beta_agg is not None:
                beta_prior = 0.15 * beta_agg + 0.85 * beta_arch
            else:
                beta_prior = beta_arch

        beta, w_data, n_eff = _beta_fit(series, t_last, beta_prior)
        # A trophy already held by nearly everyone cannot grow earners
        # faster than the tracked population; keep it from saturating.
        if r_last >= 90.0:
            beta = min(beta, 1.35)
        b_trend, trend_resid, trend_n = _trend_fit(series, t_last, h_last)
        # damped extrapolation of the logit trend
        u_last = math.log1p(max(0.0, h_last))
        u_target = math.log1p(max(0.0, h_target))
        du = max(0.0, u_target - u_last)
        trend_step = 0.42 * b_trend * du
        trend_step = clamp(trend_step, -1.8, 1.8)

        mu_persist = logit_rate(r_last)

        # (i) earner-accumulation projection, with the physical floor
        ratio = max(1.0, n_ratio)
        if e_last > 0.0:
            e_target = e_last * (ratio ** beta)
            e_target = min(e_target, n_med)  # rate <= 100
            r_earn = 100.0 * e_target / max(n_med, 1e-9)
        else:
            r_earn = r_last
        mu_earn = logit_rate(r_earn)

        # (ii) damped trend
        mu_trend = mu_persist + trend_step

        # (iii) archetype projection
        beta_arch_eff = LAUNCH_BETA.get(arch, LAUNCH_BETA["other"]) if n_mode == "launch" else beta_arch
        r_arch = r_last * (ratio ** (beta_arch_eff - 1.0))
        if e_last > 0.0:
            r_arch = max(r_arch, 100.0 * (0.95 * e_last) / max(n_hi, 1e-9))
        mu_arch = logit_rate(r_arch)

        # blend
        if n_mode == "launch":
            w_e, w_t, w_a, w_p = W_LAUNCH
        elif n_mode == "multi" and len(n_pts) >= 3:
            w_e, w_t, w_a, w_p = W_MULTI
            if trend_n < 4:
                w_t = 0.12
                w_e += 0.08
                w_a += 0.06
            elif (r_last < 50.0 and trend_n >= 5
                  and trend_resid < 0.08):
                w_t += 0.15
                w_e = max(0.05, w_e - 0.15)
        else:
            w_e, w_t, w_a, w_p = W_SINGLE

        w_sum = w_e + w_t + w_a + w_p
        if w_sum > 1e-9:
            w_e /= w_sum
            w_t /= w_sum
            w_a /= w_sum
            w_p /= w_sum

        mu = w_e * mu_earn + w_t * mu_trend + w_a * mu_arch + w_p * mu_persist
        mu = clamp(mu, logit_rate(0.01), logit_rate(99.99))

        # ---- spread ---------------------------------------------------
        sig_diff = 0.20 * math.sqrt(horizon_days) + 0.045
        sig_slope = 0.0
        if trend_n >= 4:
            sig_slope = 0.10 * math.sqrt(horizon_days) + 0.40 * trend_resid
            sig_slope = clamp(sig_slope, 0.02, 0.45)
        sig_beta = 0.14 + 0.20 / (1.0 + n_eff)
        if n_mode == "launch":
            sig_beta = 0.24
        sig_earn = abs(ln_ratio_n) * sig_beta + abs(beta - 1.0) * sig_n
        sig_small = clamp(0.30 / math.sqrt(max(e_last, 0.0) + 1.0), 0.03, 0.50)
        if r_last >= 99.9:
            sig_small *= 0.35
        sig_refresh = 0.07 if n_mode == "multi" else 0.42
        if horizon_h < 0.5:
            sig_refresh = 0.035 if n_mode == "multi" else 0.10
        sig_model = 0.07
        sig = math.sqrt(
            sig_diff * sig_diff
            + sig_slope * sig_slope
            + sig_earn * sig_earn
            + sig_small * sig_small
            + sig_refresh * sig_refresh
            + sig_model * sig_model
        )
        sig *= SIG_SCALE
        sig = clamp(sig, 0.055, 1.65)

        # physical lower bound: E(T) >= 0.95 E(last), N(T) <= N_hi
        if e_last > 0.0 and n_hi > 0.0:
            floor_rate = 100.0 * (0.95 * e_last) / n_hi
            floor_rate = clamp(floor_rate, 0.01, 99.99)
            mu = max(mu, logit_rate(floor_rate))

        params.append(
            {
                "id": tr.get("id"),
                "arch": arch,
                "r_last": r_last,
                "e_last": e_last,
                "mu": mu,
                "sig": sig,
                "floor": (100.0 * (0.95 * e_last) / n_hi) if (e_last > 0 and n_hi > 0) else 0.0,
            }
        )

    # ---- quantiles, then hard monotonicity pools ----------------------
    qlog = []
    for p in params:
        q = [p["mu"] + z * p["sig"] for z in Z]
        floor = p.get("floor", 0.0)
        if floor > 0.0:
            lo = logit_rate(floor)
            q = [max(v, lo) for v in q]
        qlog.append(q)

    # open >= ch1 >= ch2 >= ch3 >= ch4 >= final
    rank = {
        "open": 0,
        "story_ch1": 1,
        "story_ch2": 2,
        "story_ch3": 3,
        "story_ch4": 4,
        "story_final": 5,
    }
    chain = [i for i, t in enumerate(trophies) if archetype(t) in rank]
    chain.sort(key=lambda i: (rank[archetype(trophies[i])], trophies[i].get("id", i)))
    for k in range(1, len(chain)):
        a = qlog[chain[k - 1]]
        b = qlog[chain[k]]
        qlog[chain[k]] = [min(b[l], a[l]) for l in range(len(Z))]

    # 100% completion is rarer than the final mission
    comp = [i for i, t in enumerate(trophies) if archetype(t) == "completion_gold"]
    fin = [i for i, t in enumerate(trophies) if archetype(t) == "story_final"]
    if comp and fin:
        ci = comp[0]
        fi = fin[0]
        qlog[ci] = [min(qlog[ci][l], qlog[fi][l]) for l in range(len(Z))]

    # platinum is no rarer than every other trophy at every level
    plat = [i for i, t in enumerate(trophies) if archetype(t) == "platinum"]
    if plat:
        pi = plat[0]
        lim = [float("inf")] * len(Z)
        for j in range(len(qlog)):
            if j == pi:
                continue
            for l in range(len(Z)):
                if qlog[j][l] < lim[l]:
                    lim[l] = qlog[j][l]
        qlog[pi] = [min(qlog[pi][l], lim[l]) for l in range(len(Z))]

    rows = []
    for p, q in zip(params, qlog):
        vals = []
        prev = 0.0
        for v in q:
            pct = inv_logit_rate(v)
            pct = round(clamp(pct, 0.0, 100.0), 4)
            if pct < prev:
                pct = prev
            vals.append(pct)
            prev = pct
        rows.append({"id": p["id"], "q": vals})
    return rows


def forecast_case_rows(case: dict):
    return forecast_case(case)
