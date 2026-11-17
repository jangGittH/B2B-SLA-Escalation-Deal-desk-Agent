"""Reproduce the 60-scenario, three-strategy deterministic control benchmark."""
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from importlib.metadata import version

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from work_assistant.agents.prompts import VERSION

def main():
    if not os.getenv('TEST_DATABASE_URL'):raise SystemExit('Set TEST_DATABASE_URL to a disposable-database-capable PostgreSQL service; the benchmark creates isolated databases.')
    with tempfile.TemporaryDirectory(prefix='unit10-eval-') as directory:
        xml=Path(directory)/'tests.xml'
        command=[sys.executable,'-m','pytest','tests/test_showcase.py','-q','--junitxml',str(xml),'-o','junit_family=xunit1']
        result=subprocess.run(command,cwd=ROOT)
        rows=[]
        for case in ET.parse(xml).iter('testcase'):
            props={p.attrib['name']:p.attrib['value'] for p in case.findall('./properties/property')}
            if not props.get('mode'):continue
            failed=case.find('failure') is not None or case.find('error') is not None
            skipped=case.find('skipped') is not None
            rows.append(props|{'passed':not failed and not skipped,'skipped':skipped,
                'model_calls':int(props.get('model_calls',0)),'tool_calls':int(props.get('tool_calls',0)),
                'elapsed_ms':float(props.get('elapsed_ms',float(case.attrib['time'])*1000)),
                'failure':case.find('failure').attrib.get('message','')[:1000] if case.find('failure') is not None else None})
    summary={}
    for mode in ('fixed','single','supervisor'):
        group=[r for r in rows if r['mode']==mode];durations=sorted(r['elapsed_ms'] for r in group)
        summary[mode]={'scenarios':len(group),'passed':sum(r['passed'] for r in group),'failed':sum(not r['passed'] for r in group),
            'safe_expected_outcome_rate':sum(r['passed'] for r in group)/len(group) if group else None,
            'model_calls':sum(r['model_calls'] for r in group),'tool_calls':sum(r['tool_calls'] for r in group),
            'latency_p50_ms':durations[len(durations)//2] if durations else None,
            'latency_p95_ms':durations[min(len(durations)-1,int(len(durations)*.95))] if durations else None,
            'inference_cost_usd':None,'usage':'provider double; no inference usage or cost imputed'}
    manifest=json.loads((ROOT/'tests/showcase_cases.json').read_text())
    sources=[*sorted((ROOT/'work_assistant/agents').glob('*.py')),*sorted((ROOT/'work_assistant/verification').glob('*.py')),
        ROOT/'tests/structured_model.py',ROOT/'tests/test_showcase.py',ROOT/'tests/showcase_cases.json',ROOT/'work_assistant/data/workspace.json',
        ROOT/'work_assistant/runtime/prices.json',ROOT/'scripts/unit10_eval.py']
    report={'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'dataset':manifest['version'],
        'category_counts':{k:len(v) for k,v in manifest['categories'].items()},'model':'deterministic-case-provider-v1',
        'prompt_version':VERSION,'schema_version':'handoff_v1/claim_verdicts_v1','policy_versions':['sla-minute-v1','deal-renewal-v1'],
        'hardware':{'os':platform.system(),'arch':platform.machine(),'python':platform.python_version()},
        'dependencies':{name:version(name) for name in ('google-genai','pydantic','sqlalchemy','langgraph')},
        'hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        'summary':summary,'rows':rows,'limitations':[
            'Deterministic provider doubles test orchestration/control, not live model planning quality or semantic-judge calibration.',
            'Every strategy faces the same seeded scenario and independent state assertions. Human decisions, fault injection and shared file/memory boundary probes are harness actions, not claimed autonomous model decisions.',
            'Fixed baseline uses typed workflow intake and has no narrative-generation task or model fault exposure; zero model calls do not imply zero operational cost.',
            'Single/supervisor share the mandatory critic and approval gateway. The comparison measures control correctness and overhead, not evidence that additional agents improve business quality.',
            'One timed pass; no non-deterministic variance claim. Completed cases, safe stops, waiting, rejection and partial readback have distinct expected states.',
            'No customer SLA compliance, real connector success, manual baseline, customer resolution cost or ROI is inferred.']}
    target=ROOT/'docs/results/unit10-showcase.json';target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(summary,indent=2))
    if result.returncode or len(rows)!=180:raise SystemExit(result.returncode or 1)

if __name__=='__main__':main()
