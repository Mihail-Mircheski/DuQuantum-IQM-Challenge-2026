# Surface Code on IQM Garnet — Challenge Overview

**Garnet implementation:** the with-reset, distance-3 Qiskit path is executable.
Start with `python run_garnet.py` for local preparation,
`--prepare-live` for validation against the authenticated device, and
`--submit` for explicit paid QPU execution. See [HARDWARE.md](HARDWARE.md)
for installation, commands, calibration checks, job recovery and decoded results.
The original challenge also describes larger-device and Qrisp extensions; those
are separate from the implemented Garnet path.

## Quantum Error correction - Memory experiment

Quantum computers are fundamentally noise-limited: every gate, every idle moment, every measurement introduces errors that accumulate faster than useful computation can proceed. 
Quantum error correction addresses this by encoding a single logical qubit across many physical qubits and continuously measuring stabilizer operators — parity checks that reveal error syndromes without collapsing the encoded information. 
Surface codes have emerged as the leading practical approach.
A surface code is defined, via the number of physical qubits it needs, the distance it covers, and the number of logical qubits.
They map directly onto the square-lattice connectivity that superconducting processors like IQM Garnet provide.
Still, writing out a whole a whole pipeline is notoriously hard. 
The challenge you will be facing today will have you operating on the forefront of science! 

## The QEC Pipeline

You are building and completing a proof-of-concept quantum error correction pipeline that runs a surface code on real superconducting hardware and produces a logical error rate. There are two equal ways of implementing the pipeline, Qiskit and Qrisp and you should pick one. The full chain is:

```
Qiskit :  (Stim circuit  →)  Qiskit  →  IQM Garnet (Resonance)  →  Syndrome extraction  →  Decoder  →   P_L(rounds) → fit → LER
Qrisp  :  (Stim circuit  →)  Qrisp DetectorExperiment  →  IQM Garnet (Resonance)  →  Decoder  →   P_L(rounds) → fit → LER
```

Each stage has a clear role. 

* Stim, Qiskit or Qrisp defines the surface code circuit including stabilizer structure, qubit coordinates, detectors, and the logical observable. Stim is an optional package tool, specialized for creating surface code experiments. While you cannot directly run the Stim circuits on the hardware, it makes postprocessing a lot easier. 
* The Qiskit or Qrisp layer translates that abstract circuit into something the hardware can execute. You can also build the surface code experiment in this layer of the stack. 
* IQM Resonance runs the transpiled circuit and returns shot-by-shot measurement bitstrings. 
* Syndrome extraction converts those raw bitstrings into detection events, which are what the decoder actually needs, not the raw measurements themselves. If you write your code in Qrisp, the `DetectorExperiment` returns detection events directly.
* The decoder then uses those events to infer whether a logical error occurred.

In real hardware, you would then feed the information on logical errors back into the QPU and correct errors on the fly. But real hardware is limited, so this is something we cant do (yet). This is called a memory experiment.

**This pipeline is offline**. Decoding happens after all shots are collected. There is no requirement to feed correction results back to the QPU during coherence time.

---

## Judging criteria

1) Theoretical correctness. Does the experiment actually set up a surface correctly? Is the pipeline fully functional? (30%)
2) Sophistication of the implementation. Have you optimized your circuits for the performance/architecture of the quantum computer? Can you prove a low number of SWAPS? (30%)
3) Logical error rate statistics. (20%)
4) Flexibility of the experiment and integration of advanced functionalities - Circuit compilation techniques, Noise Model improvements, Pulse-level compilations,... (20%)


Bonus points will be awarded (at our discretion) for:
- Hardware flexibility.
- Integration of advanced functionalities.
- Experimentally identifying the most important reasons for improving LER.
- Other particularly cool and surprising ideas

## Submissions

