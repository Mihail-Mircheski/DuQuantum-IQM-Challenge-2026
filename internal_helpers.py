import stim
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
#  These may be useful for working with Stim, when converting to qiskit
#  and when converting measurement result back into readable files
# ─────────────────────────────────────────────────────────────────────────────

"""


"""


def get_qubit_lists(circuit: stim.Circuit) -> tuple[list, list]:
    """
    For Stim circuits: Returns (data_qubit_stim_indices, ancilla_qubit_stim_indices).

    NOTE: PLEASE DOUBLE CHECK FUNCTIONALITY!

    Supports both standard circuits (ancillas use MR) and no-reset circuits
    (ancillas use M), including X/Y-basis data readout. When QUBIT_COORDS
    annotations are present, distinguishes
    by coordinate parity:
        data qubits   → both x and y coordinates are odd
        ancilla qubits → at least one coordinate is even

    """
    coords = circuit.get_final_qubit_coordinates()

    # Collect all measured qubit indices
    all_measured: set[int] = set()
    for instr in circuit.flattened():
        if instr.name in ("M", "MX", "MY", "MR", "MRX", "MRY"):
            for t in instr.targets_copy():
                if t.is_qubit_target:
                    all_measured.add(t.value)

    data_q, anc_q = [], []
    for q in sorted(all_measured):
        if q in coords:
            x, y = coords[q]
            # data qubits sit at odd-x, odd-y grid positions
            if int(x) % 2 == 1 and int(y) % 2 == 1:
                data_q.append(q)
            else:
                anc_q.append(q)
        else:
            # No coordinates — conservative: treat as data
            data_q.append(q)
    return data_q, anc_q


def get_meas_order(circuit: stim.Circuit) -> list:
    """Returns list of Stim qubit indices in measurement order."""
    order = []
    for instr in circuit.flattened():
        if instr.name in ("M", "MX", "MY", "MR", "MRX", "MRY"):
            for t in instr.targets_copy():
                if t.is_qubit_target:
                    order.append(t.value)
    return order



def counts_to_measurement_array(
    counts: dict,
    num_measurements: int,
    total_shots: int,
) -> np.ndarray:
    """
    QISKIT TRACK ONLY. On the Qrisp track, the DetectorExperiment returns
    detection events and observable flips directly.

    Converts a Qiskit counts dict to a (total_shots, num_measurements)
    boolean numpy array in Stim's measurement order.

    Qiskit bitstrings are RIGHT-TO-LEFT: rightmost character = clbit 0.
    Stim's measurement order corresponds to clbit 0, 1, 2, … in sequence.
    """
    if isinstance(num_measurements, bool) or not isinstance(num_measurements, (int, np.integer)) or num_measurements < 1:
        raise ValueError("num_measurements must be a positive integer")
    if isinstance(total_shots, bool) or not isinstance(total_shots, (int, np.integer)) or total_shots < 1:
        raise ValueError("total_shots must be a positive integer")
    if not counts:
        raise ValueError("No measurement counts returned")
    for key, count in counts.items():
        if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 0:
            raise ValueError("Counts must be nonnegative integers")
        if not isinstance(key, str):
            raise ValueError("Expected Qiskit binary or hexadecimal count keys")
    if sum(counts.values()) != total_shots:
        raise ValueError("Returned shot count differs from requested shots")
    result = np.empty((total_shots, num_measurements), dtype=bool)
    row = 0
    for bitstring, count in counts.items():
        if bitstring.startswith("0x"):
            try:
                value = int(bitstring, 16)
            except ValueError as error:
                raise ValueError("Invalid hexadecimal count key") from error
            if value < 0 or value >= 2 ** num_measurements:
                raise ValueError("Count key exceeds measurement width")
            bits = format(value, f"0{num_measurements}b")
        else:
            bits = bitstring.replace(" ", "")
            if len(bits) != num_measurements or set(bits) - {"0", "1"}:
                raise ValueError("Count key has the wrong measurement width or nonbinary characters")
        meas = np.array([int(b) for b in bits[::-1]], dtype=bool)
        result[row:row + count] = meas
        row += count
    return result
