"""Step 2: offline MWPM decoding, logical error-rate fits and fidelity plots.

No connection or QPU submission is made. Source run folders are preserved;
counts are decoded again on copies using each experiment's saved Stim reference.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from garnet_hardware import analyze_saved_results, write_json
from logical_rate_fit import failure_probability


def resolve_runs(inputs):
    """Accept completed run directories or a completed DD comparison directory."""
    runs = []
    for supplied in inputs:
        path = Path(supplied).resolve()
        if (path / "manifest.json").is_file():
            candidates = [path]
        elif (path / "comparison.json").is_file():
            comparison = json.loads((path / "comparison.json").read_text())
            if comparison["mode"] != "hardware_complete":
                raise ValueError("The comparison has not completed")
            candidates = [(path / item["directory"]).resolve() for item in comparison["arms"].values()]
            if any(not candidate.is_relative_to(path) for candidate in candidates):
                raise ValueError("Comparison run paths must stay within their directory")
        else:
            raise ValueError(f"No saved hardware run or comparison found in {path}")
        for candidate in candidates:
            if candidate in runs:
                raise ValueError("A run was supplied twice; shots must never be counted twice")
            manifest = json.loads((candidate / "manifest.json").read_text())
            if manifest["mode"] != "hardware_complete":
                raise ValueError(f"Hardware run is incomplete: {candidate}")
            runs.append(candidate)
    if not runs:
        raise ValueError("Supply at least one completed hardware run")
    return runs


def fidelity_point(point):
    """F=1-2P, including the correctly reversed Wilson interval endpoints."""
    return {"logical_fidelity": 1 - 2 * point["failure_probability"],
            "logical_fidelity_ci95": [1 - 2 * point["ci95"][1], 1 - 2 * point["ci95"][0]]}


def _plot(series, output, fidelity=False):
    bases = sorted({s["basis"] for s in series}, reverse=True)
    fig, axes = plt.subplots(1, len(bases), figsize=(6 * len(bases), 4.6),
                             sharey=True, squeeze=False, constrained_layout=True)
    common_log_safe = fidelity and all(p["logical_fidelity_ci95"][0] > 0 for s in series for p in s["points"])
    common_log_safe = common_log_safe and all(
        not s["fits"].get("challenge") or s["fits"]["challenge"]["epsilon_ci95"][1] < .5 for s in series)
    labels = {s["group_id"]: f"DD {s['dd_profile']}" for s in series}
    for basis in bases:
        same = [s for s in series if s["basis"] == basis]
        if len({s["dd_profile"] for s in same}) < len(same):
            labels.update({s["group_id"]: f"Run {s['run_index']}, DD {s['dd_profile']}" for s in same})
    scales = {}
    for ax, basis in zip(axes[0], bases):
        groups = [s for s in series if s["basis"] == basis]
        log_safe = common_log_safe
        for index, item in enumerate(groups):
            color = {"off": "#986134", "xx": "#286c99"}.get(item["dd_profile"], f"C{index}")
            if len({s["dd_profile"] for s in groups}) < len(groups):
                color = f"C{index}"
            points = item["points"]
            rounds = np.array([p["rounds"] for p in points])
            key, interval = ("logical_fidelity", "logical_fidelity_ci95") if fidelity else ("failure_probability", "ci95")
            values = np.array([p[key] for p in points])
            bounds = np.array([p[interval] for p in points])
            factor = 1 if fidelity else 100
            ax.errorbar(rounds, factor * values,
                        yerr=factor * np.stack([values - bounds[:, 0], bounds[:, 1] - values]),
                        fmt="o", color=color, capsize=4, label=f"{labels[item['group_id']]}: data")
            fit = item["fits"].get("challenge")
            if fit:
                grid = np.linspace(0, max(rounds) + .2, 240)
                rate, (lower, upper) = fit["epsilon_per_round"], fit["epsilon_ci95"]
                curve = failure_probability(grid, rate)
                low = failure_probability(grid, lower)
                high = failure_probability(grid, upper)
                if fidelity:
                    curve, low, high = 1 - 2 * curve, 1 - 2 * high, 1 - 2 * low
                    log_safe = log_safe and bool(np.all(low > 0))
                warning = " (poor fit)" if fit["model_mismatch_flag"] else ""
                ax.plot(grid, factor * curve, "--", color=color,
                        label=f"{labels[item['group_id']]}: fit{warning}")
                ax.fill_between(grid, factor * low, factor * high, color=color, alpha=.10)
        if fidelity and log_safe:
            ax.set_yscale("log")
        elif not fidelity:
            ax.axhline(50, color="#888888", linestyle=":", linewidth=1)
        scales[basis] = "log" if fidelity and log_safe else "linear"
        ax.set_title(f"{basis}-memory")
        ax.set_xlabel("Syndrome-extraction rounds")
        ax.set_xticks(sorted({0, *(p["rounds"] for s in groups for p in s["points"])}))
        ax.set_ylabel("Logical fidelity F = 1 − 2P" if fidelity else "Decoded logical failure probability (%)")
        ax.grid(alpha=.2, which="both")
        ax.legend(fontsize=8)
    if not fidelity:
        upper = 100 * max(.5, *(p["ci95"][1] for s in series for p in s["points"]))
        axes[0, 0].set_ylim(0, 1.05 * upper)
    fig.suptitle("Logical fidelity decay" if fidelity else "Decoded probabilities and challenge-model fits")
    fig.savefig(output / ("logical-fidelity.png" if fidelity else "logical-error-probability.png"), dpi=180)
    plt.close(fig)
    return scales


def analyze_runs(inputs, output):
    runs = resolve_runs(inputs)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Choose a new analysis output directory")
    if any(output.is_relative_to(run) for run in runs):
        raise ValueError("Keep the new analysis outside the source run directories")
    output.mkdir(parents=True)
    series, provenance = [], []
    for index, source in enumerate(runs, 1):
        manifest_bytes = (source / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        destination = output / "redecoded-runs" / f"run-{index}"
        shutil.copytree(source, destination)
        decoded = analyze_saved_results(destination)  # raw counts -> m2d -> MWPM -> binomial fit
        if (source / "decoded-results.json").is_file():
            previous = json.loads((source / "decoded-results.json").read_text())
            old = {(p["basis"], p["rounds"]): (p["failures"], p["shots"]) for p in previous["points"]}
            new = {(p["basis"], p["rounds"]): (p["failures"], p["shots"]) for p in decoded["points"]}
            if old != new:
                raise ValueError("Re-decoding disagrees with saved failure counts; investigate before reporting a rate")
        provenance.append({"run_index": index, "source": str(source),
                           "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                           "calibration_set_id": manifest["calibration_set_id"],
                           "job_ids": [b["job_id"] for b in manifest["batches"]]})
        for basis in sorted({p["basis"] for p in decoded["points"]}):
            points = sorted((dict(p, **fidelity_point(p)) for p in decoded["points"] if p["basis"] == basis),
                            key=lambda p: p["rounds"])
            series.append({"group_id": f"run-{index}-{basis}", "run_index": index, "source": str(source),
                           "basis": basis, "dd_profile": manifest.get("dd_profile", "off"),
                           "calibration_set_id": manifest["calibration_set_id"],
                           "layout": next(e["layout"] for e in manifest["experiments"].values() if e["basis"] == basis),
                           "compilation_options": manifest.get("compilation_options"),
                           "points": points, "fits": decoded["fits"].get(basis, {}),
                           "interpretation": "Preliminary short-range estimate" if len(points) <= 3 else "Inspect goodness of fit before interpreting as a stationary rate"})
        if (source / "manifest.json").read_bytes() != manifest_bytes:
            raise ValueError("A source manifest changed during analysis")
    _plot(series, output)
    fidelity_scales = _plot(series, output, fidelity=True)
    summary = {"analysis": "Step 2: MWPM decoding and per-round logical error-rate estimation",
               "equation": "P_error(r) = [1 - (1 - 2*epsilon_L)**r] / 2",
               "fidelity_equation": "F(r) = 1 - 2*P_error(r) = (1 - 2*epsilon_L)**r",
               "decoder": "PyMatching MWPM from each saved noisy Stim reference",
               "fitting": "Binomial maximum likelihood on failure/shot counts; no logarithmic fitting of noisy point estimates",
               "uncertainty": "Pointwise Wilson 95% intervals; approximate profile-likelihood 95% intervals for epsilon_L",
               "provenance": provenance, "series": series, "fidelity_axis_scales": fidelity_scales,
               "caveats": ["Groups from different runs, bases and DD settings are fitted separately.",
                           "Three short round counts do not establish long-duration stationary memory performance.",
                           "Confidence intervals exclude calibration, decoder-model and temporal-correlation uncertainty.",
                           "The DD-on detector definitions remain valid for ideal identity sequences, but noise weights exclude the inserted DD pulses and idle errors."]}
    write_json(output / "logical-error-rate-results.json", summary)
    rows = []
    for item in series:
        fit = item["fits"].get("challenge")
        if fit:
            rows.append({"group_id": item["group_id"], "basis": item["basis"], "dd_profile": item["dd_profile"],
                         "round_counts": ",".join(str(p["rounds"]) for p in item["points"]),
                         "shots": sum(p["shots"] for p in item["points"]),
                         "epsilon_per_round": fit["epsilon_per_round"], "epsilon_ci95_low": fit["epsilon_ci95"][0],
                         "epsilon_ci95_high": fit["epsilon_ci95"][1],
                         "goodness_of_fit_p_value": fit["goodness_of_fit_p_value"],
                         "model_mismatch_flag": fit["model_mismatch_flag"]})
    if rows:
        with (output / "logical-error-rates.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    _write_report(summary, output)
    return summary


def _write_report(summary, output):
    lines = ["# Step 2: decoded logical error rates", "",
             "Saved QPU counts were decoded again with PyMatching using each experiment's matching Stim measurement records and detector definitions. Re-decoded failure counts agree with the original results. Source data were preserved; copies are in redecoded-runs/.", "",
             "A single experiment gives P_error after r rounds. The rate epsilon_L below is fitted across distinct round counts using:", "",
             "$$P_{error}(r)=\\frac{1-(1-2\\epsilon_L)^r}{2},\\qquad F(r)=1-2P_{error}(r).$$", "",
             "The likelihood uses integer failure and shot counts. Point error bars are Wilson 95% intervals; rate intervals are approximate profile-likelihood intervals, conditional on the model.", "",
             "| Run | Memory | DD | Round counts | Fitted error per round | Conditional 95% interval | Fit p-value | Fit check |",
             "|---|---|---|---|---:|---:|---:|---|"]
    for item in summary["series"]:
        fit = item["fits"].get("challenge")
        if not fit:
            lines.append(f"| {item['run_index']} | {item['basis']} | {item['dd_profile']} | Insufficient distinct round counts | — | — | — | No fit |")
            continue
        lower, upper = fit["epsilon_ci95"]
        check = "Poor fit" if fit["model_mismatch_flag"] else "No mismatch detected"
        rounds = ", ".join(str(p["rounds"]) for p in item["points"])
        lines.append(f"| {item['run_index']} | {item['basis']} | {item['dd_profile']} | {rounds} | {100*fit['epsilon_per_round']:.2f}% | {100*lower:.2f}–{100*upper:.2f}% | {fit['goodness_of_fit_p_value']:.3g} | {check} |")
    lines += ["", "These are per-round rates, distinct from the failure probabilities at any one round count. A good fit over three points has limited power to diagnose model failure; these short sweeps give preliminary estimates.", "",
              "![Decoded logical error probabilities](logical-error-probability.png)", "",
              "![Logical fidelity decay](logical-fidelity.png)", "",
              "Dashed curves are challenge-model fits. Shading propagates each rate's 95% parameter interval into the curve; it is not a predictive interval or a calibration-error budget. Fidelity uses a logarithmic axis only when all plotted confidence bounds and model curves are positive. Negative fidelity estimates or bounds are retained on a linear axis.", "",
              "## Preparation/readout diagnostic", "",
              "A second fit permits a nonnegative preparation/readout offset s: P_error(r)=s+(1-2s)[1-(1-2epsilon_L)^r]/2. It is diagnostic and does not replace the challenge fit. Full values and intervals are in logical-error-rate-results.json.", ""]
    for item in summary["series"]:
        fit = item["fits"].get("spam_diagnostic")
        if fit:
            lines.append(f"- {item['basis']}, DD {item['dd_profile']}: epsilon={100*fit['epsilon_per_round']:.2f}% per round; offset={100*fit['spam_offset']:.2f}%; fit p={fit['goodness_of_fit_p_value']:.3g}.")
    lines += ["", "## Scope and reproducibility", ""]
    lines += [f"- {note}" for note in summary["caveats"]]
    lines += ["- Round-count experiments can share an IQM batch; each circuit still has its own independent measurement records. Separate jobs are not required for the statistical fit.", "- No network connection, credential or new QPU execution was used.", "", "Sources:", ""]
    lines += [f"- Run {p['run_index']}: {p['source']}; calibration {p['calibration_set_id']}; jobs: {', '.join(p['job_ids'])}." for p in summary["provenance"]]
    (output / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+", help="Completed run folders or a DD comparison folder")
    parser.add_argument("--out", type=Path, required=True, help="New analysis output directory")
    args = parser.parse_args()
    try:
        summary = analyze_runs(args.runs, args.out)
    except (ValueError, FileExistsError) as error:
        parser.exit(2, f"{error}\n")
    print(f"Re-decoded {len(summary['series'])} separate memory/settings groups. Report: {args.out / 'report.md'}")


if __name__ == "__main__":
    main()
