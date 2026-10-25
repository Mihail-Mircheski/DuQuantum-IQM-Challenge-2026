"""Offline step-2 analysis: real record handling, fits and source preservation."""

from collections import Counter
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

pytest.importorskip("qiskit")
from analyze_logical_error_rate import analyze_runs, fidelity_point, resolve_runs, _plot
from garnet_hardware import analyze_saved_results, write_json
from simulation_baseline import binomial_statistics
from surface_code import make_stim_circuit


def saved_run(path, *, dd="off", rounds=(1, 2, 3), bases=("Z", "X")):
    path.mkdir(parents=True)
    calibration = "11111111-1111-1111-1111-111111111111"
    manifest = {"mode": "hardware_complete", "calibration_set_id": calibration,
                "shots_per_experiment": 64, "dd_profile": dd,
                "compilation_options": {"dd_mode": "disabled" if dd == "off" else "enabled"},
                "experiments": {}, "batches": []}
    members, records = [], []
    for basis in bases:
        for r in rounds:
            key = f"{basis}_r{r}"
            circuit = make_stim_circuit(3, r, basis=basis,
                                        noise={"after_clifford_depolarization": .01})
            file = path / f"{key}.stim"
            circuit.to_file(file)
            samples = circuit.compile_sampler(seed=20 + r).sample(64)
            counts = dict(Counter("".join(str(int(b)) for b in sample[::-1]) for sample in samples))
            manifest["experiments"][key] = {"basis": basis, "rounds": r, "layout": {"1": "QB1"},
                "stim_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                "simulation_baseline": {"failure_probability": .02}}
            member = {"experiment": key, "name": f"{key}_chunk0"}
            members.append(member)
            records.append({**member, "shots": 64, "calibration_set_id": calibration, "counts": counts,
                            "native_payload_verified": True})
    manifest["batches"] = [{"job_id": "local-test-job", "state": "collected", "shots": 64, "circuits": members}]
    write_json(path / "manifest.json", manifest)
    write_json(path / "result-0.json", {"job_id": "local-test-job", "circuits": records})
    analyze_saved_results(path)
    return path


def source_hashes(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


def test_redecode_fit_and_plot_without_connection_or_source_changes(tmp_path):
    pair = tmp_path / "comparison"
    off = saved_run(pair / "off")
    on = saved_run(pair / "xx", dd="xx")
    write_json(pair / "comparison.json", {"mode": "hardware_complete", "arms": {
        "off": {"directory": "off"}, "xx": {"directory": "xx"}}})
    before = source_hashes(pair)
    output = tmp_path / "step2"
    with patch("garnet_hardware.live_backend", side_effect=AssertionError("Must stay offline")):
        result = analyze_runs([pair], output)
    assert len(result["series"]) == 4
    assert {(s["basis"], s["dd_profile"]) for s in result["series"]} == {
        ("Z", "off"), ("X", "off"), ("Z", "xx"), ("X", "xx")}
    for series in result["series"]:
        fit = series["fits"]["challenge"]
        assert 0 <= fit["epsilon_ci95"][0] <= fit["epsilon_per_round"] <= fit["epsilon_ci95"][1] <= .5
        assert [p["rounds"] for p in series["points"]] == [1, 2, 3]
        assert all(p["shots"] == 64 for p in series["points"])
    assert source_hashes(pair) == before
    for name in ["logical-error-rate-results.json", "logical-error-rates.csv", "logical-error-probability.png",
                 "logical-fidelity.png", "report.md"]:
        assert (output / name).is_file()
    with pytest.raises(FileExistsError):
        analyze_runs([off], output)


def test_duplicate_runs_cannot_double_count_shots(tmp_path):
    run = saved_run(tmp_path / "run", bases=("Z",))
    with pytest.raises(ValueError, match="supplied twice"):
        resolve_runs([run, run])


def test_mutated_decoder_reference_is_rejected(tmp_path):
    run = saved_run(tmp_path / "run", bases=("Z",))
    with (run / "Z_r1.stim").open("a") as stream:
        stream.write("TICK\n")
    with pytest.raises(ValueError, match="Decoder reference changed"):
        analyze_runs([run], tmp_path / "analysis")


def test_redecoded_counts_must_match_previous_result(tmp_path):
    run = saved_run(tmp_path / "run", bases=("Z",))
    cached = json.loads((run / "decoded-results.json").read_text())
    cached["points"][0]["failures"] += 1
    write_json(run / "decoded-results.json", cached)
    with pytest.raises(ValueError, match="Re-decoding disagrees"):
        analyze_runs([run], tmp_path / "analysis")


def test_fidelity_intervals_reverse_probability_bounds_and_keep_negatives():
    stats = binomial_statistics(5, 10)
    fidelity = fidelity_point(stats)
    assert fidelity["logical_fidelity"] == 0
    assert fidelity["logical_fidelity_ci95"] == [1 - 2 * stats["ci95"][1], 1 - 2 * stats["ci95"][0]]
    assert fidelity["logical_fidelity_ci95"][0] < 0 < fidelity["logical_fidelity_ci95"][1]


def test_incomplete_runs_are_rejected(tmp_path):
    run = saved_run(tmp_path / "run", bases=("Z",))
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["mode"] = "offline_fixture"
    write_json(run / "manifest.json", manifest)
    with pytest.raises(ValueError, match="incomplete"):
        resolve_runs([run])


def test_single_round_does_not_become_a_fitted_rate(tmp_path):
    run = saved_run(tmp_path / "run", bases=("Z",), rounds=(1,))
    result = analyze_runs([run], tmp_path / "analysis")
    assert result["series"][0]["fits"] == {}
    assert "Insufficient distinct round counts" in (tmp_path / "analysis/report.md").read_text()


def test_fidelity_plot_uses_shared_linear_scale_when_bounds_cross_zero(tmp_path):
    series = []
    for basis, failures in [("X", 5), ("Z", 1)]:
        stats = binomial_statistics(failures, 10)
        point = {"rounds": 1, **stats, **fidelity_point(stats)}
        series.append({"group_id": basis, "run_index": 1, "basis": basis, "dd_profile": "off",
                       "points": [point], "fits": {}})
    assert _plot(series, tmp_path, fidelity=True) == {"Z": "linear", "X": "linear"}


def test_shared_probability_axes_do_not_clip_the_higher_error_basis(tmp_path):
    series = []
    for basis, failures in [("Z", 1), ("X", 5)]:
        stats = binomial_statistics(failures, 10)
        point = {"rounds": 1, **stats, **fidelity_point(stats)}
        series.append({"group_id": basis, "run_index": 1, "basis": basis, "dd_profile": "off",
                       "points": [point], "fits": {}})
    captured = []

    def save(figure, *args, **kwargs):
        captured.extend(ax.get_ylim() for ax in figure.axes)

    with patch("matplotlib.figure.Figure.savefig", new=save):
        _plot(series, tmp_path)
    assert len(captured) == 2 and captured[0] == captured[1]
    assert captured[0][0] == 0
    assert captured[0][1] > 100 * max(p["ci95"][1] for s in series for p in s["points"])
