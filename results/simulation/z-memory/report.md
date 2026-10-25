# Garnet simulation baseline

All executions were local. No QPU job was submitted.

These results are conditional on the explicit Pauli noise model; they are not measured Garnet logical error rates.

Calibration snapshot: `garnet-2026-10-24.json`; created 2026-10-24T16:39:01.009280Z.
Basis: Z; distance: 3; resets between rounds: True.
Reset-flip probability: 0.003 before applying the noise scale (assumed, not measured).

## Placement search

Enumerated 4 native-edge placements against the exported CZ couplers.
Simulated annealing selected placement 2 using cached Monte Carlo screening shots.
The search used 4 distinct noisy circuit(s). Exhaustive screening was also retained as a reference for this small search space.
Recommendation after independent holdout: placement 2.
Holdout decision: Candidate improved in independent holdout under this model.

## Logical error rate fits

| Circuit | ε per round, challenge model | Approximate 95% CI | Goodness-of-fit p |
|---|---:|---:|---:|
| baseline | 0.017379 | [0.017210, 0.017549] | 3.805e-260 |
| candidate | 0.016155 | [0.015993, 0.016318] | 9.726e-289 |

The required challenge model is P(R)=[1−(1−2ε)^R]/2. Fits use binomial counts, not a least-squares fit to noisy ratios.
A second fit allows a preparation/readout offset. If the challenge-fit goodness of fit is poor, its ε should not be treated as an adequate single-rate description.

| Circuit | Offset-model ε per round | Approximate 95% CI | Preparation/readout offset | Goodness-of-fit p |
|---|---:|---:|---:|---:|
| baseline | 0.013737 | [0.013475, 0.014001] | 0.016096 | 0.02567 |
| candidate | 0.012432 | [0.012180, 0.012686] | 0.016502 | 0.6665 |

## Decoded Monte Carlo points

| Circuit | Rounds | Failures / shots | P(failure) | 95% Wilson CI |
|---|---:|---:|---:|---:|
| baseline | 1 | 3003 / 100000 | 0.030030 | [0.028990, 0.031106] |
| baseline | 2 | 4141 / 100000 | 0.041410 | [0.040193, 0.042663] |
| baseline | 3 | 5515 / 100000 | 0.055150 | [0.053752, 0.056582] |
| baseline | 5 | 7727 / 100000 | 0.077270 | [0.075631, 0.078941] |
| baseline | 7 | 10356 / 100000 | 0.103560 | [0.101687, 0.105464] |
| baseline | 10 | 13380 / 100000 | 0.133800 | [0.131704, 0.135924] |
| candidate | 1 | 2837 / 100000 | 0.028370 | [0.027359, 0.029417] |
| candidate | 2 | 4091 / 100000 | 0.040910 | [0.039700, 0.042155] |
| candidate | 3 | 5140 / 100000 | 0.051400 | [0.050049, 0.052786] |
| candidate | 5 | 7294 / 100000 | 0.072940 | [0.071345, 0.074568] |
| candidate | 7 | 9478 / 100000 | 0.094780 | [0.092980, 0.096611] |
| candidate | 10 | 12451 / 100000 | 0.124510 | [0.122478, 0.126571] |

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
