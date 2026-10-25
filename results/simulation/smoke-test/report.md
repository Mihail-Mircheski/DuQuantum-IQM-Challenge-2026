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
| baseline | 0.017637 | [0.017101, 0.018185] | 5.088e-42 |
| candidate | 0.016194 | [0.015684, 0.016715] | 7.818e-32 |

The required challenge model is P(R)=[1−(1−2ε)^R]/2. Fits use binomial counts, not a least-squares fit to noisy ratios.
A second fit allows a preparation/readout offset. If the challenge-fit goodness of fit is poor, its ε should not be treated as an adequate single-rate description.

## Decoded Monte Carlo points

| Circuit | Rounds | Failures / shots | P(failure) | 95% Wilson CI |
|---|---:|---:|---:|---:|
| baseline | 1 | 350 / 10000 | 0.035000 | [0.031573, 0.038784] |
| baseline | 2 | 417 / 10000 | 0.041700 | [0.037955, 0.045797] |
| baseline | 3 | 571 / 10000 | 0.057100 | [0.052720, 0.061820] |
| baseline | 5 | 764 / 10000 | 0.076400 | [0.071355, 0.081771] |
| baseline | 7 | 1085 / 10000 | 0.108500 | [0.102554, 0.114747] |
| baseline | 10 | 1283 / 10000 | 0.128300 | [0.121888, 0.134998] |
| candidate | 1 | 297 / 10000 | 0.029700 | [0.026549, 0.033212] |
| candidate | 2 | 406 / 10000 | 0.040600 | [0.036905, 0.044648] |
| candidate | 3 | 522 / 10000 | 0.052200 | [0.048010, 0.056734] |
| candidate | 5 | 749 / 10000 | 0.074900 | [0.069902, 0.080224] |
| candidate | 7 | 903 / 10000 | 0.090300 | [0.084839, 0.096076] |
| candidate | 10 | 1261 / 10000 | 0.126100 | [0.119737, 0.132750] |

## Model assumptions and practical limits

- RB/IRB infidelity is converted to a depolarizing Pauli approximation.
- Readout asymmetry and correlations are replaced by symmetric flips.
- Active-reset errors are assumed, not measured in this export.
- T1/T2 idle noise is excluded because operation durations are unavailable.
- Leakage, coherent errors, crosstalk and measurement disturbance are excluded.
- Native Clifford decomposition is explicit and unoptimized; it is not an IQM transpiler output.
- A simulation result is not a prediction verified on Garnet hardware.

Search shots, independent holdout shots, and round-sweep shots use separate seeds. The saved native circuits define exactly the simulator's gate and measurement sequence.
Before a hardware comparison, verify the current backend's topology, calibrated operations, reset support, and measurement behavior; validate the chosen hardware compiler against these saved circuits.
