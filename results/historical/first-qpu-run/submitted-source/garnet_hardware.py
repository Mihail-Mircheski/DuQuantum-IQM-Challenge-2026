"""Garnet preparation, guarded execution, result recovery and MWPM decoding.

Importing this module never connects to a server. Only submit_prepared() submits
jobs. Offline preparation uses an explicitly synthetic capability fixture.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from uuid import UUID

import numpy as np
import stim
from qiskit import qpy, transpile

from garnet_model import build_native_pauli_circuit, load_garnet_model, validate_layout
from internal_helpers import counts_to_measurement_array
from logical_rate_fit import fit_logical_rate
from simulation_baseline import binomial_statistics, decode_samples, run_monte_carlo
from stim_to_qiskit import stim_to_qiskit
from surface_code import make_stim_circuit

ROOT = Path(__file__).resolve().parent
SERVER_URL = "https://resonance.iqm.tech"
CALIBRATION = ROOT / "calibrations/garnet-2026-10-24.json"
LAYOUTS = ROOT / "calibrations/garnet-selected-layouts.json"
# Conservative limits recorded from Resonance on 2026-10-24.
MAX_CIRCUIT_SHOTS = 20_000
MAX_BATCH_CIRCUITS = 100
MAX_TOTAL_SHOTS = 2_000_000


def write_json(path, value):
    """Atomic replace: a crash must not leave a half-written job ledger."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selected_layout(basis):
    return {int(q): name for q, name in json.loads(LAYOUTS.read_text())["layouts"][basis].items()}


def feedback_reference(native: stim.Circuit) -> stim.Circuit:
    """Model the *reported-bit-controlled* resets actually sent to Garnet.

    A flipped readout bit also flips the reset decision. Replacing this with
    ideal R would miss that correlated fault. The existing assumed reset X_ERROR
    remains an additional residual feedback/reset error in the Pauli model.
    """
    out = stim.Circuit()
    pristine = set(native.get_final_qubit_coordinates())
    fresh = {}
    measurements = 0
    for inst in native.flattened():
        name = inst.name
        qs = [t.value for t in inst.targets_copy() if t.is_qubit_target]
        if name == "R":
            for q in qs:
                if q in pristine:
                    out.append("R", [q])
                elif q in fresh:
                    out.append("CX", [stim.target_rec(fresh[q] - measurements), q])
                else:
                    raise ValueError("Reset does not follow a Z measurement")
                pristine.discard(q)
                fresh.pop(q, None)
        else:
            out.append(inst)
            if name == "M":
                for q in qs:
                    fresh[q] = measurements
                    measurements += 1
                    pristine.discard(q)
            elif name in {"X", "SQRT_Y", "SQRT_Y_DAG", "CZ"}:
                for q in qs:
                    pristine.discard(q)
                    fresh.pop(q, None)
    return out


def reuse_reset_feedback_labels(circuit):
    """Reuse one hardware feedback signal per qubit; preserve all result keys.

    Garnet AWGs support only one feedback label per drive. Measurement `key`
    identifies the stored result, while `feedback_key` identifies a reusable
    real-time signal. Each reset consumes the latest measurement of its own
    ancilla, so the signal can be reused on subsequent rounds.
    """
    latest = {}
    for instruction in circuit.instructions:
        if instruction.name == "measure":
            if len(instruction.locus) != 1:
                raise ValueError("Reset feedback expects individual measurements")
            q = instruction.locus[0]
            latest[q] = instruction.args.get("feedback_key")
            if latest[q]:
                instruction.args["feedback_key"] = "garnet_reset"
        elif instruction.name == "cc_prx":
            source = instruction.args["feedback_qubit"]
            if instruction.locus != (source,) or not latest.get(source):
                raise ValueError("Shared reset feedback must use the latest measurement of the same qubit")
            if instruction.args["feedback_key"] != latest[source]:
                raise ValueError("Shared reset feedback cannot refer to an older measurement")
            instruction.args["feedback_key"] = "garnet_reset"
    return circuit


def configure_garnet_feedback(backend):
    """Apply the same serialization policy to preparation and submission."""
    if getattr(backend, "_garnet_shared_reset_feedback", False):
        return backend
    original_serializer = backend.serialize_circuit

    def serialize(circuit, qubit_index_to_name=None):
        native = original_serializer(circuit, qubit_index_to_name)
        return reuse_reset_feedback_labels(native)

    backend.serialize_circuit = serialize
    backend._garnet_shared_reset_feedback = True
    return backend


