"""Render the two presentation summary plots from saved CSVs, entirely offline.

Run from the repository root:
    python results/rebuild_summary_figures.py
No credentials, IQM backend or new Monte Carlo/QPU shots are used.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np


ROOT = Path(__file__).resolve().parent
GOLD = "#d99543"
TEAL = "#244f5a"


def read_points():
    with (ROOT / "hardware/dd-comparison/comparison-points.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    points = {}
    for row in rows:
        basis, rounds = row["basis"], int(row["rounds"])
        if (basis, rounds) in points:
            raise ValueError("Duplicate basis/round point")
        shots = int(row["shots_per_arm"])
        parsed = {"shots": shots}
        for arm in ("control", "dd"):
            failures = int(row[f"{arm}_failures"])
            p = float(row[f"{arm}_probability"])
            low, high = json.loads(row[f"{arm}_ci95"])
            if shots <= 0 or not 0 <= failures <= shots or not np.isclose(p, failures / shots):
                raise ValueError("Saved failure counts and probabilities disagree")
            if not 0 <= low <= p <= high <= 1:
                raise ValueError("Invalid saved confidence interval")
            parsed[arm] = {"p": p, "low": low, "high": high, "failures": failures}
        points[(basis, rounds)] = parsed
    if set(points) != {(basis, r) for basis in ("X", "Z") for r in (1, 2, 3)}:
        raise ValueError("Expected X/Z memory at rounds 1, 2 and 3")
    return points


def style_axis(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.grid(axis="y", alpha=0.22)
    ax.set_axisbelow(True)


def save(fig, destination, stem):
    for extension in ("png", "svg"):
        path = destination / f"{stem}.{extension}"
        fig.savefig(path, dpi=200, facecolor="white")
        if extension == "svg":
            # Normalize Matplotlib's trailing spaces inside path attributes.
            path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
        print(path)
    plt.close(fig)


def short_rounds(points, destination):
    fig, ax = plt.subplots(figsize=(10, 5.8))
    for basis, color, marker in [("X", GOLD, "o"), ("Z", TEAL, "s")]:
        values = [points[(basis, r)]["dd"] for r in (1, 2, 3)]
        p = np.array([v["p"] for v in values])
        error = np.array([[v["p"] - v["low"] for v in values],
                          [v["high"] - v["p"] for v in values]])
        ax.errorbar([1, 2, 3], p, yerr=error, color=color, marker=marker,
                    markersize=7, linewidth=2, capsize=5, label=f"{basis} memory")
    ax.set(title="Garnet XX DD: rounds 1–3", xlabel="Syndrome-extraction rounds",
           ylabel="Decoded logical failure probability", xticks=[1, 2, 3],
           xlim=(0.85, 3.15), ylim=(0, 0.32))
    ax.legend(loc="upper left", frameon=False)
    style_axis(ax)
    fig.subplots_adjust(left=0.11, right=0.98, top=0.89, bottom=0.26)
    fig.text(0.11, 0.15, "500 shots per point. Error bars: 95% Wilson intervals. Lines connect measured points.", fontsize=10)
    fig.text(0.11, 0.10, "Per-round fit over rounds 1–3: X 9.40% (8.25–10.68%), Z 9.12% (7.99–10.36%).", fontsize=10)
    fig.text(0.11, 0.05, "Fit intervals assume a constant logical-flip rate; the five-round follow-up rejects that model.", fontsize=10)
    save(fig, destination, "rounds-1-to-3-xx-dd")


def dd_comparison(points, destination):
    fig, ax = plt.subplots(figsize=(10, 5.8))
    x = np.arange(2)
    for arm, label, color, offset in [("control", "DD off", GOLD, -0.18),
                                      ("dd", "XX DD", TEAL, 0.18)]:
        values = [points[(basis, 3)][arm] for basis in ("X", "Z")]
        p = np.array([v["p"] for v in values])
        error = np.array([[v["p"] - v["low"] for v in values],
                          [v["high"] - v["p"] for v in values]])
        ax.bar(x + offset, p, width=0.34, color=color, label=label,
               yerr=error, capsize=5, error_kw={"linewidth": 1.5})
        for position, value in zip(x + offset, values):
            ax.text(position, value["high"] + 0.012, f"{100 * value['p']:.1f}%",
                    ha="center", va="bottom", fontsize=12)
    ax.set(title="Garnet: dynamical decoupling comparison", xticks=x,
           xticklabels=["X memory", "Z memory"], ylim=(0, 0.58),
           ylabel="Decoded failure probability after 3 rounds")
    ax.legend(loc="upper right", frameon=False)
    style_axis(ax)
    fig.subplots_adjust(left=0.11, right=0.98, top=0.89, bottom=0.25)
    fig.text(0.11, 0.14, "X: 23.0 percentage points lower (about 53% relative). Z: 2.2 points higher, inconclusive.", fontsize=10)
    fig.text(0.11, 0.08, "500 shots per arm and basis. Error bars: 95% Wilson intervals.", fontsize=10)
    fig.text(0.11, 0.03, "One sequential job per arm; device drift and shot correlations remain possible confounds.", fontsize=10)
    save(fig, destination, "dd-three-round-comparison")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "figures")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 12, "axes.titlesize": 17, "axes.labelsize": 12})
    points = read_points()
    short_rounds(points, args.output)
    dd_comparison(points, args.output)


if __name__ == "__main__":
    main()
