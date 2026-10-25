# Running the distance-3 memory experiment on Garnet

The Qiskit execution path is implemented in `run_garnet.py` and
`garnet_hardware.py`. It uses **Garnet with ancilla resets** (`no_reset=False`).
The default command prepares and simulates locally. `--prepare-live` authenticates
and validates against Garnet's current architecture without submitting a job.
**`--submit` submits paid QPU jobs.**

## Installation

From this repository, using Python 3.11 or later:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-hardware.txt
```

This path pins IQM Client 35.0.3, Qiskit 2.1.2 and Aer 0.17.2. Do not upgrade
Qiskit independently: this IQM Client version is incompatible with Qiskit 2.2's
Target API. The original challenge's `requirements.txt` is not the installation
recipe for this path.

## Prepare and inspect locally

```sh
python run_garnet.py --basis both --rounds 1 3 5 7 10 \
  --shots 1000 --out output/garnet-offline
```

No credentials or network connection are needed. Each experiment is compiled
against an **offline capability fixture** with the 20-qubit topology from the
downloaded Garnet snapshot. Feedback support on every qubit is assumed in this
fixture. This does not establish that the same operations are currently
calibrated on the real device.

Preparation checks the compiled circuit in ideal Qiskit Aer, then samples 20,000
noisy Stim shots per experiment through the raw-measurement, detector and MWPM
pipeline. Change that local sample count with `--baseline-shots`.

The output directory contains:

- `manifest.json`: placement, measurement order, operation counts, software
  versions, calibration provenance, local checks, baseline probabilities and
  planned shot batches.
- `architecture.json` and `request-*.json`: the target and exact native IQM
  requests, with no authentication credentials.
- `Z_r3.qpy` / `X_r3.qpy`, etc.: the compiled Qiskit circuits.
- Matching `.stim` and `.dem` files: the feedback-aware noisy reference and
  detector error model used for decoding.

Directories must be new. Existing experiment ledgers are never overwritten.

## Validate against the live device

Set `IQM_TOKEN` in your shell using your Resonance API token. For zsh, this avoids
putting the token in shell history:

```sh
read -r -s 'IQM_TOKEN?IQM API token: '; export IQM_TOKEN
python run_garnet.py --prepare-live --basis both --rounds 1 3 5 \
  --shots 100 --out output/garnet-live-check
```

The provider connects to `https://resonance.iqm.tech` with
`quantum_computer="garnet"`, retrieves the dynamic architecture, and pins its
calibration-set UUID. It checks calibrated PRX, measurement, CZ and conditional
PRX availability and invokes the SDK's `create_run_request` validation. There is
no QPU submission in this mode. A token is not saved to the run directory.

If the live architecture lacks a required edge or feedback operation, preparation
fails. Investigate that failure before running; the code does not fall back to
different qubits or silently change to a no-reset experiment.

## Execute a small experiment

After inspecting preparation results, an explicit submission command is:

```sh
python run_garnet.py --submit --basis Z --rounds 1 3 5 \
  --shots 100 --out output/garnet-first-qpu
```

This requests 300 total QPU shots. Increase statistics only after inspecting the
first results. Use `--basis both` to measure X and Z memories; each uses its own
saved candidate placement. Distance 3 is fixed because the next supported odd
distance requires 49 qubits, exceeding Garnet's 20 qubits.

Shot plans are limited conservatively to the limits recorded on 2026-10-24:
20,000 shots per circuit, 100 circuits per batch, and 2,000,000 total shots per
invocation. Larger per-experiment shot counts are split into circuits and batches
with the correct common shot count per IQM job. Check current service limits if
the backend rejects a request.

Job IDs are recorded immediately after each submission returns. Before a POST,
the ledger records `submission_uncertain`; if the response is lost, the code
stops without automatically retrying and risking duplicate paid work. Check
Resonance for the job and associate a recovered ID with that batch before using
collection. Batches without IDs are never automatically submitted by recovery.

## Collect after an interruption

```sh
python run_garnet.py --collect output/garnet-first-qpu --timeout 60
```

Collection retrieves saved job IDs and never submits or cancels a job. If a job
is still queued, run collection again later. Previously saved results are reused.

The result path validates circuit names/order, executed calibration UUID,
measurement widths and exact shot totals. It reverses Qiskit count strings into
Stim record order, extracts detectors and the logical observable, and decodes
with PyMatching. Counts are sufficient for this analysis; expanding them groups
identical records and does not preserve chronological shot order.

IQM Client 35 can return generic Qiskit headers (`circuit_0`, etc.) when native
payloads are dictionaries. The collector accepts those headers only after
matching every returned native circuit name and instruction sequence to the
saved request. Original headers and the verification flag remain in the result
evidence; a mismatched native circuit is rejected.

`result-*.json` stores count evidence and `samples-*.npz` stores reconstructed raw
measurements, detectors and observable flips. `decoded-results.json` reports
failure counts, decoded failure probabilities, Wilson 95% intervals and the
matching simulation baseline for each round count. With at least three distinct
round counts per basis, it also fits the challenge's per-round logical error rate
and a preparation/readout-offset diagnostic, including goodness-of-fit checks.
One round count gives a failure probability, not a fitted per-round LER.

