"""Reproduce the five-round follow-up analysis locally; never submits QPU work."""
from collections import Counter
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from qiskit import qpy

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from garnet_hardware import analyze_saved_results, write_json
from logical_rate_fit import failure_probability, fit_logical_rate
from surface_code import make_stim_circuit

PRIOR = ROOT/'output/garnet-dd-qpu-20261024/xx'
folders = {'prior': PRIOR, 'followup': OUT}
manifests = {label: json.loads((folder/'manifest.json').read_text()) for label, folder in folders.items()}
assert all(m['mode'] == 'hardware_complete' for m in manifests.values())
old, new = manifests['prior'], manifests['followup']
for field in ['calibration_set_id', 'distance', 'no_reset', 'dd_profile', 'versions',
              'compilation_options', 'decoder_noise_provenance']:
    assert old[field] == new[field], f'Experiments differ in {field}; do not combine their fits'
assert new['dd_profile'] == 'xx' and new['total_requested_shots'] == 1000
assert set(new['experiments']) == {'X_r5', 'Z_r5'}

source_checks = {}
for name in ['garnet_hardware.py', 'stim_to_qiskit.py', 'garnet_model.py', 'simulation_baseline.py',
             'surface_code.py', 'internal_helpers.py', 'logical_rate_fit.py',
             'calibrations/garnet-selected-layouts.json']:
    before = PRIOR.parent/'submitted-source'/name
    after = OUT/'submitted-source'/name
    source_checks[name] = hashlib.sha256(before.read_bytes()).hexdigest()
    assert before.read_bytes() == after.read_bytes(), f'Submitted source changed: {name}'
    assert after.read_bytes() == (ROOT/name).read_bytes(), f'Current analysis dependency changed: {name}'
for basis in ['Z', 'X']:
    for field in ['layout', 'routing_method', 'optimization_level', 'feedback_signal_policy']:
        assert old['experiments'][f'{basis}_r1'][field] == new['experiments'][f'{basis}_r5'][field]

def operation_counts(circuit):
    counts = Counter()
    for item in circuit.data:
        counts[item.operation.name] += 1
        for block in getattr(item.operation, 'blocks', ()):
            if block is not None:
                counts.update(operation_counts(block))
    return counts

checks = []
for label, folder in folders.items():
    manifest = manifests[label]
    architecture = json.loads((folder/'architecture.json').read_text())
    edges = {frozenset(locus) for impl in architecture['gates']['cz']['implementations'].values()
             for locus in impl['loci']}
    for index, batch in enumerate(manifest['batches']):
        request = json.loads((folder/f'request-{index}.json').read_text())
        result = json.loads((folder/f'result-{index}.json').read_text())
        assert all(c['native_payload_verified'] for c in result['circuits'])
        assert len(request['circuits']) == len(batch['circuits']) == len(result['circuits'])
        for native, member in zip(request['circuits'], batch['circuits']):
            key = member['experiment']
            entry = manifest['experiments'][key]
            for suffix, field in [('qpy', 'qpy_sha256'), ('stim', 'stim_sha256')]:
                assert hashlib.sha256((folder/f'{key}.{suffix}').read_bytes()).hexdigest() == entry[field]
            with (folder/f'{key}.qpy').open('rb') as stream:
                circuit = qpy.load(stream)[0]
            counts = operation_counts(circuit)
            layout = {int(q): physical for q, physical in entry['layout'].items()}
            expected = Counter()
            ideal = make_stim_circuit(3, entry['rounds'], basis=entry['basis'])
            for instruction in ideal.flattened():
                if instruction.name in ['CX', 'CZ']:
                    targets = instruction.targets_copy()
                    for a, b in zip(targets[::2], targets[1::2]):
                        expected[frozenset((layout[a.value], layout[b.value]))] += 1
            actual = Counter(frozenset(i['locus']) for i in native['instructions'] if i['name'] == 'cz')
            qpy_pairs = Counter(frozenset(architecture['qubits'][circuit.find_bit(q).index] for q in i.qubits)
                                for i in circuit.data if i.operation.name == 'cz')
            assert counts['swap'] == counts['iswap'] == counts['cx'] == 0
            assert actual == expected == qpy_pairs
            assert counts['cz'] == sum(actual.values()) == 24*entry['rounds']
            assert all(pair in edges for pair in actual)
            measured = {circuit.find_bit(i.clbits[0]).index: architecture['qubits'][circuit.find_bit(i.qubits[0]).index]
                        for i in circuit.data if i.operation.name == 'measure'}
            expected_measurements = [entry['layout'][str(q)] for q in entry['measurement_order']]
            assert [measured[i] for i in range(len(expected_measurements))] == expected_measurements
            assert [i['locus'][0] for i in native['instructions'] if i['name'] == 'measure'] == expected_measurements
            assert entry['routing_method'] == 'none'
            checks.append({'run': label, 'experiment': key, 'routing_swaps': 0,
                           'cz_count': counts['cz'], 'cz_pairs_and_multiplicities_match_surface_code': True,
                           'all_cz_edges_native': True, 'physical_measurement_assignments_match': True,
                           'returned_native_payload_verified': True})
