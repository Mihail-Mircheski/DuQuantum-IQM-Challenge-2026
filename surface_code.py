"""Step 1: rotated surface-code memory circuits, simulated locally with Stim.

Garnet is the eventual hardware target. This module does not connect to a QPU.
Only the Stim starting point from the challenge README is implemented here.
"""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Integral, Real
from typing import TYPE_CHECKING
import math

import stim

from internal_helpers import get_meas_order, get_qubit_lists

if TYPE_CHECKING:
    from qiskit import QuantumCircuit


# Illustrative circuit-level Pauli noise, not measured Garnet calibration data.
# Each probability can be configured independently. None means noiseless.
DEFAULT_NOISE = {
    "after_clifford_depolarization": 0.003,
    "after_reset_flip_probability": 0.003,
    "before_measure_flip_probability": 0.003,
    "before_round_data_depolarization": 0.003,
}


def _validate_noise(noise: Mapping[str, float] | None) -> dict[str, float]:
    if noise is None:
        return {}
    if not isinstance(noise, Mapping):
        raise TypeError("noise must be a mapping of Stim noise names to probabilities")
    unknown = set(noise) - DEFAULT_NOISE.keys()
    if unknown:
        raise ValueError(f"Unsupported noise parameters: {sorted(unknown, key=str)}")
    result = {}
    for name, probability in noise.items():
        if (
            isinstance(probability, bool)
            or not isinstance(probability, Real)
            or not math.isfinite(probability)
            or not 0 <= probability <= 1
        ):
            raise ValueError(f"{name} must be a finite probability between 0 and 1")
        result[name] = float(probability)
    return result


def _without_ancilla_resets(
    reference: stim.Circuit, initial_reset_error: float
) -> stim.Circuit:
    """Convert a generated reset circuit and all its measurement parities.

    The reference's j-th ancilla syndrome becomes raw[j] XOR raw[previous_j]
    when its reset is omitted. Substitute that expression into *every*
    detector and observable, cancelling repeated terms modulo two. This also
    handles the second round, the unknown first-round checks, and the final
    data/ancilla boundary without hard-coded measurement offsets.

    Input is limited to Stim's generated rotated-memory circuit. Its reset
    noise must be disabled: noise after the retained initial R/RX preparations
    is added here, and there are no reset faults on removed resets.
    REPEAT blocks are unrolled so each measurement has an absolute index.
    """
    output = stim.Circuit()
    equivalents: list[set[int]] = []
    previous_ancilla_readout: dict[int, int] = {}

    for instruction in reference.flattened():
        name = instruction.name
        targets = instruction.targets_copy()
        arguments = instruction.gate_args_copy()

        if name == "MR":
            output.append("M", targets, arguments)
            for target in targets:
                qubit = target.value
                current = len(equivalents)
                parity = {current}
                if qubit in previous_ancilla_readout:
                    parity.add(previous_ancilla_readout[qubit])
                equivalents.append(parity)
                previous_ancilla_readout[qubit] = current
        elif name in {"M", "MX"}:
            # Final destructive data readout does not accumulate ancilla parity.
            output.append(instruction)
            for _ in targets:
                equivalents.append({len(equivalents)})
        elif name in {"DETECTOR", "OBSERVABLE_INCLUDE"}:
            measurement_count = len(equivalents)
            raw_parity: set[int] = set()
            for target in targets:
                if not target.is_measurement_record_target:
                    raise ValueError(f"Unexpected {name} target: {target}")
                index = measurement_count + target.value
                if not 0 <= index < measurement_count:
                    raise ValueError(f"Invalid measurement reference: {target}")
                raw_parity.symmetric_difference_update(equivalents[index])
            output.append(
                name,
                [stim.target_rec(index - measurement_count) for index in sorted(raw_parity)],
                arguments,
            )
        else:
            output.append(instruction)
            if initial_reset_error and name in {"R", "RX"}:
                # R prepares Z eigenstates; RX prepares X eigenstates.
                output.append(
                    "X_ERROR" if name == "R" else "Z_ERROR",
                    targets,
                    initial_reset_error,
                )
    return output


