"""Programmatic Garnet execution. Calling run_qiskit submits paid QPU jobs.

Use run_garnet.py for preparation without submission and recoverable job ledgers.
"""

from datetime import datetime
from pathlib import Path
import math

import numpy as np

from extract_syndromes import extract_syndromes
from garnet_hardware import live_backend, prepare_run, submit_prepared
from simulation_baseline import decode_samples


def run_qiskit(distance=3, rounds=1, shots=1000, token=None,
               quantum_computer="garnet", no_reset=False, *, basis="Z", output=None, dd="off"):
    """Execute distance-3 memory with feedback resets and return syndromes.

    Prefer IQM_TOKEN in the environment. The optional token argument is never
    saved. Job IDs, exact compiled circuits, references, counts and decoded
    statistics are persisted under output before returning.
    """
    if distance != 3 or quantum_computer != "garnet" or no_reset is not False:
        raise ValueError("This hardware path supports Garnet, distance 3, with resets")
    output = Path(output) if output else Path("output") / ("garnet-api-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backend = live_backend(token=token)
    _, backend, circuits = prepare_run(output, bases=(basis,), rounds=(rounds,), shots=shots,
                                        live=True, backend=backend, dd=dd)
    report = submit_prepared(output, backend, circuits)
    import stim
    reference = stim.Circuit.from_file(output / f"{basis}_r{rounds}.stim")
    arrays = []
    for path in sorted(output.glob("samples-*.npz")):
        with np.load(path) as samples:
            arrays.append(samples["raw_meas"])
    syndromes = extract_syndromes(np.concatenate(arrays), reference, print_summary=False)
    syndromes.update(stim_circuit=reference, decoded_statistics=report["points"][0], output_directory=str(output))
    return syndromes


def decode_hardware_results(syndromes, stim_circuit=None):
    """Return decoded failure probability and binomial standard error.

    This is P_error at a particular round count. The CLI fits epsilon per round
    only when at least three distinct round counts have completed.
    """
    reference = stim_circuit if stim_circuit is not None else syndromes.get("stim_circuit")
    if reference is None:
        raise ValueError("Supply the saved noisy Stim reference matching the executed circuit")
    stats = decode_samples(reference, syndromes["det_events"], syndromes["obs_flips"])
    p = stats["failure_probability"]
    return p, math.sqrt(p * (1 - p) / stats["shots"])


def run_qrisp(*args, **kwargs):
    raise NotImplementedError("The executable Garnet path uses Qiskit; use run_qiskit or run_garnet.py")
