"""Physics and integration checks for the README's Stim circuit generator."""

import numpy as np
import pytest
import stim

from extract_syndromes import extract_syndromes
from surface_code import DEFAULT_NOISE, get_circuit_info, make_stim_circuit


@pytest.mark.parametrize("distance", [3, 5])
@pytest.mark.parametrize("rounds", [1, 2, 3, 5])
@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("no_reset", [False, True])
def test_noiseless_memory_and_record_shapes(distance, rounds, basis, no_reset):
    circuit = make_stim_circuit(distance, rounds, basis, no_reset)
    info = get_circuit_info(circuit)
    assert info["num_data_qubits"] == distance**2
    assert info["num_anc_qubits"] == distance**2 - 1
    assert info["num_active_qubits"] == 2 * distance**2 - 1
    assert info["num_measurements"] == rounds * (distance**2 - 1) + distance**2
    assert len(info["meas_order"]) == circuit.num_measurements
    assert circuit.num_detectors == rounds * (distance**2 - 1)
    assert circuit.num_observables == 1

    # A deterministic-detector analysis catches bad initial and final boundaries.
    circuit.detector_error_model()
    raw = circuit.compile_sampler(seed=42).sample(256)
    result = extract_syndromes(raw, circuit, print_summary=False)
    assert result["det_events"].shape == (256, circuit.num_detectors)
    assert result["obs_flips"].shape == (256, 1)
    assert not result["det_events"].any()
    assert not result["obs_flips"].any()
    # Raw outcomes remain quantum-random even when all detectors are quiet.
    assert np.any(raw[1:] != raw[0])


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_reset_reference_matches_stim_and_is_default(basis):
    circuit = make_stim_circuit(3, 3, basis, noise=DEFAULT_NOISE)
    expected = stim.Circuit.generated(
        f"surface_code:rotated_memory_{basis.lower()}",
        distance=3,
        rounds=3,
        **DEFAULT_NOISE,
    )
    assert circuit == expected


def inject_after_round(circuit, round_number, pauli, qubits):
    """Inject known physical faults between stabilizer rounds, not into annotations."""
    ancillas = set(get_circuit_info(circuit)["anc_stim_indices"])
    output = stim.Circuit()
    rounds_seen = 0
    for instruction in circuit.flattened():
        output.append(instruction)
        if instruction.name in {"M", "MR"}:
            targets = {target.value for target in instruction.targets_copy()}
            if targets == ancillas:
                rounds_seen += 1
                if rounds_seen == round_number:
                    output.append(pauli + "_ERROR", qubits, 1.0)
    assert rounds_seen >= round_number
    return output


@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("no_reset", [False, True])
@pytest.mark.parametrize("pauli", ["X", "Z"])
def test_one_center_error_triggers_two_adjacent_checks(basis, no_reset, pauli):
    circuit = make_stim_circuit(3, 3, basis, no_reset)
    center = next(q for q, xy in circuit.get_final_qubit_coordinates().items() if xy == [3, 3])
    faulty = inject_after_round(circuit, 1, pauli, [center])
    detectors, _ = faulty.compile_detector_sampler(seed=7).sample(64, separate_observables=True)
    # Detector sampling uses an ideal reference: X_ERROR(1) is a known fault.
    assert np.all(detectors.sum(axis=1) == 2)


@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("no_reset", [False, True])
def test_logical_string_flips_observable_without_detection(basis, no_reset):
    circuit = make_stim_circuit(3, 3, basis, no_reset)
    info = get_circuit_info(circuit)
    coords = circuit.get_final_qubit_coordinates()
    # A vertical X string is conjugate to Z memory; a horizontal Z string to X memory.
    axis = 0 if basis == "Z" else 1
    boundary = min(coords[q][axis] for q in info["data_stim_indices"])
    logical = [q for q in info["data_stim_indices"] if coords[q][axis] == boundary]
    assert len(logical) == 3
    faulty = inject_after_round(circuit, 3, "X" if basis == "Z" else "Z", logical)
    detectors, observables = faulty.compile_detector_sampler(seed=7).sample(64, separate_observables=True)
    assert not detectors.any()
    assert observables.all()


@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("no_reset", [False, True])
def test_readout_fault_has_correct_time_separation(basis, no_reset):
    circuit = make_stim_circuit(3, 5, basis, no_reset)
    raw = circuit.compile_sampler(seed=11).sample(1)
    # A readout-only fault on one ancilla in round two.
    raw[0, 8] ^= True
    detectors, _ = circuit.compile_m2d_converter().convert(
        measurements=raw, separate_observables=True
    )
    coordinates = circuit.get_detector_coordinates()
    fired_times = {coordinates[int(i)][2] for i in np.flatnonzero(detectors[0])}
    assert fired_times == ({1, 3} if no_reset else {1, 2})


@pytest.mark.parametrize("distance", [3, 5])
@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("no_reset", [False, True])
def test_noisy_detector_model_preserves_graphlike_distance(distance, basis, no_reset):
    circuit = make_stim_circuit(distance, distance, basis, no_reset, DEFAULT_NOISE)
    model = circuit.detector_error_model(decompose_errors=True)
    assert model.num_errors > 0
    assert len(circuit.shortest_graphlike_error()) == distance


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_no_reset_removes_only_mid_circuit_reset_noise(basis):
    circuit = make_stim_circuit(3, 4, basis, True, {"after_reset_flip_probability": 0.1})
    flat = list(circuit.flattened())
    assert not any(instruction.name == "MR" for instruction in flat)
    errors = [instruction for instruction in flat if instruction.name in {"X_ERROR", "Z_ERROR"}]
    assert sum(len(instruction.targets_copy()) for instruction in errors) == 17
    first_readout = next(i for i, instruction in enumerate(flat) if instruction.name == "M")
    assert all(instruction.name not in {"X_ERROR", "Z_ERROR", "R", "RX"} for instruction in flat[first_readout:])


@pytest.mark.parametrize("kwargs,exception", [
    ({"distance": 2}, ValueError),
    ({"distance": 3.0}, TypeError),
    ({"distance": True}, TypeError),
    ({"rounds": 0}, ValueError),
    ({"rounds": 1.5}, TypeError),
    ({"basis": "Y"}, ValueError),
    ({"basis": None}, ValueError),
    ({"no_reset": 1}, TypeError),
    ({"noise": {"not_a_noise_model": 0.1}}, ValueError),
    ({"noise": {"before_measure_flip_probability": float("nan")}}, ValueError),
    ({"noise": {"before_measure_flip_probability": -0.1}}, ValueError),
    ({"noise": {"before_measure_flip_probability": 1.1}}, ValueError),
    ({"noise": {"before_measure_flip_probability": True}}, ValueError),
])
def test_rejects_invalid_configuration(kwargs, exception):
    arguments = {"distance": 3, "rounds": 3, **kwargs}
    with pytest.raises(exception):
        make_stim_circuit(**arguments)
