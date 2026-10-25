"""Run Garnet's local QEC baseline and a validated placement-search experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.stats import fisher_exact

from garnet_model import CANONICAL_LAYOUT, build_native_pauli_circuit, enumerate_native_layouts, load_garnet_model
from logical_rate_fit import failure_probability, fit_logical_rate
from mapping_search import simulated_annealing
from simulation_baseline import run_monte_carlo
from surface_code import make_stim_circuit


def derived_seed(seed, *keys):
    return int(np.random.SeedSequence([seed, *keys]).generate_state(1, dtype=np.uint64)[0])


def plot_results(summary: dict, destination: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.2, 5.0), layout="constrained")
    colors = {"baseline": "#2563a5", "candidate": "#bc5c16"}
    for label, series in summary["sweeps"].items():
        points, fit = series["points"], series["challenge_fit"]
        rounds = np.array([point["rounds"] for point in points])
        p = np.array([point["failure_probability"] for point in points])
        low = np.array([point["ci95"][0] for point in points])
        high = np.array([point["ci95"][1] for point in points])
        color = colors[label]
        ax.errorbar(rounds, p, yerr=[p - low, high - p], fmt="o", capsize=3,
                    color=color, label=f"{label}: decoded Monte Carlo, 95% CI")
        x = np.linspace(0, rounds.max(), 200)
        quality = ", poor fit" if fit["model_mismatch_flag"] else ""
        ax.plot(x, failure_probability(x, fit["epsilon_per_round"]), color=color,
                label=f"{label}: challenge ε={fit['epsilon_per_round']:.4f}{quality}")
        spam = series["spam_diagnostic_fit"]
        ax.plot(x, failure_probability(x, spam["epsilon_per_round"], spam["spam_offset"]),
                color=color, linestyle="--", alpha=0.75,
                label=f"{label}: preparation/readout-offset diagnostic")
    ax.set(xlabel="Stabilizer measurement rounds", ylabel="Decoded logical failure probability",
           title=f"Garnet {summary['config']['basis']}-memory: local Pauli-model baseline", ylim=(0, None))
    ax.grid(alpha=0.2)
    ax.legend(fontsize=8, loc="best")
    fig.savefig(destination, dpi=180)
    plt.close(fig)


def write_report(summary: dict, destination: Path) -> None:
    model = summary["noise_model"]
    search = summary["search"]
    holdout = summary["independent_holdout"]
    lines = [
        "# Garnet simulation baseline", "",
        "All executions were local. No QPU job was submitted.", "",
        "These results are conditional on the explicit Pauli noise model; they are not measured Garnet logical error rates.", "",
        f"Calibration snapshot: `{model['file']}`; created {model['created_timestamp']}.",
        f"Basis: {summary['config']['basis']}; distance: 3; resets between rounds: {not summary['config']['no_reset']}.",
        f"Reset-flip probability: {model['assumed_reset_flip_probability']} before applying the noise scale (assumed, not measured).", "",
        "## Placement search", "",
        f"Enumerated {search['candidate_count']} native-edge placements against the exported CZ couplers.",
        f"Simulated annealing selected placement {search['best_index']} using cached Monte Carlo screening shots.",
        f"The search used {search['distinct_noise_circuits']} distinct noisy circuit(s). Exhaustive screening was also retained as a reference for this small search space.",
        f"Recommendation after independent holdout: placement {holdout['recommended_index']}.",
        f"Holdout decision: {holdout['decision']}.", "",
        "## Logical error rate fits", "",
        "| Circuit | ε per round, challenge model | Approximate 95% CI | Goodness-of-fit p |",
        "|---|---:|---:|---:|",
    ]
    for label, series in summary["sweeps"].items():
        fit = series["challenge_fit"]
        lo, hi = fit["epsilon_ci95"]
        lines.append(f"| {label} | {fit['epsilon_per_round']:.6f} | [{lo:.6f}, {hi:.6f}] | {fit['goodness_of_fit_p_value']:.4g} |")
    lines += ["", "The required challenge model is P(R)=[1−(1−2ε)^R]/2. Fits use binomial counts, not a least-squares fit to noisy ratios.",
              "A second fit allows a preparation/readout offset. If the challenge-fit goodness of fit is poor, its ε should not be treated as an adequate single-rate description.", "",
              "| Circuit | Offset-model ε per round | Approximate 95% CI | Preparation/readout offset | Goodness-of-fit p |",
              "|---|---:|---:|---:|---:|"]
    for label, series in summary["sweeps"].items():
        fit = series["spam_diagnostic_fit"]
        lo, hi = fit["epsilon_ci95"]
        lines.append(f"| {label} | {fit['epsilon_per_round']:.6f} | [{lo:.6f}, {hi:.6f}] | {fit['spam_offset']:.6f} | {fit['goodness_of_fit_p_value']:.4g} |")
    lines += ["",
              "## Decoded Monte Carlo points", "",
              "| Circuit | Rounds | Failures / shots | P(failure) | 95% Wilson CI |", "|---|---:|---:|---:|---:|"]
    for label, series in summary["sweeps"].items():
        for point in series["points"]:
            lo, hi = point["ci95"]
            lines.append(f"| {label} | {point['rounds']} | {point['failures']} / {point['shots']} | {point['failure_probability']:.6f} | [{lo:.6f}, {hi:.6f}] |")
    lines += ["", "## Model assumptions and practical limits", ""]
    lines += ["- " + limitation for limitation in model["limitations"]]
    lines += ["", "Search shots, independent holdout shots, and round-sweep shots use separate seeds. The saved native circuits define exactly the simulator's gate and measurement sequence.",
              "Before a hardware comparison, verify the current backend's topology, calibrated operations, reset support, and measurement behavior; validate the chosen hardware compiler against these saved circuits.", ""]
    destination.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, default=Path(__file__).parent / "calibrations/garnet-2026-10-24.json")
    parser.add_argument("--basis", type=str.upper, choices=["Z", "X"], default="Z")
    parser.add_argument("--rounds", nargs="+", type=int, default=[1, 2, 3, 5, 7, 10])
    parser.add_argument("--shots", type=int, default=50000, help="Shots per round count and circuit")
    parser.add_argument("--screen-shots", type=int, default=5000)
    parser.add_argument("--validation-shots", type=int, default=50000)
    parser.add_argument("--search-rounds", type=int, default=3)
    parser.add_argument("--anneal-steps", type=int, default=200)
    parser.add_argument("--reset-error", type=float, default=0.003, help="Explicitly assumed reset-flip probability")
    parser.add_argument("--noise-scale", type=float, default=1.0)
    parser.add_argument("--no-reset", action="store_true", help="Optional alternative; defaults to using resets")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("output/garnet-baseline"))
    args = parser.parse_args(argv)
    if min(args.shots, args.screen_shots, args.validation_shots, args.search_rounds, args.anneal_steps) < 1:
        parser.error("Shot counts, search rounds, and annealing steps must be positive")
    if len(set(args.rounds)) != len(args.rounds) or len(args.rounds) < 3 or min(args.rounds) < 1:
        parser.error("Supply at least three distinct positive round counts")
    if not 0 <= args.seed < 2**64:
        parser.error("seed must be between 0 and 2**64-1")
    model = load_garnet_model(args.calibration, args.reset_error).scaled(args.noise_scale)
    ideal_search = make_stim_circuit(3, args.search_rounds, args.basis, args.no_reset)
    layouts = enumerate_native_layouts(ideal_search, model)
    if not layouts:
        raise ValueError("No native placement fits the calibration-export connectivity")
    baseline_index = layouts.index(CANONICAL_LAYOUT) if CANONICAL_LAYOUT in layouts else 0
    args.output.mkdir(parents=True, exist_ok=True)
    circuits_dir = args.output / "circuits"
    circuits_dir.mkdir(exist_ok=True)
    print(f"{args.basis}-memory: {len(model.prx)} physical qubits, {len(model.cz)} CZ couplers, {len(layouts)} valid placements", flush=True)
    native_search = [build_native_pauli_circuit(ideal_search, layout, model) for layout in layouts]
    signatures = [hashlib.sha256(str(circuit).encode()).hexdigest() for circuit in native_search]
    score_cache = {}
    screening = {}

    def cost(index):
        signature = signatures[index]
        if signature not in score_cache:
            result, _ = run_monte_carlo(native_search[index], args.screen_shots, derived_seed(args.seed, 1, index))
            score_cache[signature] = result
            print(f"Screen layout {index}: {result['failures']}/{result['shots']} decoded failures", flush=True)
        screening[index] = score_cache[signature]
        return screening[index]["failure_probability"]

    search = simulated_annealing(cost, len(layouts), args.anneal_steps, args.seed, baseline_index)
    # For four valid placements, enumeration provides an inexpensive reference.
    for index in range(len(layouts)):
        cost(index)
    exhaustive_best = min(range(len(layouts)), key=lambda i: (cost(i), i != baseline_index, i))
    search.update({"candidate_count": len(layouts), "baseline_index": baseline_index,
                   "distinct_noise_circuits": len(set(signatures)), "exhaustive_screening_best": exhaustive_best,
                   "matches_exhaustive_screening_optimum": cost(search["best_index"]) == cost(exhaustive_best),
                   "screening": {str(i): screening[i] for i in range(len(layouts))},
                   "objective": f"Decoded failure probability after {args.search_rounds} rounds"})
    candidate_index = search["best_index"]

    # Candidate is frozen before any holdout shots are collected.
    baseline_holdout, _ = run_monte_carlo(native_search[baseline_index], args.validation_shots, derived_seed(args.seed, 2, baseline_index))
    if signatures[candidate_index] == signatures[baseline_index]:
        candidate_holdout = baseline_holdout
        p_value, improved = 1.0, False
        decision = "Same noisy circuit as the baseline; no placement improvement established"
    else:
        candidate_holdout, _ = run_monte_carlo(native_search[candidate_index], args.validation_shots, derived_seed(args.seed, 3, candidate_index))
        table = [[candidate_holdout["failures"], args.validation_shots - candidate_holdout["failures"]],
                 [baseline_holdout["failures"], args.validation_shots - baseline_holdout["failures"]]]
        p_value = float(fisher_exact(table, alternative="less").pvalue)
        improved = p_value < 0.05 and candidate_holdout["failure_probability"] < baseline_holdout["failure_probability"]
        decision = "Candidate improved in independent holdout under this model" if improved else "No statistically established holdout improvement; retain baseline"
    holdout = {"baseline": baseline_holdout, "candidate": candidate_holdout,
               "baseline_index": baseline_index, "candidate_index": candidate_index,
               "recommended_index": candidate_index if improved else baseline_index,
               "one_sided_fisher_p_value": p_value, "decision": decision}
    print("Holdout: " + decision, flush=True)

    sweep_layouts = {"baseline": baseline_index}
    if signatures[candidate_index] != signatures[baseline_index]:
        sweep_layouts["candidate"] = candidate_index
    sweeps = {}
    for label, index in sweep_layouts.items():
        points = []
        for rounds in sorted(args.rounds):
            ideal = make_stim_circuit(3, rounds, args.basis, args.no_reset)
            circuit = build_native_pauli_circuit(ideal, layouts[index], model)
            # Fail explicitly on broken detector boundaries before sampling.
            circuit.without_noise().detector_error_model()
            stem = f"{label}_r{rounds:02d}"
            circuit.to_file(circuits_dir / (stem + ".stim"))
            circuit.detector_error_model(decompose_errors=True).to_file(circuits_dir / (stem + ".dem"))
            capture = 1024 if rounds == args.search_rounds else 0
            result, sample = run_monte_carlo(circuit, args.shots, derived_seed(args.seed, 4, index, rounds), capture=capture)
            if sample:
                np.savez_compressed(circuits_dir / (stem + "_sample.npz"), **sample)
            points.append({"rounds": rounds, **result})
            print(f"{label} R={rounds}: {result['failures']}/{args.shots}, P_fail={result['failure_probability']:.6f}", flush=True)
        sweeps[label] = {"layout_index": index, "points": points,
                         "challenge_fit": fit_logical_rate(points),
                         "spam_diagnostic_fit": fit_logical_rate(points, include_spam=True)}

    summary = {
        "config": {"basis": args.basis, "distance": 3, "round_counts": sorted(args.rounds),
                   "no_reset": args.no_reset, "shots_per_point": args.shots, "screen_shots": args.screen_shots,
                   "validation_shots": args.validation_shots, "search_rounds": args.search_rounds,
                   "anneal_steps": args.anneal_steps, "seed": args.seed, "hardware_jobs_submitted": 0},
        "noise_model": model.provenance,
        "software": {"python": sys.version.split()[0], "numpy": np.__version__},
        "layouts": layouts,
        "search": search,
        "independent_holdout": holdout,
        "sweeps": sweeps,
    }
    import stim, pymatching, scipy
    summary["software"].update({"stim": stim.__version__, "pymatching": pymatching.__version__, "scipy": scipy.__version__})
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    with (args.output / "points.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["circuit", "layout_index", "rounds", "shots", "failures", "failure_probability", "ci95_low", "ci95_high", "raw_flip_fraction"])
        for label, series in sweeps.items():
            for point in series["points"]:
                writer.writerow([label, series["layout_index"], point["rounds"], point["shots"], point["failures"], point["failure_probability"], *point["ci95"], point["raw_observable_flip_fraction"]])
    plot_results(summary, args.output / "logical_failure_vs_rounds.png")
    write_report(summary, args.output / "report.md")
    for label, series in sweeps.items():
        fit = series["challenge_fit"]
        print(f"{label}: ε={fit['epsilon_per_round']:.6f}, CI={fit['epsilon_ci95']}, goodness-of-fit p={fit['goodness_of_fit_p_value']:.4g}")
    print(f"Saved reproducible run to {args.output.resolve()}", flush=True)
    return summary


if __name__ == "__main__":
    main()
