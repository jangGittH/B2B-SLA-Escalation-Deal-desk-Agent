"""Frozen, synthetic held-out query comparison. Does not tune models or call an LLM."""
import json
import platform
import time
import hashlib
from importlib.metadata import version
from pathlib import Path
from work_assistant.repository import FixtureRepository
from work_assistant.knowledge import KnowledgeService,can_read
from work_assistant.retrieval.search import models

# The corpus is the product fixture. Queries below are never used in training/index text.
CASES=[
 ('mei','en','How quickly must someone first acknowledge a critical Acme complaint?','acme-sla','paraphrase'),
 ('mei','en','Are we promising to fix the problem within an hour?','acme-sla','paraphrase'),
 ('mei','zh','重大客户故障需要多久首次响应？','acme-sla','bilingual'),
 ('mei','en','响应时限','acme-sla','cross_display'),
 ('mei','en','What must engineers collect before blaming the system?','inc-104-runbook','paraphrase'),
 ('mei','zh','判断故障原因之前需要收集哪些资料？','inc-104-runbook','bilingual'),
 ('mei','en','AURORA-PRIVATE-742','inc-104-runbook','exact_code'),
 ('mei','en','INC-104 logs exclusions','inc-104-runbook','ticket_id'),
 ('mei','en','What time did the customer first report intermittent errors?','inc-104-history','paraphrase'),
 ('mei','zh','Acme 是哪天几点报告故障的？','inc-104-history','bilingual'),
 ('mei','en','Which facts about the outage remain unresolved?','inc-104-history','unknowns'),
 ('mei','en','Could the maintenance window overlap the disruption?','engineer-notes','hypothesis'),
 ('alex','en','Can we treat the requested renewal reduction as a settled price?','acme-deal','paraphrase'),
 ('alex','zh','续约优惠已经批准了吗？','acme-deal','bilingual'),
 ('alex','en','What does Finance need before changing commercial terms?','acme-deal','paraphrase'),
 ('alex','en','12 percent renewal discount','acme-deal','exact_terms'),
 ('lin','en','Is the credit ceiling enough to establish customer entitlement?','credit-policy','paraphrase'),
 ('lin','zh','服务抵扣上限占月费多少？','credit-policy','bilingual'),
 ('lin','en','5 percent eligible service credit','credit-policy','exact_terms'),
 ('alex','en','Who must authorize external messages and contract amendments?','operating-guide','controls'),
 ('mei','zh','调查时要区分证据、假设和建议吗？','operating-guide','bilingual'),
 ('outsider','en','How long is the response target for this company?','other-account-policy','tenant'),
 ('outsider','en','OTHER-ORG-SECRET-915','other-account-policy','exact_code'),
 ('mei','en','initial response target Acme signed contract','acme-sla','policy_reference'),
]

def main():
    pins=json.loads(Path('work_assistant/retrieval/models.json').read_text());models()
    service=KnowledgeService(FixtureRepository.load());rows=[];summary={}
    for mode in ('lexical','bm25','hybrid'):
        durations=[];correct=0;recall=0;mrr=0
        for actor,language,query,expected,category in CASES:
            began=time.perf_counter();result=service.search(actor,query,language,limit=3,mode=mode)
            elapsed=round((time.perf_counter()-began)*1000,2);durations.append(elapsed)
            ids=[h.document.id for h in result.hits]
            if mode=='hybrid':assert result.method=='bm25_dense_rrf_cross_encoder','Models unavailable; do not report BM25 as hybrid'
            assert all(can_read(service._actor(actor),service.repository.document(i)) for i in ids)
            correct+=bool(ids and ids[0]==expected);recall+=expected in ids
            mrr+=1/(ids.index(expected)+1) if expected in ids else 0
            rows.append({'mode':mode,'actor':actor,'language':language,'category':category,'query':query,'expected':expected,'top3':ids,'ms':elapsed})
        summary[mode]={'queries':len(CASES),'top1_accuracy':round(correct/len(CASES),4),'recall_at3':round(recall/len(CASES),4),'mrr_at3':round(mrr/len(CASES),4),'latency_p50_ms':sorted(durations)[len(durations)//2],'latency_p95_ms':sorted(durations)[int(len(durations)*.95)]}
    report={'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'hardware':{'os':platform.system(),'arch':platform.machine(),'python':platform.python_version()},'corpus':'sla-deal-desk-v1; 8 bilingual fixture documents','queries':'24 fixed synthetic held-out queries; no model fine-tuning or threshold tuning on results','models':pins,'summary':summary,'rows':rows,'limits':['Small synthetic corpus; no production retrieval or live-model task-success claim.','Embedding latency includes per-query encoding of authorized fixture chunks; database mode caches version-bound embeddings.','Missing factual policy revisions are validated separately in integration tests; this corpus is not a 60-case business evaluation.']}
    report['reproducibility']={
        'corpus_sha256':hashlib.sha256(Path('work_assistant/data/workspace.json').read_bytes()).hexdigest(),
        'queries_sha256':hashlib.sha256(json.dumps(CASES,ensure_ascii=False).encode()).hexdigest(),
        'implementation_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [Path(__file__),Path('work_assistant/knowledge.py'),*sorted(Path('work_assistant/retrieval').glob('*.py'))]},
        'dependencies':{name:version(name) for name in ('numpy','rank-bm25','sentence-transformers','transformers','torch')},
        'configuration':{'device':'cpu','torch_threads':2,'dense_min_cosine':0.25,'rrf_constant':60,'branch_candidates':40,'rerank_candidates':30},
        'timing':'One pass per mode; local model loading excluded, query and authorized document encoding included. No variance estimate.'}
    Path('docs/results').mkdir(exist_ok=True)
    Path('docs/results/unit9-retrieval.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
