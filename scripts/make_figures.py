"""Paper figures from the recorded results (no database access). Run from the repo root:

    python scripts/make_figures.py            # writes docs/figures/*.png (300 dpi) and *.svg

Fig 2 and 3 are computed from results/dev-006.jsonl through the same scoring code as the report.
Fig 4 uses the numbers of one live Query Studio run (2026-11-22), copied below.
Fig 5 reads the newest results/advisor/adv-*.json per dataset, if present.
"""
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.eval import report as rep  # noqa: E402

OUT = ROOT / "docs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"font.family": "serif", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.titlesize": 10, "figure.dpi": 100})
WIN, TIE, LOSS, ACC, GREY = "#4c9a6a", "#c8ccd2", "#c0504d", "#2b5c8a", "#8a9099"


def save(fig, name):
    for ext in ("png", "svg"):
        fig.savefig(OUT / f"{name}.{ext}", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


# ---------------------------------------------------------------- Fig 1: architecture
def fig_architecture():
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    ax.set_xlim(0, 100), ax.set_ylim(0, 62), ax.axis("off")

    def box(x, y, w, h, text, fc="#f4f5f7", ec="#555", bold=False, fs=7.2):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=1.2", fc=fc, ec=ec, lw=0.9))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, fontweight="bold" if bold else "normal", linespacing=1.25)

    def arrow(a, b):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=8, lw=0.9, color="#444", shrinkA=0, shrinkB=0))

    # row 1: generation
    box(1, 44, 21, 12, "Query\n(SQL + schema)")
    box(26, 44, 21, 12, "Candidate generator\nQwen2.5-Coder 7B\ntemp 0, seed 42")
    box(51, 44, 21, 12, "Correctness check\nrow count, then\nmultiset compare")
    box(76, 44, 23, 12, "Shared candidate pool\ncached to disk\nvalid rewrites only", fc="#e6eef6", ec=ACC, bold=True)
    for x0, x1 in ((22.3, 25.7), (47.3, 50.7), (72.3, 75.7)):
        arrow((x0, 50), (x1, 50))
    # row 2: the arms
    box(1, 23, 19, 11, "Arm 1  Baseline\nMySQL's plan on\nthe original query")
    box(23, 23, 18, 11, "Arm 2a  LLM\nsees SQL +\nschema only")
    box(44, 23, 17, 11, "Arm 2b  LLM\nsees SQL, schema\n+ metric table")
    box(64, 23, 17, 11, "Measured metrics\nruntime, rows read,\nEXPLAIN estimates", fc="#eef5ee")
    box(84, 23, 15, 11, "Arm 3\nOR engine\nAHP + TOPSIS", fc="#e6eef6", ec=ACC, bold=True)
    # the pool feeds arms 2a, 2b, the metric table and arm 3 through one bus
    ax.plot([87.5, 87.5], [43.8, 39], color="#444", lw=0.9)
    ax.plot([32, 91.5], [39, 39], color="#444", lw=0.9)
    for x in (32, 52.5, 72.5, 91.5):
        arrow((x, 39), (x, 34.4))
    arrow((11, 43.8), (11, 34.4))                                       # the original query goes straight to the baseline
    arrow((64, 28.5), (61.4, 28.5)), arrow((81, 28.5), (83.6, 28.5))    # metric table -> 2b and -> arm 3
    ax.text(58, 40.2, "same pool for arms 2 and 3", fontsize=6.8, color=ACC, style="italic", ha="center")
    # row 3: measurement
    box(4, 2, 92, 13, "One measurement protocol for every arm: read-only MySQL user, 2 warm-up + 5 timed runs (median, IQR),\n"
                      "tie band max(IQR, 5%), same server, configuration and buffer-pool state.\n"
                      "Output per query: win / tie / loss against the baseline", fc="#fbf6e9", ec="#a08a3c", fs=7.2)
    for x in (10.5, 32, 52.5, 91.5):
        arrow((x, 22.7), (x, 15.4))
    ax.text(0, 61.5, "Fig. 1  Experimental pipeline: arms 2 and 3 choose from the same verified pool", fontsize=9, fontweight="bold", va="top")
    save(fig, "fig1_pipeline")


# ---------------------------------------------------------------- Fig 2 + 3 from dev-006
def dev006():
    data = rep.load(ROOT / "results" / "dev-006.jsonl")
    return data, rep.score(data)


