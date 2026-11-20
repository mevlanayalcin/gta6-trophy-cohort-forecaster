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
5. **Sampled, then rounded.** Draws over N scenarios, grid points, logit-scale model error (it grows with log-time ahead) and count noise are each rounded to the page's 2 decimals. The output is the empirical quantiles, clamped to 0.01..99.99 (the scorer clamps there before its logit, so this costs nothing). Chapter order, 100% <= final mission and platinum <= every trophy are enforced per quantile. The trophy nearly everyone holds is forecast through the few players who lack it.
6. **Removed earners (R3).** When a trophy's count falls, E is treated as non-decreasing only after the drop, and the launch base is cut to what the page still shows from that state on. Earlier states keep the old base, so removed accounts are never read as real earners.
7. **Hierarchical story chain (R3).** Chapter k+1 is reached by chapter k's earners after more play time. Its posterior mixes its own (m, sigma, p) grid with the parent's posterior shifted up the m grid (x1.27^0 to x1.27^6, the same or a wider sigma, p x 0.6 to 1). Story missions borrow from the deepest chapter that at least as many players hold. The chain gets 75% of the prior weight while a step has fewer than 10 earners of its own and 10% after that. It stays off until a refresh has been observed, so single-state histories such as the practice cases are forecast exactly as in R2.

Short horizons mix in the chance that the page does not refresh before the target, using the observed refresh cadence. Everything is pure standard library and deterministic. A case takes about 0.5 s.

## R3 calibration (2026-11-20)

