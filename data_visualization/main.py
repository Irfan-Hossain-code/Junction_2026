"""
Cancer-care pathway gap explorer - Junction 2026 / AstraZeneca track.

Run:   python main.py            # uses cached public data in ./data, shows + saves figures
       python main.py --refresh  # re-download from THL Sotkanet REST API first
       python main.py --no-show  # only save PNGs to ./figures

Every number comes from either
  * THL Sotkanet open API (https://sotkanet.fi/rest/1.1/) - aggregate, public, per wellbeing services county
  * Kansallinen syopastrategia 2026-2035 (FINAL.pdf) - the national targets (page refs in SOURCES below)
Anything that is a modelling choice (benchmarks, loss weights, budget) is labelled as such on the figure.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from scipy import optimize, stats

HERE = Path(__file__).resolve().parent
CACHE = HERE / "data" / "sotkanet_cache.json"
FIG_DIR = HERE / "figures"
API = "https://sotkanet.fi/rest/1.1"

# ----------------------------------------------------------------------------------------------
# Indicators. stage = where in the care pathway it sits. better = +1 if higher is better.
# target: (value, source-label). None -> benchmark = best-quartile county (a modelling choice).
# ----------------------------------------------------------------------------------------------
INDICATORS = {
    3621: dict(short="Breast screening participation", unit="%", stage="1 Detection", better=+1,
               target=(80, "Strategy Goal 2: 80 % in every county")),
    3620: dict(short="Cervical screening participation", unit="%", stage="1 Detection", better=+1,
               target=(80, "Strategy Goal 2: 80 % in every county")),
    6103: dict(short="Colorectal screening participation", unit="%", stage="1 Detection", better=+1,
               target=(80, "Strategy Goal 2: 80 % in every county")),
    6411: dict(short="GP non-urgent visit wait > 7 d", unit="% of visits", stage="2 Primary care access", better=-1,
               target=None),
    13:   dict(short="Specialist referrals handled <= 3 d", unit="% of referrals", stage="3 Referral -> specialist", better=+1,
               target=None),
    3336: dict(short="Waited > 6 months for specialist care", unit="per 10 000 pop", stage="3 Referral -> specialist", better=-1,
               target=(0, "Health Care Act 1326/2010 s.52: max 6 months")),
    3625: dict(short="Breast cancer 5-yr relative survival", unit="%", stage="5 Outcome (Goal 3)", better=+1, target=None),
    3626: dict(short="Cervical cancer 5-yr relative survival", unit="%", stage="5 Outcome (Goal 3)", better=+1, target=None),
    3627: dict(short="Bowel cancer 5-yr relative survival", unit="%", stage="5 Outcome (Goal 3)", better=+1, target=None),
}
CONTEXT = {
    5093: "Entitled to cancer-medicine special refund (% pop, age-std.)",
    5643: "Cancer morbidity index (age-std., Finland = 100)",
    10024: "Standardised cancer death rate per 100 000 (EU / Nordic)",
    127: "Population at year end",
    171: "Population aged 75+ (% of pop)",
    5033: "Specialist referrals handled > 21 d (%)",
}
STAGES = ["1 Detection", "2 Primary care access", "3 Referral -> specialist",
          "4 Diagnosis -> treatment start", "5 Outcome (Goal 3)"]

# Wellbeing services counties (+ City of Helsinki). Aland (956) is outside the wellbeing-county system.
COUNTIES = {
    960: "Helsinki", 977: "Vantaa-Kerava", 966: "Central Uusimaa", 969: "West Uusimaa", 961: "East Uusimaa",
    978: "Southwest Finland", 976: "Satakunta", 963: "Kanta-Häme", 970: "Pirkanmaa", 975: "Päijät-Häme",
    967: "Kymenlaakso", 957: "South Karelia", 959: "South Savo", 974: "North Savo", 972: "North Karelia",
    965: "Central Finland", 958: "South Ostrobothnia", 971: "Ostrobothnia", 964: "Central Ostrobothnia",
    973: "North Ostrobothnia", 962: "Kainuu", 968: "Lapland",
}

SOURCES = {
    "sotkanet": "THL Sotkanet open data API, sotkanet.fi/rest/1.1 (indicator ids shown on each panel)",
    "strategy": "Kansallinen syöpästrategia 2026–2035, Kuva 1 p.5 & p.5–6",
}

# Strategy numbers (Kansallinen syopastrategia 2026-2035, p.5 'Kuva 1' + footnotes, p.5-6 text)
NORDIC_SURVIVAL_GAP_PP = {"Lung": 14, "Pancreas": 6, "Liver": 14}   # pp gain needed to reach SE/NO average (Nordcan 2023)
CANCER_DEATHS_NOW = 13_300                                            # per year, p.5
MORTALITY_REDUCTION = 0.10                                            # -10 % by 2035, p.5
CASES_NOW, CASES_2035 = 38_000, 45_000                                # p.6 ("hieman vajaa 38 000" -> "lähes 45 000")

# ----------------------------------------------------------------------------------------------
# Palette (validated reference palette from the dataviz skill; categorical slots used in order)
# ----------------------------------------------------------------------------------------------
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
GOOD, BAD, MID = "#2a78d6", "#e34948", "#f0efec"   # diverging poles: blue = better, red = worse


def style():
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "text.color": INK, "axes.titlecolor": INK, "axes.titlesize": 11, "axes.titleweight": "bold",
        "axes.titlelocation": "left", "font.size": 9, "axes.grid": True, "axes.axisbelow": True, "grid.color": GRID,
        "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
        "lines.linewidth": 2, "lines.markersize": 5, "legend.frameon": False, "figure.dpi": 100,
    })


# ----------------------------------------------------------------------------------------------
# Data layer
# ----------------------------------------------------------------------------------------------
def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "junction-hackathon-dataviz"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def refresh_cache():
    print("Downloading from THL Sotkanet ...")
    regions = _get(f"{API}/regions")
    keep_cat = {"MAA", "HYVINVOINTIALUE", "POHJOISMAAT"}
    keep = {r["id"]: {"cat": r["category"], "fi": r["title"].get("fi"), "en": r["title"].get("en")}
            for r in regions if r["category"] in keep_cat}
    years = "".join(f"&years={y}" for y in range(2005, date.today().year + 1))
    out = {"fetched": date.today().isoformat(), "source": API, "regions": keep, "indicators": {}}
    for ind in list(INDICATORS) + list(CONTEXT):
        rows = _get(f"{API}/json?indicator={ind}{years}&genders=total&genders=male&genders=female")
        rows = [dict(region=x["region"], year=x["year"], gender=x["gender"], value=x.get("value"))
                for x in rows if x["region"] in keep and x.get("value") is not None]
        try:
            meta = _get(f"{API}/indicators/{ind}")
            title = meta.get("title", {})
        except Exception:
            title = {}
        out["indicators"][str(ind)] = {"title": title, "rows": rows}
        print(f"  {ind:>6}: {len(rows):5d} rows  {title.get('en', '')[:70]}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {CACHE}")


def load():
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    regions = {int(k): v for k, v in raw["regions"].items()}
    frames = []
    for ind, blob in raw["indicators"].items():
        df = pd.DataFrame(blob["rows"])
        if df.empty:
            continue
        df["ind"] = int(ind)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["cat"] = df.region.map(lambda r: regions[r]["cat"])
    df["name"] = df.region.map(lambda r: COUNTIES.get(r) or regions[r]["en"] or regions[r]["fi"])
    return df, regions, raw["fetched"]


def national(df, ind, gender="total"):
    s = df[(df.ind == ind) & (df.cat == "MAA") & (df.gender == gender)].sort_values("year")
    return s.year.to_numpy(), s.value.to_numpy()


def county_latest(df, ind, min_regions=18):
    """Latest year with data for most counties -> Series indexed by county name."""
    d = df[(df.ind == ind) & (df.cat == "HYVINVOINTIALUE") & (df.gender == "total") & df.region.isin(COUNTIES)]
    counts = d.groupby("year").region.nunique()
    good = counts[counts >= min_regions]
    if good.empty:
        return pd.Series(dtype=float), None
    yr = int(good.index.max())
    return d[d.year == yr].set_index("name").value.astype(float), yr


def county_panel(df):
    cols, years = {}, {}
    for ind in INDICATORS:
        s, y = county_latest(df, ind)
        cols[ind], years[ind] = s, y
    panel = pd.DataFrame(cols).reindex(list(COUNTIES.values()))
    return panel, years


def population(df):
    s, _ = county_latest(df, 127)
    return s.reindex(list(COUNTIES.values()))


# ----------------------------------------------------------------------------------------------
# The cost function
# ----------------------------------------------------------------------------------------------
def targets_for(panel):
    """Target per indicator: official where one exists, else best-quartile county (benchmark)."""
    t = {}
    for ind, m in INDICATORS.items():
        if m["target"] is not None:
            t[ind] = (float(m["target"][0]), m["target"][1])
        else:
            q = 0.75 if m["better"] > 0 else 0.25
            t[ind] = (float(panel[ind].quantile(q)), "benchmark: best-quartile county")
    return t


def shortfall(panel, targets):
    """s[r,k] = max(0, better_k * (T_k - x_rk)) / sd_k   -> 'how many county-SDs short of target'."""
    S = pd.DataFrame(index=panel.index)
    for ind, m in INDICATORS.items():
        x = panel[ind]
        sd = x.std(ddof=0) or 1.0
        S[ind] = np.maximum(0.0, m["better"] * (targets[ind][0] - x)) / sd
    return S.fillna(0.0)


def equity_loss(S, w):
    """L = sum_r w_r * sum_k s_rk^2  (population-weighted squared shortfall).
    Squaring = equity: one county far behind costs more than many slightly behind."""
    return (S.pow(2).mul(w, axis=0)).sum().sum()


# ----------------------------------------------------------------------------------------------
# Figures
# ----------------------------------------------------------------------------------------------
def src(fig, text):
    fig.text(0.01, 0.005, "Source: " + text, fontsize=7, color=MUTED, ha="left", va="bottom")


def end_label(ax, x, y, text, color, dy=0):
    ax.annotate(text, (x, y), xytext=(4, dy), textcoords="offset points", fontsize=8, color=INK2,
                va="center", ha="left")
    ax.plot([x], [y], "o", color=color, ms=5, mec=SURFACE, mew=1.5, zorder=5)


def fig1_national_pathway(df, plt):
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.2))
    fig.suptitle("1 · National trend at each step of the cancer pathway (Finland)", x=0.01, ha="left",
                 fontsize=14, fontweight="bold")
    # a screening
    ax = axes[0, 0]
    for ind, c, lab, dy in [(3621, S1, "Breast", 0), (3620, S2, "Cervical", -7), (6103, S3, "Colorectal", 7)]:
        x, y = national(df, ind)
        if len(x):
            ax.plot(x, y, color=c, marker="o" if len(x) < 5 else None)
            end_label(ax, x[-1], y[-1], f"{lab} {y[-1]:.0f}%", c, dy=dy)
    ax.axhline(80, color=BAD, lw=1, ls="--"); ax.text(ax.get_xlim()[0], 80.4, "target 80 % (Goal 2)", color=BAD, fontsize=8)
    ax.set_title("DETECTION · screening participation, % invited\n[3621, 3620, 6103]")
    # b GP wait
    ax = axes[0, 1]; x, y = national(df, 6411)
    ax.plot(x, y, color=S1, marker="o"); end_label(ax, x[-1], y[-1], f"{y[-1]:.0f}%", S1)
    ax.set_ylim(0, max(y) * 1.25)
    ax.set_title("PRIMARY CARE · non-urgent GP visits waiting > 7 d, %\n[6411] (lower = better)")
    # c referrals handled in 3 d
    ax = axes[0, 2]; x, y = national(df, 13)
    ax.plot(x, y, color=S1); end_label(ax, x[-1], y[-1], f"{y[-1]:.0f}%", S1)
    ax.set_ylim(50, 100)
    ax.set_title("REFERRAL · referrals processed ≤ 3 d, %\n[13] (higher = better)")
    # d >6 mo waiting
    ax = axes[1, 0]; x, y = national(df, 3336)
    ax.fill_between(x, 0, y, color=BAD, alpha=0.12, lw=0); ax.plot(x, y, color=BAD)
    end_label(ax, x[-1], y[-1], f"{y[-1]:.0f} / 10k", BAD)
    i0 = np.argmin(np.abs(x - 2019)); ax.annotate(f"{y[i0]:.0f} in {x[i0]}", (x[i0], y[i0]), xytext=(0, 18),
                                                   textcoords="offset points", fontsize=8, color=INK2, ha="center",
                                                   arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))
    ax.set_title("SPECIALIST ACCESS · waited > 6 months, per 10 000 pop\n[3336] legal max is 6 mo → target 0")
    # e survival
    ax = axes[1, 1]
    for ind, c, lab in [(3625, S1, "Breast"), (3626, S2, "Cervical"), (3627, S3, "Bowel")]:
        x, y = national(df, ind)
        ax.plot(x, y, color=c); end_label(ax, x[-1], y[-1], f"{lab} {y[-1]:.0f}%", c)
    ax.set_title("OUTCOME (Goal 3) · 5-yr relative survival, %\n[3625, 3626, 3627]")
    # f treatment load
    ax = axes[1, 2]; x, y = national(df, 5093)
    ax.plot(x, y, color=S1, marker="o"); end_label(ax, x[-1], y[-1], f"{y[-1]:.1f}%", S1)
    ax.set_ylim(0, max(y) * 1.4)
    ax.set_title("DEMAND · cancer-medicine refund, % pop\n[5093] (age-standardised)")
    for ax in axes.flat:
        ax.margins(x=0.12); ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    fig.text(0.01, 0.035, "Gap in the data: there is NO public county-level indicator for "
             "diagnosis → treatment start (THL 21/2016 target times). That step is invisible in open data.",
             fontsize=9, color=BAD, fontweight="bold")
    src(fig, SOURCES["sotkanet"])
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    return fig


def fig2_heatmap(panel, years, plt):
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    z = pd.DataFrame(index=panel.index)
    for ind, m in INDICATORS.items():
        x = panel[ind]
        z[ind] = -m["better"] * (x - x.mean()) / (x.std(ddof=0) or 1)   # + = worse than average
    z.insert(list(INDICATORS).index(3336) + 1, "dx2tx", np.nan)              # missing stage column
    order = z.mean(axis=1).sort_values(ascending=False).index
    z = z.loc[order]
    cmap = LinearSegmentedColormap.from_list("div", [GOOD, MID, BAD]); cmap.set_bad(SURFACE)
    fig, ax = plt.subplots(figsize=(13, 9))
    im = ax.imshow(z.to_numpy(dtype=float), cmap=cmap, norm=TwoSlopeNorm(0, -2.5, 2.5), aspect="auto")
    labels = []
    for c in z.columns:
        if c == "dx2tx":
            labels.append("Diagnosis →\ntreatment start\n(NO PUBLIC DATA)")
        else:
            labels.append(f"{INDICATORS[c]['short']}\n[{c}] {years[c]}")
    ax.set_xticks(range(len(z.columns)), labels, rotation=40, ha="right", fontsize=8)
    ax.set_yticks(range(len(z)), z.index)
    ax.grid(False)
    j = list(z.columns).index("dx2tx")
    ax.add_patch(matplotlib.patches.Rectangle((j - 0.5, -0.5), 1, len(z), hatch="///", fill=False,
                                              edgecolor=MUTED, lw=0))
    raw = panel.loc[order]
    for i in range(len(z)):
        for jj, c in enumerate(z.columns):
            if c == "dx2tx" or pd.isna(raw.loc[z.index[i], c]):
                continue
            v = raw.loc[z.index[i], c]; zz = z.iloc[i, jj]
            ax.text(jj, i, f"{v:.0f}", ha="center", va="center", fontsize=7,
                    color="white" if abs(zz) > 1.6 else INK)
    # stage separators
    stages = [INDICATORS[c]["stage"] if c != "dx2tx" else STAGES[3] for c in z.columns]
    for k in range(1, len(stages)):
        if stages[k] != stages[k - 1]:
            ax.axvline(k - 0.5, color=SURFACE, lw=4)
    short = {STAGES[0]: "DETECTION", STAGES[1]: "PRIMARY\nCARE", STAGES[2]: "REFERRAL →\nSPECIALIST",
             STAGES[3]: "DIAGNOSIS →\nTREATMENT", STAGES[4]: "OUTCOME\n(GOAL 3)"}
    for k, st in enumerate(stages):
        if k == 0 or stages[k] != stages[k - 1]:
            n = stages.count(st)
            ax.text(k - 0.5 + n / 2, -0.7, short[st], ha="center", va="bottom", fontsize=8.5,
                    fontweight="bold", color=INK2)
    cb = fig.colorbar(im, ax=ax, shrink=0.6, pad=0.02)
    cb.set_label("SDs from county mean (red = worse, blue = better)")
    ax.set_title("2 · WHERE does each county fall behind?  Rows sorted by average gap (worst at top). "
                 "Cell text = actual value", pad=40)
    src(fig, SOURCES["sotkanet"] + ". Latest year with ≥18 counties reporting. Blank = not reported "
             "(Uusimaa counties: referral data reported jointly by HUS).")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    return fig


def fig3_inequality_by_stage(panel, years, targets, plt):
    inds = list(INDICATORS)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(15, 7), gridspec_kw=dict(width_ratios=[2.2, 1]))
    rng = np.random.default_rng(0)
    for i, ind in enumerate(inds):
        x = panel[ind].dropna()
        ax.scatter(x, np.full(len(x), i) + rng.uniform(-0.12, 0.12, len(x)), s=22, color=S1, alpha=0.75,
                   edgecolor=SURFACE, lw=0.6, zorder=3)
        t, tl = targets[ind]
        ax.plot([t, t], [i - 0.3, i + 0.3], color=BAD if "benchmark" not in tl else MUTED, lw=2, zorder=4)
        worst = x.idxmin() if INDICATORS[ind]["better"] > 0 else x.idxmax()
        ax.annotate(worst, (x[worst], i), xytext=(0, -13), textcoords="offset points", fontsize=7,
                    color=INK2, ha="center")
    # each indicator has different units -> separate x via twin? No: normalise display by using
    # per-row axes is cleaner; here we just show raw values on a shared % scale where possible.
    ax.set_yticks(range(len(inds)), [f"{INDICATORS[i]['short']} ({INDICATORS[i]['unit']}) [{i}]" for i in inds])
    ax.invert_yaxis()
    ax.set_xlabel("county value (each dot = one wellbeing services county)")
    ax.set_title("3a · Spread across counties (red tick = official target, gray = best-quartile benchmark)",
                 fontsize=10)
    # b: inequality metric - coefficient of variation and worst/best ratio
    cv, ratio = [], []
    for ind in inds:
        x = panel[ind].dropna()
        cv.append(100 * x.std(ddof=0) / x.mean())
        best, worst = (x.max(), x.min()) if INDICATORS[ind]["better"] > 0 else (x.min(), x.max())
        ratio.append(worst / best if best else np.nan)
    order = np.argsort(cv)
    ax2.barh(range(len(inds)), np.array(cv)[order], color=[S1] * len(inds), height=0.6)
    ax2.set_yticks(range(len(inds)), [INDICATORS[inds[k]]["short"] for k in order])
    for k, o in enumerate(order):
        ax2.text(cv[o] + 0.5, k, f"{cv[o]:.0f}%", va="center", fontsize=8, color=INK2)
    ax2.set_xlabel("coefficient of variation across counties, %")
    ax2.set_title("3b · Which step is most UNEQUAL?")
    src(fig, SOURCES["sotkanet"] + ". CV = sd/mean; high CV = postcode lottery.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    return fig


def fig4_backlog_early_warning(df, plt):
    d = df[(df.ind == 3336) & (df.cat == "HYVINVOINTIALUE") & df.region.isin(COUNTIES)]
    piv = d.pivot_table(index="year", columns="name", values="value")
    recent = piv.loc[piv.index >= piv.index.max() - 3]
    slopes = {c: stats.linregress(recent.index, recent[c]).slope for c in recent.columns if recent[c].notna().sum() >= 3}
    slopes = pd.Series(slopes).sort_values(ascending=False)
    top = list(slopes.index[:3])
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(15, 6.5), gridspec_kw=dict(width_ratios=[1.6, 1]))
    for c in piv.columns:
        if c not in top:
            ax.plot(piv.index, piv[c], color=GRID, lw=1.2)
    for c, col in zip(top, [S1, S2, S3]):
        ax.plot(piv.index, piv[c], color=col, lw=2)
        end_label(ax, piv.index[-1], piv[c].iloc[-1], c, col)
    x, y = national(df, 3336)
    ax.plot(x, y, color=INK, lw=2.5); end_label(ax, x[-1], y[-1], "Finland", INK)
    ax.margins(x=0.15)
    ax.set_ylabel("people waited > 6 months for specialist care, per 10 000")
    ax.set_title("4a · Specialist-care backlog: 3 fastest-growing counties highlighted [3336]")
    yrs = f"{int(recent.index.min())}–{int(recent.index.max())}"
    cols = [BAD if v > 0 else GOOD for v in slopes.values]
    ax2.barh(range(len(slopes)), slopes.values, color=cols, height=0.65)
    ax2.set_yticks(range(len(slopes)), slopes.index, fontsize=8); ax2.invert_yaxis()
    ax2.axvline(0, color=INK2, lw=0.8)
    ax2.set_xlabel(f"trend {yrs}: change per year (per 10 000)  — scipy.stats.linregress")
    ax2.set_title("4b · EARLY-WARNING score: who is getting worse fastest?")
    src(fig, SOURCES["sotkanet"] + ". All specialties (not cancer-only) - a system-capacity signal.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    return fig


def fig5_upstream_vs_outcome(panel, plt):
    pairs = [(6103, 3627, "Colorectal screening → bowel cancer survival"),
             (3621, 3625, "Breast screening → breast cancer survival"),
             (6411, 3627, "GP wait > 7 d → bowel cancer survival"),
             (3336, 3627, "Specialist backlog → bowel cancer survival")]
    fig, axes = plt.subplots(1, 4, figsize=(17, 4.8))
    for ax, (a, b, title) in zip(axes, pairs):
        d = panel[[a, b]].dropna()
        x, y = d[a].to_numpy(), d[b].to_numpy()
        rho, p = stats.spearmanr(x, y)
        lr = stats.linregress(x, y)
        ax.scatter(x, y, s=30, color=S1, edgecolor=SURFACE, lw=0.8, zorder=3)
        xx = np.linspace(x.min(), x.max(), 20)
        ax.plot(xx, lr.intercept + lr.slope * xx, color=MUTED, lw=1.2, ls="--")
        for nm in d.index:
            r = d.loc[nm]
            if abs(stats.zscore(y)[list(d.index).index(nm)]) > 1.4 or abs(stats.zscore(x)[list(d.index).index(nm)]) > 1.6:
                ax.annotate(nm, (r[a], r[b]), xytext=(4, 3), textcoords="offset points", fontsize=7, color=INK2)
        ax.set_xlabel(f"{INDICATORS[a]['short']} [{a}]", fontsize=8)
        ax.set_ylabel(f"{INDICATORS[b]['short']} [{b}]", fontsize=8)
        ax.set_title(f"{title}\nSpearman ρ = {rho:+.2f}, p = {p:.2f}, n = {len(d)}", fontsize=9)
    fig.suptitle("5 · Do upstream bottlenecks show up in outcomes? (county level, ecological — correlation ≠ causation)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    src(fig, SOURCES["sotkanet"] + ". Survival is for patients diagnosed years earlier; n≈22 → weak power.")
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    return fig


def fig6_goal3(df, regions, plt):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    # a Nordic survival gap
    ax = axes[0]
    names = list(NORDIC_SURVIVAL_GAP_PP); vals = list(NORDIC_SURVIVAL_GAP_PP.values())
    ax.bar(names, vals, color=BAD, width=0.55)
    for i, v in enumerate(vals):
        ax.text(i, v + 0.3, f"+{v} pp", ha="center", fontsize=10, fontweight="bold")
    ax.set_ylabel("5-yr relative survival gain needed, pp")
    ax.set_title("6a · Goal 3: poor-prognosis cancers vs\nSweden/Norway avg (Nordcan 2023)")
    # b Nordic mortality
    ax = axes[1]
    d = df[(df.ind == 10024) & (df.cat == "POHJOISMAAT") & (df.gender == "total")]
    want = [("Suomi", "Finland", INK), ("Ruotsi", "Sweden", S1), ("Norja", "Norway", S2), ("Tanska", "Denmark", S3)]
    for fi, en, col in want:
        rid = [r for r, v in regions.items() if v["cat"] == "POHJOISMAAT" and (v.get("fi") == fi or v.get("en") == en)]
        s = d[d.region.isin(rid)].sort_values("year")
        if len(s):
            ax.plot(s.year, s.value, color=col, lw=2.5 if en == "Finland" else 2)
            end_label(ax, s.year.iloc[-1], s.value.iloc[-1], en, col)
    ax.margins(x=0.18)
    ax.set_ylabel("standardised cancer deaths / 100 000")
    ax.set_title("6b · Cancer mortality, Nordic comparison [10024]\n(Eurostat via Sotkanet, ends 2015)")
    # c mortality target vs case growth (indexed, single axis)
    ax = axes[2]
    yrs = np.arange(2025, 2036)
    cases = 100 * np.interp(yrs, [2025, 2035], [1, CASES_2035 / CASES_NOW])
    deaths = 100 * np.interp(yrs, [2025, 2035], [1, 1 - MORTALITY_REDUCTION])
    cfr = 100 * deaths / cases
    for y, col, lab in [(cases, S2, f"New cases ({CASES_NOW//1000}k → {CASES_2035//1000}k)"),
                        (deaths, S1, f"Deaths target ({CANCER_DEATHS_NOW:,} → {int(CANCER_DEATHS_NOW*0.9):,})"),
                        (cfr, BAD, "⇒ deaths per case must fall")]:
        ax.plot(yrs, y, color=col); end_label(ax, yrs[-1], y[-1], f"{lab}: {y[-1]:.0f}", col)
    ax.axhline(100, color=MUTED, lw=0.8)
    ax.set_xlim(2025, 2042); ax.set_ylabel("index, 2025 = 100")
    ax.set_title("6c · What Goal 3 '−10 % mortality' really demands\n(linear path, strategy p.5–6)")
    src(fig, SOURCES["strategy"] + "; " + SOURCES["sotkanet"])
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def fig7_loss(panel, targets, w, plt):
    S = shortfall(panel, targets)
    stage_of = {ind: m["stage"] for ind, m in INDICATORS.items()}
    # MEAN over a stage's indicators, so a stage with 3 indicators is not weighted 3x
    per_stage = pd.DataFrame({st: (S[[i for i in S.columns if stage_of[i] == st]] ** 2).mean(axis=1)
                              for st in STAGES if any(stage_of[i] == st for i in S.columns)})
    total = per_stage.sum(axis=1).sort_values()
    per_stage = per_stage.loc[total.index]
    weighted = per_stage.mul(w.loc[per_stage.index], axis=0).sum()
    share = 100 * weighted / weighted.sum()
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(15, 7.5), gridspec_kw=dict(width_ratios=[1.6, 1]))
    left = np.zeros(len(per_stage))
    cols = [S1, S2, S3, S4]
    for st, c in zip(per_stage.columns, cols):
        ax.barh(range(len(per_stage)), per_stage[st], left=left, color=c, height=0.7, label=st,
                edgecolor=SURFACE, lw=1)
        left += per_stage[st].to_numpy()
    ax.set_yticks(range(len(per_stage)), per_stage.index)
    ax.legend(loc="lower right", title="pathway stage")
    ax.set_xlabel("county loss = Σ_stages mean_k∈stage s_k²   (s_k = shortfall to target in county-SD units)")
    ax.set_title("7a · Cost function per county: s_rk = max(0, T_k − x_rk) / σ_k ,  L_r = Σ_stage mean(s²)")
    ax2.bar(range(len(share)), share.values, color=cols[:len(share)], width=0.6)
    ax2.set_xticks(range(len(share)), [s.replace(" ", "\n", 1) for s in share.index], fontsize=8)
    for i, v in enumerate(share.values):
        ax2.text(i, v + 1, f"{v:.0f}%", ha="center", fontsize=10, fontweight="bold")
    ax2.set_ylabel("% of national loss  L = Σ_r w_r L_r  (w = population share)")
    ax2.set_title("7b · BOTTLENECK: which stage drives the national loss?")
    tlist = "; ".join(f"[{k}] {v[0]:.0f} ({'target' if 'benchmark' not in v[1] else 'bench'})" for k, v in targets.items())
    fig.text(0.01, 0.03, "Targets T_k: " + tlist, fontsize=7, color=INK2, wrap=True)
    src(fig, SOURCES["sotkanet"] + ". Benchmarks (best-quartile county) and squared loss are modelling choices.")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    return fig, share, per_stage.sum(axis=1).sort_values(ascending=False)


def fig8_allocation(panel, w, plt):
    """Where should a fixed screening-improvement effort go?  Same national gain, two cost functions."""
    T = 80.0
    cand = {ind: float((np.maximum(0, T - panel[ind]) * w).sum()) for ind in (3621, 3620, 6103)}
    ind = max(cand, key=cand.get)
    x = panel[ind].to_numpy(dtype=float); ww = w.reindex(panel.index).to_numpy(dtype=float)
    ok = ~np.isnan(x); x, ww, names = x[ok], ww[ok] / ww[ok].sum(), panel.index[ok]
    gain = 2.0  # national (pop-weighted) participation +2 pp - illustrative effort size
    ub = np.maximum(0, 95 - x)

    def sq_loss(dx):  # equity loss: weighted squared shortfall
        return float(np.sum(ww * np.maximum(0, T - x - dx) ** 2))

    cons = [{"type": "eq", "fun": lambda dx: np.sum(ww * dx) - gain}]
    uniform = np.full_like(x, gain)
    res = optimize.minimize(sq_loss, uniform.copy(), method="SLSQP", bounds=list(zip(np.zeros_like(x), ub)),
                            constraints=cons, options=dict(maxiter=500, ftol=1e-10))
    opt = res.x
    order = np.argsort(x)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(15, 7.5), gridspec_kw=dict(width_ratios=[1.2, 1]))
    yy = np.arange(len(x))
    ax.barh(yy - 0.2, uniform[order], height=0.38, color=S2, label="Uniform national campaign (+2 pp everywhere)")
    ax.barh(yy + 0.2, opt[order], height=0.38, color=S1, label="Optimised for equity loss (scipy SLSQP)")
    ax.set_yticks(yy, [f"{n}  ({v:.0f}%)" for n, v in zip(names[order], x[order])], fontsize=8)
    ax.invert_yaxis(); ax.legend(loc="lower right")
    ax.set_xlabel("added participation, pp   (both plans add the SAME number of screened people nationally)")
    ax.set_title(f"8a · Allocate effort: {INDICATORS[ind]['short']} [{ind}]\n(current value in brackets)")
    rows = []
    for lab, dx in [("Now", np.zeros_like(x)), ("Uniform", uniform), ("Optimised", opt)]:
        after = x + dx
        rows.append((lab, sq_loss(dx), int((after < T).sum()), after.min()))
    labs = [r[0] for r in rows]; losses = [r[1] for r in rows]
    ax2.bar(labs, losses, color=[MUTED, S2, S1], width=0.55)
    for i, r in enumerate(rows):
        ax2.text(i, r[1] * 1.02 + 0.05, f"loss {r[1]:.1f}\n{r[2]} counties < 80 %\nworst = {r[3]:.1f}%",
                 ha="center", va="bottom", fontsize=9)
    ax2.set_ylim(0, max(losses) * 1.35)
    ax2.set_ylabel("Σ_r w_r · max(0, 80 − x_r)²")
    ax2.set_title("8b · Same effort, different cost function\n→ different winners")
    src(fig, SOURCES["sotkanet"] + ". Simulation: +2 pp national effort size and 95 % cap are assumptions, "
             "not estimates of a real programme.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    return fig


# ----------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download Sotkanet data")
    ap.add_argument("--no-show", action="store_true", help="save PNGs only, no windows")
    a = ap.parse_args()
    if a.no_show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    style()
    if a.refresh or not CACHE.exists():
        refresh_cache()
    df, regions, fetched = load()
    print(f"Loaded {len(df):,} rows (Sotkanet snapshot {fetched})")
    panel, years = county_panel(df)
    pop = population(df)
    w = (pop / pop.sum()).fillna(0)
    targets = targets_for(panel)

    FIG_DIR.mkdir(exist_ok=True)
    figs = {
        "fig1_national_pathway": fig1_national_pathway(df, plt),
        "fig2_county_heatmap": fig2_heatmap(panel, years, plt),
        "fig3_inequality_by_stage": fig3_inequality_by_stage(panel, years, targets, plt),
        "fig4_backlog_early_warning": fig4_backlog_early_warning(df, plt),
        "fig5_upstream_vs_outcome": fig5_upstream_vs_outcome(panel, plt),
        "fig6_goal3_targets": fig6_goal3(df, regions, plt),
    }
    f7, share, county_loss = fig7_loss(panel, targets, w, plt)
    figs["fig7_cost_function"] = f7
    figs["fig8_intervention_allocation"] = fig8_allocation(panel, w, plt)
    for name, f in figs.items():
        f.savefig(FIG_DIR / f"{name}.png", dpi=130)
    print(f"Saved {len(figs)} figures to {FIG_DIR}")

    # ---- console summary ------------------------------------------------------------------
    print("\n=== Bottleneck ranking (share of population-weighted squared-shortfall loss) ===")
    for st, v in share.sort_values(ascending=False).items():
        print(f"  {st:<28} {v:5.1f} %")
    print("  4 Diagnosis -> treatment start   (no public county data — data gap)")
    print("\n=== Counties with the largest total loss ===")
    for nm, v in county_loss.head(5).items():
        print(f"  {nm:<22} {v:6.2f}")
    print("\n=== Latest year used per indicator ===")
    for ind, y in years.items():
        t = targets[ind]
        print(f"  [{ind:>4}] {INDICATORS[ind]['short']:<42} {y}   target {t[0]:.1f} ({t[1]})")
    if not a.no_show:
        plt.show()


if __name__ == "__main__":
    sys.exit(main())
