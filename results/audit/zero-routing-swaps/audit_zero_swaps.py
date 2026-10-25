"""Audit saved Garnet gate circuits without connecting to the QPU."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from qiskit import qpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from surface_code import make_stim_circuit
from logical_rate_fit import failure_probability

out=Path(__file__).resolve().parent
out.mkdir(exist_ok=True)
comparison=ROOT/'output/garnet-dd-qpu-20261024'
checks=[]

def operations(circuit):
    names=Counter()
    for item in circuit.data:
        names[item.operation.name]+=1
        for block in getattr(item.operation,'blocks',()):
            if block is not None:names.update(operations(block))
    return names

for arm in ['off','xx']:
    folder=comparison/arm
    manifest=json.loads((folder/'manifest.json').read_text())
    arch=json.loads((folder/'architecture.json').read_text())
    edges={frozenset(locus) for implementation in arch['gates']['cz']['implementations'].values()
           for locus in implementation['loci']}
    evidence=json.loads((folder/'result-0.json').read_text())
    request=json.loads((folder/'request-0.json').read_text())
    assert all(c['native_payload_verified'] for c in evidence['circuits'])
    for native,member in zip(request['circuits'],manifest['batches'][0]['circuits']):
        name=member['experiment']
        entry=manifest['experiments'][name]
        path=folder/f'{name}.qpy'
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['qpy_sha256']
        with path.open('rb') as stream:circuit=qpy.load(stream)[0]
        counts=operations(circuit)
        layout={int(q):physical for q,physical in entry['layout'].items()}
        expected=Counter()
        ideal=make_stim_circuit(3,entry['rounds'],basis=entry['basis'])
        for inst in ideal.flattened():
            if inst.name in ['CX','CZ']:
                targets=inst.targets_copy()
                for a,b in zip(targets[::2],targets[1::2]):expected[frozenset((layout[a.value],layout[b.value]))]+=1
        actual=Counter(frozenset(inst['locus']) for inst in native['instructions'] if inst['name']=='cz')
        expected_layout=[arch['qubits'].index(entry['layout'][q]) for q in sorted(entry['stim_to_dense'],key=entry['stim_to_dense'].get)]
        assert counts['swap']==counts['iswap']==counts['cx']==0
        assert counts['cz']==sum(actual.values())==24*entry['rounds']
        assert actual==expected
        assert all(edge in edges for edge in actual)
        assert circuit.layout.initial_index_layout()[:len(expected_layout)]==expected_layout
        measured={circuit.find_bit(inst.clbits[0]).index:arch['qubits'][circuit.find_bit(inst.qubits[0]).index]
                  for inst in circuit.data if inst.operation.name=='measure'}
        expected_measurements=[entry['layout'][str(q)] for q in entry['measurement_order']]
        assert [measured[i] for i in range(len(expected_measurements))]==expected_measurements
        assert entry['routing_method']=='none'
        checks.append({'arm':arm,'experiment':name,'rounds':entry['rounds'],
                       'qpy_swap_gates':counts['swap'],'qpy_cz_gates':counts['cz'],
                       'expected_cz_gates':24*entry['rounds'],
                       'physical_cz_pairs_and_multiplicities_match_surface_code':True,
                       'every_cz_is_native':True,'recorded_physical_qubit_assignments_unchanged':True,
                       'returned_native_payload_verified':True,'routing_method':'none'})
result={'scope':'Routing SWAPs in saved Qiskit and submitted/verified native gate circuits; pulse playlists were not inspected.',
        'circuits_audited':len(checks),'all_checks_passed':True,'routing_swaps':0,'checks':checks}
(out/'zero-swap-audit.json').write_text(json.dumps(result,indent=2)+'\n')
print('Independent zero-SWAP audit passed for',len(checks),'executed circuits')
print('CZ counts:',sorted({c['qpy_cz_gates'] for c in checks}))
fits=json.loads((ROOT/'output/garnet-step2-ler-20261024/logical-error-rate-results.json').read_text())
for group in fits['series']:
    epsilon=group['fits']['challenge']['epsilon_per_round']
    print('Extrapolation',group['basis'],group['dd_profile'],[(r,round(float(failure_probability(r,epsilon)),4)) for r in [5,7,10]])
