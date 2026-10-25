"""Offline Garnet topology and an explicit Pauli approximation to benchmark data."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import re

import stim


CANONICAL_LAYOUT = {
    1: "QB19", 3: "QB16", 5: "QB12", 8: "QB14", 10: "QB10",
    12: "QB6", 15: "QB8", 17: "QB4", 19: "QB2", 2: "QB20",
    9: "QB15", 11: "QB11", 13: "QB7", 14: "QB13", 16: "QB9",
    18: "QB5", 25: "QB1",
}


@dataclass
class GarnetNoiseModel:
    prx: dict[str, float]
    cz: dict[frozenset[str], float]
    readout: dict[str, float]
    reset: float
    provenance: dict

    def scaled(self, factor: float) -> GarnetNoiseModel:
        if not math.isfinite(factor) or factor < 0:
            raise ValueError("noise scale must be finite and nonnegative")
        if any(p * factor > 1 for p in [*self.prx.values(), *self.cz.values(), *self.readout.values(), self.reset]):
            raise ValueError("scaled probabilities must not exceed one")
        return replace(
            self,
            prx={q: p * factor for q, p in self.prx.items()},
            cz={pair: p * factor for pair, p in self.cz.items()},
            readout={q: p * factor for q, p in self.readout.items()},
            reset=self.reset * factor,
            provenance={**self.provenance, "noise_scale": factor},
        )


def load_garnet_model(path: str | Path, reset_error: float = 0.003) -> GarnetNoiseModel:
    """Read the downloaded quality-metric export; no server connection is made.

    RB/IRB average infidelity r is approximated by a uniform nonidentity Pauli
    error probability p=(d+1)/d*r: 3r/2 for one qubit and 5r/4 for two.
    This is a modeling choice, not a reconstruction of the physical channel.
    Reported assignment_error is approximated by a symmetric readout flip.
    Active-reset error is not provided by this export and must be assumed.
    """
    path = Path(path)
    raw = path.read_bytes()
    snapshot = json.loads(raw)
    if snapshot.get("invalid") or snapshot.get("observation_set_type") != "quality-metric-set":
        raise ValueError("Expected a valid IQM quality-metric-set export")
    if not math.isfinite(reset_error) or not 0 <= reset_error <= 1:
        raise ValueError("reset_error must be a probability")
    prx, cz, readout = {}, {}, {}
    for observation in snapshot["observations"]:
        if observation.get("invalid"):
            continue
        key = observation["dut_field"]
        match = re.fullmatch(r"metrics\.rb\.prx\.drag_crf_sx\.(QB\d+)\.fidelity:par=d2", key)
        if match:
            prx[match[1]] = 1.5 * (1 - float(observation["value"]))
        match = re.fullmatch(r"metrics\.irb\.cz\.slepian_crf\.(QB\d+)__(QB\d+)\.fidelity:par=d2", key)
        if match:
            cz[frozenset((match[1], match[2]))] = 1.25 * (1 - float(observation["value"]))
        match = re.fullmatch(r"metrics\.ssro\.measure\.constant\.(QB\d+)\.assignment_error", key)
        if match:
            readout[match[1]] = float(observation["value"])
    if not prx or set(prx) != set(readout):
        raise ValueError("Missing per-qubit PRX or standard-readout metrics")
    if not cz or any(not pair <= prx.keys() for pair in cz):
        raise ValueError("Missing CZ metrics or unknown coupler endpoints")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in [*prx.values(), *cz.values(), *readout.values()]):
        raise ValueError("The inferred Pauli model contains invalid probabilities")
    return GarnetNoiseModel(prx, cz, readout, reset_error, {
        "source_url": "https://resonance.iqm.tech/#garnet",
        "file": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "observation_set_id": snapshot.get("observation_set_id"),
        "describes_id": snapshot.get("describes_id"),
        "created_timestamp": snapshot.get("created_timestamp"),
        "prx_metric": "metrics.rb.prx.drag_crf_sx.*.fidelity:par=d2",
        "cz_metric": "metrics.irb.cz.slepian_crf.*.fidelity:par=d2",
        "readout_metric": "metrics.ssro.measure.constant.*.assignment_error",
        "assumed_reset_flip_probability": reset_error,
        "limitations": [
            "RB/IRB infidelity is converted to a depolarizing Pauli approximation.",
            "Readout asymmetry and correlations are replaced by symmetric flips.",
            "Active-reset errors are assumed, not measured in this export.",
            "T1/T2 idle noise is excluded because operation durations are unavailable.",
            "Leakage, coherent errors, crosstalk and measurement disturbance are excluded.",
            "Native Clifford decomposition is explicit and unoptimized; it is not an IQM transpiler output.",
            "A simulation result is not a prediction verified on Garnet hardware.",
            "Confidence intervals cover sampling/fit uncertainty; calibration and model uncertainty are not propagated.",
        ],
    })


def circuit_graph(circuit: stim.Circuit) -> dict[int, set[int]]:
    graph = {q: set() for q in circuit.get_final_qubit_coordinates()}
    for instruction in circuit.flattened():
        if instruction.name in {"CX", "CZ"}:
            targets = instruction.targets_copy()
            for a, b in zip(targets[::2], targets[1::2]):
                graph[a.value].add(b.value)
                graph[b.value].add(a.value)
    return graph


def validate_layout(circuit: stim.Circuit, layout: dict[int, str], model: GarnetNoiseModel) -> None:
    graph = circuit_graph(circuit)
    if set(layout) != set(graph) or len(set(layout.values())) != len(layout):
        raise ValueError("Layout must map every active Stim qubit injectively")
    if not set(layout.values()) <= model.prx.keys():
        raise ValueError("Layout references a qubit absent from the snapshot")
    for a, neighbours in graph.items():
        for b in neighbours:
            if frozenset((layout[a], layout[b])) not in model.cz:
                raise ValueError(f"Non-native interaction: {layout[a]} -- {layout[b]}")


def enumerate_native_layouts(circuit: stim.Circuit, model: GarnetNoiseModel) -> list[dict[int, str]]:
    """Enumerate injective edge-preserving embeddings; no SWAP insertion."""
    code = circuit_graph(circuit)
    hardware = {q: set() for q in model.prx}
    for a, b in map(tuple, model.cz):
        hardware[a].add(b)
        hardware[b].add(a)
    if len(code) > len(hardware):
        return []

    def compatible(a, b):
        degrees = sorted(len(code[n]) for n in code[a])
        available = sorted(len(hardware[n]) for n in hardware[b])
        return len(available) >= len(degrees) and all(
            x <= y for x, y in zip(degrees, available[-len(degrees):])
        )

    candidates = {a: {b for b in hardware if compatible(a, b)} for a in code}
    solutions = []

    def visit(mapping, used):
        if len(mapping) == len(code):
            solutions.append(mapping.copy())
            return
        options = []
        for a in code.keys() - mapping.keys():
            allowed = [b for b in candidates[a] - used if all(
                mapping[n] in hardware[b] for n in code[a] & mapping.keys()
            )]
            if not allowed:
                return
            options.append((len(allowed), -len(code[a] & mapping.keys()), -len(code[a]), a, allowed))
        _, _, _, a, allowed = min(options)
        for b in sorted(allowed, key=lambda q: int(q[2:])):
            mapping[a] = b
            visit(mapping, used | {b})
            del mapping[a]

    visit({}, set())
    return sorted(solutions, key=lambda m: tuple(int(m[q][2:]) for q in sorted(code)))


def build_native_pauli_circuit(
    ideal: stim.Circuit, layout: dict[int, str], model: GarnetNoiseModel
) -> stim.Circuit:
    """Lower the ideal circuit to CZ and native Clifford PRX pulses, adding noise.

    H is SQRT_Y then X (two equatorial rotations); CX is H(target)-CZ-H(target).
    RX preparation is R then SQRT_Y; final MX is SQRT_Y_DAG then M. Every
    explicit PRX pulse and CZ receives its own modeled physical error.
    MR measures the whole ancilla group, then resets the group. The measurement
    record order and all detector/observable definitions are preserved.
    """
    validate_layout(ideal, layout, model)
    output = stim.Circuit()

    def pulse(name, q):
        output.append(name, [q])
        if probability := model.prx[layout[q]]:
            output.append("DEPOLARIZE1", [q], probability)

    def hadamard(q):
        pulse("SQRT_Y", q)
        pulse("X", q)

    def reset(q):
        output.append("R", [q])
        if model.reset:
            output.append("X_ERROR", [q], model.reset)

    def measure(q):
        output.append("M", [q], model.readout[layout[q]])

    for instruction in ideal.flattened():
        name = instruction.name
        targets = [target.value for target in instruction.targets_copy() if target.is_qubit_target]
        if name in {"QUBIT_COORDS", "TICK", "DETECTOR", "OBSERVABLE_INCLUDE", "SHIFT_COORDS"}:
            output.append(instruction)
        elif name == "R":
            for q in targets:
                reset(q)
        elif name == "RX":
            for q in targets:
                reset(q)
                pulse("SQRT_Y", q)
        elif name == "H":
            for q in targets:
                hadamard(q)
        elif name in {"CX", "CZ"}:
            for a, b in zip(targets[::2], targets[1::2]):
                if name == "CX":
                    hadamard(b)
                output.append("CZ", [a, b])
                if probability := model.cz[frozenset((layout[a], layout[b]))]:
                    output.append("DEPOLARIZE2", [a, b], probability)
                if name == "CX":
                    hadamard(b)
        elif name in {"M", "MX", "MR"}:
            if name == "MX":
                for q in targets:
                    pulse("SQRT_Y_DAG", q)
            for q in targets:
                measure(q)
            if name == "MR":
                for q in targets:
                    reset(q)
        else:
            raise ValueError(f"Expected a noiseless generated memory circuit; unsupported operation {name}")
    return output
