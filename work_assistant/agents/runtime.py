"""Request-bound supervisor DAG; business effects remain in the existing durable workflows."""
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
import anyio
import httpx
from google import genai
from pydantic import ValidationError
from ..agent import AgentResponse, ToolEvent
from ..tools import ToolGateway, SCHEMAS, DESCRIPTIONS
from ..policy import TOOL_RISK
from ..telemetry import event, current_run, source_reference
from ..context.builder import TaskContext
from ..runtime.queue import CapacityError
from ..runtime.pricing import estimate
from ..verification.claims import current_sources, validate_claims, review_errors, render, EvidenceChanged, Denied
from .contracts import Plan, Node, WorkerTurn, Handoff, Review, ROLE_TOOLS, Intake, ConversationCheck
from .budget import Budget, BudgetExceeded
from .model import StructuredReply
from .prompts import PROMPTS, VERSION
from .storage import save_plan, save_step

class InvalidOutput(Exception): pass
class UnknownOutcome(Exception): pass

CAPABILITIES = '''Northstar can investigate authorized SLA/customer evidence, calculate stored SLA
credit proposals and deal renewal terms, draft private memos/exports, and start independent human
reviews. Only authorized human reviewers approve business actions; financial/contract effects
are local sandbox operations. No real customer messages are sent. Single mode uses one tool
worker plus a critic. Specialist team uses a supervisor, scoped specialists and a critic, with
independent reads potentially parallel. Both have access/evidence checks and bounded budgets.
The assistant can converse, explain these capabilities and ask contextual follow-up questions.'''

@dataclass
class NodeResult:
    handoff: Handoff
    evidence: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    mutated: bool = False


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()


def transient(exc):
    return isinstance(exc,(TimeoutError,httpx.TimeoutException,httpx.NetworkError,CapacityError)) or (
        isinstance(exc,genai.errors.APIError) and exc.code in (429,500,502,503,504))

class CollaborationAgent:
    def __init__(self, knowledge, model, files=None, budget_factory=Budget):
        self.knowledge=knowledge;self.model=model;self.files=files;self.budget_factory=budget_factory

    async def ask(self,employee_id,request,history=None):
        session=Session(self,employee_id,request,history)
        return await session.run()

