"""Checks of the physical model, decoder, statistics and placement optimization."""

import json
from pathlib import Path

import numpy as np
import pytest
import stim

from garnet_model import CANONICAL_LAYOUT, build_native_pauli_circuit, enumerate_native_layouts, load_garnet_model, validate_layout
from logical_rate_fit import failure_probability, fit_logical_rate
from mapping_search import simulated_annealing
from simulation_baseline import binomial_statistics, decode_samples, run_monte_carlo
from surface_code import make_stim_circuit
from extract_syndromes import extract_syndromes
from internal_helpers import counts_to_measurement_array


SNAPSHOT = Path(__file__).resolve().parents[1] / "calibrations/garnet-2026-10-24.json"


@pytest.fixture(scope="module")
def model():
    return load_garnet_model(SNAPSHOT)


def test_downloaded_topology_and_model(model):
    assert len(model.prx) == len(model.readout) == 20
    assert len(model.cz) == 30
    assert model.provenance["created_timestamp"].startswith("2026-10-24")
    assert model.provenance["assumed_reset_flip_probability"] == 0.003
    assert len(model.provenance["sha256"]) == 64


def test_snapshot_with_missing_readout_fails_explicitly(tmp_path):
    data = json.loads(SNAPSHOT.read_text())
    data["observations"] = [o for o in data["observations"] if o["dut_field"] != "metrics.ssro.measure.constant.QB1.assignment_error"]
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Missing"):
        load_garnet_model(path)


def test_native_layouts_use_exported_couplers(model):
    ideal = make_stim_circuit(3, 3)
    layouts = enumerate_native_layouts(ideal, model)
    assert len(layouts) == 4
    assert CANONICAL_LAYOUT in layouts
    for layout in layouts:
        validate_layout(ideal, layout, model)
        assert set(model.prx) - set(layout.values()) == {"QB3", "QB17", "QB18"}
        assert layout[10] == "QB10"
    assert enumerate_native_layouts(make_stim_circuit(5, 1), model) == []


def test_rejects_non_native_and_non_injective_maps(model):
    ideal = make_stim_circuit(3, 3)
    bad = dict(CANONICAL_LAYOUT)
    bad[1] = bad[3]
    with pytest.raises(ValueError):
        validate_layout(ideal, bad, model)
    bad = dict(CANONICAL_LAYOUT)
    bad[1], bad[19] = bad[19], bad[1]
    with pytest.raises(ValueError, match="Non-native"):
        validate_layout(ideal, bad, model)


def test_hadamard_is_two_native_clifford_pulses():
    assert stim.Circuit("SQRT_Y 0\nX 0").to_tableau() == stim.Circuit("H 0").to_tableau()