def fig_win_tie_loss(sc):
    arms = [("1", "1  Baseline (MySQL)"), ("2a", "2a  LLM, SQL + schema"), ("2b", "2b  LLM, + metrics"),
            ("3-raw", "3-raw  OR, no guard"), ("3", "3  OR engine (AHP + TOPSIS)")]
    fig, ax = plt.subplots(figsize=(7, 2.9))
    for i, (a, label) in enumerate(arms):
        u = [s for s in sc if s["arm"] == a]
        c = Counter(s["outcome"] for s in u)
        n = len(u)
        left = 0
        for key, col, hatch in (("win", WIN, ""), ("tie", TIE, ""), ("loss", LOSS, "///")):
            w = 100 * c[key] / n
            ax.barh(i, w, left=left, color=col, edgecolor="white", hatch=hatch, height=0.62)
            if c[key]:
                ax.text(left + w / 2, i, f"{c[key]}", ha="center", va="center", fontsize=8, fontweight="bold",
                        color="white" if key == "win" else "#333" if key == "tie" else "#6b1c1a",
                        bbox=dict(fc="white", ec="none", pad=1.0, alpha=0.9) if key == "loss" else None)
            left += w
        ax.text(101.5, i, f"n = {n}", va="center", fontsize=8, color="#555")
    ax.set_yticks(range(len(arms)), [l for _, l in arms]), ax.invert_yaxis()
    ax.set_xlim(0, 112), ax.set_xticks([0, 25, 50, 75, 100]), ax.set_xlabel("share of units (%)")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c, hatch=h) for c, h in ((WIN, ""), (TIE, ""), (LOSS, "///"))],
              labels=["win (faster than baseline)", "tie (within noise)", "loss (slower)"], ncol=3, loc="upper center",
              bbox_to_anchor=(0.45, -0.20), frameon=False, fontsize=8)
    ax.set_title("Fig. 2  Outcome against MySQL's own plan, dev split (run dev-006)", loc="left", fontweight="bold")
    fig.text(0.01, -0.31, "Units: queries for arms 1, 3, 3-raw (15); query x repeat for the LLM arms (75). Counts printed on the bars.",
             fontsize=7.5, color="#555")
    save(fig, "fig2_win_tie_loss")


def fig_per_query(data, sc):
    contested = [q["query_id"] for q in data["recs"] if q["type"] == "query" and q["n_valid"] > 0]
    ratio = defaultdict(dict)
    for arm in ("3", "2b", "2a"):
        for q in contested:
            rs = [s["ratio"] for s in sc if s["arm"] == arm and s["query_id"] == q and s["ratio"] is not None]
            ratio[q][arm] = float(np.mean(rs)) if rs else np.nan
    order = sorted(contested, key=lambda q: ratio[q]["3"])
    base = {q: data["ev"][(q, "original")]["runtime_ms_median"] for q in contested}
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.axvspan(0.95, 1.05, color="#eef0f3", zorder=0)
    ax.axvline(1.0, color="#555", lw=0.8, zorder=1)
    for i, q in enumerate(order):
        ax.plot([min(ratio[q].values()), max(ratio[q].values())], [i, i], color="#d5d8dd", lw=1.2, zorder=1)
    for arm, m, col, lab, size in (("2a", "^", LOSS, "2a  LLM, SQL + schema", 36), ("2b", "s", GREY, "2b  LLM, + metrics", 30),
                                   ("3", "o", ACC, "3  OR engine", 46)):
        ax.scatter([ratio[q][arm] for q in order], range(len(order)), marker=m, s=size, color=col, label=lab, zorder=3,
                   edgecolor="white", linewidth=0.5)
    ax.set_xscale("log"), ax.set_xlim(0.07, 2.4)
    ax.set_xticks([0.1, 0.2, 0.5, 1, 2], ["0.1\n(10x faster)", "0.2", "0.5", "1.0\n(= baseline)", "2.0\n(2x slower)"])
    ax.set_yticks(range(len(order)), [f"{q}  ({base[q]:,.0f} ms)" for q in order], fontsize=7.5), ax.invert_yaxis()
    ax.set_xlabel("time of the picked rewrite / time of the original (log scale; mean over repeats for the LLM arms)")
    ax.legend(loc="lower left", frameon=True, framealpha=0.95, fontsize=8, borderpad=0.4)
    ax.set_title("Fig. 3  Per contested query: what each arm picked, dev split (run dev-006)", loc="left", fontweight="bold")
    fig.text(0.01, -0.05, "Grey band = tie band (5%). Baseline runtime in brackets. 12 queries have at least one valid rewrite; "
                          "the other 3 tie by construction.", fontsize=7.5, color="#555")
    save(fig, "fig3_per_query")