write_json(OUT/'zero-swap-audit.json', {'all_checks_passed': True, 'checks': checks,
           'scope': 'Saved/submitted/returned native gate circuits; no server pulse playlist was inspected.'})

cached = json.loads((OUT/'decoded-results.json').read_text())
redecoded = analyze_saved_results(OUT)
assert [(p['experiment'], p['failures'], p['shots']) for p in cached['points']] == [
       (p['experiment'], p['failures'], p['shots']) for p in redecoded['points']]
prior = json.loads((PRIOR/'decoded-results.json').read_text())
points = [dict(p, run='prior') for p in prior['points']] + [dict(p, run='followup') for p in redecoded['points']]
assert sum(p['shots'] for p in redecoded['points']) == 1000
fits = {}
for basis in ['Z', 'X']:
    selected = sorted([p for p in points if p['basis'] == basis], key=lambda p: p['rounds'])
    assert [p['rounds'] for p in selected] == [1, 2, 3, 5]
    fits[basis] = {'challenge': fit_logical_rate(selected),
                   'spam_diagnostic': fit_logical_rate(selected, include_spam=True),
                   'prediction_at_r5_from_prior_fit': float(failure_probability(5, prior['fits'][basis]['challenge']['epsilon_per_round']))}
summary = {'calibration_set_id': new['calibration_set_id'], 'dd_profile': 'xx',
           'new_shots': 1000, 'earlier_shots_reused': 3000, 'points': points, 'fits': fits,
           'compatibility_checks_passed': True, 'matching_submitted_source_sha256': source_checks,
           'new_job_ids': [b['job_id'] for b in new['batches']],
           'prior_job_ids': [b['job_id'] for b in old['batches']],
           'limitations': 'Sequential jobs, not interleaved; same calibration UUID does not exclude drift. Binomial/profile intervals exclude shot correlations, noise-model error and drift. Fits assume stationary independent logical flips. No new DD-off control was run.'}
status = json.loads((OUT/'job-status-0.json').read_text())
assert status['qiskit_status'] == 'DONE'
completed_utc = next(item['timestamp'] for item in status['job_data']['timeline']
                     if item['source'] == 'iqm-server' and item['status'] == 'completed')
completed_local = datetime.fromisoformat(completed_utc).astimezone(ZoneInfo('America/New_York'))
summary['completed_utc'] = completed_utc
summary['completed_new_york'] = completed_local.isoformat()
write_json(OUT/'followup-analysis.json', summary)
with (OUT/'followup-points.csv').open('w', newline='') as stream:
    fields = ['run', 'basis', 'rounds', 'shots', 'failures', 'failure_probability', 'ci95']
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(sorted(points, key=lambda p: (p['basis'], p['rounds'])))

fig, axes = plt.subplots(1, 2, figsize=(10.7, 4.4), sharey=True, constrained_layout=True)
grid = np.linspace(0, 5.3, 300)
y_top = max(55, 100*max(p['ci95'][1] for p in points)+3)
for ax, basis in zip(axes, ['Z', 'X']):
    fit = fits[basis]['challenge']
    ax.plot(grid, 100*failure_probability(grid, fit['epsilon_per_round']), color='#356993', label='Fit to 1, 2, 3, 5 rounds')
    lo, hi = fit['epsilon_ci95']
    ax.fill_between(grid, 100*failure_probability(grid, lo), 100*failure_probability(grid, hi), color='#356993', alpha=.12)
    ax.plot(grid, 100*failure_probability(grid, prior['fits'][basis]['challenge']['epsilon_per_round']),
            '--', color='#999999', label='Earlier 1–3-round fit')
    for label, marker, color in [('prior', 'o', '#356993'), ('followup', 's', '#d06a30')]:
        ps = sorted([p for p in points if p['basis'] == basis and p['run'] == label], key=lambda p: p['rounds'])
        y = np.array([p['failure_probability'] for p in ps])
        ci = np.array([p['ci95'] for p in ps])
        ax.errorbar([p['rounds'] for p in ps], 100*y, yerr=100*np.stack([y-ci[:, 0], ci[:, 1]-y]),
                    fmt=marker, color=color, capsize=4, ms=6, label='New 5-round measurement' if label == 'followup' else 'Earlier measurements')
    ax.axhline(50, color='#888888', lw=1, ls=':')
    fit_status = 'rejected' if fit['model_mismatch_flag'] else 'not rejected'
    ax.set(title=f"{basis}-memory, distance 3, XX DD\nConstant-rate fit {fit_status} (p={fit['goodness_of_fit_p_value']:.2g})", xlabel='Syndrome-extraction rounds',
           xticks=[1, 2, 3, 4, 5], xlim=(0, 5.4), ylim=(0, y_top))
    ax.grid(alpha=.2)
