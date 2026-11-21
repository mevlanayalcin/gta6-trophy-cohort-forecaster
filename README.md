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
7. **Hierarchical story chain (R3).** Chapter k+1 is reached by chapter k's earners after more play time. Its posterior mixes its own (m, sigma, p) grid with the parent's posterior shifted up the m grid (x1.27^0 to x1.27^6, the same or a wider sigma, p x 0.6 to 1). Story missions borrow from the deepest chapter that at least as many players hold. The chain gets 75% of the prior weight while a step has fewer than 30 earners of its own and 30% after that (R3: 10% after 10). It stays off until a refresh has been observed, so single-state histories such as the practice cases are forecast as in R2.
8. **Onset delay (R4).** Nobody plays more than about 18 hours a day, so a trophy that needs T hours of play cannot be earned in under about T / 0.75 wall hours. The time to earn is a log-normal shifted by d x m, with d in {0, 0.1, 0.2}. Only trophies with at least 30 earners above their base fit d. With fewer earners, a late onset cannot be told apart from a low reach, so the plain log-normal is kept.
9. **m ceiling (R4).** Once a refresh has been seen, the median wall time to earn stops at 1,500 h for story, side and grind trophies (a 250 h grind at the ~4 h of play a day of a typical launch-week player). Past that, every m forecasts the same week-one zero, and R3's log-uniform m prior put most of the mass of a 0-earner trophy there: its median stayed at 0.01% whatever the history said. The platinum and 100% completion keep the full grid (m >= 250 h).
10. **Grind wording (R4).** "3 Collectible Card sets" and "8 mission strands" now count as grinds (low reach prior, m >= 30 h), next to "all/every/each" and counts of 20 or more.

Short horizons mix in the chance that the page does not refresh before the target, using the observed refresh cadence. The likelihood is tempered by 0.35 (R3: 0.5) and every trophy gets 2,000 draws (R3: 800). Everything is pure standard library and deterministic. A 400-read final-shaped case takes 0.6 s on one CPU.

## R4 calibration (2026-11-21)

Live Exophase read at 2026-11-21 08:30Z, 56.5 h after release (all 55 rates kept for the backtest, not in the code):

| read (UTC) | h after release | players tracked | trophies earned | per player | Chapter 1 | Chapter 2 | Chapter 3 | final mission | Beat Cal | platinum |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-19 21:44 | 21.7 | 14,028 | 14,882 | 1.06 | 0.73 | 0.14 | - | - | 1.89 | 0.03 |
| 2026-11-20 09:37 | 33.6 | 36,080 | 45,648 | 1.27 | 7.99 | 0.90 | 0.02 | 0.02 | 10.57 | 0.00 |
| 2026-11-21 08:30 | 56.5 | 54,144 | 85,479 | 1.58 | 18.02 | 3.61 | 0.07 | 0.03 | 16.73 | 0.00 |

