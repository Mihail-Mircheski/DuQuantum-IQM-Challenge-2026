"""Offline matched DD analysis; no network calls or submissions."""
import csv
import json
import math
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import fisher_exact

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from garnet_hardware import write_json
out=Path(__file__).resolve().parent
comparison=json.loads((out/'comparison.json').read_text())
assert comparison['mode']=='hardware_complete'
reports={arm:json.loads((out/arm/'decoded-results.json').read_text()) for arm in ['off','xx']}
manifest={arm:json.loads((out/arm/'manifest.json').read_text()) for arm in ['off','xx']}
assert len({m['calibration_set_id'] for m in manifest.values()})==1
points={arm:{(p['basis'],p['rounds']):p for p in report['points']} for arm,report in reports.items()}
rows=[]
for key in sorted(points['off']):
    a,b=points['off'][key],points['xx'][key]
    assert a['shots']==b['shots']==500
    assert manifest['off']['experiments'][a['experiment']]['stim_sha256']==manifest['xx']['experiments'][b['experiment']]['stim_sha256']
    p0,p1=a['failure_probability'],b['failure_probability']
    lower=p1-p0-math.sqrt((p1-b['ci95'][0])**2+(a['ci95'][1]-p0)**2)
    upper=p1-p0+math.sqrt((b['ci95'][1]-p1)**2+(p0-a['ci95'][0])**2)
    pvalue=float(fisher_exact([[b['failures'],b['shots']-b['failures']],
                              [a['failures'],a['shots']-a['failures']]],alternative='two-sided').pvalue)
    rows.append({'basis':key[0],'rounds':key[1],'control_failures':a['failures'],'dd_failures':b['failures'],
                 'shots_per_arm':500,'control_probability':p0,'dd_probability':p1,
                 'control_ci95':a['ci95'],'dd_ci95':b['ci95'],
                 'difference_dd_minus_control':p1-p0,'difference_ci95':[max(-1,lower),min(1,upper)],
                 'difference_ci_method':'Unadjusted Newcombe interval from independent Wilson intervals',
                 'two_sided_fisher_p':pvalue})
previous=0.0
for rank,index in enumerate(sorted(range(len(rows)),key=lambda i:rows[i]['two_sided_fisher_p'])):
    previous=max(previous,min(1.0,(len(rows)-rank)*rows[index]['two_sided_fisher_p']))
    rows[index]['holm_adjusted_p']=previous
for row in rows:
    significant=row['holm_adjusted_p']<0.05
    row['assessment']=('DD lower failure' if row['difference_dd_minus_control']<0 else 'DD higher failure') if significant else 'Inconclusive at this shot count'
result={'source':'Matched real Garnet QPU experiment','calibration_set_id':comparison['calibration_set_id'],
        'shots_per_arm':3000,'total_returned_shots':sum(p['shots'] for report in reports.values() for p in report['points']),
        'rounds':[1,2,3],'points':rows,'job_ids':{arm:comparison['arms'][arm]['job_ids'] for arm in ['off','xx']},
        'note':'Both arms use corrected measurement grouping. DD mode/strategy acceptance is verified, but inserted pulse counts and timings are not available. The Stim model does not predict DD benefit.'}