def offline_backend(model):
    """SDK-compatible test fixture; feedback capability is assumed, not queried."""
    from iqm.iqm_client import DynamicQuantumArchitecture, GateInfo, GateImplementationInfo, IQMClient
    from iqm.qiskit_iqm import IQMBackend

    qubits = sorted(model.prx, key=lambda q: int(q[2:]))

    def gate(loci):
        return GateInfo(implementations={"offline_fixture": GateImplementationInfo(loci=tuple(loci))},
                        default_implementation="offline_fixture", override_default_implementation={})

    architecture = DynamicQuantumArchitecture(
        calibration_set_id=UUID(int=0), qubits=qubits, computational_resonators=[],
        gates={"prx": gate([(q,) for q in qubits]), "measure": gate([(q,) for q in qubits]),
               "cc_prx": gate([(q,) for q in qubits]),
               "cz": gate([tuple(sorted(edge)) for edge in model.cz])},
    )

    class OfflineClient:
        quantum_computer_name = "garnet_offline_fixture"

        def get_dynamic_quantum_architecture(self, calibration_set_id=None):
            return architecture

        create_run_request = IQMClient.create_run_request

        def submit_run_request(self, *args, **kwargs):
            raise RuntimeError("An offline fixture cannot submit jobs")

    return configure_garnet_feedback(IQMBackend(OfflineClient(), calibration_set_id=architecture.calibration_set_id))


def live_backend(*, token=None, calibration_set_id=None):
    """Read live architecture; credentials stay in the process environment."""
    from iqm.qiskit_iqm import IQMProvider
    environment_token = os.environ.get("IQM_TOKEN")
    if not token and not environment_token:
        raise ValueError("Set IQM_TOKEN in your shell before using --prepare-live, --submit or --collect")
    # IQM Client rejects mixing constructor auth with environment auth, even
    # when both contain exactly the same token. Use one source exclusively.
    if token and environment_token and token != environment_token:
        raise ValueError("The explicit token differs from IQM_TOKEN; choose one credential source")
    authentication = {} if environment_token else {"token": token}
    provider = IQMProvider(SERVER_URL, quantum_computer="garnet", **authentication)
    backend = provider.get_backend(calibration_set_id=UUID(str(calibration_set_id)) if calibration_set_id else None)
    # Reopen explicitly pinned to the architecture used for transpilation.
    if calibration_set_id is None:
        backend = provider.get_backend(calibration_set_id=backend.architecture.calibration_set_id)
    return configure_garnet_feedback(backend)


def compile_experiment(backend, basis, rounds, model, layout=None):
    layout = selected_layout(basis) if layout is None else layout
    ideal = make_stim_circuit(3, rounds, basis=basis, no_reset=False)
    validate_layout(ideal, layout, model)
    native = build_native_pauli_circuit(ideal, layout, model)
    reference = feedback_reference(native)
    # Fail before transpilation if feedback is not advertised on an ancilla.
    architecture = backend.architecture
    for gate in ("prx", "measure", "cz", "cc_prx"):
        if gate not in architecture.gates:
            raise ValueError(f"Live target does not advertise {gate}; cannot run this reset circuit")
    qc, dense, order = stim_to_qiskit(native.without_noise(), feedback_reset=True)
    physical = [backend.qubit_name_to_index(layout[q]) for q in sorted(dense, key=dense.get)]
    compiled = transpile(qc, backend=backend, initial_layout=physical,
                         routing_method="none", optimization_level=0, seed_transpiler=42)
    compiled.name = f"garnet_d3_{basis.lower()}_r{rounds}"
    expected_name_order = [layout[q] for q in order]
    serialized = backend.serialize_circuit(compiled)
    seen = []
    operations = Counter()
    reset_signals = defaultdict(set)
    for inst in serialized.instructions:
        operations[inst.name] += 1
        if inst.name not in {"prx", "cc_prx", "cz", "measure", "barrier"}:
            raise ValueError(f"Unexpected serialized hardware operation: {inst.name}")
        if inst.name == "measure":
            if len(inst.locus) != 1:
                raise ValueError("Expected individual recorded measurements")
            seen.append(inst.locus[0])
            bit = len(seen) - 1
            if inst.args["key"] != f"c_{len(order)}_0_{bit}":
                raise ValueError("Transpilation changed the measurement/classical-bit assignment")
        elif inst.name == "cc_prx":
            if inst.locus not in architecture.gates["cc_prx"].loci:
                raise ValueError(f"No calibrated feedback rotation on {inst.locus}")
            if inst.args["feedback_qubit"] != inst.locus[0]:
                raise ValueError("Reset feedback must come from the same ancilla")
            reset_signals[inst.locus[0]].add((inst.args["feedback_qubit"], inst.args["feedback_key"]))
    if any(len(signals) != 1 for signals in reset_signals.values()):
        raise ValueError("Garnet permits only one feedback signal label per drive")
    if seen != expected_name_order or compiled.num_clbits != native.num_measurements:
        raise ValueError("Transpilation changed the measurement record or placement")
    if operations["cz"] != 24 * rounds or operations["cc_prx"] != 8 * rounds:
        raise ValueError("Unexpected CZ/reset count; placement or circuit changed")
    expected_prx = sum(len(inst.targets_copy()) for inst in native.without_noise().flattened()
                       if inst.name in {"X", "SQRT_Y", "SQRT_Y_DAG"})
    if operations["prx"] != expected_prx:
        raise ValueError("Transpilation changed the native pulse count; update the simulation model first")
    if compiled.layout.final_index_layout() != physical:
        raise ValueError("Transpilation changed the specified physical placement")
    # Uses SDK static + calibration-dependent validation; never submits a job.
    backend.create_run_request(compiled, shots=1)
    audit = {"basis": basis, "rounds": rounds, "active_qubits": len(dense),
             "num_measurements": len(order), "num_detectors": reference.num_detectors,
             "operations": dict(operations), "swap_count": 0,
             "routing_method": "none", "optimization_level": 0,
             "layout": layout, "stim_to_dense": dense, "measurement_order": order,
             "feedback_signal_policy": "One reusable garnet_reset signal per ancilla; unique stored result keys",
             "depth": compiled.depth(), "calibration_set_id": str(architecture.calibration_set_id)}
    return compiled, reference, audit