- Arrivals: 591/h (4.5 to 21.7 h), 1,853/h (21.7 to 33.6 h, Exophase's sync catch-up), then 789/h (33.6 to 56.5 h). At the deadline the earlier of R3's two 24 h windows still holds the spike, so the fitted decay exponent is about 0.67, shrunk to about 0.63. That gives a median of about 110k players tracked on 26 Nov (90% range 92k to 140k). This part is unchanged.
- Chapter 2 holds 1,955 players but Chapter 3 only 38, and the final mission 16. With one shared play-pace distribution the 50x drop from Chapter 2 to 3 does not fit Chapter 3 at 40 to 55 h of play. The simulated worlds that match the real reads put it at 60 to 100 h. The Ch.3+ earners of day 2.5 look like a small cluster of story rushers, so R4 leans more on the chain for those steps.
- Seven grinds are still at 0.00%: the platinum, 100% completion, 250 stunts, Strangers & Freaks, 3 card sets, 100 WAiNK locations and 50 challenges.

### Backtests (mean pinball loss on the logit scale, lower is better)

**Real reads.** Cases built from the real page states: 0 h (the practice history), 4.5 h, 21.7 h, 33.6 h, scored on the real 56.5 h page. Only some per-trophy values at 21.7 h and 33.6 h are known: chapters 1 and 2, Beat Cal, the platinum, the eight later story steps (0.02% at 33.6 h), and the six grinds that are 0.00% at 56.5 h (so also 0 at 33.6 h). The others are imputed from the 56.5 h values, and the page states between the real reads are interpolated. "known 19" scores only the trophies whose values are real.

| case | R4 all 55 | R4 known 19 | R3 all 55 | R3 known 19 | house | house curve |
|---|---|---|---|---|---|---|
| 34.5 h history -> 56.5 h page | 0.2325 | **0.1699** | **0.2265** | 0.1700 | 0.3978 | 0.6361 |
| 22.5 h history -> 56.5 h page | 0.5719 | **0.4747** | **0.5576** | 0.4791 | 0.8521 | 0.9983 |

R4 ties or beats R3 on the 19 known trophies and is slightly worse on all 55. The difference comes from the imputed rare trophies. In the 22.5 h case, the launch accounts' removal pulled every rare trophy to 0.00 to 0.01%, and R4's wider upper quantiles pay for that. Neither model can foresee the removal from a 22.5 h history. Both models miss Chapter 1's jump on the real data: from the 34.5 h history their q95 is about 12.9%, and the page showed 18.02%.

**Simulated launch weeks.** These come from an agent-based simulator (dev only, not shipped). Players arrive along the real N(t) anchors and then decay with a weekend lift. Each player has a capped play rate, a head start before Exophase tracks them, a sync lag and the launch-day catch-up backlog. Story chapters share a per-player pace. Every trophy is fitted by ABC to its real 56.5 h count, and Chapters 1 and 2 must also match the 21.7 h and 33.6 h reads. The rare Ch.3+ earners are partly a rusher cluster, and a "discovery" event can lift side trophies later in the week. There are four regimes: baseline, weekend surge, fast decay and casual influx. 240 worlds were used for tuning and 240 others are held out.

| benchmark | R4 | R3 | house | house curve |
|---|---|---|---|---|
| final-shaped: 59.5 h history -> 168 h page, tuning worlds | **0.4647** | 0.5718 | 2.1762 | 1.3959 |
| same, held-out worlds | **0.4636** | 0.5745 | 2.1733 | 1.4057 |
| ... baseline regime (held out) | **0.4627** | 0.5714 | 2.2031 | 1.4191 |
| ... weekend surge (held out) | **0.4589** | 0.5819 | 2.1310 | 1.3899 |
| ... fast decay (held out) | **0.4544** | 0.5560 | 2.3658 | 1.4709 |
| ... casual influx (held out) | **0.4754** | 0.5895 | 1.9991 | 1.3438 |
| short: 34.5 h history -> 56.5 h page, held out | **0.3565** | 0.4050 | 0.6034 | 0.7179 |
| practice p00 + p01, on a rebuilt 4.5 h page | 0.1409 | 0.1420 | - | - |

- Paired over the same worlds, R4 - R3 at 168 h is -0.111 on the held-out worlds (95% bootstrap CI -0.115 to -0.107), and R4 is better in 240 of 240. On the tuning worlds it is -0.107 (CI -0.112 to -0.102), also 240 of 240.
- By tier at 168 h (tuning worlds, R3 -> R4): common >1% 0.462 -> 0.319, Ch.3+ story 0.431 -> 0.366, optional mid 0.366 -> 0.309, rare and grind (<0.05%) 0.768 -> 0.631, 100% anchor 0 -> 0. 90% coverage goes from 0.82 to 0.89. R3 over-forecast the common trophies (median logit bias -0.52, now -0.20) and under-forecast the rare ones (+0.53, now +0.25).
- In the simulated weeks the day-7 story runs (10th to 90th percentile across worlds) Ch.1 45 to 64%, Ch.2 20 to 35%, Ch.3 3 to 7% and the final mission 0.6 to 2.5%. The 0.00% grinds have a median of about 0.2%. On a final-shaped case built from the real reads, R4 forecasts those grinds at a median of 0.02% with a 95% quantile of about 0.65%, and the platinum at 0.01% to 0.21%.
- The practice answer is not public. The rebuilt 4.5 h page puts every trophy at its launch-night earners over 3,868 players, plus a few early earners. With one page state, R4 forecasts the practice cases as R3 does except for the extra draws and the grind wording.
- Runs are deterministic: Python 3.13 in Docker (1 CPU, no network) and Python 3.14 on the host give the same SHA-256 for every output. They also stay valid on interleaved recorders (403 reads), a stale cached read, null rates, an empty history, N = 0 and malformed input.
- The simulator numbers reflect the simulator's assumptions, and the same simulator produced the held-out worlds. The real-read cases are the only out-of-time test, and they cover 22 h, not 4.5 days.

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

## R3 backtest A/B (2026-11-20, superseded by the R4 tables above)

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