write_json(out/'comparison-results.json',result)
with (out/'comparison-points.csv').open('w',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
fig,axes=plt.subplots(1,2,figsize=(10.7,4.2),sharey=True,constrained_layout=True)
for ax,basis in zip(axes,['Z','X']):
    for arm,label,color,offset in [('off','Corrected circuit, DD off','#946234',-.04),('xx','Same circuit, XX DD','#286c99',.04)]:
        ps=[points[arm][(basis,r)] for r in [1,2,3]]
        y=np.array([p['failure_probability'] for p in ps]);ci=np.array([p['ci95'] for p in ps])
        ax.errorbar(np.array([1,2,3])+offset,100*y,yerr=100*np.stack([y-ci[:,0],ci[:,1]-y]),fmt='o-',
                    color=color,capsize=4,lw=1.6,label=label)
    ax.axhline(50,color='#888888',ls='--',lw=1)
    ax.set(title=f'{basis}-memory, distance 3',xlabel='Syndrome-extraction rounds',xticks=[1,2,3],ylim=(0,57),xlim=(.7,3.3))
    ax.grid(alpha=.2)
axes[0].set_ylabel('Decoded logical failure probability (%)')
axes[0].legend(fontsize=8)
fig.suptitle('Garnet: matched DD-off / XX-DD comparison — 500 shots per point',fontsize=12)
fig.savefig(out/'dd-comparison.png',dpi=180)
plt.close(fig)
lines=['# Garnet DD-off versus XX-DD experiment','',
       'Both arms ran on real Garnet using identical corrected gate circuits, physical placements, measurement grouping, calibration UUID and shot counts. The arms differ in requested server-side DD configuration.','',
       f"- Calibration UUID: `{comparison['calibration_set_id']}`.",
       f"- DD-off job: `{result['job_ids']['off'][0]}`.",f"- XX-DD job: `{result['job_ids']['xx'][0]}`.",
       '- X/Z memories at 1, 2 and 3 rounds; 500 shots per point; 6,000 returned shots in total.',
       '- XX targets the nine physical data qubits. Unique measurement records and reusable ancilla-feedback labels are preserved.','',
       '| Basis | Rounds | DD off | XX DD | DD minus control | Holm-adjusted p | Assessment |',
       '|---|---:|---:|---:|---:|---:|---|']
for row in sorted(rows,key=lambda p:(p['basis']!='Z',p['rounds'])):
    lines.append(f"| {row['basis']} | {row['rounds']} | {100*row['control_probability']:.1f}% | {100*row['dd_probability']:.1f}% | {100*row['difference_dd_minus_control']:+.1f} pp | {row['holm_adjusted_p']:.3g} | {row['assessment']} |")
lines += ['', 'Negative differences favor DD. Tests are two-sided Fisher exact tests with Holm correction across all six comparisons. Exact failure counts, Wilson intervals and unadjusted Newcombe intervals for differences are saved in comparison-results.json. Inconclusive results do not establish that DD has no effect.','',
          '![Matched hardware DD comparison](dd-comparison.png)','',
          '## Interpretation and limits','',
          'The hardware comparison addresses the requested compiler configuration. A verified DD-enabled flag does not prove which pulse sequences were inserted or how many eligible idle windows existed. The existing Stim baseline excludes timed idle noise, extra DD pulses and refocusing effects; its identical predictions for the two arms are expected by construction.','',
          'Only three short round counts were used. Per-arm challenge/SPAM fits are retained in each decoded-results.json, including goodness-of-fit flags; these should not be promoted to reliable stationary LERs when fit checks fail.','',
          'The earlier 12,000-shot experiment used different measurement grouping. Differences from that historical run cannot be attributed specifically to DD.','',
          'Saved evidence includes each arm\'s manifest, live architecture, native requests, QPY circuits, Stim/DEM references, validated counts, reconstructed samples and decoded results. The submitted-source directory preserves the implementation used for this comparison.','']
interpretation = lines.index('## Interpretation and limits') + 2
x = next(p for p in rows if p['basis']=='X' and p['rounds']==3)
ci = x['difference_ci95']
if all(p['holm_adjusted_p'] < .05 and p['difference_dd_minus_control'] < 0 for p in rows if p['basis']=='X'):
    note = f"The X-memory failure reductions are statistically significant at all three round counts after correcting the six tests. At three rounds the absolute reduction is {-100*x['difference_dd_minus_control']:.1f} percentage points (unadjusted 95% interval: {-100*ci[1]:.1f}–{-100*ci[0]:.1f} points), or about {-100*x['difference_dd_minus_control']/x['control_probability']:.0f}% relative to the control."
else:
    note = 'Use the per-point assessments above; effects vary with round count and sampling uncertainty.'
if all(p['holm_adjusted_p'] >= .05 for p in rows if p['basis']=='Z'):
    note += ' Z-memory differences are not statistically distinguishable from sampling fluctuations in this pilot.'
lines[interpretation:interpretation] = [note, '', 'This pattern is consistent with refocusable phase noise contributing to X-memory loss. It does not isolate a unique noise source. One job per arm was run sequentially; binomial intervals and tests do not account for drift or shot correlations. Repeated or interleaved paired runs would test reproducibility.', '']
lines += ['', '## Preliminary short-range fits', '', 'These fits use only 1, 2 and 3 rounds, with 500 shots per point. Passing a goodness-of-fit check with so few points does not establish a stationary long-duration memory rate. Intervals are conditional on the fitted model.', '', '| Basis | DD | Fitted error per round | Conditional 95% interval | Fit p-value |', '|---|---|---:|---:|---:|']
for basis in ['Z','X']:
    for arm in ['off','xx']:
        fit = reports[arm]['fits'][basis]['challenge']
        lo, hi = fit['epsilon_ci95']
        lines.append(f"| {basis} | {arm} | {100*fit['epsilon_per_round']:.2f}% | {100*lo:.2f}–{100*hi:.2f}% | {fit['goodness_of_fit_p_value']:.3g} |")
lines += ['', 'The primary comparison is the observed per-experiment failure probabilities. Full challenge and SPAM fits remain in each arm’s decoded-results.json.', '']
(out/'report.md').write_text('\n'.join(lines))
print(json.dumps({'returned_shots':result['total_returned_shots'],'points':rows},indent=2))