# ---------------------------------------------------------------- Fig 4: estimates vs measurement (live Studio run)
def fig_estimate_vs_measured():
    # Query Studio, ap03_correlated_subquery, 2026-11-22 (alternatives that passed the correctness check)
    alts = ["original", "c1", "c2", "c3", "c5"]
    runtime = [590.19, 54.35, 536.28, 639.54, 678.18]
    est_cost = [1564, 140309, 1564, 1564, 5328]
    rows = [477441, 33289, 494086, 493487, 494087]
    fig, axs = plt.subplots(1, 3, figsize=(7.2, 2.7))
    for ax, vals, title, fmt in ((axs[0], runtime, "Measured runtime (ms)", "{:,.0f}"), (axs[1], rows, "Measured rows examined", "{:,.0f}"),
                                 (axs[2], est_cost, "Optimizer's estimated cost", "{:,.0f}")):
        cols = [ACC if a == "c1" else GREY for a in alts]
        ax.barh(range(len(alts)), vals, color=cols, height=0.62)
        for i, v in enumerate(vals):
            ax.text(v, i, " " + fmt.format(v), va="center", fontsize=7)
        ax.set_xlim(0, max(vals) * 1.32), ax.set_title(title, fontsize=8.5)
        ax.set_yticks(range(len(alts)), alts if ax is axs[0] else [""] * len(alts)), ax.invert_yaxis()
        ax.set_xticks([])
        ax.spines["bottom"].set_visible(False)
    fig.suptitle("Fig. 4  ap03_correlated_subquery: the optimizer's estimate ranks the fastest rewrite (c1) last", x=0.01, ha="left",
                 fontsize=9.5, fontweight="bold", y=1.04)
    fig.text(0.01, -0.07, "c1 is 10.9x faster than the original and reads 14x fewer rows, yet its estimated cost is 90x higher. "
                          "Query Studio live run; c4 rejected by the correctness check.", fontsize=7.5, color="#555")
    save(fig, "fig4_estimate_vs_measured")


# ---------------------------------------------------------------- Fig 5: workload advisor
def fig_advisor():
    runs = {}
    for ds in ("sakila", "employees", "tpch"):
        fs = sorted(glob.glob(str(ROOT / "results" / "advisor" / f"adv-{ds}-*.json")))
        if fs:
            runs[ds] = json.loads(Path(fs[-1]).read_text(encoding="utf-8"))
    if not runs:
        print("no advisor results yet: skipping fig 5")
        return
    fig, axs = plt.subplots(1, len(runs), figsize=(7.6, 3.5), squeeze=False)
    for ax, (ds, r) in zip(axs[0], runs.items()):
        sel = r["selections"]["ilp"]
        n = len(sel["chosen"])
        head = f"{ds}: {n} index" + ("" if n == 1 else "es") + " chosen"
        if not sel["actual"]:
            ax.set_title(head + "\nno index passed the consistency + 15% rule", fontsize=8)
            ax.axis("off")
            continue
        ax.set_title(f"{head}\nsaved: predicted {sel['predicted_ms']:,.0f}, measured {sel['actual']['gain_ms']:,.0f} ms", fontsize=7.6)
        per = sel["actual"]["per_query"]
        qs = [q for q in per if per[q]["base_ms"]]
        y = np.arange(len(qs))
        lo = min(min(per[q]["base_ms"], per[q]["with_ms"]) for q in qs)
        hi = max(max(per[q]["base_ms"], per[q]["with_ms"]) for q in qs)
        for i, q in enumerate(qs):
            b0, w0 = per[q]["base_ms"], per[q]["with_ms"]
            ax.plot([b0, w0], [i, i], color="#b9bec6", lw=1.6, zorder=1)
            ratio = w0 / b0
            col = ACC if ratio < 0.85 else LOSS if ratio > 1.15 else GREY
            ax.text(max(b0, w0) * 1.25, i, f"x{ratio:.2f}", va="center", fontsize=6.8, color=col, fontweight="bold" if col != GREY else "normal")
        ax.scatter([per[q]["base_ms"] for q in qs], y, s=26, color=GREY, zorder=3, label="without the index set")
        ax.scatter([per[q]["with_ms"] for q in qs], y, s=26, color=ACC, zorder=3, label="with the ILP's index set")
        ax.set_xscale("log"), ax.set_xlim(lo * 0.6, hi * 3.2)
        ax.set_yticks(y, [q if len(q) <= 17 else q[:15] + "…" for q in qs], fontsize=6.5), ax.invert_yaxis()
        ax.set_xlabel("median ms (log scale)", fontsize=7.5), ax.tick_params(axis="x", labelsize=7)
    h, l = axs[0][0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=2, frameon=False, fontsize=8, bbox_to_anchor=(0.5, -0.04))
    fig.suptitle("Fig. 5  Workload Advisor: workload time without and with the ILP's index set, measured on the server",
                 x=0.01, ha="left", fontsize=9.5, fontweight="bold", y=1.02)
    fig.text(0.01, -0.09, "Each query measured with the index set hidden and shown in alternating rounds. x-ratio = with / without; "
                          "blue = more than 15% faster, red = more than 15% slower.", fontsize=7, color="#555")
    fig.tight_layout()
    save(fig, "fig5_advisor")


if __name__ == "__main__":
    data, sc = dev006()
    fig_architecture()
    fig_win_tie_loss(sc)
    fig_per_query(data, sc)
    fig_estimate_vs_measured()
    fig_advisor()