Live Exophase reads of the PS5 page (the page's own "Players Tracked" and per-trophy `data-average`, read through r.jina.ai because Exophase answers direct requests with 403):

| read (UTC) | h after release | players tracked | trophies earned | per player | Chapter 1 | Chapter 2 | Beat Cal | platinum |
|---|---|---|---|---|---|---|---|---|
| 2026-11-19 00:00 (launch night) | 0 | 638 | - | - | 1.25 | 1.10 | 2.04 | 0.63 |
| first refresh (RULES.txt) | 4.5 | 3,868 | - | - | - | - | - | - |
| 2026-11-19 21:44 | 21.7 | 14,028 | 14,882 | 1.06 | 0.73 | 0.14 | 1.89 | 0.03 |
| 2026-11-20 09:37 (unchanged to 10:13) | 33.6 | 36,080 | 45,648 | 1.27 | 7.99 | 0.90 | 10.57 | 0.00 |

What these reads show:

- **Arrivals are still speeding up.** The page added 587 players an hour between 4.5 h and 21.7 h, then 1,853 an hour between 21.7 h and 33.6 h. That is not the decaying power law R2's prior assumes.
- **Trophies sync well after their players.** Over the 12 hours, Chapter 1's earners grew about 28x (about 100 to 2,880) while players tracked grew 2.6x. Breakfast Run, Beat Cal, bowling and the arcade trophy grew 7x to 27x. God of War Ragnarok's Exophase page showed the same in its launch week: "Equip an Enchantment" went from 2.83% at day 0.9 to 42.3% at day 7.1.
- **The launch-night accounts left.** The 4 accounts holding the platinum and every other trophy were removed between the two live reads. The platinum went 0.03% -> 0.00% and 41 of 55 rates fell. The platinum is still unearned.
- **The story is in Chapter 1 to 2.** Chapters 3, 4 and the final mission, and the five later story missions, are still at the 0.02% floor. There is no viral-guide jump: every collectible and "all/every" trophy is at 0.00 to 0.01%. The PS5 list has no online/multiplayer trophies.

R2's residuals, forecasting the 33.6 h page from the 22.5 h history (the only real out-of-time test in this data; one case, 55 trophies):

| category | trophies | rose / fell | R2 median logit bias (y - q50) | R2 90% coverage | R2 loss | R3 loss |
|---|---|---|---|---|---|---|
| platinum / 100% | 2 | 0 / 2 | -0.69 | 0.00 | 0.478 | 0.478 |
| story (chapters + missions) | 11 | 3 / 8 | -0.05 (Ch1 +1.9, Ch3 and later -0.7) | 0.09 | 0.763 | 0.798 |
| collectibles / grind | 17 | 1 / 16 | -0.76 | 0.24 | 0.655 | 0.655 |
| side activities / misc | 9 | 6 / 3 | +0.11 | 0.44 | 0.644 | 0.644 |
| strangers & freaks | 7 | 0 / 7 | -0.88 | 0.29 | 0.669 | 0.669 |
| combat / crime | 8 | 3 / 5 | -0.43 | 0.62 | 0.435 | 0.435 |
| **all** | 54 + 1 at 100% | | | 0.31 | **0.626479** | **0.633548** |

Two things drive R2's misses. The account removal pulled every dormant trophy below its floor, and nothing in a 22.5 h history could foresee that. The trophies that moved were under-forecast: Chapter 1's q95 was 4.45 against 7.99. R3 handles the removal from here on. On this case R3 is 0.007 worse: the chain lifts the data-poor story steps, and those steps then fell with the removal.

## Backtest A/B

The house's recording is not public, so out-of-time tests use an agent-based simulator of the launch week (dev only, not shipped). Each world draws arrivals (a rising-then-decaying intensity with a daily cycle and a weekend lift), sync lags, play rates, page refresh times, the launch accounts' removal and each trophy's difficulty. It is conditioned on every real read above: 3,868 players at 4.5 h, players and every rate at 33.6 h (fitted), and the 21.7 h read (worlds whose mean logit error on the 21.7 h rates exceeds 0.6 are rejected; about 7% pass). Target: the page at 2026-11-26T00:00Z (168 h). Mean pinball loss on the logit scale (lower is better):

| cases | R3 | R2 | house curve | house persistence |
|---|---|---|---|---|
| 202 f00-shaped worlds (checkpoint 59.5 h, the deadline) | **0.539824** | 0.603372 | 1.151815 | 1.574646 |
| 87 day-1.5 worlds (checkpoint 36 h) | **0.853659** | 0.870600 | 2.467838 | 2.054962 |
| real: GTA VI, 22.5 h history -> 33.6 h page | 0.633548 | **0.626479** | - | - |
| real: God of War Ragnarok, Exophase day 0.9 -> day 7.1 | 1.171931 | 1.171931 | - | 1.164040 |
| practice p00 + p01 (server) | 0.150223 (same loss as R2) | 0.150223 | - | about 1.51 |

- Paired over the same worlds, R3 - R2 is -0.0635 at 59.5 h (95% bootstrap CI -0.071 to -0.056; R3 better in 182/202) and -0.0169 at 36 h (CI -0.024 to -0.010; 63/87).
- At 59.5 h by category (R2 -> R3): collectibles/grind 0.735 -> 0.596, strangers 0.686 -> 0.620, story 0.718 -> 0.668, platinum/100% 0.456 -> 0.399, side/misc 0.411 -> 0.404.
- The constants were tuned on 60 worlds and checked on the other 142 held out (0.603 -> 0.546 there).
- The God of War case holds one read, and its wording has no "Complete Chapter n", so neither R3 change applies and R3 equals R2.
- The practice outputs differ from R2's only where a quantile moved from 0 to 0.01 or from 100 to 99.99. The scorer's clamp makes that loss-neutral, which was checked to 9 decimals on reconstructed practice answers.
- Other launch-week comparables were not usable. The Wayback Machine has no Exophase launch-week captures for RDR2, GTA V PS5 or Spider-Man 2, Cyberpunk's day-0 capture tracks 29 players, and Elden Ring, Hogwarts Legacy and FF XVI have one capture each.
- The simulator numbers reflect its assumptions, not the real week.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 -P -s main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=0fhdkvxa&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