def make_stim_circuit(
    distance: int,
    rounds: int,
    basis: str = "Z",
    no_reset: bool = False,
    noise: Mapping[str, float] | None = None,
) -> stim.Circuit:
    """Generate the circuit required by README step 1.

    Args:
        distance: Odd integer >= 3. Uses d**2 data and d**2-1 ancilla qubits.
        rounds: Positive number of stabilizer measurement rounds.
        basis: Z stores logical |0>; X stores logical |+>. Case insensitive.
        no_reset: Omit ancilla resets between rounds and rewrite detectors.
            Initial preparations are always retained. False gives Stim's
            standard measure-and-reset reference. Either choice is simulatable.
        noise: None for noiseless, or a mapping using the four DEFAULT_NOISE
            keys. Omitted keys are zero. These are Pauli error probabilities,
            not a complete model of decoherence, leakage, or Garnet hardware.

    Returns:
        stim.Circuit with coordinates, a validated extraction schedule,
        DETECTOR annotations and one logical OBSERVABLE_INCLUDE.

    Stim keeps its sparse geometric qubit labels: num_qubits is the largest
    index plus one, not the number of active physical qubits. For d=3 these
    values are 26 and 17 respectively; use get_circuit_info to distinguish them.
    """
    if isinstance(distance, bool) or not isinstance(distance, Integral):
        raise TypeError("distance must be an odd integer >= 3")
    if distance < 3 or distance % 2 == 0:
        raise ValueError("distance must be an odd integer >= 3")
    if isinstance(rounds, bool) or not isinstance(rounds, Integral):
        raise TypeError("rounds must be a positive integer")
    if rounds < 1:
        raise ValueError("rounds must be a positive integer")
    if not isinstance(basis, str) or basis.upper() not in {"X", "Z"}:
        raise ValueError("basis must be 'X' or 'Z'")
    if not isinstance(no_reset, bool):
        raise TypeError("no_reset must be True or False")

    probabilities = _validate_noise(noise)
    initial_reset_error = probabilities.get("after_reset_flip_probability", 0.0)
    if no_reset:
        probabilities["after_reset_flip_probability"] = 0.0
    reference = stim.Circuit.generated(
        f"surface_code:rotated_memory_{basis.lower()}",
        distance=int(distance),
        rounds=int(rounds),
        **probabilities,
    )
    if no_reset:
        return _without_ancilla_resets(reference, initial_reset_error)
    return reference


def get_circuit_info(circuit: stim.Circuit) -> dict:
    """Summarize active qubits and measurement records for generated circuits."""
    data, ancillas = get_qubit_lists(circuit)
    return {
        "num_qubits": circuit.num_qubits,
        "num_active_qubits": len(data) + len(ancillas),
        "num_data_qubits": len(data),
        "num_anc_qubits": len(ancillas),
        "num_measurements": circuit.num_measurements,
        "num_detectors": circuit.num_detectors,
        "num_observables": circuit.num_observables,
        "data_stim_indices": sorted(data),
        "anc_stim_indices": sorted(ancillas),
        "qubit_coordinates": circuit.get_final_qubit_coordinates(),
        "meas_order": get_meas_order(circuit),
    }


def make_qiskit_circuit(
    distance: int, rounds: int, basis: str = "Z", no_reset: bool = False
) -> QuantumCircuit:
    """Unimplemented alternative starting point from the challenge."""
    raise NotImplementedError("Use make_stim_circuit(); the Stim track implements step 1")


def make_qrisp_experiment(
    distance: int,
    rounds: int,
    basis: str = "Z",
    no_reset: bool = False,
    p: float | None = None,
):
    """Unimplemented alternative starting point from the challenge."""
    raise NotImplementedError("Use make_stim_circuit(); the Stim track implements step 1")
