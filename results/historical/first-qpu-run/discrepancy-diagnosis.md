# Discrepancy diagnosis

The run identifies a substantial mismatch between the simulation model and hardware. It does not uniquely identify a physical root cause.

## Confirmed from the saved data and request

- At three rounds, mean detector firing is 31.9% in Z memory and 33.0% in X memory, compared with 11.3% and 11.2% in the model. This discrepancy exists before decoding.
- At three rounds, raw observable flips are 36.9% (Z) and 47.9% (X); decoding reduces these to 33.8% and 46.6%. Decoder fitting alone cannot account for the observed state degradation.
- The baseline contains no duration-dependent idle T1/T2 noise. The saved feature snapshot reports median T1 of 37.08 microseconds, Ramsey T2 of 9.08 microseconds and echo T2 of 19.90 microseconds.
- The submitted native gate list interleaves each ancilla measurement with its conditional reset before the next ancilla measurement. Explicit barriers were absent between the complete measurement group and the reset group. Actual pulse scheduling and readout concurrency have not been retrieved.
- The submitted compilation disabled dynamical decoupling and used optimization_level=0. Native single-qubit gates were intentionally left unoptimized to match the explicit simulator decomposition.
- Residual feedback/reset error was assumed to be 0.3%, not measured for these repeated checks. Readout was modeled as symmetric and independent; leakage, coherent errors, crosstalk and measurement disturbance were excluded.
- All native instructions, physical locations, result keys, calibration IDs and shot counts were verified. The original compiler failure and generic SDK result names are separately documented integration issues; they do not establish the cause of poor memory performance in the completed job.

## Ranked hypotheses

1. Data-qubit decoherence during measurement and feedback waits. Missing time-dependent noise, short coherence times and rapid X-memory loss make this a leading hypothesis.
2. Readout/reset scheduling overhead, potentially worsened by the interleaved gate list. IQM recommends barriers around measurement groups to preserve multiplexing opportunities. The resulting pulse timing must be checked before assigning a contribution.
3. Actual feedback-reset errors and readout disturbance/correlations exceeding the assumed model.
4. Leakage, coherent/correlated gate errors and mismatched MWPM weights.

The hardware execution timeline spans the whole batch and includes initialization between shots; it cannot be used to infer a single QEC round duration.

## Local correction and next checks

Measurement groups are now bracketed with barriers before applying resets. Local tests verify eight consecutive ancilla measurements per round and nine final data measurements, unchanged measurement record length, native placement, and ideal decoder correctness. This scheduling correction has not been tested on the QPU. The completed run's request, QPY, noisy references and submitted source snapshot remain unchanged.

Next, characterize repeated measurement/conditional-reset behavior, inspect scheduled cycle timing and multiplexing, and add timed data-qubit noise to the simulator. These checks should precede another surface-code sweep. No extra QPU shots were taken for this diagnosis.

IQM multiplexing documentation: https://docs.iqm.tech/iqm-client/user_guide_qiskit.html#multiplexed-measurements