axes[0].set_ylabel('Decoded logical failure probability (%)')
axes[0].legend(fontsize=7.5, loc='upper left')
fig.suptitle('Garnet five-round follow-up — 500 shots per point', fontsize=12)
fig.savefig(OUT/'five-round-followup.png', dpi=180)
plt.close(fig)

lines = ['# Garnet five-round XX-DD follow-up', '',
         'New real-QPU experiment: five rounds, X/Z memory, 500 shots per basis, 1,000 returned shots total. Earlier 1–3-round XX-DD measurements are reused for the explicitly combined fit; they are not new shots.', '',
         f"Calibration UUID: `{new['calibration_set_id']}`. New job: `{summary['new_job_ids'][0]}`.", '',
         f"Completed successfully at {completed_local.strftime('%I:%M:%S %p on %B %d, %Y')} New York time. All 1,000 requested shots returned.", '',
         'Each five-round circuit uses 17 active qubits, 120 native CZ gates, 40 feedback resets, 49 measurements and 40 detectors. The independent gate-circuit audit verifies zero routing SWAPs, exact physical CZ pairs/multiplicities and measurement assignments. The returned native payload and requested DD configuration are verified.', '',
         '| Basis | Failures / shots | Five-round failure probability | Wilson 95% interval | Earlier-fit prediction |',
         '|---|---:|---:|---:|---:|']
for basis in ['Z', 'X']:
    p = next(p for p in redecoded['points'] if p['basis'] == basis)
    lo, hi = p['ci95']
    lines.append(f"| {basis} | {p['failures']} / {p['shots']} | {100*p['failure_probability']:.1f}% | {100*lo:.1f}–{100*hi:.1f}% | {100*fits[basis]['prediction_at_r5_from_prior_fit']:.1f}% |")
lines += ['', 'The earlier-fit column is an extrapolation made from the previous 1–3-round data. Its value is not an independent measurement or an uncertainty interval.', '',
          '![Five-round follow-up](five-round-followup.png)', '',
          '## Combined fit across 1, 2, 3 and 5 rounds', '',
          'Combining these particular jobs is explicit: the calibration UUID, physical maps, DD options, noise provenance, package versions and submitted implementation files match. These checks do not rule out time-dependent drift.', '',
          '| Basis | Fitted error per round | Conditional 95% interval | Fit p-value | Model mismatch flag |',
          '|---|---:|---:|---:|---|']
for basis in ['Z', 'X']:
    fit = fits[basis]['challenge']
    lo, hi = fit['epsilon_ci95']
    lines.append(f"| {basis} | {100*fit['epsilon_per_round']:.2f}% | {100*lo:.2f}–{100*hi:.2f}% | {fit['goodness_of_fit_p_value']:.3g} | {fit['model_mismatch_flag']} |")
lines += ['', 'The fit uses P(r) = [1 − (1 − 2ε)^r]/2 with binomial maximum likelihood. Full challenge and SPAM diagnostic fits are saved in followup-analysis.json. A mismatch flag means that a single stationary rate does not adequately explain the measured round dependence.', '',
          '## Pulse-playlist interpretation', '',
          'A playlist contains timed drive, coupler, readout and waiting instructions. The standard compiler resolves physically placed native gates into these schedules and can add DD pulses during eligible waits. Our circuit already uses native edges and requires no routing SWAPs; XX DD consists of single-qubit pulses and does not exchange states. Playlist inspection would establish detailed timing and DD insertion, which have not been audited here.', '',
          'Compiler documentation: https://docs.iqm.tech/iqm-pulla/Configuration%20and%20Usage.html', '',
          '## Limits', '', summary['limitations'], '',
          'The simulator baseline is the saved gate/readout Pauli model. It excludes timed idle noise, DD effects, leakage and several hardware correlations. Do not interpret it as a calibrated prediction of DD-enabled memory performance.', '',
          'Saved counts were redecoded and reproduce the original failure counts. No additional shots were submitted by this analysis.', '']
rejected = [basis for basis in ['Z', 'X'] if fits[basis]['challenge']['model_mismatch_flag']]
if rejected:
    lines += ['## Interpretation of the longer-round measurement', '',
              'The five-round measurements lie above the extrapolations from the earlier short-round fit. The combined constant-rate model is rejected at the 5% level for '+', '.join(rejected)+' memory. Its fitted rate parameters and conditional intervals are retained as diagnostics; they should not be presented as established stationary memory rates.', '',
              'The two jobs were sequential. Greater degradation in longer circuits, leakage, other unmodeled correlations or drift between jobs could contribute; these data do not identify a unique cause. No five-round DD-off arm was measured, so this experiment alone cannot establish DD benefit at five rounds.', '']
(OUT/'report.md').write_text('\n'.join(lines))
print(json.dumps({'new_points': redecoded['points'], 'combined_fits': fits, 'audited_circuits': len(checks)}, indent=2))
