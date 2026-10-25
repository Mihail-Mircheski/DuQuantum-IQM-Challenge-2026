"""Prepare locally by default; paid QPU execution requires --submit."""

import argparse
from datetime import datetime
from pathlib import Path

from garnet_hardware import collect_run, prepare_run, submit_prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-live", action="store_true", help="Read live Garnet architecture and validate; no submission")
    mode.add_argument("--submit", action="store_true", help="Prepare live, submit paid Garnet QPU jobs, and collect/decode")
    mode.add_argument("--collect", type=Path, metavar="RUN_DIRECTORY", help="Recover existing jobs; never resubmit")
    parser.add_argument("--basis", choices=["X", "Z", "both"], default="Z")
    parser.add_argument("--dd", choices=["off", "xx", "xy4", "standard"], default="off",
                        help="Server-side dynamical decoupling on data qubits; default off")
    parser.add_argument("--rounds", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument("--shots", type=int, default=1000, help="QPU shots per basis/round count (only charged with --submit)")
    parser.add_argument("--baseline-shots", type=int, default=20_000, help="Local noisy Stim shots per experiment")
    parser.add_argument("--out", type=Path, help="New output directory; existing runs are never overwritten")
    parser.add_argument("--timeout", type=float, default=60, help="Seconds to wait per job during --collect; timeout never cancels")
    args = parser.parse_args()
    try:
        if args.collect:
            result = collect_run(args.collect, timeout=args.timeout)
            print(f"Decoded {len(result['points'])} experiments: {args.collect / 'decoded-results.json'}")
            return
        output = args.out or Path("output") / ("garnet-qpu-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        manifest, backend, circuits = prepare_run(
            output, bases=("Z", "X") if args.basis == "both" else (args.basis,),
            rounds=args.rounds, shots=args.shots, live=args.prepare_live or args.submit,
            baseline_shots=args.baseline_shots, dd=args.dd)
        print(f"Prepared {len(circuits)} experiments, {manifest['total_requested_shots']:,} planned QPU shots")
        print(f"Mode: {manifest['mode']}; manifest: {output / 'manifest.json'}")
        print(f"DD: {manifest['dd_profile']}; the Stim baseline does not model DD or idle decoherence.")
        if args.submit:
            print("Submitting paid Garnet jobs. Job IDs are recorded immediately in the manifest.", flush=True)
            result = submit_prepared(output, backend, circuits)
            print(f"Decoded {len(result['points'])} experiments: {output / 'decoded-results.json'}")
        else:
            print("No QPU jobs submitted.")
    except (ValueError, FileExistsError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