Submit your code and any other key results through [this Google form](https://docs.google.com/forms/d/e/1FAIpQLSeKTmoGUzxhrc4bPtLJ5Vp3VtoFSrtoes1UP3_6IoLgZDLIxg/viewform?usp=publish-editor). 


# Useful Resources

**Please see the ``UsefulResources.md`` markdown for an overview over useful resources!** Such as, links to **IQM PulLA**, client-side libraries such as **Dynamical Decoupling**, access links to IQM Resonance!
**To access PulLA you need to be a part of a paid organisation, please approach your mentors about this!**

### Related papers

* [Quantum error correction below the surface code threshold](https://www.nature.com/articles/s41586-024-08449-y) (Google Quantum AI, Nature 2024): the current state of the art for what you are doing in this challenge. Too long to read in full during the event, but some details may help you.
* [Suppressing quantum errors by scaling a surface code logical qubit](https://www.nature.com/articles/s41586-022-05434-1) (Google Quantum AI, Nature 2023): older, more basic and clearer. Fig. 3 shows the logical error probability vs. number of rounds, which is the plot you should produce (see section 2 below).
* [Exponential suppression of bit or phase errors with cyclic error correction](https://www.nature.com/articles/s41586-021-03588-y#Sec4) (Google Quantum AI, Nature 2021): source of the fit function used to extract the logical error rate.




---

## Provided functionality

**All the provided functionality is optional!** Should you wish to build from scratch, feel free to do so! 

Install everything you should need with the requirements file of this repo: `pip install -r requirements.txt` (Python ≥ 3.11).

### Circuit translation (`stim_to_qiskit.py`, `stim_to_qrisp.py`)

The implemented Qiskit converter densely reindexes active Stim qubits and
preserves every measurement index. It supports Z/X preparation and readout,
keeps TICKs as barriers, and requires noise to be removed explicitly with
`without_noise()`. The Garnet path lowers gates to native PRX/CZ first and uses
measurement-conditioned X for ancilla resets without extra recorded bits.
The Qrisp converter is an alternative starter and is not used by this hardware path.

### Simulation baseline (`raw_stim_d3_surface_code.py`)

We provide a d=3 rotated surface code memory experiment in Stim. It models the hardware with uniform noise at circuit level and decodes the syndromes with PyMatching. You can use to first familiarize yourself with how an experiment is written, how to set up decoding and get to an expected LER. This original starter uses uniform noise. For the Garnet execution path, preparation instead saves and samples a matching feedback-reset reference using the downloaded per-qubit noise approximation; see [HARDWARE.md](HARDWARE.md).

### Syndrome extraction (`extract_syndromes.py`)

This only needed for the Qiskit implementation. The full syndrome extraction step is implemented using Stim's ``compile_m2d_converter()``, which converts raw measurement bitstrings into detection events and observable flips in a single call. If you are working within a pure Qiskit workflow, you can replace this with a manual implementation: detection events are the XOR of consecutive ancilla measurement rounds with reset. Without reset, the XOR of the measurements two rounds apart. (a stabilizer that changes between rounds indicates an error), and the logical observable is recovered from the parity of the final data qubit measurements. Either way, what matters for the decoder is the same output shape: a [shots, num_detectors]-shaped boolean array of detection events and a [shots, num_observables] array of logical outcomes. The dictionary also carries per-shot syndrome weights and per-detector firing rates as diagnostics.

### Hardware execution (`run_garnet.py`, `garnet_hardware.py`, `run_on_hardware.py`)

The Garnet Qiskit path prepares, validates, explicitly submits, retrieves,
extracts syndromes, decodes with MWPM, and fits the per-round LER. It uses the
saved native-edge placement with routing disabled and ancilla resets enabled.
Default preparation is offline; `--prepare-live` validates the current calibrated
architecture without submitting; `--submit` executes paid jobs. Job IDs and
artifacts are saved for collection after interruption. See [HARDWARE.md](HARDWARE.md).

`run_qiskit()` is the programmatic execution entry point. `run_qrisp()` remains
unimplemented and raises an error.

### Utilities (*`internal_helpers.py`*)

Three helper functions support the pipeline, mostly for stim to qiskit and postprocessing functionality.

* `get_qubit_lists` classifies qubits as data or ancilla using the coordinate parity convention Stim uses for the rotated surface code. 
* `get_meas_order` returns qubit indices in the order they are measured to align hardware bitstrings with Stim's measurement record. 
* `counts_to_measurement_array` does the Qiskit-to-numpy conversion including the endianness correction (Qiskit implementation only). 

Note the docstrings reminding you, that you should verify this logic carefully before fully trusting it.

---

## What Needs To Be Built

### 1. A way to generate the surface code circuit

The pipeline currently has no entry point. You need to build a surface code generator in the framework of your choice! 

Concretely, you need a function that accepts:

* a code distance
* a number of stabilizer rounds, 
* which memory experiment we are looking at (Z or X)
* whether ancillas are reset between rounds (`no_reset`)
* optionally a noise model, 
* and returns a `stim.Circuit` or a `qiskit.QuantumCircuit`, a Qrisp `DetectorExperiment`, or ...

`make_stim_circuit()` in `surface_code.py` implements the Stim starting point for step 1. It supports distance, rounds, X/Z memory, optional noise, and the `no_reset` switch. For this project's Garnet study, ancillas are reset between rounds by default (`no_reset=False`). The alternative Qiskit-native and Qrisp-native entry points remain unimplemented.

See [SIMULATION.md](SIMULATION.md) for the simulator-only setup, Python examples, and `simulate_surface_code.py`. This local path needs no IQM credentials and submits no QPU jobs.

**The full local Garnet baseline is also implemented:** `run_simulation_baseline.py`
generates and lowers the circuit to native Clifford/CZ operations, applies an
explicit Pauli approximation to the downloaded Garnet benchmarking data, samples
raw measurements, extracts detectors, decodes with PyMatching, and fits decoded
failure probability across round counts. It evaluates native-edge placements
with Monte Carlo screening and simulated annealing, then uses independent
holdout shots before recommending a change. Ancilla resets are enabled by default.
See [SIMULATION.md](SIMULATION.md#full-decoded-baseline-and-mapping-optimization)
for commands, statistical checks, and the noise-model assumptions. This is a
simulation baseline, not a measured Garnet logical error rate.

You do not have to build this in Stim or Qiskit if you prefer to use another framework! 

Garnet uses resets in this implementation (`no_reset=False`). A no-reset
variant remains available for simulator comparisons, but is not the hardware
execution default. The implemented hardware reset is a measurement followed by
a conditional X; the saved reference models readout faults in that feedback.

### 2. A way to decode the syndromes and compute the logical error rate

`decode_hardware_results` now uses the matching noisy reference to decode with PyMatching; the CLI aggregates completed experiments and fits the LER. The detection events and observable flips are already correctly formatted when they arrive here — `det_events` is a `(shots, num_detectors)` boolean array, `obs_flips` is `(shots, num_observables)`. The implemented Garnet pipeline performs decoding, binomial uncertainty estimation and fits across round counts. If you decode with a detector error model (DEM), its detectors must match the circuit that you executed on hardware

The standalone step-2 report is generated without credentials or new QPU jobs:

```sh
python analyze_logical_error_rate.py output/garnet-dd-qpu-20261024 \
  --out output/step2-logical-error-rate
```

It preserves the original records, rechecks MWPM decoding, and reports the fitted
per-round rate with a 95% interval, goodness of fit, P_error versus rounds and
logical fidelity F=1-2P_error. Memory bases and DD settings remain separate.
See [HARDWARE.md](HARDWARE.md#step-2-decode-saved-shots-and-report-the-logical-error-rate).

Options are plenty, but PyMatching (specifically the MWPM decoder) will give you the fastest out-of-the-box result. 

__Logical error probability vs. logical error rate.__ What one decoded experiment gives you is the logical error probability `P_error` after `n_rounds` rounds, not the logical error rate. It is normal for it to approach 50% after a few rounds. To get the logical error rate per round `ε_L` you need to: 

* Run the memory experiment for a number of round counts, e.g. `rounds = 2, 3, 4, …`, each as a separate job.
* Decode each one and plot the logical error probability against the number of rounds (compare Fig. 3 of the 2023 paper in "Related papers").
* Fit the related function to your data. It describes the exponential decay of the logical fidelity `F = 1 - 2 P_error` with the number of rounds. The fitted `ε_L` is your logical error rate.

$$2P_\text{error} = 1 - (1 - 2\varepsilon_L)^{n_\text{rounds}}$$

For a single round count this solves to `ε_L = ½ [1 - (1 - 2 P_error)^(1/n_rounds)]`, but the fit over many round counts is more robust.


### 3. (Optional but Recommended) A simulation baseline

Before spending QPU time, validate the same circuit through a noisy simulator.
`run_garnet.py` prepares a feedback-aware reference, checks the compiled Qiskit
circuit with ideal Aer, and runs the matching noisy Stim/MWPM baseline. The
standalone optimization study remains documented in [SIMULATION.md](SIMULATION.md).
Both use with-reset circuits for Garnet, with the feedback distinction described
in [HARDWARE.md](HARDWARE.md).

### 4. (Optional) Custom hardware qubit placement

The implemented Garnet path uses the saved native-edge maps in
`calibrations/garnet-selected-layouts.json`. It validates all edges against the
live architecture and rejects routing, changed placement or changed measurement
assignment. The legacy mapping starter is not used.

---

## Extensions

### Pulse-level compilation with PulLA

The current pipeline operates at the gate level and relies on Qiskit's transpiler to map to the hardware's native gate set. A more direct path is to compile the stabilizer circuit to pulses using **PulLA**, IQM's pulse-level compilation framework. This bypasses the gate abstraction entirely and can produce significantly shallower circuits by fusing or reshaping pulses that would otherwise be separate gates. For a noise-limited experiment like this, reducing circuit depth directly translates to fewer errors before the decoder sees the syndrome, which is the most direct lever on logical error rate.
**Also activate advanced circuit compilation techniques, such as dynamical decoupling (DD), readily available within IQMs stack.**


### Different decoders and advanced functionalities

PyMatching (minimum-weight perfect matching) is the natural starting point, but it is not the only option. **Union-Find decoding** is asymptotically faster and nearly as accurate near threshold. **Belief propagation** decoders can incorporate more detailed noise information. The MWPM graph itself can be weighted by calibration data from Resonance, which typically improves decoding accuracy on real hardware. **There are many more, so feel to investigate!** 
In particular using calibration data and setting up experiments for extended noise model characterization are of very high value! These can massively improve the decoding pipeline. 

For a more substantial extension, the **NVIDIA Ising Predecoder** offers a hardware-accelerated decoding path. This route requires working through the Ising model's `MemoryCircuit` class for circuit generation (which handles native X-basis preparation and measurement and a different Stim-to-Qiskit translation), and is therefore more involved. Please approach us if you are interested! 

### Different hardware patches and code distances

The implemented hardware path targets a distance-3 rotated surface code (17 active qubits) on Garnet. Comparing its four native-edge embeddings is a natural extension. Distance 5 requires 49 qubits and does not fit on Garnet's 20 qubits.