def validate_on_aer(circuit, reference, shots=128):
    """Check the mapped Qiskit feedback circuit against Stim detector definitions."""
    from qiskit_aer import AerSimulator
    simulator = AerSimulator(method="stabilizer")
    executable = transpile(circuit, simulator, optimization_level=1)
    result = simulator.run(executable, shots=shots, seed_simulator=734).result()
    if not result.success:
        raise ValueError("Local validation of the compiled circuit failed")
    raw = counts_to_measurement_array(result.get_counts(), reference.num_measurements, shots)
    det, obs = reference.compile_m2d_converter().convert(measurements=raw, separate_observables=True)
    if det.any() or obs.any():
        raise ValueError("Compiled ideal circuit disagrees with its Stim detectors/observable")
    return {"simulator": "Qiskit Aer stabilizer", "shots": shots,
            "detection_events": 0, "logical_flips": 0, "noise": "none"}


def shot_batches(experiments, shots):
    """IQM has one shot count per job. Group equal-sized chunks together."""
    if isinstance(shots, bool) or not isinstance(shots, int) or shots < 1:
        raise ValueError("shots must be a positive integer")
    if not experiments or len(experiments) * shots > MAX_TOTAL_SHOTS:
        raise ValueError(f"Plan must contain experiments and at most {MAX_TOTAL_SHOTS:,} total shots")
    groups = defaultdict(list)
    for experiment in experiments:
        remaining, chunk = shots, 0
        while remaining:
            n = min(remaining, MAX_CIRCUIT_SHOTS)
            groups[n].append({"experiment": experiment, "name": f"{experiment}_chunk{chunk}"})
            remaining -= n
            chunk += 1
    return [{"shots": n, "circuits": members[i:i + MAX_BATCH_CIRCUITS], "state": "prepared"}
            for n, members in groups.items() for i in range(0, len(members), MAX_BATCH_CIRCUITS)]


