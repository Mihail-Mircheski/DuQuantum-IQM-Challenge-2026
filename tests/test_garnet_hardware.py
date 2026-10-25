"""Local SDK/Aer integration only. No test can access a paid QPU."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
import stim

pytest.importorskip("iqm.qiskit_iqm")
pytest.importorskip("qiskit_aer")
from qiskit import transpile
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, ReadoutError

from garnet_hardware import (
    CALIBRATION, analyze_saved_results, collect_run, compile_experiment,
    feedback_reference, load_garnet_model, offline_backend, prepare_run,
    selected_layout, sha256, shot_batches, submit_prepared, write_json, live_backend,
    reuse_reset_feedback_labels,
    dd_configuration, compilation_options, verify_returned_dd_options,
)
from internal_helpers import counts_to_measurement_array
from run_on_hardware import decode_hardware_results
from simulation_baseline import decode_samples
from stim_to_qiskit import stim_to_qiskit


@pytest.fixture(scope="module")
def model():
    return load_garnet_model(CALIBRATION)


@pytest.fixture(scope="module")
def backend(model):
    return offline_backend(model)


def aer_raw(qc, shots=128, noise_model=None):
    simulator = AerSimulator(method="stabilizer", noise_model=noise_model)
    executable = transpile(qc, simulator, optimization_level=1)
    result = simulator.run(executable, shots=shots, seed_simulator=19).result()
    assert result.success
    return counts_to_measurement_array(result.get_counts(), qc.num_clbits, shots)


@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("rounds", [1, 3, 5, 10])
def test_native_sdk_compilation_and_aer_decoding(backend, model, basis, rounds):
    qc, reference, audit = compile_experiment(backend, basis, rounds, model)
    assert audit["operations"]["cz"] == 24 * rounds
    assert audit["operations"]["cc_prx"] == 8 * rounds
    assert audit["num_measurements"] == 8 * rounds + 9
    assert audit["active_qubits"] == 17
    assert set(qc.count_ops()) <= {"r", "cz", "if_else", "measure", "barrier"}
    native = backend.serialize_circuit(qc)
    records = [inst.args["key"] for inst in native.instructions if inst.name == "measure"]
    assert len(set(records)) == len(records) == 8 * rounds + 9
    signals = {}
    for inst in native.instructions:
        if inst.name in {"measure", "cc_prx"} and "feedback_key" in inst.args:
            signals.setdefault(inst.locus[0], set()).add(inst.args["feedback_key"])
    assert len(signals) == 8
    assert all(keys == {"garnet_reset"} for keys in signals.values())
    measurement_groups = []
    current_group = 0
    for inst in native.instructions:
        if inst.name == "measure":
            current_group += 1
        elif current_group:
            measurement_groups.append(current_group)
            current_group = 0
    if current_group:
        measurement_groups.append(current_group)
    assert measurement_groups == [8] * rounds + [9]
    raw = aer_raw(qc)
    det, obs = reference.compile_m2d_converter().convert(measurements=raw, separate_observables=True)
    assert not det.any()
    assert not obs.any()
    assert decode_samples(reference, det, obs)["failures"] == 0
    assert len(reference.shortest_graphlike_error()) == 3
    p, error = decode_hardware_results({"det_events": det, "obs_flips": obs, "stim_circuit": reference})
    assert p == error == 0


def test_feedback_reference_propagates_readout_fault():
    native = stim.Circuit("QUBIT_COORDS(0, 0) 0\nR 0\nM(1) 0\nR 0\nM 0")
    # Ideal R would erase the effect; measured-bit feedback resets incorrectly.
    assert not native.compile_sampler(seed=5).sample(10)[:, 1].any()
    feedback = feedback_reference(native)
    assert feedback.compile_sampler(seed=5).sample(10)[:, 1].all()
    assert feedback.num_measurements == native.num_measurements


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_aer_readout_faults_match_stim_feedback(backend, model, basis):
    qc, reference, audit = compile_experiment(backend, basis, 3, model)
    ancilla = audit["measurement_order"][0]
    physical = backend.qubit_name_to_index(selected_layout(basis)[ancilla])
    noise = NoiseModel()
    noise.add_readout_error(ReadoutError([[0, 1], [1, 0]]), [physical])
    raw = aer_raw(qc, noise_model=noise)
    # Make the same deterministic readout fault in the *feedback* Stim circuit.
    faulty = stim.Circuit()
    for instruction in reference.without_noise():
        if instruction.name == "M":
            for target in instruction.targets_copy():
                faulty.append("M", [target], int(target.value == ancilla))
        else:
            faulty.append(instruction)
    converter = reference.compile_m2d_converter()
    actual = converter.convert(measurements=raw, separate_observables=True)
    expected = converter.convert(measurements=faulty.compile_sampler(seed=23).sample(128), separate_observables=True)
    for a, b in zip(actual, expected):
        assert np.array_equal(a, b)
    assert actual[0].any()


def test_missing_feedback_capability_fails_before_submission(backend, model):
    incomplete = Mock(wraps=backend)
    incomplete.architecture = backend.architecture.model_copy(deep=True)
    del incomplete.architecture.gates["cc_prx"]
    with pytest.raises(ValueError, match="cc_prx"):
        compile_experiment(incomplete, "Z", 3, model)
    incomplete.run.assert_not_called()


def test_non_native_mapping_rejected(backend, model):
    bad = selected_layout("Z")
    bad[1], bad[19] = bad[19], bad[1]
    with pytest.raises(ValueError, match="Non-native"):
        compile_experiment(backend, "Z", 3, model, layout=bad)


def test_converter_rejects_noise_or_unmeasured_reset():
    with pytest.raises(NotImplementedError):
        stim_to_qiskit(stim.Circuit("R 0\nX_ERROR(0.1) 0\nM 0"))
    with pytest.raises(ValueError, match="noise"):
        stim_to_qiskit(stim.Circuit("R 0\nM(0.1) 0"))
    with pytest.raises(ValueError, match="fresh Z measurement"):
        stim_to_qiskit(stim.Circuit("R 0\nH 0\nR 0\nM 0"), feedback_reset=True)


@pytest.mark.parametrize("counts,width,shots,expected", [
    ({"001": 2, "110": 1}, 3, 3, [[1, 0, 0], [1, 0, 0], [0, 1, 1]]),
    ({"01 10": 1}, 4, 1, [[0, 1, 1, 0]]),
    ({"0x1": 1}, 4, 1, [[1, 0, 0, 0]]),
])
def test_counts_record_endianness(counts, width, shots, expected):
    assert np.array_equal(counts_to_measurement_array(counts, width, shots), expected)


@pytest.mark.parametrize("counts,width,shots", [
    ({"0": 1}, 1, 2), ({"000": 2}, 2, 2), ({"10q": 1}, 3, 1),
    ({"1": -1}, 1, 1), ({"1": 1.5}, 1, 1), ({"1": True}, 1, 1),
    ({"0x8": 1}, 3, 1), ({"0xg": 1}, 3, 1), ({}, 3, 1),
])
def test_counts_fail_instead_of_padding_or_truncating(counts, width, shots):
    with pytest.raises(ValueError):
        counts_to_measurement_array(counts, width, shots)


def test_shot_chunking_and_limits():
    batches = shot_batches(["Z_r1", "Z_r3"], 45_000)
    assert [b["shots"] for b in batches] == [20_000, 5000]
    assert [len(b["circuits"]) for b in batches] == [4, 2]
    assert sum(b["shots"] * len(b["circuits"]) for b in batches) == 90_000
    assert max(len(b["circuits"]) for b in shot_batches([f"Z_r{r}" for r in range(150)], 10)) == 100
    with pytest.raises(ValueError):
        shot_batches(["Z_r3"], 2_000_001)


def test_default_preparation_is_offline_and_unsubmittable(tmp_path, backend):
    output = tmp_path / "run"
    with patch.object(backend, "run") as run:
        manifest, _, circuits = prepare_run(output, rounds=(1,), shots=10, baseline_shots=128, backend=backend)
        run.assert_not_called()
    assert manifest["mode"] == "offline_fixture"
    assert (output / "request-0.json").exists()
    assert manifest["experiments"]["Z_r1"]["simulation_baseline"]["shots"] == 128
    with pytest.raises(ValueError, match="Offline"):
        submit_prepared(output, backend, circuits)
    with pytest.raises(FileExistsError):
        prepare_run(output, backend=backend)


def local_live_fixture(output, backend, rounds=(1,), shots=16):
    # Explicit test injection: a local SDK target, not a live backend.
    manifest, _, circuits = prepare_run(output, rounds=rounds, shots=shots, baseline_shots=64,
                                        live=True, backend=backend)
    return manifest, circuits


def test_uncertain_submission_is_not_retried(tmp_path, backend):
    output = tmp_path / "uncertain"
    manifest, circuits = local_live_fixture(output, backend)
    wrapped = Mock(wraps=backend)
    wrapped.architecture = backend.architecture
    wrapped.run.side_effect = TimeoutError("POST response lost")
    with pytest.raises(TimeoutError):
        submit_prepared(output, wrapped, circuits)
    saved = json.loads((output / "manifest.json").read_text())
    assert saved["batches"][0]["state"] == "submission_uncertain"
    with pytest.raises(ValueError, match="already attempted"):
        submit_prepared(output, wrapped, circuits)
    assert wrapped.run.call_count == 1
    with pytest.raises(ValueError, match="no job ID"):
        collect_run(output, backend=wrapped)
    assert wrapped.run.call_count == 1


def test_qpy_tampering_rejected_before_submission(tmp_path, backend):
    output = tmp_path / "tampered"
    _, circuits = local_live_fixture(output, backend)
    (output / "Z_r1.qpy").write_bytes(b"changed")
    wrapped = Mock(wraps=backend)
    wrapped.architecture = backend.architecture
    with pytest.raises(ValueError, match="artifacts changed"):
        submit_prepared(output, wrapped, circuits)
    wrapped.run.assert_not_called()


@pytest.mark.parametrize("dictionary_payload", [False, True])
def test_saved_results_decode_and_recover_without_submission(tmp_path, backend, dictionary_payload):
    output = tmp_path / "completed"
    manifest, circuits = local_live_fixture(output, backend, rounds=(1, 3, 5))
    batch = manifest["batches"][0]
    batch.update(state="submitted", job_id="local-test-job")
    write_json(output / "manifest.json", manifest)
    # Match the IQM/Qiskit Result interface without any server access.
    simulator = AerSimulator(method="stabilizer")
    tq = transpile([circuits[item["experiment"]].copy(name=item["name"]) for item in batch["circuits"]],
                   simulator, optimization_level=1)
    aer_result = simulator.run(tq, shots=batch["shots"], seed_simulator=31).result()
    for entry in aer_result.results:
        entry.calibration_set_id = manifest["calibration_set_id"]
    if dictionary_payload:
        aer_result.circuits = json.loads((output / "request-0.json").read_text())["circuits"]
        for native in aer_result.circuits:
            for inst in native["instructions"]:
                inst["locus"] = tuple(inst["locus"])
            native["instructions"] = tuple(native["instructions"])
        for i, entry in enumerate(aer_result.results):
            entry.header["name"] = f"circuit_{i}"
    job = Mock()
    job.result.return_value = aer_result
    wrapped = Mock(wraps=backend)
    wrapped.architecture = backend.architecture
    wrapped.retrieve_job.return_value = job
    report = collect_run(output, backend=wrapped)
    assert all(p["failures"] == 0 and p["shots"] == 16 for p in report["points"])
    assert "Z" in report["fits"]
    assert list(output.glob("samples-*.npz"))
    if dictionary_payload:
        saved = json.loads((output / "result-0.json").read_text())
        assert all(c["native_payload_verified"] for c in saved["circuits"])
        assert saved["circuits"][0]["returned_header_name"] == "circuit_0"
    job.result.assert_called_once_with(timeout=60, cancel_after_timeout=False)
    wrapped.run.assert_not_called()
    wrapped.retrieve_job.reset_mock()
    assert collect_run(output, backend=wrapped) == report
    wrapped.retrieve_job.assert_not_called()
    wrapped.run.assert_not_called()
    # Missing shots cannot silently become zero-filled valid data.
    saved = json.loads((output / "result-0.json").read_text())
    first_key = next(iter(saved["circuits"][0]["counts"]))
    saved["circuits"][0]["counts"][first_key] -= 1
    write_json(output / "result-0.json", saved)
    with pytest.raises(ValueError, match="shot count"):
        analyze_saved_results(output)


def test_submission_saves_job_id_before_waiting_and_recovers(tmp_path, backend):
    output = tmp_path / "submit"
    manifest, circuits = local_live_fixture(output, backend, shots=8)
    batch = manifest["batches"][0]
    simulator = AerSimulator(method="stabilizer")
    tq = transpile(circuits["Z_r1"].copy(name=batch["circuits"][0]["name"]), simulator, optimization_level=1)
    result = simulator.run(tq, shots=8, seed_simulator=33).result()
    result.results[0].calibration_set_id = manifest["calibration_set_id"]
    job = Mock()
    job.job_id.return_value = "test-submitted-job"

    def result_after_saved_id(**kwargs):
        saved = json.loads((output / "manifest.json").read_text())
        assert saved["batches"][0]["job_id"] == "test-submitted-job"
        assert saved["batches"][0]["state"] == "submitted"
        return result

    job.result.side_effect = result_after_saved_id
    with patch.object(backend, "run", return_value=job) as run, \
         patch.object(backend, "retrieve_job", return_value=job):
        report = submit_prepared(output, backend, circuits)
    assert run.call_count == 1
    assert report["points"][0]["shots"] == 8
    assert json.loads((output / "manifest.json").read_text())["mode"] == "hardware_complete"


def test_collection_rejects_wrong_executed_calibration(tmp_path, backend):
    output = tmp_path / "wrong-calibration"
    manifest, circuits = local_live_fixture(output, backend, shots=8)
    batch = manifest["batches"][0]
    batch.update(state="submitted", job_id="test-wrong-cal")
    write_json(output / "manifest.json", manifest)
    simulator = AerSimulator(method="stabilizer")
    result = simulator.run(transpile(circuits["Z_r1"], simulator, optimization_level=1), shots=8).result()
    result.results[0].calibration_set_id = "different-calibration"
    job = Mock()
    job.result.return_value = result
    with patch.object(backend, "retrieve_job", return_value=job), patch.object(backend, "run") as run:
        with pytest.raises(ValueError, match="Executed calibration"):
            collect_run(output, backend=backend)
    run.assert_not_called()
    assert not (output / "decoded-results.json").exists()


@pytest.mark.parametrize("explicit,environment", [
    (None, "local-test-token"),
    ("local-test-token", "local-test-token"),
    ("local-test-token", None),
])
def test_live_backend_uses_one_authentication_source(monkeypatch, backend, explicit, environment):
    if environment:
        monkeypatch.setenv("IQM_TOKEN", environment)
    else:
        monkeypatch.delenv("IQM_TOKEN", raising=False)
    with patch("iqm.qiskit_iqm.IQMProvider") as provider:
        provider.return_value.get_backend.return_value = backend
        assert live_backend(token=explicit) is backend
        expected = {} if environment else {"token": explicit}
        provider.assert_called_once_with("https://resonance.iqm.tech", quantum_computer="garnet", **expected)


def test_live_backend_rejects_conflicting_authentication(monkeypatch):
    monkeypatch.setenv("IQM_TOKEN", "first-local-test-token")
    with patch("iqm.qiskit_iqm.IQMProvider") as provider:
        with pytest.raises(ValueError, match="credential source"):
            live_backend(token="second-local-test-token")
        provider.assert_not_called()


def test_shared_feedback_rejects_stale_or_cross_qubit_results():
    def instruction(name, q, **args):
        return SimpleNamespace(name=name, locus=(q,), args=args)

    for source, key in [("QB1", "old"), ("QB2", "recent")]:
        native = SimpleNamespace(instructions=[
            instruction("measure", "QB1", key="record0", feedback_key="old"),
            instruction("measure", "QB1", key="record1", feedback_key="recent"),
            instruction("cc_prx", "QB1", feedback_qubit=source, feedback_key=key),
        ])
        with pytest.raises(ValueError, match="Shared reset feedback"):
            reuse_reset_feedback_labels(native)


def test_generic_result_names_require_matching_native_instructions(tmp_path, backend):
    output = tmp_path / "wrong-native-payload"
    manifest, circuits = local_live_fixture(output, backend, shots=8)
    batch = manifest["batches"][0]
    batch.update(state="submitted", job_id="test-wrong-payload")
    write_json(output / "manifest.json", manifest)
    simulator = AerSimulator(method="stabilizer")
    result = simulator.run(transpile(circuits["Z_r1"], simulator, optimization_level=1), shots=8).result()
    result.results[0].calibration_set_id = manifest["calibration_set_id"]
    result.results[0].header["name"] = "circuit_0"
    result.circuits = json.loads((output / "request-0.json").read_text())["circuits"]
    result.circuits[0]["instructions"][0]["locus"] = ["QB1"]
    job = Mock()
    job.result.return_value = result
    with patch.object(backend, "retrieve_job", return_value=job), patch.object(backend, "run") as run:
        with pytest.raises(ValueError, match="native circuit identity"):
            collect_run(output, backend=backend)
    run.assert_not_called()
    assert not (output / "decoded-results.json").exists()


@pytest.mark.parametrize("profile,patterns", [
    ("off", None), ("xx", ["XX"]), ("xy4", ["YXYX"]),
    ("standard", ["XYXYYXYX", "YXYX", "XX"]),
])
def test_dd_profiles_and_ideal_identity(profile, patterns):
    config = dd_configuration(profile, {"QB2", "QB10"})
    options = compilation_options(config)
    if profile == "off":
        assert options.dd_mode.value == "disabled" and options.dd_strategy is None
    else:
        assert options.dd_mode.value == "enabled"
        assert options.dd_strategy.target_qubits == {"QB2", "QB10"}
        assert config["dd_strategy"]["target_qubits"] == ["QB10", "QB2"]
        assert [p[1] for p in options.dd_strategy.gate_sequences] == patterns
        for pattern in patterns:
            sequence = stim.Circuit()
            for gate in pattern:
                sequence.append(gate, [0])
            assert sequence.to_tableau() == stim.Tableau(1)


def test_dd_invalid_configuration():
    with pytest.raises(ValueError, match="dd must"):
        dd_configuration("invalid", {"QB1"})
    with pytest.raises(ValueError, match="physical data"):
        dd_configuration("xx", set())
    with pytest.raises(ValueError, match="explicit saved strategy"):
        compilation_options({"dd_mode": "enabled", "dd_strategy": None})


def test_dd_options_preserved_from_preparation_to_submission(tmp_path, backend):
    output = tmp_path / "dd-prepared"
    manifest, _, circuits = prepare_run(output, bases=("Z", "X"), rounds=(1,), shots=8,
                                        live=True, backend=backend, baseline_shots=64, dd="xx")
    config = manifest["compilation_options"]
    expected_data = {"QB2", "QB4", "QB6", "QB8", "QB10", "QB12", "QB14", "QB16", "QB19"}
    assert config["dd_mode"] == "enabled"
    assert set(config["dd_strategy"]["target_qubits"]) == expected_data
    request = json.loads((output / "request-0.json").read_text())
    assert {k: request[k] for k in config} == config
    assert all(not e["simulation_baseline"]["dd_effect_modeled"] for e in manifest["experiments"].values())
    wrapped = Mock(wraps=backend)
    wrapped.architecture = backend.architecture
    wrapped.run.side_effect = TimeoutError("local transport simulation")
    with pytest.raises(TimeoutError):
        submit_prepared(output, wrapped, circuits)
    options = wrapped.run.call_args.kwargs["circuit_compilation_options"]
    assert options.dd_mode.value == "enabled"
    assert options.dd_strategy.target_qubits == expected_data


def test_changed_dd_options_rejected_before_submission(tmp_path, backend):
    output = tmp_path / "changed-dd"
    manifest, _, circuits = prepare_run(output, rounds=(1,), shots=8, live=True,
                                        backend=backend, baseline_shots=64, dd="xx")
    manifest["compilation_options"] = {"dd_mode": "disabled", "dd_strategy": None}
    write_json(output / "manifest.json", manifest)
    wrapped = Mock(wraps=backend)
    wrapped.architecture = backend.architecture
    with pytest.raises(ValueError, match="compilation options differ"):
        submit_prepared(output, wrapped, circuits)
    wrapped.run.assert_not_called()


def test_returned_dd_flags_are_verified():
    config = dd_configuration("xx", {"QB2", "QB10"})
    options = compilation_options(config)
    assert verify_returned_dd_options(SimpleNamespace(parameters=options), config) == config
    with pytest.raises(ValueError, match="options differ"):
        verify_returned_dd_options(SimpleNamespace(parameters=compilation_options()), config)
    with pytest.raises(ValueError, match="No returned compilation"):
        verify_returned_dd_options(SimpleNamespace(), config)
