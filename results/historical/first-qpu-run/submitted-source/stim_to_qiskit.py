"""Stim memory circuits to Qiskit, preserving every measurement-record index."""

from math import pi

import stim
from qiskit import QuantumCircuit


def stim_to_qiskit(circuit: stim.Circuit, *, feedback_reset: bool = False):
    """Return (circuit, sparse-Stim-to-dense map, measurement order).

    Pass a noise-free circuit. TICKs become barriers. In feedback_reset mode,
    initial R operations rely on backend shot initialization, and subsequent
    resets use the preceding measurement's existing classical bit followed by
    conditional X. No hidden measurements/classical bits are introduced.
    """
    flat = circuit.flattened()
    active = sorted({t.value for inst in flat if inst.name != "QUBIT_COORDS"
                     for t in inst.targets_copy() if t.is_qubit_target})
    dense = {q: i for i, q in enumerate(active)}
    qc = QuantumCircuit(len(active), circuit.num_measurements)
    pristine = set(active)
    fresh_measurement = {}
    order = []

    def rotate(q, angle, phase):
        qc.r(angle, phase, dense[q])
        pristine.discard(q)
        fresh_measurement.pop(q, None)

    def reset(q):
        if not feedback_reset:
            qc.reset(dense[q])
        elif q in pristine:
            pass  # IQM initializes qubits before each shot.
        elif q in fresh_measurement:
            with qc.if_test((qc.clbits[fresh_measurement[q]], 1)):
                qc.r(pi, 0, dense[q])
        else:
            raise ValueError("Feedback reset needs a fresh Z measurement on the same qubit")
        pristine.discard(q)
        fresh_measurement.pop(q, None)

    def measure(q):
        bit = len(order)
        qc.measure(dense[q], bit)
        fresh_measurement[q] = bit
        pristine.discard(q)
        order.append(q)

    for inst in flat:
        name = inst.name
        if name in {"QUBIT_COORDS", "SHIFT_COORDS", "DETECTOR", "OBSERVABLE_INCLUDE"}:
            continue
        if name == "TICK":
            qc.barrier()
            continue
        targets = inst.targets_copy()
        if any(not t.is_qubit_target or t.is_inverted_result_target for t in targets):
            raise ValueError(f"Unsupported target in {name}")
        qs = [t.value for t in targets]
        if name in {"M", "MX", "MR"} and inst.gate_args_copy() and inst.gate_args_copy()[0] != 0:
            raise ValueError("Remove simulation noise explicitly with circuit.without_noise()")
        if name in {"R", "RX"}:
            for q in qs:
                reset(q)
                if name == "RX":
                    rotate(q, pi / 2, pi / 2)
        elif name in {"X", "SQRT_Y", "SQRT_Y_DAG"}:
            for q in qs:
                angle, phase = {"X": (pi, 0), "SQRT_Y": (pi / 2, pi / 2),
                                "SQRT_Y_DAG": (pi / 2, -pi / 2)}[name]
                rotate(q, angle, phase)
        elif name == "H":
            for q in qs:
                qc.h(dense[q])
                pristine.discard(q)
                fresh_measurement.pop(q, None)
        elif name in {"CX", "CZ"}:
            for a, b in zip(qs[::2], qs[1::2]):
                getattr(qc, name.lower())(dense[a], dense[b])
                for q in (a, b):
                    pristine.discard(q)
                    fresh_measurement.pop(q, None)
        elif name in {"M", "MX", "MR"}:
            if name == "MX":
                for q in qs:
                    rotate(q, pi / 2, -pi / 2)
            for q in qs:
                measure(q)
            if name == "MR":
                for q in qs:
                    reset(q)
        else:
            raise NotImplementedError(f"Unsupported Stim operation {name}; pass a noise-free memory circuit")
    if len(order) != circuit.num_measurements:
        raise ValueError("Measurement record length changed during conversion")
    return qc, dense, order