class Session:
    def __init__(self,agent,actor,request,history):
        self.agent=agent;self.actor=actor;self.request=request;self.knowledge=agent.knowledge
        self.budget=agent.budget_factory();self.revision=1;self.completed={};self.trace=[]
        self.plan=None;self.evidence={};self.artifacts=[];self.cache={};self.repeat={};self.mutated=False
        self.task=TaskContext(request.question,self.budget.max_model_calls,self.budget.max_tool_calls)
        self.task.data['planning_mode']=request.mode
        self.history=[{'question':r['question'][:1000],'answer':r['answer'] if len(r['answer'])<=2500 else r['answer'][:1200]+'\n[excerpt omitted]\n'+r['answer'][-1200:]} for r in (history or [])[-6:]]
        self.resolved_question=request.question
        self.verification=None;self.memory=[]

    async def model(self,role,payload,schema,node_id):
        actor=self.knowledge._actor(self.actor)
        payload={'prompt_version':VERSION,'language':self.request.language,**payload}
        size=len(serialized(payload))+len(serialized(schema.model_json_schema()))+len(PROMPTS[role].encode())
        for attempt in range(2):
            self.budget.model(size)
            gate=permit=None;started=time.monotonic()
            span={'role':role,'node_id':node_id,'plan_revision':self.revision,'call':self.budget.model_calls,
                  'model':getattr(self.agent.model,'model','scripted-structured'),'input_bytes':size,'prompt_version':VERSION}
            event('model_started',span)
            try:
                if self.knowledge.cases:
                    from ..runtime.provider import ProviderGate
                    gate=ProviderGate(self.knowledge.repository)
                    # Distributed provider slots serialize actual inference as configured. Ready
                    # specialist tasks and their read tools still run concurrently.
                    while permit is None:
                        try:
                            permit=await anyio.to_thread.run_sync(gate.acquire,actor.organization_id);break
                        except CapacityError:
                            self.budget.check();await anyio.sleep(.1)
                with anyio.fail_after(min(45,max(.001,self.budget.deadline_seconds-(time.monotonic()-self.budget.started)))):
                    reply=await self.agent.model.structured(role,payload,schema,self.request.language,self.budget.output_tokens)
                if not isinstance(reply,StructuredReply):reply=StructuredReply(reply)
                event('model_call',span|{'duration_ms':round((time.monotonic()-started)*1000),'usage':reply.usage,
                                       **estimate(span['model'],reply.usage)})
                self.budget.usage(reply.usage)
                self.knowledge._actor(self.actor)
                return schema.model_validate(reply.data)
            except (ValidationError,ValueError,TypeError) as exc:
                details=[{'field':'.'.join(map(str,e['loc'])),'type':e['type'],'message':e['msg'][:150]}
                         for e in exc.errors(include_input=False,include_url=False)] if isinstance(exc,ValidationError) else []
                event('model_schema_error',span|{'fields':details})
                raise InvalidOutput('Response schema: '+('; '.join(d['field']+' '+d['message'] for d in details)[:700] or 'invalid JSON')) from exc
            except Exception as exc:
                if isinstance(exc,(BudgetExceeded,InvalidOutput)):raise
                event('model_failed',span|{'category':'transient' if transient(exc) else 'denied_or_provider','usage':None,'cost_usd':None})
                if not transient(exc) or attempt:raise
                event('agent_retry',span|{'attempt':attempt+1,'category':'transient'})
                await anyio.sleep(.1*(attempt+1))
            finally:
                if gate and permit:
                    with anyio.CancelScope(shield=True):await anyio.to_thread.run_sync(gate.release,permit)
        raise InvalidOutput('Provider unavailable')

    def summary(self):
        return {'revision':self.revision,'mode':self.request.mode,'nodes':[
            {'id':n.id,'role':n.role,'dependencies':n.dependencies,'status':'completed' if n.id in self.completed else 'pending'}
            for n in self.plan.nodes] if self.plan else [],'completed':list(self.completed)}

    def sync(self):
        self.task.data['active_plan_revision']=self.revision
        self.task.data['evidence']=[source_reference(d) for d in list(self.evidence.values())[-12:]]
        self.task.data['budget']=self.budget.snapshot();self.task.save()

    def response(self,status,message='',claims=None):
        self.sync()
        sources={s.source_id for c in (claims or []) for s in c.evidence}
        return AgentResponse(status=status,answer=message or render(claims,self.request.language),
            sources=[self.evidence[i] for i in sorted(sources)],files=self.artifacts,
            tool_trace=self.trace,model_calls=self.budget.model_calls,tool_calls=self.budget.tool_calls,
            agent_mode=self.request.mode,plan=self.summary(),verification=self.verification,budget=self.budget.snapshot())

    async def plan_next(self,feedback=None):
        payload={'question':self.resolved_question,'original_message':self.request.question,'history':self.history,'task_context':self.task.data,
                 'capabilities':{k:sorted(v) for k,v in ROLE_TOOLS.items() if k!='single'},
                 'tool_descriptions':{name:DESCRIPTIONS[name] for name in ROLE_TOOLS['single']},
                 'completed':{k:{'node':next((n.model_dump() for n in self.plan.nodes if n.id==k),None) if self.plan else None,
                                  'result':v.handoff.model_dump()} for k,v in self.completed.items()},
                 'feedback':feedback}
        for attempt in range(2):
            try:
                proposed=await self.model('supervisor',payload,Plan,'supervisor')
                if self.plan:
                    old={n.id:n for n in self.plan.nodes};new={n.id:n for n in proposed.nodes}
                    for ident in list(self.completed):
                        if ident not in new or new[ident]!=old[ident]:
                            if self.completed[ident].mutated:raise Denied('Completed mutation cannot be replaced')
                            del self.completed[ident]
                    for _ in range(8):
                        for ident in list(self.completed):
                            if not set(new[ident].dependencies)<=self.completed.keys():
                                if self.completed[ident].mutated:raise Denied('A completed mutation dependency cannot be replaced')
                                del self.completed[ident]
                self.plan=proposed;save_plan(self.revision,proposed)
                event('plan_validated',{'revision':self.revision,'nodes':len(proposed.nodes),'roles':[n.role for n in proposed.nodes],
                                        'decision':proposed.decision,'prompt_version':VERSION})
                self.sync();return
            except InvalidOutput as exc:
                if attempt:raise
                self.budget.repair();payload['feedback']=str(exc)

    def handoff_context(self,dependencies):
        evidence={key:doc for dependency in dependencies for key,doc in self.completed[dependency].evidence.items()}
        return ({dependency:self.completed[dependency].handoff.model_dump(mode='json') for dependency in dependencies},evidence)

    async def tool(self,node,role,gateway,call):
        self.budget.tool();self.knowledge._actor(self.actor)
        if call.name not in ROLE_TOOLS[role] or (role!='single' and call.name not in node.tools):
            if TOOL_RISK.get(call.name)==0:
                self.budget.repair()
                return {'ok':False,'error':{'code':'TOOL_NOT_IN_NODE','message':'Choose an available tool for this node. For typed records use get_deal/get_sla_case, not read_document.',
                    'available_tools':sorted(ROLE_TOOLS[role] if role=='single' else set(node.tools)&ROLE_TOOLS[role])}}
            raise Denied('Tool outside specialist capability')
        # Canonicalize schema defaults before repetition detection and dispatch.
        try:args=SCHEMAS[call.name].model_validate(call.arguments).model_dump()
        except ValidationError:
            return {'ok':False,'error':{'code':'INVALID_ARGUMENTS','message':'Use the declared schema; identity is server-owned.'}}
        key=hashlib.sha256(serialized([call.name,args])).hexdigest()
        cache=self.cache.get(key)
        if cache:
            try:current_sources(self.knowledge,self.actor,cache['evidence'])
            except EvidenceChanged:self.cache.pop(key,None)
            else:
                self.repeat[key]=self.repeat.get(key,0)+1
                if self.repeat[key]>2:raise InvalidOutput('Repeated ineffective tool selection')
                gateway.evidence.update(cache['evidence'])
                event('tool_cache_hit',{'node_id':node.id,'tool':call.name,'plan_revision':self.revision})
                return cache['result']
        changing=TOOL_RISK[call.name]>0
        if changing:
            # Fresh inputs must still match just before drafting/starting a review.
            current_sources(self.knowledge,self.actor,gateway.evidence)
        started=time.monotonic()
        for attempt in range(2):
            try:result=await anyio.to_thread.run_sync(gateway.execute,call.name,args);break
            except (TimeoutError,httpx.HTTPError):
                if changing:raise UnknownOutcome('Mutation outcome unknown; inspect saved artifacts/workflows before retrying') from None
                if attempt:raise
                self.budget.tool();event('agent_retry',{'node_id':node.id,'tool':call.name,'category':'read_transient'})
                await anyio.sleep(.1)
        ids=([result['document']['id']] if result.get('document') else [h['document_id'] for h in result.get('hits',[])])
        trace=ToolEvent(tool=call.name,outcome='ok' if result.get('ok') else result.get('error',{}).get('code','error'),
            document_ids=ids,risk_tier=TOOL_RISK[call.name],duration_ms=round((time.monotonic()-started)*1000),
            role=role,node_id=node.id,plan_revision=self.revision)
        self.trace.append(trace);event('tool_call',trace.model_dump())
        if result.get('ok'):
            self.cache[key]={'result':result,'evidence':dict(gateway.evidence)}
            self.mutated=self.mutated or changing
        else:
            code=result.get('error',{}).get('code')
            if code=='CONFLICT':raise EvidenceChanged('Tool version conflict')
            if code in ('APPROVAL_REQUIRED','UNKNOWN_TOOL'):raise Denied('Requested action is unavailable')
            self.repeat[key]=self.repeat.get(key,0)+1
            if self.repeat[key]>1:raise InvalidOutput('Repeated ineffective tool call')
        return result

    async def worker(self,node,role=None):
        role=role or node.role
        handoffs,inherited=self.handoff_context(node.dependencies)
        current_sources(self.knowledge,self.actor,inherited)
        gateway=ToolGateway(self.knowledge,self.actor,self.request.language,self.agent.files)
        gateway.evidence.update(inherited)
        tools=ROLE_TOOLS[role] if role=='single' else set(node.tools)
        payload={'question':self.resolved_question,'original_message':self.request.question,'history':self.history,'objective':node.objective,'handoffs':handoffs,
            'sources':[{'id':d.id,'version':d.version,'content':d.content[:5000],'truncated':len(d.content)>5000} for d in inherited.values()],
            'memories':self.memory,'tools':{name:SCHEMAS[name].model_json_schema() for name in sorted(tools)},
            'tool_descriptions':{name:DESCRIPTIONS[name] for name in sorted(tools)},'observations':[]}
        save_step(self.revision,node,'running',{'dependencies':node.dependencies})
        event('specialist_started',{'node_id':node.id,'role':role,'plan_revision':self.revision,'dependencies':node.dependencies})
        before=len(gateway.artifacts);changed=False
        try:
            for _ in range(node.max_calls):
                try:turn=await self.model(role,payload,WorkerTurn,node.id)
                except InvalidOutput as exc:
                    self.budget.repair();payload['feedback']=str(exc);continue
                if turn.calls:
                    if len(turn.calls)>self.budget.max_tool_calls-self.budget.tool_calls:raise BudgetExceeded('tool batch')
                    for call in turn.calls:
                        result=await self.tool(node,role,gateway,call)
                        changed=changed or bool(result.get('ok') and TOOL_RISK.get(call.name,0)>0)
                        payload['observations'].append({'tool':call.name,'arguments':call.arguments,'result':result})
                    continue
                result=turn.result
                if (any(i not in gateway.evidence for i in result.source_ids) or
                    any(s.source_id not in gateway.evidence for claim in result.claims for s in claim.evidence) or
                    (result.status=='complete' and not gateway.evidence)):
                    self.budget.repair()
                    payload['feedback']='Read the source with read_document or the typed case/deal tool before claiming completion. Search previews are not reads. Currently read IDs: '+', '.join(gateway.evidence)
                    continue
                if node.role=='analyst' and result.status=='complete' and not any(
                    o['result'].get('ok') and o['tool'] in ('count_workspace_cases','list_sla_cases','list_deals','calculate_sla_impact','calculate_deal_terms','get_sla_case','get_deal')
                    for o in payload['observations']):
                    self.budget.repair()
                    payload['feedback']='Before completing, call the typed list, count, read or calculation tool for your objective. An inherited handoff does not satisfy this independent check.'
                    continue
                if result.status=='not_found' and not payload['observations']:raise InvalidOutput('Investigate before reporting no evidence')
                current_sources(self.knowledge,self.actor,gateway.evidence)
                returned=NodeResult(result,dict(gateway.evidence),gateway.artifacts[before:],changed)
                save_step(self.revision,node,'completed',{'handoff':result.model_dump(),'sources':[source_reference(d) for d in gateway.evidence.values()]})
                event('specialist_completed',{'node_id':node.id,'role':role,'plan_revision':self.revision,
                    'source_ids':list(gateway.evidence),'status':result.status,'claim_count':len(result.claims)})
                return returned
            raise BudgetExceeded('specialist call budget')
        except BaseException:
            # Preserve successful private artifacts and accepted review work, even on a later failure.
            self.evidence.update(gateway.evidence)
            for artifact in gateway.artifacts:
                if all(a.id!=artifact.id for a in self.artifacts):self.artifacts.append(artifact)
            save_step(self.revision,node,'stopped',{'partial_artifact_ids':[a.id for a in gateway.artifacts]})
            raise

    async def verify(self,node):
        handoffs,evidence=self.handoff_context(node.dependencies)
        current_sources(self.knowledge,self.actor,evidence)
        # Prefer the Action's synthesis; otherwise inspect the union of independent handoffs.
        action=[self.completed[d].handoff for d in node.dependencies if next((n.role for n in (self.plan.nodes if self.plan else []) if n.id==d),'')=='action']
        drafts=action or [self.completed[d].handoff for d in node.dependencies]
        claims=[];seen=set()
        for draft in drafts:
            for claim in draft.claims:
                key=claim.model_dump_json()
                if key not in seen:seen.add(key);claims.append(claim)
        if len(claims)>12:raise InvalidOutput('Claim budget exceeded')
        draft=Handoff(status='complete' if claims else 'needs_input',claims=claims,source_ids=list(evidence)[:12])
        if not claims:
            self.evidence.update(evidence)
            return self.response('needs_clarification','请补充案例编号或缺失的事实。' if self.request.language=='zh' else 'Please supply the case/deal ID or missing facts before continuing.')
        save_step(self.revision,node,'running',{'claim_count':len(claims)})
        while True:
            current_sources(self.knowledge,self.actor,evidence)
            deterministic=validate_claims(draft.claims,evidence)
            # Provide surrounding source context, not just model-selected quotes.
            reviews=[]
            for index,claim in enumerate(draft.claims):
                supports=[]
                for s in claim.evidence:
                    doc=evidence.get(s.source_id)
                    if doc:
                        at=doc.content.find(s.quote);start=max(0,at-250)
                        supports.append({'source_id':doc.id,'version':doc.version,'excerpt':doc.content[start:start+len(s.quote)+500]})
                reviews.append({'index':index,'claim':claim.model_dump(),'sources':supports})
            review=await self.model('critic',{'claims':reviews},Review,node.id)
            errors=deterministic+review_errors(draft.claims,review)
            event('claims_checked',{'node_id':node.id,'plan_revision':self.revision,'claims':len(draft.claims),
                'supported':len(draft.claims)-len({e['index'] for e in errors if e['index']>=0}),
                'passed':not errors,'errors':[{'index':e['index'],'code':e['code']} for e in errors]})
            if not errors:
                current_sources(self.knowledge,self.actor,evidence)
                self.evidence.update(evidence)
                self.verification={'checked_claims':len(draft.claims),'supported_claims':len(draft.claims),
                    'method':'deterministic_provenance_and_independent_semantic_critic','repairs':self.budget.repairs,
                    'claims':[c.model_dump() for c in draft.claims]}
                self.completed[node.id]=NodeResult(draft,evidence)
                save_step(self.revision,node,'completed',{'verification':self.verification})
                return self.response('answered',claims=draft.claims)
            self.budget.repair()
            event('claim_repair_requested',{'node_id':node.id,'plan_revision':self.revision,'repair':self.budget.repairs})
            draft=await self.model('repair',{'question':self.resolved_question,'original_message':self.request.question,'history':self.history,'draft':draft.model_dump(),
                'feedback':errors,'sources':[{'id':d.id,'version':d.version,'content':d.content[:5000]} for d in evidence.values()]},Handoff,'repair')
            if not draft.claims:
                return self.response('needs_clarification','Evidence could not support a verified answer. Please review the missing facts.' if self.request.language=='en' else '证据不足以支持已验证的回答，请补充事实。')

    async def rebase(self):
        if self.mutated:raise UnknownOutcome('Inputs changed after a private action; inspect saved work before retrying')
        self.budget.replan();invalid=set()
        for ident,result in self.completed.items():
            try:current_sources(self.knowledge,self.actor,result.evidence)
            except EvidenceChanged:invalid.add(ident)
        # Invalidate every dependent step, even one with no direct evidence pointer.
        for _ in range(8):
            for node in self.plan.nodes:
                if set(node.dependencies)&invalid:invalid.add(node.id)
        for ident in invalid:self.completed.pop(ident,None)
        self.evidence={k:d for result in self.completed.values() for k,d in result.evidence.items()}
        self.cache={};self.repeat={};self.revision+=1
        event('evidence_reset',{'references':[source_reference(d) for d in self.evidence.values()]})
        event('plan_invalidated',{'revision':self.revision-1,'invalidated_nodes':sorted(invalid),'preserved_nodes':list(self.completed)})
        await self.plan_next('Source changed: refresh invalidated evidence and dependent calculations/drafts. Preserve unchanged completed nodes.')

    async def run(self):
        self.knowledge._actor(self.actor)
        try:
            intake_payload={'question':self.request.question,'history':self.history,'capabilities':CAPABILITIES,
                            'mode':self.request.mode}
            try:
                intake=await self.model('intake',intake_payload,Intake,'intake')
            except InvalidOutput as exc:
                self.budget.repair()
                intake=await self.model('intake',intake_payload|{'feedback':str(exc)},Intake,'intake')
            event('intake_routed',{'route':intake.route})
            self.resolved_question=intake.resolved_request or self.request.question
            self.task.data['resolved_request']=self.resolved_question
            if intake.route!='investigate':
                check=await self.model('conversation_guard',intake_payload|{'reply':intake.reply},ConversationCheck,'conversation_guard')
                event('conversation_checked',{'passed':check.safe})
                if check.safe:
                    return self.response('needs_clarification' if intake.route=='clarify' else 'answered',intake.reply)
                # A casual route never authorizes factual prose or tools; rejected replies
                # fall through to the normal evidence-backed investigation.
            if self.knowledge.cases:
                from ..memory.service import MemoryService
                memories=await anyio.to_thread.run_sync(MemoryService(self.knowledge.repository).recall,self.actor,self.request.question)
                self.memory=[{'kind':r['kind'],'value':r['value']} for r in memories]
                self.task.data['memories']=[{'id':r['id'],'version':r['version']} for r in memories]
            self.sync()
            if self.request.mode=='single':
                node=Node(id='single',role='action',objective='Resolve the requested task with current evidence',
                          tools=[],completion='draft_ready',output_schema='handoff_v1',max_calls=5)
                result=await self.worker(node,role='single');self.completed[node.id]=result
                self.evidence.update(result.evidence);self.artifacts.extend(result.artifacts)
                critic=Node(id='critic',role='critic',objective='Check every final claim',dependencies=['single'],
                            completion='claims_verified',output_schema='claim_verdicts_v1')
                return await self.verify(critic)
            await self.plan_next()
            while True:
                if self.plan.decision=='clarify':return self.response('needs_clarification',
                    '请提供要处理的案例或交易编号，以及所需操作。' if self.request.language=='zh' else 'Please specify the case or deal and the action you want.')
                self.budget.check()
                try:
                    ready=[n for n in self.plan.nodes if n.id not in self.completed and set(n.dependencies)<=self.completed.keys()]
                    if not ready:raise InvalidOutput('No ready node')
                    critic=next((n for n in ready if n.role=='critic'),None)
                    if critic:return await self.verify(critic)
                    # Ready independent reads run concurrently; all mutation-capable work is serial.
                    readers=[n for n in ready if n.role in ('knowledge','analyst')]
                    selected=readers or ready[:1]
                    results=await asyncio.gather(*(self.worker(n) for n in selected),return_exceptions=True)
                    failures=[]
                    for node,result in zip(selected,results):
                        if isinstance(result,BaseException):failures.append(result);continue
                        self.completed[node.id]=result;self.evidence.update(result.evidence);self.artifacts.extend(result.artifacts)
                    self.sync()
                    if failures:
                        failure=next((e for e in failures if isinstance(e,(Denied,PermissionError))),failures[0])
                        raise failure
                except EvidenceChanged:
                    await self.rebase()
        except BudgetExceeded as exc:
            limit=str(exc)
            event('collaboration_stopped',{'reason':'budget','limit':limit,'budget':self.budget.snapshot()})
            messages={
                'repair budget':('I could not produce a verified answer after two correction attempts. Please check the available evidence or tell me which part to investigate next.', '两次修正后仍无法生成通过验证的回答。请检查现有证据，或告诉我接下来要调查哪一部分。'),
                'deadline':('The investigation took too long and stopped. Please review any saved work before retrying.', '调查耗时过长，已停止。重试前请查看已保存的结果。'),
            }
            message=messages.get(limit,('The investigation reached its '+limit+' limit. Review saved work before continuing.', '调查已达到限制（'+limit+'）。继续前请查看已保存的结果。'))
            return self.response('budget_exceeded',message[self.request.language=='zh'])
        except PermissionError:raise
        except (Denied,InvalidOutput,UnknownOutcome,EvidenceChanged,TimeoutError,httpx.HTTPError,genai.errors.APIError,CapacityError) as exc:
            event('collaboration_stopped',{'reason':type(exc).__name__,'budget':self.budget.snapshot(),
                'detail':str(exc)[:200] if isinstance(exc,(Denied,InvalidOutput,UnknownOutcome,EvidenceChanged)) else 'Provider or transport failure'})
            return self.response('invalid_response','The investigation stopped safely. Review current sources and any saved draft or workflow before retrying.' if self.request.language=='en' else '调查已安全停止。重试前请检查当前来源及已保存的草稿或工作流。')
        except Exception as exc:
            event('collaboration_stopped',{'reason':'internal','exception_type':type(exc).__name__,'budget':self.budget.snapshot()})
            return self.response('invalid_response','Investigation interrupted. Review saved work before retrying.' if self.request.language=='en' else '调查已中断，请在重试前查看已保存的结果。')
