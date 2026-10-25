# Implementation assessment and zero-routing-SWAP evidence

The current submission is a functioning distance-3 surface-code memory experiment on Garnet, with verified native-edge placement, MWPM decoding, error-rate fits and a measured XX-DD comparison. All four judging categories are represented. Several optimization and characterization claims remain preliminary.

| Criterion | Demonstrated | Remaining work |
|---|---|---|
| Theoretical correctness and functionality | Stim rotated-code generator; 9 data + 8 ancillas; X/Z memories; appropriate detector/observable boundaries; injected-fault and graphlike-distance checks; ideal Aer/Stim checks; actual QPU measurement-to-decoder-to-fit pipeline | Hardware experiments so far prepare logical +Z/+X; test both signs and equal-duration unencoded memory before claiming arbitrary-state preservation or breakeven |
| Architecture and optimization | Four native embeddings evaluated in the approximate simulator model; fixed physical mapping; no routing; measurement grouping protected; data-only XX-DD improved X-memory in a matched hardware pilot | Qiskit optimization_level=0 leaves the explicit single-qubit decomposition unoptimized; no actual cycle-duration audit, final-model placement reoptimization or demonstrated custom pulse fusion |
| Logical error-rate statistics | Failure counts, Wilson point intervals, binomial maximum-likelihood rate fits, profile-likelihood intervals, preparation/readout diagnostic, fit checks, F=1-2P plots, multiplicity-adjusted DD comparison | Current comparable DD data cover only 1/2/3 rounds at 500 shots per point; repeat/interleave arms and add informative longer round counts |
| Flexibility and advanced features | Configurable simulated distance/rounds/basis/reset/noise; Garnet distance-3 hardware path; DD off/XX/XY4/standard profiles; per-qubit and per-edge Pauli weights with saved calibration provenance | Hardware path is Garnet-specific; direct client PulLA compilation and custom waveforms are absent; timed idle noise, DD effect, leakage/crosstalk and measured repeated-reset errors are absent from the baseline |

## Zero routing SWAPs

The independent audit in zero-swap-audit.json checked all 12 executed circuits of the matched DD experiment. It does not rely on the manifest's stored swap_count field.

- Saved QPY hashes match the prepared experiment hashes.
- Recursive QPY operation counts contain zero SWAP, iSWAP or CX gates. CZ is the only two-qubit gate.
- CZ counts are exactly 24, 48 and 72 for 1, 2 and 3 rounds, respectively, for both memory bases and DD settings.
- The physical CZ pair multiplicities exactly match those derived from the intended Stim surface-code circuit and saved placement.
- Every CZ pair is in the saved live Garnet architecture.
- Each classical measurement record still comes from the specified physical qubit. Initial placement and disabled routing are also checked.
- Collection verified the returned native circuit payloads against the saved requests.

This supports the precise claim: **zero routing SWAPs in the submitted and verified native gate circuits.** The audit does not claim that we inspected the server's pulse playlists. The separate physical CZ and measurement-assignment checks are used as operational evidence rather than relying on QPY final-layout metadata after serialization.

## Gate versus pulse compilation

The current client workflow is Stim -> Qiskit -> native PRX/CZ/measurement/feedback gates plus compiler settings -> IQM server -> pulses -> QPU. DD is requested through CircuitCompilationOptions and applied during server-side pulse compilation in eligible data-qubit idle windows.

Our code does not construct a PulLA compiler, inspect a playlist, modify pulse waveforms, or submit a custom playlist. Enabling DD is an advanced pulse-level compiler feature, but does not mean that a client-side custom PulLA workflow has been implemented. The compatible local environment has iqm-client 35.0.3 and iqm-pulse 14.0.3; iqm-pulla is not installed.

The saved Garnet feature snapshot reported pulse-level access unavailable. The repository's UsefulResources.md says PulLA access requires a paid organization. Current access would need to be established before custom pulse execution. Existing circuit-level DD remains usable, as the completed runs demonstrate.

Moving compilation to PulLA does not automatically shorten circuits: IQM's standard client compiler can be equivalent to its server compiler. Performance improvement must come from an actual scheduling, fusion, calibration or waveform change and be validated.

## More rounds

Additional informative round counts improve the decay estimate and expose startup/readout offsets, leakage buildup or nonstationarity. More rounds also add storage time and noisy operations; they do not automatically lower cumulative failure probability.

The current X/XX-DD challenge fit extrapolates P_error to approximately 32.4% at 5 rounds, 38.4% at 7 and 43.8% at 10. Those are model extrapolations from only three short points, not hardware measurements. Five and seven rounds are useful next checks. The X/DD-off fit is already near 48% at five rounds, so many long off-control points would be poorly informative.

Keep the corrected circuits and calibration policy fixed while extending the sweep. Do not pool the older differently grouped circuit with the latest DD data. Improve statistics with repeated shots and paired runs, not just increasingly long circuits.

## High-value next changes

1. Test IQM single-qubit gate optimization while preserving placement and readout grouping; update the corresponding noise/decoder reference before hardware comparisons.
2. Characterize actual idle/feedback timing and repeated reset/readout; extend the noise model in a way that can represent refocusing.
3. Repeat DD comparisons and extend the informative sweep, while testing both logical signs and an equal-time unencoded memory control.
4. If pulse access is available, use PulLA to inspect durations and validate specific pulse-level changes; avoid claiming a pulse-level optimization merely from installing the library.

References: https://docs.iqm.tech/iqm-client/user_guide_qiskit.html ; https://docs.iqm.tech/iqm-pulla/Quick%20Start.html ; https://docs.iqm.tech/iqm-pulla/Configuration%20and%20Usage.html