def prepare_run(output, *, bases=("Z",), rounds=(1, 3, 5), shots=1000,
                live=False, backend=None, baseline_shots=20_000, calibration=CALIBRATION):
    if not bases or len(set(bases)) != len(bases) or set(bases) - {"X", "Z"}:
        raise ValueError("Select distinct X/Z bases")
    if not rounds or len(set(rounds)) != len(rounds):
        raise ValueError("Select distinct positive round counts")
    for r in rounds:
        make_stim_circuit(3, r)  # validates before making a directory or connection
    keys = [f"{basis}_r{r}" for basis in bases for r in rounds]
    batches = shot_batches(keys, shots)
    if isinstance(baseline_shots, bool) or not isinstance(baseline_shots, int) or baseline_shots < 1:
        raise ValueError("baseline_shots must be positive")
    output = Path(output)
    if output.exists():
        raise FileExistsError("Choose a new output directory; existing QPU ledgers are never overwritten")
    model = load_garnet_model(calibration)
    backend = backend or (live_backend() if live else offline_backend(model))
    output.mkdir(parents=True)
    write_json(output / "architecture.json", backend.architecture.model_dump(mode="json"))
    manifest = {"format_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                "device": "garnet", "server_url": SERVER_URL,
                "mode": "live_prepared" if live else "offline_fixture",
                "calibration_set_id": str(backend.architecture.calibration_set_id),
                "distance": 3, "no_reset": False, "shots_per_experiment": shots,
                "total_requested_shots": len(keys) * shots, "batches": batches, "experiments": {},
                "decoder_noise_provenance": model.provenance,
                "noise_note": "Feedback readout faults propagate to reset decisions; other feedback/reset faults use the assumed residual reset probability. Initial R models backend shot initialization. Idle, leakage and correlated hardware noise are not modeled.",
                "versions": {name: importlib.metadata.version(name) for name in
                             ("iqm-client", "qiskit", "qiskit-aer", "stim", "pymatching")}}
    circuits = {}
    for index, (basis, r) in enumerate((b, n) for b in bases for n in rounds):
        key = f"{basis}_r{r}"
        circuit, reference, audit = compile_experiment(backend, basis, r, model)
        audit["ideal_qiskit_validation"] = validate_on_aer(circuit, reference)
        circuits[key] = circuit
        qpy_path = output / f"{key}.qpy"
        with qpy_path.open("wb") as stream:
            qpy.dump(circuit, stream)
        reference.to_file(output / f"{key}.stim")
        reference.detector_error_model(decompose_errors=True).to_file(output / f"{key}.dem")
        baseline, _ = run_monte_carlo(reference, baseline_shots, seed=1_000_000 + index)
        manifest["experiments"][key] = {**audit, "qpy_sha256": sha256(qpy_path),
                                      "stim_sha256": sha256(output / f"{key}.stim"),
                                      "simulation_baseline": baseline}
    for index, batch in enumerate(batches):
        request = backend.create_run_request(_batch_circuits(batch, circuits), shots=batch["shots"])
        write_json(output / f"request-{index}.json", request.model_dump(mode="json"))
    write_json(output / "manifest.json", manifest)
    return manifest, backend, circuits


def _batch_circuits(batch, circuits):
    return [circuits[item["experiment"]].copy(name=item["name"]) for item in batch["circuits"]]


def submit_prepared(output, backend, circuits):
    """Submit once; record IDs immediately. Never retries an uncertain POST."""
    output = Path(output)
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest["mode"] != "live_prepared":
        raise ValueError("Offline fixtures cannot be submitted; prepare against the live backend")
    if str(backend.architecture.calibration_set_id) != manifest["calibration_set_id"]:
        raise ValueError("Backend calibration changed after preparation")
    if any(b["state"] != "prepared" for b in manifest["batches"]):
        raise ValueError("This ledger already attempted submission; use --collect and never blindly resubmit")
    for key, entry in manifest["experiments"].items():
        if sha256(output / f"{key}.qpy") != entry["qpy_sha256"] or sha256(output / f"{key}.stim") != entry["stim_sha256"]:
            raise ValueError("Prepared artifacts changed")
        # Submit exactly the reviewed QPY, rather than a mutable caller object.
        with (output / f"{key}.qpy").open("rb") as stream:
            circuits[key] = qpy.load(stream)[0]
    # Validate every batch before the first paid submission.
    for batch in manifest["batches"]:
        backend.create_run_request(_batch_circuits(batch, circuits), shots=batch["shots"])
    for batch in manifest["batches"]:
        batch["state"] = "submission_uncertain"
        write_json(output / "manifest.json", manifest)
        job = backend.run(_batch_circuits(batch, circuits), shots=batch["shots"])
        batch.update(state="submitted", job_id=str(job.job_id()))
        write_json(output / "manifest.json", manifest)
    return collect_run(output, backend=backend)


