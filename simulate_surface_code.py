"""Run the README step-1 generator entirely in the local Stim simulator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from surface_code import DEFAULT_NOISE, get_circuit_info, make_stim_circuit


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--basis", type=str.upper, choices=["Z", "X"], default="Z")
    reset = parser.add_mutually_exclusive_group()
    reset.add_argument("--no-reset", dest="no_reset", action="store_true")
    reset.add_argument("--with-reset", dest="no_reset", action="store_false")
    parser.set_defaults(no_reset=False)
    parser.add_argument("--noise", type=float, default=0.0, help="Uniform Pauli-noise probability")
    parser.add_argument("--shots", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("output/step1"))
    args = parser.parse_args(argv)
    if args.shots < 1:
        parser.error("--shots must be positive")
    if not 0 <= args.seed < 2**64:
        parser.error("--seed must be between 0 and 2**64-1")

    try:
        circuit = make_stim_circuit(
            distance=args.distance,
            rounds=args.rounds,
            basis=args.basis,
            no_reset=args.no_reset,
            noise={key: args.noise for key in DEFAULT_NOISE},
        )
    except (ValueError, TypeError) as error:
        parser.error(str(error))

    # This raises if any detector or logical observable is nondeterministic
    # under ideal execution. Do not silently permit gauge detectors.
    circuit.without_noise().detector_error_model()
    raw = circuit.compile_sampler(seed=args.seed).sample(args.shots)
    detectors, observables = circuit.compile_m2d_converter().convert(
        measurements=raw, separate_observables=True
    )
    if args.noise == 0 and (detectors.any() or observables.any()):
        raise RuntimeError("Noiseless simulation produced a detector or logical flip")

    info = get_circuit_info(circuit)
    metadata = {
        "config": {
            "distance": args.distance,
            "rounds": args.rounds,
            "basis": args.basis,
            "no_reset": args.no_reset,
            "noise": {key: args.noise for key in DEFAULT_NOISE},
            "shots": args.shots,
            "seed": args.seed,
        },
        "circuit": info,
        "simulation": {
            "shots_with_detection_events": int(np.count_nonzero(detectors.any(axis=1))),
            "raw_observable_flip_fraction": float(observables.mean()),
            "decoded": False,
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    circuit.to_file(args.output / "circuit.stim")
    (args.output / "timeline.svg").write_text(str(circuit.diagram("timeline-svg")), encoding="utf-8")
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    np.savez_compressed(
        args.output / "samples.npz",
        raw_meas=raw,
        det_events=detectors,
        obs_flips=observables,
    )
    print(f"{args.basis}-memory, distance={args.distance}, rounds={args.rounds}, no_reset={args.no_reset}")
    print(f"Active qubits: {info['num_active_qubits']} ({info['num_data_qubits']} data + {info['num_anc_qubits']} ancillas)")
    print(f"Raw measurements: {raw.shape}; detectors: {detectors.shape}; observables: {observables.shape}")
    print(f"Shots with detection events: {metadata['simulation']['shots_with_detection_events']}/{args.shots}")
    print(f"Raw observable flip fraction: {observables.mean():.6f} (before decoding; not a logical error rate)")
    print(f"Files saved to {args.output.resolve()}")


if __name__ == "__main__":
    main()
