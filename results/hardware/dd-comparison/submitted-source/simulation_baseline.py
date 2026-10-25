"""Local raw-measurement simulation, syndrome extraction and MWPM decoding."""

from __future__ import annotations

from statistics import NormalDist
import math

import numpy as np
import pymatching
import stim


def binomial_statistics(failures: int, shots: int) -> dict:
    if isinstance(shots, bool) or not isinstance(shots, (int, np.integer)) or shots < 1:
        raise ValueError("shots must be a positive integer")
    if isinstance(failures, bool) or not isinstance(failures, (int, np.integer)) or not 0 <= failures <= shots:
        raise ValueError("failures must be an integer between zero and shots")
    p = failures / shots
    z = NormalDist().inv_cdf(0.975)
    denominator = 1 + z * z / shots
    center = (p + z * z / (2 * shots)) / denominator
    half = z * math.sqrt(p * (1 - p) / shots + z * z / (4 * shots * shots)) / denominator
    return {
        "shots": int(shots),
        "failures": int(failures),
        "failure_probability": p,
        "ci95": [max(0.0, center - half), min(1.0, center + half)],
        "confidence_method": "95% Wilson binomial interval",
    }


def matching_for_circuit(circuit: stim.Circuit) -> pymatching.Matching:
    model = circuit.detector_error_model(decompose_errors=True)
    matching = pymatching.Matching.from_detector_error_model(model)
    # A noiseless DEM can have no fault IDs; still return all logical bits.
    matching.ensure_num_fault_ids(circuit.num_observables)
    return matching


def decode_samples(circuit: stim.Circuit, detectors, observables) -> dict:
    detectors = np.asarray(detectors, dtype=bool)
    observables = np.asarray(observables, dtype=bool)
    if detectors.ndim != 2 or observables.ndim != 2:
        raise ValueError("detectors and observables must be two-dimensional")
    shots = detectors.shape[0]
    if detectors.shape != (shots, circuit.num_detectors) or observables.shape != (shots, circuit.num_observables):
        raise ValueError("Samples do not match this circuit's detector/observable dimensions")
    if shots == 0:
        raise ValueError("At least one shot is required")
    predicted = matching_for_circuit(circuit).decode_batch(detectors)
    failures = int(np.count_nonzero(np.any(predicted != observables, axis=1)))
    return binomial_statistics(failures, shots)


def run_monte_carlo(circuit: stim.Circuit, shots: int, seed: int, batch_size: int = 8192, capture: int = 0):
    """Exercise the same raw -> detectors -> decoder chain needed after QPU runs.

    The decoder DEM and raw-measurement converter both come from *this* noisy
    circuit. No backend or hardware-execution module is imported.
    """
    binomial_statistics(0, shots)
    if batch_size < 1 or capture < 0:
        raise ValueError("batch_size must be positive and capture nonnegative")
    sampler = circuit.compile_sampler(seed=seed)
    converter = circuit.compile_m2d_converter()
    matching = matching_for_circuit(circuit)
    failures = raw_flips = detected = processed = 0
    saved = {"raw_meas": [], "det_events": [], "obs_flips": []}
    captured = 0
    while processed < shots:
        raw = sampler.sample(min(batch_size, shots - processed))
        detectors, observables = converter.convert(measurements=raw, separate_observables=True)
        predictions = matching.decode_batch(detectors)
        failures += int(np.count_nonzero(np.any(predictions != observables, axis=1)))
        raw_flips += int(np.count_nonzero(np.any(observables, axis=1)))
        detected += int(np.count_nonzero(np.any(detectors, axis=1)))
        keep = min(capture - captured, len(raw))
        if keep > 0:
            for name, values in zip(saved, [raw, detectors, observables]):
                saved[name].append(values[:keep].copy())
            captured += keep
        processed += len(raw)
    result = {
        **binomial_statistics(failures, shots),
        "seed": int(seed),
        "batch_size": int(batch_size),
        "raw_observable_flip_fraction": raw_flips / shots,
        "shots_with_detection_events": detected,
        "decoder": "PyMatching MWPM using the matching circuit DEM",
    }
    arrays = {name: np.concatenate(parts) for name, parts in saved.items() if parts}
    return result, arrays
