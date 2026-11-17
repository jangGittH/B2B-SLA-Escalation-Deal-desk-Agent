"""Small repeated live-provider evaluation on isolated fictional data, with a shared cost cap."""
import argparse
import asyncio
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sqlalchemy import create_engine,select
from sqlalchemy.engine import make_url
from work_assistant.agent import AgentRequest,configured_model
from work_assistant.agents.runtime import CollaborationAgent
from work_assistant.agents.budget import Budget,BudgetExceeded
from work_assistant.agents.prompts import PROMPTS,VERSION
from work_assistant.db.store import Database,migrate
from work_assistant.db.models import events
from work_assistant.knowledge import KnowledgeService
from work_assistant.repository import FixtureRepository
from work_assistant.runtime.pricing import estimate,SCHEDULE
from work_assistant.telemetry import current_run

# Independent assertions are not added to provider prompts or runtime search data.
CASES=[
    ('response_target','mei','What is Acme\'s initial response target? Distinguish response from resolution.','en','60','acme-sla'),
    ('sla_proposal','mei','Calculate the proposed SLA credit for INC-105 from current records. Do not start a review or create a file.','en','45','case:INC-105'),
    ('deal_proposal','alex','Calculate the proposed monthly renewal price for DEAL-101. Do not start a review or change any contract.','en','900','deal:DEAL-101'),
    ('bilingual_target','mei','Acme 的首次响应时限是多少？这是否承诺在该时限内解决故障？','zh','60','acme-sla'),
]

class CappedModel:
    def __init__(self,model,cap):self.inner=model;self.model=model.model;self.cap=Decimal(cap);self.spent=Decimal(0);self.reserved=Decimal(0);self.unknown=False;self.calls=0
    async def structured(self,role,payload,schema,language,output_tokens=2048):
        # Conservative serialized-byte reservation, including schema/system prompt and
        # an extra 4K framing allowance. Actual usage is reconciled after every call.
        size=len(json.dumps(payload,ensure_ascii=False).encode())+len(json.dumps(schema.model_json_schema()).encode())+len(PROMPTS[role].encode())+4096
        cost=estimate(self.model,{'prompt_token_count':size,'candidates_token_count':output_tokens,'total_token_count':size+output_tokens})['cost_usd']
        if cost is None or self.unknown:raise BudgetExceeded('Live cost cannot be bounded with current pricing/usage')
        reserve=Decimal(cost)
        if self.spent+self.reserved+reserve>self.cap:raise BudgetExceeded('Live evaluation cost cap')
        self.reserved+=reserve;self.calls+=1
        try:
            reply=await self.inner.structured(role,payload,schema,language,output_tokens)
            measured=estimate(self.model,reply.usage)['cost_usd']
            if measured is None:self.unknown=True
            else:self.spent+=Decimal(measured)
            return reply
        except BaseException:
            # A timed-out provider may still have billed; reserve it conservatively.
            self.spent+=reserve
            raise
        finally:self.reserved-=reserve

async def evaluate(args):
    raw=configured_model()
    if raw is None:raise SystemExit('A configured Gemini key is required; no credentials are written to reports.')
    capped=CappedModel(raw,args.max_cost_usd)
    base=os.environ.get('TEST_DATABASE_URL')
    if not base:raise SystemExit('Set TEST_DATABASE_URL; each case uses a new isolated database.')
    admin=create_engine(base,isolation_level='AUTOCOMMIT');rows=[]
    try:
        for repeat in range(args.repeats):
            for mode in args.modes:
                for ident,actor,question,language,number,source in CASES[:args.cases]:
                    name='northstar_test_live_'+uuid4().hex
                    with admin.connect() as c:c.exec_driver_sql(f'CREATE DATABASE "{name}"')
                    url=make_url(base).set(database=name).render_as_string(hide_password=False);db=Database(url)
                    try:
                        migrate(url);db.seed(FixtureRepository.load().workspace)
                        employee=db.employee(actor);conv=db.create_conversation(employee,'Live evaluation '+ident,language)
                        run,_=db.submit(employee,'chat',{'question':question,'language':language,'mode':mode},'live',conv['id'],120)
                        token=current_run.set((db,run));start=time.perf_counter()
                        try:
                            result=await CollaborationAgent(KnowledgeService(db),capped,budget_factory=lambda:Budget(max_model_calls=16)).ask(actor,AgentRequest(question=question,language=language,mode=mode))
                            db.finish(run,'completed' if result.status=='answered' else 'failed',result=result.model_dump(mode='json'))
                        finally:current_run.reset(token)
                        success=result.status=='answered' and re.search(r'(?<!\d)'+number+r'(?!\d)',result.answer) is not None and source in [d.id for d in result.sources]
                        from work_assistant.workflows.tables import credits,workflows
                        from work_assistant.deals.tables import effects
                        with db.engine.connect() as c:
                            assert not c.execute(select(credits)).first() and not c.execute(select(effects)).first() and not c.execute(select(workflows)).first()
                            trace=[{'kind':r.kind,'payload':r.payload} for r in c.execute(select(events).where(events.c.run_id==run['id']).order_by(events.c.id))]
                        rows.append({'case':ident,'mode':mode,'repeat':repeat,'passed':bool(success),'duration_ms':round((time.perf_counter()-start)*1000),
                                     'response':result.model_dump(mode='json'),'events':trace})
                        print(f'{ident} {mode} repeat={repeat}: {result.status}, assertion={bool(success)}',flush=True)
                    finally:
                        db.close()
                        with admin.connect() as c:c.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
                    if capped.unknown:break
    finally:admin.dispose();await raw.aclose()
    report={'created_at':datetime.now(timezone.utc).isoformat(),'model':capped.model,'prompt_version':VERSION,
        'pricing_version':SCHEDULE['version'],'cost_cap_usd':str(capped.cap),'known_estimated_cost_usd':str(capped.spent),
        'unknown_usage':capped.unknown,'provider_calls':capped.calls,'repeats':args.repeats,'rows':rows,
        'passed':sum(r['passed'] for r in rows),'total':len(rows),'dataset_sha256':hashlib.sha256(json.dumps(CASES).encode()).hexdigest(),
        'limits':['Small fixed synthetic smoke set, not the 60-scenario live quality benchmark.',
                  'Independent expected source/numeric/no-mutation assertions supplement the model critic; no human-calibrated groundedness claim.',
                  'Standard paid list-price estimate, not actual billing. Timed-out calls retain their conservative reservation.']}
    (ROOT/'docs/results/unit10-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('passed','total','provider_calls','known_estimated_cost_usd','unknown_usage')},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--max-cost-usd',default='0.50');parser.add_argument('--cases',type=int,choices=range(1,5),default=4)
    parser.add_argument('--repeats',type=int,choices=range(1,4),default=2);parser.add_argument('--modes',nargs='+',choices=['single','supervisor'],default=['single','supervisor'])
    asyncio.run(evaluate(parser.parse_args()))