@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("rounds", [1, 2, 3, 5])
@pytest.mark.parametrize("no_reset", [False, True])
def test_lowering_preserves_noiseless_detectors_and_readout(model, basis, rounds, no_reset):
    ideal = make_stim_circuit(3, rounds, basis, no_reset)
    native = build_native_pauli_circuit(ideal, CANONICAL_LAYOUT, model.scaled(0))
    assert native.num_measurements == ideal.num_measurements
    assert native.num_detectors == ideal.num_detectors
    assert native.num_observables == 1
    native.detector_error_model()
    statistics, sample = run_monte_carlo(native, 256, 42, batch_size=100, capture=32)
    assert statistics["failures"] == 0
    assert statistics["raw_observable_flip_fraction"] == 0
    assert statistics["ci95"][1] > 0
    assert sample["raw_meas"].shape == (32, ideal.num_measurements)
    assert not sample["det_events"].any()
    assert not sample["obs_flips"].any()
    cz_count = sum(len(op.targets_copy()) // 2 for op in native.flattened() if op.name == "CZ")
    assert cz_count == 24 * rounds


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_native_noisy_model_has_distance_three(model, basis):
    native = build_native_pauli_circuit(make_stim_circuit(3, 3, basis), CANONICAL_LAYOUT, model)
    assert len(native.shortest_graphlike_error()) == 3


def test_matching_corrects_a_single_fault_on_a_logical_path(model):
    reference = make_stim_circuit(3, 3)
    faulty = stim.Circuit()
    inserted = False
    for operation in reference.flattened():
        faulty.append(operation)
        if operation.name == "MR" and not inserted:
            faulty.append("X_ERROR", [1], 1.0)
            inserted = True
    detectors, observables = faulty.compile_detector_sampler(seed=11).sample(64, separate_observables=True)
    assert observables.all()
    assert detectors.any()
    noisy = build_native_pauli_circuit(reference, CANONICAL_LAYOUT, model)
    assert decode_samples(noisy, detectors, observables)["failures"] == 0


def test_undetected_logical_string_is_counted_as_a_failure(model):
    reference = make_stim_circuit(3, 3)
    faulty = stim.Circuit()
    for operation in reference.flattened():
        if operation.name == "M":
            faulty.append("X_ERROR", [1, 8, 15], 1.0)
        faulty.append(operation)
    detectors, observables = faulty.compile_detector_sampler(seed=11).sample(64, separate_observables=True)
    assert not detectors.any()
    assert observables.all()
    noisy = build_native_pauli_circuit(reference, CANONICAL_LAYOUT, model)
    assert decode_samples(noisy, detectors, observables)["failures"] == 64


def test_decoding_reduces_raw_logical_flips_under_this_model(model):
    native = build_native_pauli_circuit(make_stim_circuit(3, 3), CANONICAL_LAYOUT, model)
    statistics, _ = run_monte_carlo(native, 10000, 82)
    assert 0 < statistics["failure_probability"] < statistics["raw_observable_flip_fraction"]


def test_wilson_intervals_handle_zero_and_all_failures():
    zero = binomial_statistics(0, 1000)
    assert zero["ci95"][0] == 0
    assert 0.003 < zero["ci95"][1] < 0.004
    full = binomial_statistics(1000, 1000)
    assert 0.996 < full["ci95"][0] < 0.997
    assert full["ci95"][1] == 1
    with pytest.raises(ValueError):
        binomial_statistics(1, 0)


def test_challenge_fit_recovers_known_rate():
    rounds = [1, 2, 3, 5, 7, 10]
    shots = 1_000_000
    epsilon = 0.02
    points = [{"rounds": r, "shots": shots, "failures": round(shots * failure_probability(r, epsilon))} for r in rounds]
    fit = fit_logical_rate(points)
    assert abs(fit["epsilon_per_round"] - epsilon) < 1e-5
    assert fit["epsilon_ci95"][0] < epsilon < fit["epsilon_ci95"][1]
    assert not fit["model_mismatch_flag"]


def test_spam_diagnostic_recovers_offset_and_flags_wrong_challenge_model():
    rounds = [1, 2, 3, 5, 7, 10]
    shots = 1_000_000
    points = [{"rounds": r, "shots": shots, "failures": round(shots * failure_probability(r, 0.015, 0.03))} for r in rounds]
    challenge = fit_logical_rate(points)
    spam = fit_logical_rate(points, include_spam=True)
    assert challenge["model_mismatch_flag"]
    assert abs(spam["epsilon_per_round"] - 0.015) < 1e-5
    assert abs(spam["spam_offset"] - 0.03) < 1e-5
    assert not spam["model_mismatch_flag"]


def test_zero_failure_rate_has_nonzero_upper_confidence_limit():
    fit = fit_logical_rate([{"rounds": r, "shots": 10000, "failures": 0} for r in [1, 3, 5]])
    assert fit["epsilon_per_round"] == 0
    assert fit["epsilon_ci95"][0] == 0
    assert 0 < fit["epsilon_ci95"][1] < 0.001
    assert failure_probability(0, 0.5) == 0


def test_annealing_finds_known_finite_space_optimum():
    costs = [0.10, 0.08, 0.03, 0.06]
    result = simulated_annealing(lambda i: costs[i], 4, steps=100, seed=42)
    assert result["best_index"] == 2
    assert result["best_screening_cost"] == 0.03
    assert len(result["trace"]) == 100


def test_a_flat_optimization_objective_retains_baseline():
    result = simulated_annealing(lambda i: 0.05, 4, steps=20, start=3)
    assert result["best_index"] == 3


def test_zero_noise_layouts_are_identical_under_the_model(model):
    ideal = make_stim_circuit(3, 3)
    circuits = [str(build_native_pauli_circuit(ideal, layout, model.scaled(0))) for layout in enumerate_native_layouts(ideal, model)]
    assert len(set(circuits)) == 1


def test_noisy_native_measurements_match_saved_converter(model):
    native = build_native_pauli_circuit(make_stim_circuit(3, 3), CANONICAL_LAYOUT, model)
    _, sample = run_monte_carlo(native, 512, 53, capture=512)
    d, o = native.compile_m2d_converter().convert(measurements=sample["raw_meas"], separate_observables=True)
    assert np.array_equal(d, sample["det_events"])
    assert np.array_equal(o, sample["obs_flips"])


def test_qiskit_style_counts_roundtrip_preserves_decoded_failures(model):
    from collections import Counter
    native = build_native_pauli_circuit(make_stim_circuit(3, 3), CANONICAL_LAYOUT, model)
    _, sample = run_monte_carlo(native, 512, 93, capture=512)
    # Qiskit puts classical bit zero at the right-hand end of the string.
    counts = Counter("".join("1" if bit else "0" for bit in row[::-1]) for row in sample["raw_meas"])
    recovered = counts_to_measurement_array(counts, native.num_measurements, 512)
    extracted = extract_syndromes(recovered, native, print_summary=False)
    before = decode_samples(native, sample["det_events"], sample["obs_flips"])
    after = decode_samples(native, extracted["det_events"], extracted["obs_flips"])
    assert before["failures"] == after["failures"]


def test_complete_command_noiseless_control(tmp_path):
    from run_simulation_baseline import main
    summary = main(["--rounds", "1", "2", "3", "--shots", "64", "--screen-shots", "32",
                    "--validation-shots", "64", "--noise-scale", "0", "--anneal-steps", "8",
                    "--output", str(tmp_path)])
    assert not summary["config"]["no_reset"]
    assert summary["config"]["hardware_jobs_submitted"] == 0
    assert summary["search"]["candidate_count"] == 4
    assert summary["search"]["distinct_noise_circuits"] == 1
    assert summary["independent_holdout"]["recommended_index"] == summary["search"]["baseline_index"]
    assert summary["sweeps"]["baseline"]["challenge_fit"]["epsilon_per_round"] == 0
    assert (tmp_path / "report.md").is_file()
    assert (tmp_path / "logical_failure_vs_rounds.png").stat().st_size > 1000
    assert (tmp_path / "circuits/baseline_r03_sample.npz").is_file()
