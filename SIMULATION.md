# Local surface-code simulation and Garnet baseline

This implements `make_stim_circuit()` from the challenge README. It returns a
`stim.Circuit` for a rotated surface-code memory experiment. All examples here
run in a local simulator. No IQM token, hardware SDK, or QPU submission is used.

The intended later hardware target is Garnet. The default is **ancilla reset
between rounds** (`no_reset=False`), as requested. No hardware support is inferred
from the simulator results.

## Install the simulator dependencies

Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-sim.txt
```

The full challenge `requirements.txt` is not needed for these local simulations.

## Full decoded baseline and mapping optimization

The complete local entry point is:

```bash
python run_simulation_baseline.py --basis Z --shots 100000 --screen-shots 10000 --validation-shots 100000 --output output/garnet-baseline-z
python run_simulation_baseline.py --basis X --shots 100000 --screen-shots 10000 --validation-shots 100000 --output output/garnet-baseline-x
```

It runs this chain, with ancilla resets enabled by default:

```text
make_stim_circuit -> native Clifford/CZ decomposition -> physical-qubit Pauli noise
-> raw Monte Carlo measurements -> measurement-to-detector conversion
-> PyMatching MWPM -> decoded failure counts -> round sweep -> logical-rate fit
```

The model reads the downloaded `calibrations/garnet-2026-10-24.json` snapshot.
The Garnet features were viewed directly in authenticated Google Chrome;
`calibrations/garnet-features-2026-10-24.json` records that dated observation.
The source file and its SHA-256 hash are recorded in each result. No live API,
IQM token, Qiskit SDK, or QPU execution is involved in this command.

The export supplies 20 per-qubit PRX/readout metrics and 30 CZ-coupler metrics.
The code enumerates native-edge embeddings from those couplers; this snapshot
admits four embeddings for the 17-qubit distance-3 circuit. All four put the
center on QB10 and leave QB3, QB17, and QB18 unused.

For each placement, the simulator uses the same ideal stabilizer schedule and
measurement record. CNOTs are decomposed into H-CZ-H; each H is SQRT_Y followed
by X, both native equatorial Clifford rotations. X-basis preparation/readout
use one appropriate equatorial rotation. This is an explicit, unoptimized
native-gate model, **not the output of the IQM Qiskit compiler**. A later hardware
compiler must be checked against this model before claiming a matched baseline.

### Noise-model assumptions

- Each PRX and CZ gets its physical qubit/coupler's benchmark value. Reported
  average gate infidelity r is approximated by a uniform nonidentity Pauli
  probability p=3r/2 for PRX and p=5r/4 for CZ.
- Standard `measure.constant` assignment error is approximated by a symmetric
  classical measurement flip; asymmetry and correlations are not reproduced.
- Active-reset error is **assumed**, not obtained from the snapshot. The default
  is 0.003; set `--reset-error` to perform a sensitivity study.
- T1/T2 values are available in the export, but operation durations are not.
  Idle decoherence, leakage, coherent error, crosstalk and measurement disturbance
  are not modeled. The exported leakage/QND metrics are not silently treated as
  equivalent Pauli-error probabilities.
- `--noise-scale 0` gives a noiseless control; other scales are hypothetical
  noise-sensitivity experiments, not new hardware calibrations.

Thus, this is a **calibration-informed Pauli approximation**. Its logical failure
probabilities are simulation measurements under that model, not measured QPU
logical errors or validated predictions of Garnet's performance.

### Monte Carlo search and simulated annealing

The search objective is the decoded failure probability at `--search-rounds`
(default 3), rather than an uncorrected logical-bit flip fraction. Simulated
annealing proposes another fully valid placement; every candidate respects
native edges. Screening results are cached. Identical noisy circuits share a
score, preventing random samples from falsely favoring a mere relabeling.

With only four candidates, exhaustive screening is also recorded as a reference.
Annealing is included as a reusable search interface, not as a claim that it is
necessary for this small space. A selected candidate is frozen before collecting
independent holdout shots. A one-sided Fisher exact test is used for that single
held-out candidate-versus-baseline comparison. If improvement is not established,
the recommendation retains the baseline. Search, holdout and sweep seeds differ.

### Failure probability versus logical error rate

For each round count R, the decoder's prediction is XOR-compared with the actual
logical observable. The fraction of mismatches estimates P_error(R). The report
includes 95% Wilson intervals, which retain a nonzero upper bound even when no
failures are seen.

The required challenge model is fitted directly to binomial counts:

```text
P_error(R) = [1 - (1 - 2 ε_L)^R] / 2
```

The fit returns ε_L, an approximate 95% profile-likelihood interval, and a
goodness-of-fit diagnostic. A second diagnostic allows a preparation/readout
offset b:

```text
P_error(R) = [1 - (1 - 2 b) (1 - 2 ε_L)^R] / 2
```

The report flags poor fits. A narrow interval from an inadequate model is not
evidence for an accurate constant per-round rate. Do not interpret fixed-round
failure probability, raw observable flip fraction and fitted ε_L interchangeably.

### Reproducible outputs

Each output directory contains:

- `report.md`: baseline, search/holdout decision, fits and limitations.
- `summary.json`: shot/failure counts, confidence intervals, placement maps,
  annealing trace, seeds, software versions and calibration provenance.
- `points.csv`: decoded failure probability versus round count.
- `logical_failure_vs_rounds.png`: points with confidence intervals and both fits.
- `circuits/*.stim` and `circuits/*.dem`: exact noisy simulator circuits and
  their matching detector error models.
- `circuits/*_sample.npz`: example raw/detector/observable samples for debugging.

The decoder lives in `simulation_baseline.py`, physical model and graph search
in `garnet_model.py`, rate fit in `logical_rate_fit.py`, and annealing in
`mapping_search.py`. The hardware submission functions in the starter remain
separate and are never imported by this pipeline.

For a quick check, reduce `--shots`, `--screen-shots`, and `--validation-shots`.
Use larger independent samples to resolve close candidates. Fixed seeds are
reproducible for the recorded software, batch size and execution environment;
Stim sampling is not guaranteed bit-for-bit across versions or CPU types.

## Use the generator

```python
from surface_code import DEFAULT_NOISE, get_circuit_info, make_stim_circuit

# Noiseless Z memory: initialize logical |0>, measure checks three times,
# then destructively measure the data in Z.
circuit = make_stim_circuit(distance=3, rounds=3, basis="Z")
print(circuit)
print(get_circuit_info(circuit))

# X memory stores logical |+> and ends with X-basis data measurements.
x_memory = make_stim_circuit(distance=3, rounds=3, basis="X")

# Optional illustrative noise. Individual probabilities can be set separately.
noisy = make_stim_circuit(3, 3, "Z", noise=DEFAULT_NOISE)

# Ideal detectors and the logical observable must all be quiet.
dets, obs = circuit.compile_detector_sampler(seed=42).sample(
    shots=1024, separate_observables=True
)
assert not dets.any()
assert not obs.any()
```

Distance is an odd integer at least 3; rounds is a positive integer. Both basis
names are case insensitive. For distance d, there are d² data qubits and d²−1
ancillas. For d=3 and three rounds:

- 9 data qubits and 8 ancillas: **17 active qubits**.
- 8 × 3 + 9 = **33 measurement records**.
- **24 detectors** and **one logical observable**.

Stim uses sparse geometric labels. Its `circuit.num_qubits` is 26 at d=3 because
the highest index is 25. This does **not** mean the code needs 26 physical qubits.
`get_circuit_info(circuit)["num_active_qubits"]` reports 17. Later hardware
conversion must map those 17 used labels onto physical qubits.

## Run and inspect a local experiment

```bash
python simulate_surface_code.py --distance 3 --rounds 3 --basis Z --shots 1024
python simulate_surface_code.py --basis X --noise 0.001 --shots 10000 --output output/x-noisy
```

The command saves the following in its output directory (default `output/step1`):

- `circuit.stim`: generated circuit, including detector and observable annotations.
- `timeline.svg`: circuit drawing that can be opened in a browser.
- `metadata.json`: parameters, coordinates, qubit roles, measurement order and summary.
- `samples.npz`: `raw_meas`, `det_events`, and `obs_flips` arrays. These have the
  same record order and shapes expected by `extract_syndromes()` in the starter.

Each run replaces these files in the selected output directory; choose a new
`--output` directory to retain a previous run.

The reported observable flip fraction is **before decoding**. It is neither a
decoded logical failure probability nor a fitted logical error rate per round.
Use `run_simulation_baseline.py` above for the decoded probability and rate fit.

## Noise and the optional no-reset switch

`noise=None` is noiseless. A noise mapping accepts the four standard Stim keys:
`after_clifford_depolarization`, `before_round_data_depolarization`,
`before_measure_flip_probability`, and `after_reset_flip_probability`.
Omitted keys default to zero. `DEFAULT_NOISE` assigns 0.003 to each: this is an
illustrative Pauli model, **not** a fit to Garnet's calibration. T1/T2 evolution,
leakage, crosstalk, readout disturbance and routing overhead are not modeled.

The README's `no_reset` argument remains available, but it is optional here:

```python
optional_variant = make_stim_circuit(3, 3, "Z", no_reset=True)
```

This variant keeps initial preparations, removes subsequent ancilla resets and
rewrites all detector parities. The same syndrome-change check uses raw records
two rounds apart in interior rounds. The first rounds and final boundary are
handled by substituting each reference syndrome with the XOR of its current
and previous raw ancilla readouts. Reset noise is applied only to preparations
that remain. Loops are unrolled to perform this bookkeeping.

## Verify correctness

```bash
python -m pip install -r requirements-sim-dev.txt
python -m pytest -q
```

Tests cover noiseless X/Z memory at distances 3 and 5, initial and final detector
boundaries, raw measurement conversion, controlled single-qubit faults, a
logical string that flips the observable without detection, the graphlike
distance under the configured noise, and reset/no-reset readout correlations.

This implements the **Stim starting point** offered in step 1. The alternative
Qiskit-native and Qrisp-native generators remain explicitly unimplemented. The
provided converters have separate limitations for X-basis preparation/readout;
this simulator implementation does not claim to repair those converters.

References: [Stim generator API](https://github.com/quantumlib/Stim/blob/main/doc/python_api_reference_vDev.md#stimcircuitgenerated)
and the challenge README's “What Needs To Be Built”, item 1.
