# Data visualization: where Finland's cancer pathway breaks

```bash
pip install -r requirements.txt
python main.py             # opens 8 figure windows and saves PNGs to ./figures
python main.py --no-show   # save PNGs only
python main.py --refresh   # re-download the public data from THL Sotkanet (otherwise uses data/sotkanet_cache.json)
```

All data is public and aggregate. It comes from the THL Sotkanet REST API (`sotkanet.fi/rest/1.1`), and the
snapshot was taken on 2026-11-14. Each panel shows its Sotkanet indicator id in `[brackets]`. The national
targets come from *Kansallinen syöpästrategia 2026–2035* (Kuva 1, p.5).

## Figures

| # | Question it answers | Main takeaway (from the 2026-11-14 snapshot) |
|---|---|---|
| 1 | National trend at each pathway step | Specialist care backlog (>6 mo wait) went from about 6 to 45 per 10k between 2019 and 2024. GP waits over 7 d went from 38 % to 55 %. Cervical and colorectal screening are below the 80 % target. |
| 2 | **Where** each county falls behind (heatmap) | North Karelia, Helsinki and Vantaa-Kerava have the largest gaps. Diagnosis → treatment start has **no public data** (hatched column). |
| 3 | Which step is most unequal | The >6-month specialist backlog has by far the largest variation between counties (CV about 83 %). The next largest is GP access. |
| 4 | Early warning | The slope of the backlog over the last 4 years flags North Ostrobothnia, North Karelia and Southwest Finland. |
| 5 | Do upstream gaps show up in survival? | At county level, no correlation is significant (n≈22, ecological data, survival lags by years). Say this honestly. |
| 6 | Goal 3 targets | Survival must rise +14 pp (lung), +6 pp (pancreas) and +14 pp (liver) to reach the Nordic level. A −10 % mortality target while cases grow +18 % means **deaths per case must fall about 24 %**. |
| 7 | **Cost function / bottleneck** | Detection makes up about 57 % of the weighted loss, primary care 17 %, referral 14 % and outcome 12 %. |
| 8 | **Intervention simulation** | The same national screening effort lowers the equity loss more when targeted than when spread uniformly. |

## Cost functions you can minimise

Notation: `x_rk` is the value of indicator k in county r. `T_k` is the target: official where one exists
(80 % screening, 0 people waiting more than 6 months), otherwise the best-quartile county as a benchmark.
`σ_k` is the spread between counties, and `w_r` is the county's share of the population.

1. **Shortfall:** `s_rk = max(0, ±(T_k − x_rk)) / σ_k`. Only being *behind* counts, and the units are comparable across indicators.
2. **Equity loss (used in fig 7–8):** `L = Σ_r w_r Σ_stage mean_k s_rk²`. Squaring penalises a county that is far behind more than several counties that are slightly behind. That matches the "equal care wherever you live" goal.
3. Alternatives worth discussing with mentors:
   * Linear `Σ w s` measures efficiency (total people affected) and is indifferent to who is affected.
   * Minimax `max_r s_r` is a strict "no county left behind" rule. It ignores everyone except the worst county.
   * Time-to-treatment loss `P(wait > target)` is what Goal 3 actually measures. It needs FICAN or quality-registry data, which is not public.

## Limitations (say these in the pitch)
* Waiting indicators (6411, 13, 3336) cover **all specialties**, not cancer only. Treat them as system-capacity signals.
* Survival (3625–3627) reflects patients diagnosed years earlier, and small counties are noisy.
* The Uusimaa counties report referral data jointly through HUS, so those cells are blank.
* Nordic mortality (10024) ends in 2015 in Sotkanet. Use NORDCAN for newer figures.
* Benchmarks, squared loss, and the +2 pp effort in fig 8 are modelling choices, not estimates.
