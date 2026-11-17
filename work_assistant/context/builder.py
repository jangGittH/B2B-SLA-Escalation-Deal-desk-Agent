"""Bounded structured state, never hidden reasoning or authoritative old facts."""
import re
import time
import json
from sqlalchemy.dialects.postgresql import insert
from .tables import contexts
from ..telemetry import current_run,source_reference

class TaskContext:
    MAX_BYTES = 24000

    def __init__(self,question,model_budget,tool_budget):
        self.data={'goal':question[:4000],'entities':[x[:100] for x in re.findall(r'\b(?:INC|DEAL)-[\w-]+',question)[:12]],
            'active_plan_revision':0,'planning_mode':'fixed_tool_loop','steps':[],'evidence':[],'artifacts':[],
            'unresolved':[],'memories':[],'budget':{'model_calls':model_budget,'tool_calls':tool_budget},
            'notice':'History and memories are context only; read current sources before claims. No hidden reasoning is stored.'}
        from .state import prior_task
        previous=prior_task.get()
        if previous:
            self.data['previous']={key:value[:8] for key,value in previous.items()}
        self.bound()

    def bound(self):
        # Bound serialized UTF-8, including bilingual text. This is not a tokenizer estimate.
        def size():return len(json.dumps(self.data,ensure_ascii=False).encode())
        for key in ('previous','steps','artifacts','evidence','unresolved','entities'):
            if size()<=self.MAX_BYTES:break
            if key=='previous':self.data.pop(key,None)
            else:
                while self.data[key] and size()>self.MAX_BYTES:self.data[key].pop(0)

    def record(self,name,result,gateway):
        self.data['steps']=(self.data['steps']+[{'tool':name[:100],'outcome':'ok' if result.get('ok') else result.get('error',{}).get('code','error')}])[-8:]
        self.data['evidence']=[source_reference(doc) for doc in list(gateway.evidence.values())[-12:]]
        if result.get('ref'):self.data['artifacts']=(self.data['artifacts']+[result['ref']])[-8:]
        self.save()
    def save(self):
        self.bound()
        ctx=current_run.get()
        if ctx:
            db,run=ctx
            with db.engine.begin() as c:c.execute(insert(contexts).values(run_id=run['id'],payload=self.data,updated_at=time.time()).on_conflict_do_update(
                index_elements=['run_id'],set_={'payload':self.data,'updated_at':time.time()}))