## Mapping, compilation and reset semantics

The saved X/Z candidate maps in `calibrations/garnet-selected-layouts.json` came
from the earlier independent simulator holdouts. They are native-edge embeddings
and starting candidates for hardware. They have not been reoptimized for this
feedback-reset model or demonstrated to be best on the real QPU.

The 17 active Stim indices are first densely numbered for Qiskit. Physical
indices are obtained from the backend's qubit-name lookup, rather than assuming
`QB1` corresponds to a particular integer. Every interaction is already native:
transpilation uses the explicit placement, `routing_method="none"`, and
`optimization_level=0`. It fails if routing would be needed. Audits require the
same final placement, native pulse count, measurement-to-classical-bit assignment,
24 CZs and eight ancilla resets per round. TICKs become barriers. Native
decomposition uses equatorial R/PRX pulses and CZ. Dynamical decoupling defaults
to disabled and is configurable with `--dd`.

Initial R operations rely on IQM's per-shot qubit initialization by relaxation
(`active_reset_cycles=None` in the pinned SDK's compilation defaults). Each subsequent
ancilla reset uses the ancilla's just-recorded Z measurement and a conditional
X, serialized as `cc_prx`. It does not insert another measurement or change the
Stim record length: there are `8 * rounds + 9` recorded bits. The logical
observable is Z parity for a Z-memory experiment and X parity for X memory.

Garnet's instrument compiler permits only one feedback signal label per drive.
Serialization therefore reuses `feedback_key="garnet_reset"` for each ancilla
across rounds. Every measurement retains its unique result `key`, so all
syndrome bits are still saved. Each conditional X uses the latest measurement of
that same ancilla. This policy applies identically to saved requests and actual
submission; changing Qiskit's classical bits to reuse storage would lose records.

Measurement groups are bracketed with barriers before applying conditional
resets. This preserves each eight-ancilla readout group through transpilation
and enables IQM's automatic readout multiplexing where supported. The first
completed hardware run predated this barrier correction; its saved QPY and
request remain unchanged. Gate-list grouping alone does not establish the
resulting pulse schedule or its contribution to the observed failure rate.

The noisy reference explicitly uses record-controlled X for those resets, so a
reported readout flip can also cause a wrong reset. This differs from the earlier
baseline's independent ideal R. PRX/CZ/readout weights still use the saved
2026-10-24 quality metrics; residual feedback/reset error is assumed to be 0.003.
The residual reset error includes imperfections of the conditional operation;
there is no separately calibrated conditional-X error channel. Initialization
uses the same assumed reset-error probability. Idle T1/T2 noise, leakage,
crosstalk and coherent errors remain excluded. The live gate calibration UUID
and the older benchmark-snapshot provenance are saved separately. Statistical
intervals do not cover these noise-model uncertainties.

## Python API and tests

`run_on_hardware.run_qiskit(..., quantum_computer="garnet", no_reset=False,
basis="Z")` executes and returns syndromes, the reference circuit, decoded
statistics and the saved output location. **Calling this function submits jobs.**
`decode_hardware_results` returns the decoded failure probability and binomial
standard error for that round count. The Qrisp execution path remains
unimplemented and raises an explicit error.

```sh
python -m pip install -r requirements-sim-dev.txt
python -m pytest -q
```

Hardware integration tests use the local IQM SDK target, Qiskit Aer and mocked
job transport. They exercise both bases, measurement/reset feedback faults,
zero-SWAP compilation, result recovery, shot limits and decoding. They never
contact or submit to a QPU.

SDK reference: [IQM Client Qiskit guide](https://docs.iqm.tech/iqm-client/user_guide_qiskit.html).
The tested pinned SDK API is `create_run_request`; newer documentation may name
the preparation method differently.

## Number of rounds and dynamical decoupling

Distance and round count are independent. Distance 3 describes the spatial code;
`--rounds 1` is a valid single-round experiment. A single round measures the
combined preparation, syndrome extraction and final-readout failure probability.
Repeated rounds also test the ancilla-reset cycle and preserve a history for
decoding time-dependent faults. A sweep across round counts is needed to fit a
per-round memory error rate while checking initialization/readout effects.
The first QPU run used 1, 2, 3, 5, 7 and 10 rounds, not only three rounds.

The DD profiles are:

| CLI option | Server pulse strategy | Protected qubits |
|---|---|---|
| `--dd off` | Disabled (default) | None |
| `--dd xx` | Two centered X pulses per sufficiently long idle window | The nine physical data qubits |
| `--dd xy4` | Centered YXYX sequence in longer idle windows | The nine physical data qubits |
| `--dd standard` | SDK standard EDD/XY4/XX selection according to window size | The nine physical data qubits |

The paired pulses have net identity in an ideal circuit. The server compiler
places them in eligible physical idle windows; they are not appended blindly
between Stim TICKs. Default settings skip leading and trailing waits. Reset
ancillas are excluded from the DD target set. DD can be requested through the
circuit-level `CircuitCompilationOptions` API.

Start with a small, matched comparison after the measurement-grouping correction:

```sh
# Both commands prepare locally and submit no QPU jobs.
python run_garnet.py --basis both --rounds 1 2 3 --shots 500 \
  --dd off --out output/dd-control-preview
python run_garnet.py --basis both --rounds 1 2 3 --shots 500 \
  --dd xx --out output/dd-xx-preview
```

Add `--prepare-live` to validate against the authenticated architecture. Actual
execution still requires `--submit`. For an experimental comparison use the
same corrected circuits, placements, calibration UUID, round counts and shot
counts in both arms. Comparing DD-on with the old run would confound DD with the
measurement-grouping change. Short 1/2/3-round circuits are useful first because
the old X-memory run was already near random-guess failure by two rounds.

DD mode and the full strategy are saved in the manifest and each native request.
Submission reconstructs those exact options, checks the saved request hash and
rejects mismatched options before sending any job. Collection verifies returned
DD parameters when DD is enabled. A requested/enabled flag does **not** establish
that any eligible windows existed or that DD pulses were actually inserted;
that requires pulse-schedule evidence or a controlled hardware experiment.

DD can suppress some slow phase noise and unwanted coherent interactions. Extra
PRX pulses also introduce errors, and DD does not generally repair irreversible
T1 relaxation, readout mistakes or reset failures. Improvement is not guaranteed.
The minimal XX profile is a conservative first test; IQM's benchmark examples
also use such a strategy.

The saved Garnet feature snapshot reports median Ramsey T2 of 9.08 microseconds
and echo T2 of 19.90 microseconds. This motivates testing refocusing, but those
separate characterization numbers do not predict the surface-code LER or a
particular improvement factor under DD.

**The current Stim model does not predict DD performance.** It excludes physical
idle durations, the server-inserted pulses and the noise correlations that
refocusing acts on. DD-on preparations label their simulation result as a
pre-DD gate/readout reference (`dd_effect_modeled=false`), not a DD baseline.
The ideal detector definitions remain applicable because the DD sequences have
net identity; the saved MWPM weights remain approximate. Modeling benefit needs
scheduled idle windows, additional pulse errors, and a suitable dephasing model
or independently characterized coherence under the chosen DD sequence.

The Python execution API accepts the same profile, e.g.
`run_qiskit(..., dd="xx")`; calling it submits paid jobs.

References: [IQM DD strategy](https://docs.iqm.tech/iqm-station-control-client/api/iqm.station_control.interface.models.circuit.DDStrategy.html),
[IQM minimal-DD example](https://docs.iqm.tech/iqm-benchmarks/examples/example_ghz.html).

## Step 2: decode saved shots and report the logical error rate

The standalone analysis command produces the challenge's per-round rate,
uncertainty, failure-probability curve and logical-fidelity decay plot from saved
QPU results. It never connects to IQM or submits jobs:

```sh
python analyze_logical_error_rate.py output/garnet-dd-qpu-20261024 \
  --out output/step2-logical-error-rate
```

Pass a completed individual run directory, a completed DD comparison directory,
or several run directories. Every run/basis/DD group is fitted separately;
datasets with different circuits or calibrations are never silently pooled.
Original data are preserved. The command copies each run, reconstructs raw
measurement records from counts, checks the saved Stim-reference hash, extracts
detectors and logical observable flips, and decodes again using PyMatching MWPM.
Failure counts must agree with the previously saved results.

The primary fit is the required equation:

\[
P_{\mathrm{error}}(r)=\frac{1-(1-2\epsilon_L)^r}{2},\qquad
F(r)=1-2P_{\mathrm{error}}(r)=(1-2\epsilon_L)^r.
\]

The fit maximizes the binomial likelihood of the failure/shot counts. It uses
Wilson 95% intervals for individual failure probabilities and approximate
profile-likelihood 95% intervals for the fitted rate. It includes the existing
preparation/readout-offset diagnostic and goodness-of-fit flags. A single round
count is reported as a probability; it is not promoted to a fitted per-round
rate. At least three distinct round counts are required by this fitting path.

Outputs are `report.md`, `logical-error-rate-results.json`,
`logical-error-rates.csv`, `logical-error-probability.png`,
`logical-fidelity.png` and the copied/redecoded runs. Fidelity intervals reverse
the probability-interval endpoints under F=1-2P. A logarithmic fidelity axis is
used only when all bounds are positive; otherwise negative estimates and bounds
are retained on a linear axis. Shaded fit envelopes show rate-parameter
uncertainty, not hardware/model uncertainty.

The DD experiment currently has only 1/2/3-round data with 500 shots per point.
Its fits are preliminary short-range estimates. A passing goodness-of-fit check
with three points does not establish stationary behavior over longer storage
times. The earlier longer sweep used different measurement grouping and must
remain a separate analysis.

`decode_hardware_results()` returns P_error and a standard error for one round
count. `fit_logical_rate()` performs the multi-round rate fit; the new command
ties both parts together and makes their distinction explicit.
