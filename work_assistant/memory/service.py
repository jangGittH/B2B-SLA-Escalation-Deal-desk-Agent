"""Human-governed presentation preferences and verified sandbox episodes, never policy."""
import time
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,insert,update,and_,or_,text
from ..context.tables import memories as m,memory_events as audit
from ..knowledge import KnowledgeService,terms
from ..telemetry import source_reference,current_run,event
from ..db.store import ConflictError

class Proposal(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['preference','episode']='preference'
    value:Literal['overview_first','evidence_first']='overview_first'
    source_id:str|None=Field(default=None,max_length=100)
    scope:Literal['user','team','tenant']='user'
    team_id:str|None=None
    expires_days:int=Field(default=90,ge=1,le=365)

class Change(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1)
    action:Literal['confirm','edit','delete']
    value:Literal['overview_first','evidence_first']|None=None
    expires_days:int=Field(default=90,ge=1,le=365)

class MemoryService:
    def __init__(self,db):self.db=db;self.knowledge=KnowledgeService(db)
    def visible(self,actor):
        return and_(m.c.organization_id==actor.organization_id,or_(and_(m.c.scope=='user',m.c.owner_id==actor.id),
            and_(m.c.scope=='team',m.c.team_id.in_(actor.team_ids)),m.c.scope=='tenant'))
    def _valid(self,actor,row):
        if row['expires_at']<=time.time():return False
        try:
            return all(source_reference(self.knowledge.read(actor.id,s['id'],s['language']))==s for s in row['sources'])
        except LookupError:return False
    def list(self,actor_id):
        actor=self.knowledge._actor(actor_id)
        with self.db.engine.connect() as c:
            rows=[dict(r) for r in c.execute(select(m).where(self.visible(actor),m.c.status.not_in(['deleted','superseded'])).order_by(m.c.updated_at.desc()).limit(100)).mappings()]
        # Invalid memories expose no source IDs or remembered content.
        return [r|{'usable':r['status']=='active','stale':False} if self._valid(actor,r)
                else {k:r[k] for k in ('id','scope','kind','status','version','expires_at','owner_id')}|{'usable':False,'stale':True} for r in rows]
    def propose(self,actor_id,body):
        actor=self.knowledge._actor(actor_id)
        if body.scope!='team' and body.team_id:raise ValueError('team_id requires team scope')
        if body.scope=='team' and body.team_id not in actor.team_ids:raise PermissionError('Choose one of your teams')
        sources=[];key='export_layout';value=body.value
        if body.kind=='episode':
            if not body.source_id or not body.source_id.startswith(('workflow:','deal-workflow:')):raise ValueError('Use a verified completed workflow source')
            doc=self.knowledge.read(actor_id,body.source_id,'en')
            import json
            record=json.loads(doc.content)
            if record['status']!='completed' or not record.get('receipt'):raise ValueError('Only completed, verified sandbox outcomes can become episode memories')
            key=body.source_id
            value='Verified sandbox workflow completed: '+body.source_id+'. This is not a confirmed incident root cause, real payment, or customer delivery.'
            sources=[source_reference(doc)]
            # Completed workflow views can already have a stale proposal snapshot after
            # their own sandbox effect. Bind current typed inputs separately so a later
            # policy/contract edit cannot silently preserve a usable episode memory.
            subject=('deal:'+record['deal_id'] if body.source_id.startswith('deal-workflow:')
                     else 'case:'+record['ticket_id'])
            sources.append(source_reference(self.knowledge.read(actor_id,subject,'en')))
        elif body.source_id:raise ValueError('Presentation preferences do not claim source facts')
        ctx=current_run.get();stamp=time.time()
        with self.db.engine.begin() as c:
            self.db._lock_actor(c,actor)
            c.execute(text('SELECT pg_advisory_xact_lock(81009,1)'))
            existing=c.execute(select(m).where(self.visible(actor),m.c.owner_id==actor.id,m.c.scope==body.scope,
                m.c.team_id.is_(None) if body.team_id is None else m.c.team_id==body.team_id,
                m.c.key==key,m.c.value==value,m.c.status.in_(['pending','active']),m.c.expires_at>stamp)
                .order_by(m.c.updated_at.desc()).limit(1)).mappings().first()
            if existing and existing['sources']==sources:return dict(existing)
            row=dict(id=uuid4().hex,organization_id=actor.organization_id,owner_id=actor.id,scope=body.scope,team_id=body.team_id,
                kind=body.kind,key=key,value=value,status='pending',version=1,sources=sources,confidence=1.0 if sources else 0.5,
                supersedes=None,confirmed_by=None,origin_run_id=ctx[1]['id'] if ctx else None,created_at=stamp,updated_at=stamp,
                expires_at=stamp+body.expires_days*86400,last_used_at=None)
            c.execute(insert(m).values(**row));self._audit(c,actor.id,row,'proposed')
        return row
    def _audit(self,c,actor_id,row,action):
        c.execute(insert(audit).values(id=uuid4().hex,memory_id=row['id'],actor_id=actor_id,action=action,version=row['version'],created_at=time.time()))
    def change(self,actor_id,ident,body):
        actor=self.knowledge._actor(actor_id)
        # Validate source reads before locking actor rows; workflow reads take shared actor locks.
        with self.db.engine.connect() as c:
            candidate=c.execute(select(m).where(self.visible(actor),m.c.id==ident)).mappings().first()
        valid=bool(candidate and self._valid(actor,candidate)) if body.action!='delete' else True
        with self.db.engine.begin() as c:
            self.db._lock_actor(c,actor)
            c.execute(text('SELECT pg_advisory_xact_lock(81009,1)'))
            row=c.execute(select(m).where(self.visible(actor),m.c.id==ident).with_for_update()).mappings().first()
            if not row or row['status'] in ('deleted','superseded'):raise LookupError('Memory not found')
            if (row['scope']=='user' and row['owner_id']!=actor.id) or (row['scope']!='user' and 'operations' not in actor.team_ids):raise PermissionError('Shared memories require Operations review')
            if row['version']!=body.expected_version:raise ConflictError('Memory changed; reload before editing')
            row=dict(row)
            if body.action!='delete' and (not valid or not candidate or candidate['version']!=row['version']):raise ConflictError('Memory source changed or expired; propose a fresh memory')
            values={'version':row['version']+1,'updated_at':time.time()}
            if body.action=='delete':values|={'status':'deleted','value':'','sources':[],'key':'deleted','origin_run_id':None}
            elif body.action=='edit':
                if row['kind']!='preference' or body.value is None:raise ValueError('Only presentation preferences can be edited')
                values|={'value':body.value,'status':'pending','confirmed_by':None,'expires_at':time.time()+body.expires_days*86400}
            else:
                if body.value is not None:raise ValueError('Edit the memory before confirming a changed value')
                # One active value per exact scope/key. A user preference takes precedence over shared defaults.
                old=c.execute(select(m).where(m.c.organization_id==actor.organization_id,m.c.scope==row['scope'],
                    m.c.owner_id==row['owner_id'] if row['scope']=='user' else True,
                    m.c.team_id.is_(None) if row['team_id'] is None else m.c.team_id==row['team_id'],
                    m.c.key==row['key'],m.c.status=='active',m.c.id!=ident)).mappings().all()
                for previous in old:
                    c.execute(update(m).where(m.c.id==previous['id']).values(
                        status='superseded',version=previous['version']+1,updated_at=time.time()))
                    self._audit(c,actor.id,dict(previous)|{'version':previous['version']+1},'superseded')
                values|={'status':'active','confidence':1.0,'confirmed_by':actor.id,'supersedes':old[0]['id'] if old else None}
            c.execute(update(m).where(m.c.id==ident).values(**values));row.update(values);self._audit(c,actor.id,row,body.action)
        return row
    def recall(self,actor_id,query='',limit=5):
        actor=self.knowledge._actor(actor_id)
        rows=[r for r in self.list(actor_id) if r['usable']]
        rows.sort(key=lambda r:({'user':3,'team':2,'tenant':1}[r['scope']],len(terms(query)&terms(r['value'])),r['confidence'],r['updated_at']),reverse=True)
        # Conflicting same-key defaults in multiple teams are omitted, not picked arbitrarily.
        selected=[];seen=set()
        for r in rows:
            if r['key'] in seen:continue
            same=[x for x in rows if x['key']==r['key'] and x['scope']==r['scope']]
            seen.add(r['key'])
            if len({x['value'] for x in same})>1:continue
            selected.append(r)
            if len(selected)>=limit:break
        if selected:
            with self.db.engine.begin() as c:c.execute(update(m).where(m.c.id.in_([r['id'] for r in selected])).values(last_used_at=time.time()))
            event('memory_used',{'memories':[{'id':r['id'],'version':r['version']} for r in selected]})
        return selected
    def layout(self,actor_id):
        return next((r['value'] for r in self.recall(actor_id) if r['key']=='export_layout'),'overview_first')
    def consolidate(self,actor_id,source_ids):
        # Deterministic consolidation only. No free-text root-cause inference and no automatic promotion.
        for source in source_ids[:8]:
            if source.startswith(('workflow:','deal-workflow:')):
                try:self.propose(actor_id,Proposal(kind='episode',source_id=source))
                except (ValueError,LookupError):pass

    def consolidate_completed(self):
        from ..db.models import runs,events
        done=select(events.c.id).where(events.c.run_id==runs.c.id,events.c.kind=='memory_consolidated').exists()
        with self.db.engine.connect() as c:
            rows=[dict(r) for r in c.execute(select(runs).where(runs.c.kind=='chat',runs.c.status=='completed',~done).order_by(runs.c.created_at).limit(20)).mappings()]
        for row in rows:
            token=current_run.set((self.db,row))
            try:
                try:self.consolidate(row['employee_id'],[s['id'] for s in (row['result'] or {}).get('sources',[])])
                except PermissionError:pass
                event('memory_consolidated',{'rule':'verified_sandbox_episode_proposal_v1','auto_promoted':False})
            finally:current_run.reset(token)
        return len(rows)
