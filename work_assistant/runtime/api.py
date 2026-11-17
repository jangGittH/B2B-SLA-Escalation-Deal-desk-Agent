"""Authorized progress replay and tenant/participant operating summaries."""
import json
import time
from datetime import timedelta
import anyio
from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, case, extract, cast, Float, and_
from ..models import Employee
from ..workflows import tables as w
from ..db.models import runs, events as chat_events
from ..workflows.service import WorkflowService, canonical
from ..deals.workflow import DealWorkflowService
from . import tables as t
from .queue import now, limits, TERMINAL
from decimal import Decimal


def service_for(db,kind): return (DealWorkflowService if kind=='deal' else WorkflowService)(db)


def authorized_events(db,actor_id,kind,wid,after):
    service=service_for(db,kind)
    actor=service.records.actor(actor_id)
    with db.engine.connect() as c:
        row=service._row(c,wid,actor,lock=False)
        rows=c.execute(select(w.events).where(w.events.c.workflow_id==wid,w.events.c.organization_id==actor.organization_id,
            w.events.c.id>after).order_by(w.events.c.id).limit(100)).mappings().all()
    return row,[canonical(dict(event)) for event in rows]


def summary(db,actor,days=7):
    stamp=now();since=stamp-timedelta(days=days)
    tenant=w.workflows.c.organization_id==actor.organization_id
    scope=tenant if 'operations' in actor.team_ids else and_(tenant,(w.workflows.c.employee_id==actor.id)|(w.workflows.c.reviewer_id==actor.id))
    window=and_(scope,w.workflows.c.created_at>=since)
    ids=select(w.workflows.c.id).where(scope)
    period_ids=select(w.workflows.c.id).where(window)
    with db.engine.connect() as c:
        statuses=[{'kind':'deal' if deal else 'sla','status':status,'count':count} for deal,status,count in c.execute(
            select(w.workflows.c.deal_id.is_not(None),w.workflows.c.status,func.count()).where(window).group_by(w.workflows.c.deal_id.is_not(None),w.workflows.c.status))]
        queue=dict(c.execute(select(t.jobs.c.status,func.count()).where(t.jobs.c.workflow_id.in_(ids)).group_by(t.jobs.c.status)).all())
        closed=w.workflows.c.status.in_(TERMINAL)
        human=w.workflows.c.status.in_(['waiting_for_approval','waiting_for_input'])
        elapsed=func.greatest(0,extract('epoch',stamp-w.workflows.c.updated_at)*1000)
        human_ms=w.workflows.c.human_wait_ms+case((human,elapsed),else_=0)
        system_ms=w.workflows.c.system_elapsed_ms+case((and_(~closed,~human),elapsed),else_=0)
        timing=c.execute(select(func.count(),func.percentile_cont(.5).within_group(system_ms),func.percentile_cont(.95).within_group(system_ms),
            func.sum(human_ms),func.sum(system_ms)).where(window,w.workflows.c.timing_started_at.is_not(None))).one()
        work=c.execute(select(func.count(),func.percentile_cont(.95).within_group(t.attempts.c.queue_wait_ms),func.sum(t.attempts.c.duration_ms)).where(
            t.attempts.c.workflow_id.in_(period_ids))).one()
        recent=[dict(r) for r in c.execute(select(w.workflows.c.id,w.workflows.c.ticket_id,w.workflows.c.deal_id,w.workflows.c.status,
            w.workflows.c.conversation_id,w.workflows.c.origin_run_id,w.workflows.c.approval_id,w.workflows.c.updated_at,
            t.jobs.c.status.label('delivery_status'),t.jobs.c.last_error,w.receipts.c.operation_id).outerjoin(t.jobs,t.jobs.c.workflow_id==w.workflows.c.id)
            .outerjoin(w.receipts,w.receipts.c.workflow_id==w.workflows.c.id)
            .where(window,(w.workflows.c.employee_id==actor.id)|(w.workflows.c.reviewer_id==actor.id)).order_by(w.workflows.c.updated_at.desc()).limit(30)).mappings()]
        model_scope=runs.c.organization_id==actor.organization_id
        if 'operations' not in actor.team_ids: model_scope=and_(model_scope,runs.c.employee_id==actor.id)
        model_rows=c.execute(select(chat_events.c.kind,chat_events.c.payload).join(runs,runs.c.id==chat_events.c.run_id).where(
            model_scope,chat_events.c.created_at>=since.timestamp(),chat_events.c.kind.in_(['model_started','model_call','model_failed']))).all()
        tokens={'prompt':0,'output':0,'total':0};known=0;failed=0;started=0;reported=0
        priced=0;cost=Decimal(0);price_versions=set()
        for kind,p in model_rows:
            if kind=='model_started': started+=1;continue
            if kind=='model_failed': failed+=1;continue
            reported+=1
            if p.get('cost_usd') is not None:
                cost+=Decimal(str(p['cost_usd']));priced+=1;price_versions.add(p.get('pricing_version'))
            usage=p.get('usage')
            if usage and usage.get('total_token_count') is not None: known+=1
            if usage:
                for label,key in [('prompt','prompt_token_count'),('output','candidates_token_count'),('total','total_token_count')]:
                    tokens[label]+=usage.get(key) or 0
        latest=c.execute(select(func.max(t.workers.c.heartbeat_at)).where(t.workers.c.draining==0)).scalar_one()
        oldest=c.execute(select(func.min(t.jobs.c.enqueued_at)).where(t.jobs.c.workflow_id.in_(ids),t.jobs.c.status=='pending')).scalar_one()
        stale_reviews=c.execute(select(func.count()).select_from(w.approvals).where(w.approvals.c.workflow_id.in_(ids),
            w.approvals.c.status=='pending',w.approvals.c.expires_at<=stamp)).scalar_one()
        cfg=limits(c)
        agent_events=c.execute(select(chat_events.c.kind,chat_events.c.payload).join(runs,runs.c.id==chat_events.c.run_id).where(
            model_scope,chat_events.c.created_at>=since.timestamp(),chat_events.c.kind.in_(['claims_checked','plan_validated','plan_invalidated','collaboration_stopped']))).all()
        reviews=[p for kind,p in agent_events if kind=='claims_checked']
        decisions=dict(c.execute(select(w.approvals.c.decision,func.count()).where(w.approvals.c.workflow_id.in_(period_ids),
            w.approvals.c.decision.is_not(None)).group_by(w.approvals.c.decision)).all())
        triage=[max(0,(row.created_at-row.received).total_seconds()*1000) for row in c.execute(
            select(w.events.c.created_at,w.workflows.c.created_at.label('received')).join(w.workflows,w.workflows.c.id==w.events.c.workflow_id)
            .where(window,w.events.c.kind=='approval_requested')).all()]
    return canonical({'scope':'tenant' if 'operations' in actor.team_ids else 'participant','window_days':days,'as_of':stamp,
        'outcomes':statuses,'queue':queue,'recent':recent,'limits':cfg.model_dump(),
        'timing':{'covered_workflows':timing[0],'system_elapsed_p50_ms':timing[1],'system_elapsed_p95_ms':timing[2],
            'human_wait_total_ms':timing[3] or 0,'system_elapsed_total_ms':timing[4] or 0,'delivery_attempts':work[0],
            'queue_wait_p95_ms':work[1],'worker_active_total_ms':work[2] or 0},
        'model':{'calls':max(started,reported),'failed':failed,'known_tokens':tokens,'unknown_usage_calls':max(started,reported)-known,
            'cost_usd':str(cost) if priced and priced==max(started,reported) else None,
            'known_estimated_cost_usd':str(cost) if priced else None,'unknown_cost_calls':max(started,reported)-priced,
            'pricing_version':next(iter(price_versions)) if len(price_versions)==1 else None,
            'cost_basis':'Standard paid text API list-price estimate; not actual billing or total resolution cost'},
        'agents':{'plan_revisions':sum(kind=='plan_validated' for kind,_ in agent_events),
            'invalidations':sum(kind=='plan_invalidated' for kind,_ in agent_events),
            'stopped':sum(kind=='collaboration_stopped' for kind,_ in agent_events),
            'claim_checks':sum(r['claims'] for r in reviews),'supported_checks':sum(r['supported'] for r in reviews),
            'rejected_drafts':sum(not r['passed'] for r in reviews)},
        'business':{'review_decisions':decisions,'approved_decisions':decisions.get('approve',0)+decisions.get('modify',0),
            'modified_approvals':decisions.get('modify',0),'draft_ready_samples':len(triage),
            'draft_ready_p50_ms':sorted(triage)[len(triage)//2] if triage else None,
            'definition':'Draft-ready observations are approval-request events, including revisions; no customer-resolution or manual ROI baseline'},
        'alerts':{'dead_deliveries':queue.get('dead',0),'expired_reviews':stale_reviews,
            'oldest_pending_seconds':max(0,(stamp-oldest).total_seconds()) if oldest else 0,
            'worker_seen_recently':bool(latest and stamp-latest<timedelta(seconds=30))}})


def install_routes(app,db,authorize):
    @app.get('/v1/operations')
    def operations(days:int=Query(default=7,ge=1,le=30),actor:Employee=Depends(authorize)):
        return summary(db,actor,days)

    @app.get('/health/ready')
    def ready():
        try:
            with db.engine.connect() as c:
                c.execute(select(t.settings.c.id)).scalar_one()
                c.execute(select(w.workflows.c.timing_started_at).limit(1)).first()
            return {'status':'ready','dispatch':'postgresql_durable_queue'}
        except Exception:
            raise HTTPException(503,'Database or runtime schema unavailable') from None

    def stream_route(kind):
        async def stream(workflow_id:str,request:Request,after:int=Query(default=0,ge=0),last_event_id:str|None=Header(default=None),actor:Employee=Depends(authorize)):
            try:
                cursor=max(after,int(last_event_id or 0))
                if cursor<0: raise ValueError()
            except ValueError:
                raise HTTPException(422,'Invalid event cursor') from None
            try: await anyio.to_thread.run_sync(authorized_events,db,actor.id,kind,workflow_id,cursor)
            except (PermissionError,LookupError): raise HTTPException(404,'Workflow unavailable') from None
            async def generate():
                nonlocal cursor
                started=time.monotonic()
                yield ': connected\n\n'
                while time.monotonic()-started<25 and not await request.is_disconnected():
                    try:
                        row,events=await anyio.to_thread.run_sync(authorized_events,db,actor.id,kind,workflow_id,cursor)
                    except (LookupError,PermissionError):
                        yield 'event: revoked\ndata: {}\n\n';return
                    for event in events:
                        cursor=event['id']
                        yield f'id: {cursor}\nevent: progress\ndata: {json.dumps(event,ensure_ascii=False)}\n\n'
                    if row['status'] in TERMINAL and len(events)<100:
                        yield 'event: closed\ndata: {}\n\n';return
                    if not events: yield ': heartbeat\n\n'
                    await anyio.sleep(.5)
            return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
        return stream
    for kind in ('sla','deal'):
        app.add_api_route(f'/v1/{kind}-workflows/{{workflow_id}}/events',stream_route(kind),methods=['GET'],name=kind+'_workflow_events')
