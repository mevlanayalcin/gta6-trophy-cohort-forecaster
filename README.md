# gta6-trophy-cohort-forecaster

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/mevlanayalcin/gta6-trophy-cohort-forecaster/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/mevlanayalcin/gta6-trophy-cohort-forecaster/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts every GTA VI PS5 trophy's Exophase earn rate at the end of week one, as five quantiles scored by pinball loss on the logit scale.

## How it works

Exophase prints `rate = round(100 * E / N, 2)`, with N the players tracked and E the tracked players holding the trophy. The page only changes when Exophase refreshes it, so the model works on counts:

1. **Refresh states.** Consecutive identical reads are one stale page. Reads whose N falls below an earlier read (an old cached copy) are dropped unless the drop persists.
2. **Integer earners.** Each printed rate gives an integer interval for E. The intervals are intersected under "E never decreases", so the exact launch-night counts (N = 638) pin down later ones.
3. **Players tracked.** Arrivals per hour over the last two 24 h windows give the decay exponent of a power-law intensity. That exponent is shrunk to a prior and integrated to the target. A history that holds only one state (the launch-night backlog) falls back to `N ~ (h + 1)^0.95`.
4. **Cohort earning.** Each refresh adds a cohort of new players. A trophy's earners above its launch base are modelled as `p * sum_k dN_k * G((t - s_k) / m)`, with G a log-normal time-to-earn. A grid over (m, sigma, p) is weighted by a tempered likelihood of the observed counts. Wording sets the priors: story trophies get a high p; collection trophies get a low p and m >= 30 h; the platinum and 100% completion get m >= 250 h.
5. **Sampled, then rounded.** Draws over N scenarios, grid points, logit-scale model error (it grows with log-time ahead) and count noise are each rounded to the page's 2 decimals. The output is the empirical quantiles, so the zero-earner and near-zero trophies land exactly on 0.01 / 0.02 / ... Chapter order, 100% <= final mission and platinum <= every trophy are enforced per quantile. The trophy nearly everyone holds is forecast through the few players who lack it.

Short horizons mix in the chance that the page does not refresh before the target, using the observed refresh cadence. Everything is pure standard library and deterministic. A case takes well under a second.

## Backtest

The practice cases cannot test a week-ahead forecast: every read in their history is the same unrefreshed page. The model was tuned instead on simulated launch weeks. An agent-based simulator draws player arrivals, play rates, Exophase scan lags, page refresh times and per-trophy difficulty. Each world is conditioned to reproduce the real launch reads: 638 players at the first read, 3,868 at the first refresh, and a live read of 14,028 players with every trophy's rate at 2026-11-19T21:44Z. Mean loss (lower is better):

| cases (simulated) | this model | house curve | house persistence | round-1 model |
|---|---|---|---|---|
| 120 f00-shaped (59.5 h -> 168 h) | **0.539** (cov 0.94) | 0.955 | 1.183 | 1.247 |
| 160 other checkpoints (24-120 h -> 168 h) | **0.557** | 1.353 | 1.186 | 1.409 |
| 60 with slower late buyers | **0.489** | 0.872 | 1.012 | - |
| 80 short horizons (2.5 h, 32 h) | **0.166** | 0.387 | 0.386 | 0.418 |

These numbers reflect the simulator's assumptions, not the real week.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 -P -s main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=0fhdkvxa&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
