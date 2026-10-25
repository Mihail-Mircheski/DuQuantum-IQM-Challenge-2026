# Garnet simulation baseline

All executions were local. No QPU job was submitted.

These results are conditional on the explicit Pauli noise model; they are not measured Garnet logical error rates.

Calibration snapshot: `garnet-2026-10-24.json`; created 2026-10-24T16:39:01.009280Z.
Basis: X; distance: 3; resets between rounds: True.
Reset-flip probability: 0.003 before applying the noise scale (assumed, not measured).

## Placement search

Enumerated 4 native-edge placements against the exported CZ couplers.
Simulated annealing selected placement 1 using cached Monte Carlo screening shots.
The search used 4 distinct noisy circuit(s). Exhaustive screening was also retained as a reference for this small search space.
Recommendation after independent holdout: placement 1.
Holdout decision: Candidate improved in independent holdout under this model.

## Logical error rate fits

| Circuit | ε per round, challenge model | Approximate 95% CI | Goodness-of-fit p |
|---|---:|---:|---:|
| baseline | 0.020521 | [0.020334, 0.020710] | 1.957e-296 |
| candidate | 0.019474 | [0.019293, 0.019657] | 4.536e-301 |

The required challenge model is P(R)=[1−(1−2ε)^R]/2. Fits use binomial counts, not a least-squares fit to noisy ratios.
A second fit allows a preparation/readout offset. If the challenge-fit goodness of fit is poor, its ε should not be treated as an adequate single-rate description.

| Circuit | Offset-model ε per round | Approximate 95% CI | Preparation/readout offset | Goodness-of-fit p |
|---|---:|---:|---:|---:|
| baseline | 0.016162 | [0.015869, 0.016455] | 0.019055 | 0.0161 |
| candidate | 0.015242 | [0.014960, 0.015524] | 0.018563 | 0.874 |

## Decoded Monte Carlo points

| Circuit | Rounds | Failures / shots | P(failure) | 95% Wilson CI |
|---|---:|---:|---:|---:|
| baseline | 1 | 3362 / 100000 | 0.033620 | [0.032521, 0.034755] |
| baseline | 2 | 5114 / 100000 | 0.051140 | [0.049792, 0.052523] |
| baseline | 3 | 6372 / 100000 | 0.063720 | [0.062223, 0.065251] |
| baseline | 5 | 9349 / 100000 | 0.093490 | [0.091701, 0.095310] |
| baseline | 7 | 11740 / 100000 | 0.117400 | [0.115420, 0.119410] |
| baseline | 10 | 15259 / 100000 | 0.152590 | [0.150375, 0.154832] |
| candidate | 1 | 3359 / 100000 | 0.033590 | [0.032491, 0.034725] |
| candidate | 2 | 4718 / 100000 | 0.047180 | [0.045883, 0.048512] |
| candidate | 3 | 6071 / 100000 | 0.060710 | [0.059247, 0.062207] |
| candidate | 5 | 8786 / 100000 | 0.087860 | [0.086121, 0.089630] |
| candidate | 7 | 11236 / 100000 | 0.112360 | [0.110418, 0.114332] |
| candidate | 10 | 14698 / 100000 | 0.146980 | [0.144799, 0.149188] |

## Model assumptions and practical limits

- RB/IRB infidelity is converted to a depolarizing Pauli approximation.
- Readout asymmetry and correlations are replaced by symmetric flips.
- Active-reset errors are assumed, not measured in this export.
- T1/T2 idle noise is excluded because operation durations are unavailable.
- Leakage, coherent errors, crosstalk and measurement disturbance are excluded.
- Native Clifford decomposition is explicit and unoptimized; it is not an IQM transpiler output.
- A simulation result is not a prediction verified on Garnet hardware.
- Confidence intervals cover sampling/fit uncertainty; calibration and model uncertainty are not propagated.

Search shots, independent holdout shots, and round-sweep shots use separate seeds. The saved native circuits define exactly the simulator's gate and measurement sequence.
Before a hardware comparison, verify the current backend's topology, calibrated operations, reset support, and measurement behavior; validate the chosen hardware compiler against these saved circuits.
