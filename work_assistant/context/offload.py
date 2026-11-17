"""Private, expiring, inspectable large tool results with fresh source checks."""
import hashlib
import json
import time
from uuid import uuid4
from sqlalchemy import select,insert
from ..context.tables import offloads
from ..telemetry import current_run,source_reference,event


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()

def list_guard(knowledge,actor,name,args):
    if name=='list_sla_cases':return knowledge.cases.list_tickets(actor,**args)
    if name=='list_deals':
        from ..deals.service import DealService
        return DealService(knowledge.repository).list(actor,args.get('query',''))
    raise LookupError('Result unavailable')

class OffloadStore:
    def __init__(self,knowledge,actor_id):self.knowledge=knowledge;self.actor_id=actor_id;self.db=knowledge.repository
    def save(self,result,name,args):
        actor=self.knowledge._actor(self.actor_id)
        if len(encoded(result))>2000000:raise ValueError('Tool result exceeds durable offload budget')
        guards=[]
        if result.get('document'):
            from ..knowledge import DocumentView
            supplied=source_reference(DocumentView.model_validate(result['document']))
            current=source_reference(self.knowledge.read(actor.id,supplied['id'],supplied['language']))
            if supplied!=current:raise LookupError('Result changed before storage')
            guards=[{'source':supplied}]
        elif name in ('list_sla_cases','list_deals'):
            records=result['tickets' if name=='list_sla_cases' else 'deals']
            if encoded(records)!=encoded(list_guard(self.knowledge,actor.id,name,args)):
                raise LookupError('Result changed before storage')
            guards=[{'list':name,'args':args,'checksum':hashlib.sha256(encoded(records)).hexdigest()}]
        else:raise ValueError('Result cannot be safely offloaded')
        ident=uuid4().hex;ctx=current_run.get();stamp=time.time()
        with self.db.engine.begin() as c:c.execute(insert(offloads).values(id=ident,employee_id=actor.id,organization_id=actor.organization_id,
            run_id=ctx[1]['id'] if ctx else None,payload=result,guards=guards,created_at=stamp,expires_at=stamp+86400))
        event('result_offloaded',{'id':ident,'bytes':len(encoded(result)),'tool':name})
        return ident
    def read(self,ident,cursor=0,limit=8,contains=''):
        if type(cursor) is not int or cursor<0 or type(limit) is not int or not 1<=limit<=8:
            raise ValueError('Use a nonnegative cursor and a page size of 1–8')
        if not isinstance(contains,str) or len(contains)>100:
            raise ValueError('Literal filter is limited to 100 characters')
        actor=self.knowledge._actor(self.actor_id)
        with self.db.engine.connect() as c:
            row=c.execute(select(offloads).where(offloads.c.id==ident,offloads.c.employee_id==actor.id,
                offloads.c.organization_id==actor.organization_id,offloads.c.expires_at>time.time())).mappings().first()
        if not row:raise LookupError('Result unavailable')
        for guard in row['guards']:
            if 'source' in guard:
                ref=guard['source']
                if source_reference(self.knowledge.read(actor.id,ref['id'],ref['language']))!=ref:raise LookupError('Result unavailable')
            elif hashlib.sha256(encoded(list_guard(self.knowledge,actor.id,guard['list'],guard['args']))).hexdigest()!=guard['checksum']:raise LookupError('Result unavailable')
        value=row['payload'];doc=value.get('document')
        records=[{'text':doc['content'][i:i+1200]} for i in range(0,len(doc['content']),1200)] if doc else value.get('tickets',value.get('deals',[]))
        # Arbitrary nested rows are serialized into bounded fragments; cursor counts fragments.
        fragments=[]
        for index,record in enumerate(records):
            data=json.dumps(record,ensure_ascii=False,default=str)
            fragments.extend({'row':index,'part':offset//400,'text':data[offset:offset+400]} for offset in range(0,len(data),400))
        matching=[r for r in fragments if contains.casefold() in r['text'].casefold()]
        page=matching[cursor:cursor+limit]
        return {'ok':True,'ref':ident,'total_rows':len(records),'matching_fragments':len(matching),'cursor':cursor,
            'next_cursor':cursor+len(page) if cursor+len(page)<len(matching) else None,'items':page,
            'document_id':doc['id'] if doc else None,'document_version':doc['version'] if doc else None,
            'scope':'Partial view; not a whole-table calculation. Use deterministic calculation tools for money.'}
    def compact(self,result,name,args):
        if len(encoded(result))<=12000:return result
        ident=self.save(result,name,args)
        # Do not call previews evidence or silently imply all omitted facts were read.
        return {'ok':True,'offloaded':True,'ref':ident,'bytes':len(encoded(result)),
            'document_id':result.get('document',{}).get('id'),'version':result.get('document',{}).get('version'),
            'scope':'Large result stored privately for 24 hours. Inspect pages before using omitted facts; samples are not totals.',
            'preview':self.read(ident,limit=2),'memo_id':result.get('memo_id'),'workflow_id':result.get('workflow_id')}