def collect_run(output, *, backend=None, timeout=60):
    """Retrieve saved job IDs and decode. This function cannot submit a job."""
    output = Path(output)
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest["mode"] not in {"live_prepared", "hardware_complete"}:
        raise ValueError("This is an offline fixture, not a QPU run")
    backend = backend or live_backend(calibration_set_id=manifest["calibration_set_id"])
    if str(backend.architecture.calibration_set_id) != manifest["calibration_set_id"]:
        raise ValueError("Collection backend does not match the saved calibration")
    for index, batch in enumerate(manifest["batches"]):
        if not batch.get("job_id"):
            raise ValueError("A batch has no job ID. Check Resonance for an uncertain submission; this command never resubmits")
        result_file = output / f"result-{index}.json"
        if not result_file.exists():
            job = backend.retrieve_job(batch["job_id"])
            result = job.result(timeout=timeout, cancel_after_timeout=False)
            if not result.success:
                raise RuntimeError(f"QPU job {batch['job_id']} did not complete successfully")
            # IQM Result contains more SDK objects; store only decoding evidence.
            if len(result.results) != len(batch["circuits"]):
                raise ValueError("Returned circuit count differs from the submitted batch")
            entries = []
            for i, item in enumerate(batch["circuits"]):
                experiment = result.results[i]
                actual_calibration = getattr(experiment, "calibration_set_id", None)
                if str(actual_calibration) != manifest["calibration_set_id"]:
                    raise ValueError("Executed calibration is absent or differs from the pinned calibration")
                header = experiment.header
                name = header.get("name") if isinstance(header, dict) else header.name
                if name != item["name"] or not experiment.success:
                    raise ValueError("Returned circuit name/order or success flag does not match")
                entries.append({**item, "counts": result.get_counts(i),
                                "shots": experiment.shots, "calibration_set_id": str(actual_calibration)})
            write_json(result_file, {"job_id": batch["job_id"], "circuits": entries})
        batch["state"] = "collected"
        write_json(output / "manifest.json", manifest)
    return analyze_saved_results(output)


def analyze_saved_results(output):
    """Decode only verified saved counts, keeping zero failures and actual shots."""
    output = Path(output)
    manifest = json.loads((output / "manifest.json").read_text())
    totals = {key: {"failures": 0, "shots": 0, "detected": 0} for key in manifest["experiments"]}
    for index, batch in enumerate(manifest["batches"]):
        saved = json.loads((output / f"result-{index}.json").read_text())
        if saved["job_id"] != batch["job_id"] or len(saved["circuits"]) != len(batch["circuits"]):
            raise ValueError("Result file does not match the job ledger")
        for expected, actual in zip(batch["circuits"], saved["circuits"]):
            key = expected["experiment"]
            entry = manifest["experiments"][key]
            if any(actual[k] != expected[k] for k in ("name", "experiment")):
                raise ValueError("Result order differs from the job ledger")
            if actual["calibration_set_id"] != manifest["calibration_set_id"] or actual["shots"] != batch["shots"]:
                raise ValueError("Executed calibration or returned shot count changed")
            if sha256(output / f"{key}.stim") != entry["stim_sha256"]:
                raise ValueError("Decoder reference changed after submission")
            reference = stim.Circuit.from_file(output / f"{key}.stim")
            raw = counts_to_measurement_array(actual["counts"], reference.num_measurements, actual["shots"])
            det, obs = reference.compile_m2d_converter().convert(measurements=raw, separate_observables=True)
            stats = decode_samples(reference, det, obs)
            totals[key]["failures"] += stats["failures"]
            totals[key]["shots"] += stats["shots"]
            totals[key]["detected"] += int(np.any(det, axis=1).sum())
            np.savez_compressed(output / f"samples-{index}-{actual['name']}.npz", raw_meas=raw, det_events=det, obs_flips=obs)
    points = []
    for key, total in totals.items():
        if total["shots"] != manifest["shots_per_experiment"]:
            raise ValueError("Incomplete shot total; no complete-run LER will be fitted")
        entry = manifest["experiments"][key]
        points.append({"experiment": key, "basis": entry["basis"], "rounds": entry["rounds"],
                       **binomial_statistics(total["failures"], total["shots"]),
                       "shots_with_detection_events": total["detected"],
                       "simulation_baseline": entry["simulation_baseline"]})
    fits = {}
    for basis in sorted({p["basis"] for p in points}):
        subset = [p for p in points if p["basis"] == basis]
        if len(subset) >= 3:
            fits[basis] = {"challenge": fit_logical_rate(subset), "spam_diagnostic": fit_logical_rate(subset, include_spam=True)}
    report = {"source": "Garnet QPU", "calibration_set_id": manifest["calibration_set_id"],
              "points": points, "fits": fits,
              "decoder_note": "MWPM weights use the saved approximate calibration snapshot, not a measured hardware noise channel."}
    write_json(output / "decoded-results.json", report)
    manifest["mode"] = "hardware_complete"
    write_json(output / "manifest.json", manifest)
    return report
