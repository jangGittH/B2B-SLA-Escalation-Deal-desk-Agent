"""Authoritative approval and sandbox action state, committed in short transactions."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import select, insert, update
from sqlalchemy.exc import IntegrityError

from ..db.models import entities, conversations
from ..db.store import ConflictError
from ..models import Employee, Document
from ..knowledge import can_read, view
from ..sla import tables as sla
from ..sla.domain import Ticket, Contract, Policy
from ..sla.calculator import calculate
from ..sla.service import SlaService, memo_text
from ..telemetry import current_run
from . import tables as t

TERMINAL = {'completed', 'rejected', 'cancelled', 'failed', 'partial'}
REVIEW_POLICY = 'sandbox-sla-review-v1'
MAX_PROPOSALS = 5


def now():
    return datetime.now(timezone.utc)


def canonical(value):
    return json.loads(json.dumps(value, default=lambda x: x.isoformat() if isinstance(x, datetime) else str(x), ensure_ascii=False))


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def employee(conn, employee_id, organization_id):
    payload = conn.execute(select(entities['employees'].c.payload).where(
        entities['employees'].c.id == employee_id,
        entities['employees'].c.organization_id == organization_id).with_for_update(read=True)).scalar_one_or_none()
    actor = Employee.model_validate(payload) if payload else None
    if not actor or not actor.active:
        raise PermissionError('Workflow participant is no longer active')
    return actor


def workflow_snapshot(service, conn, actor, ticket_id, reviewer_id):
    """Sandbox ledger is authoritative; incident prior-credit attestations cannot change it."""
    snapshot = service._case(conn, actor, ticket_id)
    ticket = Ticket.model_validate(snapshot['ticket'])
    contract = Contract.model_validate(snapshot['contract'])
    policy = Policy.model_validate(snapshot['policy'])
    # Keep financial terms stable until the enclosing action/decision transaction commits.
    for table, rid in [(sla.accounts, contract.account_id), (sla.policies, policy.id)]:
        conn.execute(select(table.c.id).where(table.c.organization_id == actor.organization_id,
            table.c.id == rid).with_for_update(read=True)).first()
    rows = list(conn.execute(select(t.credits).where(t.credits.c.organization_id == actor.organization_id,
        t.credits.c.contract_id == contract.id, t.credits.c.period_start == ticket.billing_period_start
        ).order_by(t.credits.c.operation_id)).mappings()) if ticket.billing_period_start else []
    total = sum((r['amount'] for r in rows), Decimal('0.00'))
    result = calculate(ticket.model_copy(update={'prior_credit_amount': total}), contract, policy)
    if snapshot['overlapping_ticket_ids']:
        result.update(status='missing_inputs', proposal_amount=None)
        result['missing_inputs'].append('overlapping_incidents_require_consolidation')
    if any(r['currency'] != contract.currency or r['period_end'] != ticket.billing_period_end for r in rows):
        result.update(status='missing_inputs', proposal_amount=None)
        result['missing_inputs'].append('ledger_currency_or_period_requires_reconciliation')
    result['prior_credit_source'] = 'sandbox_ledger'
    result['recorded_prior_credit_attestation'] = snapshot['ticket']['prior_credit_amount']
    snapshot['calculation'] = result
    snapshot['ledger'] = {'total': f'{total:.2f}', 'entry_count': len(rows),
                          'checksum': digest([dict(r) for r in rows]), 'scope': 'sandbox_only'}
    snapshot['reviewer_id'] = reviewer_id
    reviewer = employee(conn, reviewer_id, actor.organization_id)
    if reviewer.id == actor.id or 'operations' not in reviewer.team_ids:
        raise PermissionError('A separate active Operations reviewer is required')
    source_ids = {contract.account_id + '-sla', ticket_id.lower() + '-history',
                  ticket_id.lower() + '-runbook', 'operating-guide'}
    documents = conn.execute(select(entities['documents'].c.payload).where(
        entities['documents'].c.organization_id == actor.organization_id,
        entities['documents'].c.id.in_(source_ids)).order_by(entities['documents'].c.id).with_for_update(read=True)).scalars()
    # Share only evidence both participants can currently read. Private runbooks are not promoted.
    snapshot['shared_evidence'] = [view(d, 'en').model_dump(mode='json') for data in documents
                                  if can_read(actor, d := Document.model_validate(data)) and can_read(reviewer, d)]
    snapshot['review_policy'] = REVIEW_POLICY
    return snapshot


class WorkflowService:
    """Shared review lifecycle; business adapters own proposal math and effects."""
    kind = 'sla'
    subject = 'ticket'
    modification_key = 'credit_fraction'
    title = 'SLA workflow'
    tables = t

    @property
    def subject_key(self):
        return self.subject + '_id'

    @property
    def namespace(self):
        return 'northstar-' + self.kind + '-v1:'

    def __init__(self, database, *, clock=now):
        self.db = database
        self.records = self.sla = SlaService(database)
        self.clock = clock

    def _row(self, conn, workflow_id, actor=None, lock=True):
        stmt = select(t.workflows).where(t.workflows.c.id == workflow_id, t.workflows.c[self.subject_key].is_not(None))
        if actor:
            stmt = stmt.where(t.workflows.c.organization_id == actor.organization_id,
                (t.workflows.c.employee_id == actor.id) | (t.workflows.c.reviewer_id == actor.id))
        row = conn.execute(stmt.with_for_update() if lock else stmt).mappings().first()
        if row is None:
            raise LookupError('Workflow not found')
        if actor and row['employee_id'] != actor.id and 'operations' not in actor.team_ids:
            raise LookupError('Workflow not found')
        return dict(row)

    def _reviewer(self, conn, actor):
        rows=conn.execute(select(entities['employees'].c.payload).where(
            entities['employees'].c.organization_id==actor.organization_id).order_by(entities['employees'].c.id)).scalars()
        return next((e for data in rows if (e:=Employee.model_validate(data)).active and e.id!=actor.id and 'operations' in e.team_ids),None)

    def _participants(self, conn, row):
        actors = {pid: employee(conn, pid, row['organization_id'])
                  for pid in sorted({row['employee_id'], row['reviewer_id']} - {None})}
        return actors[row['employee_id']]

    def _event(self, conn, row, kind, payload):
        conn.execute(insert(t.events).values(organization_id=row['organization_id'], workflow_id=row['id'],
            kind=kind, payload=canonical(payload), created_at=self.clock()))

    def _change(self, conn, row, **changes):
        if row.get('timing_started_at') and row['status'] not in TERMINAL:
            field='human_wait_ms' if row['status'] in ('waiting_for_input','waiting_for_approval') else 'system_elapsed_ms'
            changes[field]=row[field]+max(0,round((self.clock()-row['updated_at']).total_seconds()*1000))
        changes.update(version=row['version'] + 1, updated_at=self.clock())
        conn.execute(update(t.workflows).where(t.workflows.c.id == row['id']).values(**changes))
        row.update(changes)

    def _approval(self, conn, row):
        result = conn.execute(select(t.approvals).where(t.approvals.c.id == row['approval_id'],
            t.approvals.c.workflow_id == row['id'], t.approvals.c.organization_id == row['organization_id']
            ).with_for_update()).mappings().first()
        if not result:
            raise ConflictError('Approval is unavailable')
        return dict(result)

    def _snapshot(self, conn, row):
        actor = self._participants(conn, row)
        return workflow_snapshot(self.sla, conn, actor, row['ticket_id'], row['reviewer_id'])

    def _invalidate(self, conn, row, reason, approval_status='stale'):
        if row['approval_id']:
            conn.execute(update(t.approvals).where(t.approvals.c.id == row['approval_id'],
                t.approvals.c.status.in_(['pending', 'approved'])).values(status=approval_status))
        self._change(conn, row, status='waiting_for_input', phase='wait', missing_inputs=[reason],
                     error={'category':'conflict', 'message':reason})
        self._event(conn, row, 'proposal_invalidated', {'reason':reason})

    def _check_start(self, conn, actor, subject_id, expected_version):
        case = self.sla._case(conn, actor, subject_id)
        if case['ticket']['version'] != expected_version:
            raise ConflictError('Case changed; reload before starting')
        paid = conn.execute(select(t.credits.c.operation_id).where(t.credits.c.organization_id == actor.organization_id,
            t.credits.c.ticket_id == subject_id)).first()
        if paid:
            raise ConflictError('This case already has a sandbox credit; review its receipt')

    def _memo(self, conn, row):
        return self.sla._row(conn, sla.memos, employee(conn,row['employee_id'],row['organization_id']), row['memo_id'])

    def _memo_text(self, memo):
        return memo_text(memo)

    def start(self, employee_id, body):
        actor = self.records.actor(employee_id)
        hashed = digest(body.model_dump(mode='json', exclude={'request_key'}))
        try:
            with self.db.engine.begin() as conn:
                self.db._lock_actor(conn, actor)
                old = conn.execute(select(t.workflows).where(t.workflows.c.organization_id == actor.organization_id,
                    t.workflows.c.employee_id == actor.id, t.workflows.c.request_key == body.request_key)).mappings().first()
                if old:
                    if old['request_hash'] != hashed or old[self.subject_key] is None:
                        raise ConflictError('Request key was used for different workflow inputs')
                    return dict(old)
                from ..runtime.queue import admit, enqueue
                admit(conn, actor.organization_id)
                subject_id = getattr(body, self.subject_key)
                self._check_start(conn, actor, subject_id, getattr(body, 'expected_' + self.subject + '_version'))
                reviewer = self._reviewer(conn, actor)
                conversation_id = body.conversation_id
                if conversation_id:
                    self.db._owned(conn, conversations, conversation_id, actor)
                else:
                    conversation_id = uuid4().hex
                    conn.execute(insert(conversations).values(id=conversation_id, organization_id=actor.organization_id,
                        employee_id=actor.id, title=self.title + ': ' + subject_id, language=body.language,
                        created_at=time.time(), updated_at=time.time()))
                context = current_run.get()
                row = dict(id=uuid4().hex, organization_id=actor.organization_id, employee_id=actor.id,
                    reviewer_id=reviewer.id if reviewer else None, **{self.subject_key: subject_id},
                    conversation_id=conversation_id, origin_run_id=context[1]['id'] if context else None,
                    request_key=body.request_key, request_hash=hashed, language=body.language, version=1,
                    status='queued', phase='investigate', proposal_revision=0, snapshot=None, memo_id=None,
                    approval_id=None, missing_inputs=[], error=None, outcome=None, attempts=0, retry_at=None,
                    human_wait_ms=0,system_elapsed_ms=0,timing_started_at=self.clock(),
                    created_at=self.clock(), updated_at=self.clock())
                conn.execute(insert(t.workflows).values(**row))
                enqueue(conn,row,self.kind)
                self._event(conn, row, 'accepted', {self.subject_key:subject_id, 'conversation_id':conversation_id})
                return row
        except IntegrityError:
            raise ConflictError('A workflow is already active for this case') from None

    def list(self, employee_id, ticket_id=None):
        actor = self.records.actor(employee_id)
        with self.db.engine.connect() as conn:
            stmt = select(t.workflows.c.id).where(t.workflows.c.organization_id == actor.organization_id, t.workflows.c[self.subject_key].is_not(None),
                (t.workflows.c.employee_id == actor.id) | ((t.workflows.c.reviewer_id == actor.id)
                if 'operations' in actor.team_ids else False))
            if ticket_id:
                stmt = stmt.where(t.workflows.c[self.subject_key] == ticket_id)
            ids = conn.execute(stmt.order_by(t.workflows.c.created_at.desc()).limit(50)).scalars().all()
        return [self.get(employee_id, wid) for wid in ids]

    def get(self, employee_id, workflow_id):
        actor = self.records.actor(employee_id)
        with self.db.engine.begin() as conn:
            row = self._row(conn, workflow_id, actor)
            approval = self._approval(conn, row) if row['approval_id'] else None
            evidence_current = False
            memo = None
            if row['snapshot'] and row['reviewer_id']:
                try:
                    evidence_current = self._snapshot(conn, row) == row['snapshot']
                    if evidence_current and row['memo_id']:
                        memo = self._memo(conn, row)
                except (LookupError, PermissionError):
                    evidence_current = False
            receipt = conn.execute(select(t.receipts).where(t.receipts.c.workflow_id == workflow_id,
                t.receipts.c.organization_id == actor.organization_id)).mappings().first()
            events = [dict(e) for e in conn.execute(select(t.events).where(t.events.c.workflow_id == workflow_id,
                t.events.c.organization_id == actor.organization_id).order_by(t.events.c.id).limit(200)).mappings()]
        public = {key:row[key] for key in ('id',self.subject_key,'employee_id','reviewer_id','conversation_id','language',
            'origin_run_id','human_wait_ms','system_elapsed_ms','timing_started_at','version','status','phase','proposal_revision','memo_id','missing_inputs','error','outcome','attempts','retry_at','created_at','updated_at')}
        from ..runtime.tables import jobs
        with self.db.engine.connect() as conn:
            delivery=conn.execute(select(jobs.c.status,jobs.c.deliveries,jobs.c.last_error).where(jobs.c.workflow_id==workflow_id)).mappings().first()
        public['delivery']=dict(delivery) if delivery else None
        public.update(snapshot=row['snapshot'] if evidence_current else None, evidence_current=evidence_current,
                      events=events, receipt=dict(receipt) if receipt else None,
                      memo={'id':memo['id'], 'version':memo['version'], 'content':self._memo_text(memo)} if memo else None)
        if approval:
            public['approval'] = {k:approval[k] for k in ('id','version','status','reviewer_id','payload_hash',
                'approved_hash','expires_at','decision','reason','decided_at')}
            public['approval']['stale'] = (not evidence_current or (memo is not None and (memo['version'] != approval['payload']['memo_version'] or digest(memo['notes']) != approval['payload']['memo_hash'])) or self.clock() >= approval['expires_at'])
            public['approval'].update(payload=approval['payload'] if evidence_current else None,
                                      approved_payload=approval['approved_payload'] if evidence_current else None)
        else:
            public['approval'] = None
        return canonical(public)

    def investigate(self, workflow_id):
        with self.db.engine.begin() as conn:
            row = self._row(conn, workflow_id)
            if row['phase'] != 'investigate' or row['status'] in TERMINAL:
                return row['phase']
            actor = self._participants(conn, row)
            if row['reviewer_id'] is None:
                reviewer = self._reviewer(conn, actor)
                if reviewer:
                    self._change(conn, row, reviewer_id=reviewer.id)
                else:
                    self._change(conn, row, status='waiting_for_input', phase='wait', missing_inputs=['separate_operations_reviewer'])
                    self._event(conn, row, 'clarification_required', {'fields':['separate_operations_reviewer']})
                    return 'wait'
            snapshot = self._snapshot(conn, row)
            missing = snapshot['calculation']['missing_inputs']
            if missing:
                self._change(conn, row, status='waiting_for_input', phase='wait', snapshot=snapshot, missing_inputs=missing)
                self._event(conn, row, 'clarification_required', {'fields':missing})
                return 'wait'
            self._change(conn, row, status='running', phase='prepare', snapshot=snapshot, missing_inputs=[], error=None)
            self._event(conn, row, 'investigated', {'sources':snapshot['provenance'],
                'formula_version':snapshot['calculation']['formula_version'], 'ledger':snapshot.get('ledger'),
                'proposal_amount':snapshot['calculation']['proposal_amount']})
            return 'prepare'

    def prepare(self, workflow_id):
        with self.db.engine.begin() as conn:
            row = self._row(conn, workflow_id)
            if row['phase'] != 'prepare' or row['status'] in TERMINAL:
                return row['phase']
            snapshot = self._snapshot(conn, row)
            if snapshot != row['snapshot']:
                self._invalidate(conn, row, 'case_or_evidence_changed')
                return 'wait'
            if row['proposal_revision'] >= MAX_PROPOSALS:
                self._change(conn, row, status='failed', phase='end', error={'category':'budget','message':'Proposal revision limit reached'})
                return 'end'
            mid, aid = uuid4().hex, uuid4().hex
            revision = row['proposal_revision'] + 1
            payload = self._create_proposal(conn, row, snapshot, mid, revision)
            conn.execute(insert(t.approvals).values(id=aid, organization_id=row['organization_id'], workflow_id=row['id'],
                proposal_revision=revision, reviewer_id=row['reviewer_id'], version=1, status='pending', payload=payload,
                payload_hash=digest(payload), expires_at=self.clock()+timedelta(hours=24)))
            self._change(conn,row,status='waiting_for_approval',phase='wait',proposal_revision=revision,memo_id=mid,approval_id=aid)
            self._event(conn,row,'approval_requested',{'approval_id':aid,'memo_id':mid,'proposal_revision':revision,'risk_tier':2})
            return 'wait'

    def _create_proposal(self, conn, row, snapshot, mid, revision):
        memo = dict(id=mid, organization_id=row['organization_id'], version=1, ticket_id=row['ticket_id'],
            employee_id=row['employee_id'], run_id=row['origin_run_id'], language=row['language'], snapshot=snapshot,
            notes={'hypotheses':'', 'next_steps':'Review the bounded sandbox credit proposal.', 'customer_response_draft':''},
            updated_at=self.clock())
        conn.execute(insert(sla.memos).values(**memo))
        conn.execute(insert(sla.revisions).values(organization_id=row['organization_id'],memo_id=mid,version=1,payload=canonical(memo)))
        amount = snapshot['calculation']['proposal_amount']
        payload = dict(operation_id=f"sla:{row['id']}:{revision}", action='sandbox_credit' if Decimal(amount)>0 else 'record_no_credit',
            risk_tier=2, workflow_id=row['id'], organization_id=row['organization_id'], ticket_id=row['ticket_id'],
            contract_id=snapshot['contract']['id'], currency=snapshot['contract']['currency'], maximum_amount=amount,
            amount=amount, credit_fraction='1', period_start=snapshot['ticket']['billing_period_start'],
            period_end=snapshot['ticket']['billing_period_end'], snapshot_hash=digest(snapshot),
            memo_id=mid, memo_version=1, memo_hash=digest(memo['notes']), review_policy=REVIEW_POLICY,
            sources=snapshot['provenance'], before={'sandbox_period_total':snapshot['ledger']['total']},
            after={'sandbox_period_total':f"{Decimal(snapshot['ledger']['total'])+Decimal(amount):.2f}"},
            recipient_scope='internal sandbox ledger only; no customer delivery')
        return payload

    def _valid_proposal(self, conn, row, approval):
        if approval['payload_hash'] != digest(approval['payload']) or approval['payload']['review_policy'] != REVIEW_POLICY:
            return 'proposal_integrity_changed'
        try:
            current = self._snapshot(conn, row)
            memo = self._memo(conn, row)
        except (LookupError, PermissionError):
            return 'source_or_participant_access_changed'
        if current != row['snapshot'] or digest(current) != approval['payload']['snapshot_hash']:
            return 'case_policy_or_ledger_changed'
        p=approval['payload']
        expected={'workflow_id':row['id'],'organization_id':row['organization_id'],'ticket_id':row['ticket_id'],
            'contract_id':current['contract']['id'],'currency':current['contract']['currency'],
            'amount':current['calculation']['proposal_amount'],'maximum_amount':current['calculation']['proposal_amount'],
            'period_start':current['ticket']['billing_period_start'],'period_end':current['ticket']['billing_period_end'],
            'before':{'sandbox_period_total':current['ledger']['total']},'sources':current['provenance'],'risk_tier':2}
        if any(p.get(k)!=v for k,v in expected.items()):
            return 'proposal_fields_changed'
        if memo['version'] != approval['payload']['memo_version'] or digest(memo['notes']) != approval['payload']['memo_hash']:
            return 'memo_changed'
        if self.clock() >= approval['expires_at']:
            return 'approval_expired'
        return None

    def preview(self, employee_id, workflow_id, approval_id, expected_version, fraction):
        actor=self.records.actor(employee_id)
        with self.db.engine.begin() as conn:
            row=self._row(conn,workflow_id,actor)
            if actor.id!=row['reviewer_id'] or actor.id==row['employee_id'] or 'operations' not in actor.team_ids:
                raise PermissionError('Only the designated reviewer may preview a decision')
            approval=self._approval(conn,row)
            if approval['id']!=approval_id or approval['status']!='pending' or approval['version']!=expected_version:
                raise ConflictError('Approval changed; reload')
            reason=self._valid_proposal(conn,row,approval)
            if reason:
                raise ConflictError(reason.replace('_',' '))
            payload = self._modified_payload(approval['payload'], fraction)
            return {key: payload[key] for key in ('amount','currency','before','after',self.modification_key)} | {'approval_version':approval['version']}

    def _modified_payload(self, original, fraction):
        if not Decimal(0) <= fraction <= Decimal(1):
            raise ConflictError('Modification is outside the allowed range')
        payload = dict(original)
        amount = (Decimal(payload['maximum_amount'])*fraction).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        payload.update(amount=f'{amount:.2f}',credit_fraction=str(fraction),
            action='sandbox_credit' if amount>0 else 'record_no_credit',
            after={'sandbox_period_total':f"{Decimal(payload['before']['sandbox_period_total'])+amount:.2f}"})
        return payload

    def _default_modification(self, payload):
        return Decimal(1)

    def decide(self, employee_id, workflow_id, body):
        actor = self.records.actor(employee_id)
        decision_hash = digest(body.model_dump(mode='json', exclude={'request_key'}))
        conflict = None
        with self.db.engine.begin() as conn:
            row = self._row(conn, workflow_id, actor)
            actor = employee(conn, actor.id, actor.organization_id)
            if actor.id != row['reviewer_id'] or actor.id == row['employee_id'] or 'operations' not in actor.team_ids:
                raise PermissionError('Only the designated independent Operations reviewer may decide')
            employee(conn, actor.id, actor.organization_id)
            approval = self._approval(conn, row)
            if approval['id'] != body.approval_id:
                raise ConflictError('This decision refers to a replaced proposal; review the new approval')
            if approval['decision_key'] == body.request_key:
                if approval['decision_hash'] != decision_hash:
                    raise ConflictError('Decision key was reused for different content')
                return row
            if approval['status'] != 'pending' or approval['version'] != body.expected_version or row['status'] != 'waiting_for_approval':
                raise ConflictError('Approval is no longer pending; reload its current state')
            if body.decision != 'reject':
                conflict = self._valid_proposal(conn, row, approval)
            if conflict:
                self._invalidate(conn, row, conflict, 'expired' if conflict == 'approval_expired' else 'stale')
            else:
                fraction = getattr(body, self.modification_key) if body.decision == 'modify' else self._default_modification(approval['payload'])
                payload = self._modified_payload(approval['payload'], fraction) if body.decision != 'reject' else approval['payload']
                rejected = body.decision == 'reject'
                conn.execute(update(t.approvals).where(t.approvals.c.id==approval['id']).values(
                    version=approval['version']+1,status='rejected' if rejected else 'approved',decision=body.decision,
                    decision_key=body.request_key,decision_hash=decision_hash,reason=body.reason,decided_at=self.clock(),
                    approved_payload=None if rejected else payload,approved_hash=None if rejected else digest(payload)))
                self._change(conn,row,status='rejected' if rejected else 'approved',phase='end' if rejected else 'execute',
                    outcome={'status':'rejected','reason':body.reason,'effect':'none'} if rejected else None)
                if not rejected:
                    from ..runtime.queue import enqueue
                    enqueue(conn,row,self.kind)
                self._event(conn,row,'rejected' if rejected else 'approved',{
                    'reviewer_id':actor.id,'decision':body.decision,'reason':body.reason,
                    'amount':None if rejected else payload['amount'],'approval_id':approval['id']})
        if conflict:
            raise ConflictError(conflict.replace('_',' ') + '; refresh and request new review')
        return row

    def resume(self, employee_id, workflow_id, expected_version):
        actor = self.records.actor(employee_id)
        with self.db.engine.begin() as conn:
            row = self._row(conn,workflow_id,actor)
            if row['employee_id'] != actor.id:
                raise PermissionError('Only the requester may recheck or recover this workflow')
            if row['version'] != expected_version:
                raise ConflictError('Workflow changed; reload')
            if row['status'] in TERMINAL:
                raise ConflictError('This workflow cannot be resumed in its current state')
            if row['status']=='waiting_for_approval':
                approval=self._approval(conn,row)
                reason=self._valid_proposal(conn,row,approval)
                if not reason:
                    raise ConflictError('A current proposal is waiting for its designated reviewer')
                self._invalidate(conn,row,reason,'expired' if reason=='approval_expired' else 'stale')
            if row['retry_at'] and self.clock() < row['retry_at']:
                raise ConflictError('Retry delay has not elapsed')
            if row['status'] == 'waiting_for_input':
                self._change(conn,row,status='queued',phase='investigate',missing_inputs=[],error=None)
            else:
                self._change(conn,row,status='queued' if row['phase']!='execute' else 'approved',retry_at=None,error=None)
            from ..runtime.queue import enqueue
            enqueue(conn,row,self.kind)
            self._event(conn,row,'resume_requested',{'phase':row['phase']})
        return row

    def cancel(self, employee_id, workflow_id, expected_version):
        actor = self.records.actor(employee_id)
        with self.db.engine.begin() as conn:
            row = self._row(conn,workflow_id,actor)
            if row['employee_id'] != actor.id:
                raise PermissionError('Only the requester may cancel')
            if row['status']=='cancelled':
                return row
            if row['version'] != expected_version:
                raise ConflictError('Workflow changed; reload')
            receipt = conn.execute(select(t.receipts.c.operation_id).where(t.receipts.c.workflow_id==workflow_id)).first()
            if receipt or row['status'] in TERMINAL:
                raise ConflictError('A committed effect or terminal outcome cannot be cancelled')
            if row['approval_id']:
                conn.execute(update(t.approvals).where(t.approvals.c.id==row['approval_id']).values(status='cancelled'))
            self._change(conn,row,status='cancelled',phase='end',outcome={'status':'cancelled','effect':'none'})
            self._event(conn,row,'cancelled',{'employee_id':actor.id})
        return row

    def execute(self, workflow_id):
        with self.db.engine.begin() as conn:
            row=self._row(conn,workflow_id)
            if row['status'] in TERMINAL:
                return 'end'
            existing=conn.execute(select(t.receipts).where(t.receipts.c.workflow_id==workflow_id)).mappings().first()
            if existing:
                self._change(conn,row,status='verifying',phase='verify')
                return 'verify'
            if row['phase']!='execute' or row['status']!='approved':
                return row['phase']
            approval=self._approval(conn,row)
            if approval['status']!='approved' or not approval['approved_payload']:
                raise PermissionError('A persisted approval is required')
            conflict=self._valid_proposal(conn,row,approval)
            if conflict:
                self._invalidate(conn,row,conflict,'expired' if conflict=='approval_expired' else 'stale')
                return 'wait'
            payload=approval['approved_payload']
            amount=Decimal(payload['amount'])
            expected=(Decimal(approval['payload']['maximum_amount'])*Decimal(payload['credit_fraction'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            base_keys=('operation_id','organization_id','workflow_id','ticket_id','contract_id','currency','period_start','period_end',
                       'snapshot_hash','memo_id','memo_version','memo_hash','review_policy','sources','before','recipient_scope','maximum_amount')
            if (digest(payload)!=approval['approved_hash'] or amount!=expected or not Decimal(0)<=Decimal(payload['credit_fraction'])<=Decimal(1)
                    or any(payload.get(k)!=approval['payload'].get(k) for k in base_keys)
                    or payload['action']!=('sandbox_credit' if amount>0 else 'record_no_credit')
                    or payload.get('risk_tier')!=2 or set(payload)!=set(approval['payload'])
                    or payload['after']!={'sandbox_period_total':f"{Decimal(payload['before']['sandbox_period_total'])+amount:.2f}"}
                    or amount>Decimal(row['snapshot']['calculation']['remaining_cap'])):
                raise PermissionError('Approved action failed deterministic verification')
            duplicate=conn.execute(select(t.credits.c.operation_id).where(t.credits.c.organization_id==row['organization_id'],
                t.credits.c.ticket_id==row['ticket_id'])).first()
            if duplicate:
                self._invalidate(conn,row,'case_already_credited')
                return 'wait'
            if amount>0:
                conn.execute(insert(t.credits).values(operation_id=payload['operation_id'],organization_id=row['organization_id'],
                    ticket_id=row['ticket_id'],contract_id=payload['contract_id'],workflow_id=workflow_id,approval_id=approval['id'],
                    amount=amount,currency=payload['currency'],period_start=datetime.fromisoformat(payload['period_start'].replace('Z','+00:00')),
                    period_end=datetime.fromisoformat(payload['period_end'].replace('Z','+00:00')),payload_hash=approval['approved_hash'],created_at=self.clock()))
            receipt=dict(operation_id=payload['operation_id'],organization_id=row['organization_id'],workflow_id=workflow_id,
                approval_id=approval['id'],payload_hash=approval['approved_hash'],payload=payload,
                result={'effect':'sandbox_credit_recorded' if amount>0 else 'no_credit_recorded','amount':payload['amount'],
                        'currency':payload['currency'],'customer_message_sent':False},created_at=self.clock())
            conn.execute(insert(t.receipts).values(**receipt))
            self._change(conn,row,status='verifying',phase='verify')
            self._event(conn,row,'effect_committed',{'operation_id':payload['operation_id'],**receipt['result']})
        return 'verify'

    def verify(self, workflow_id):
        with self.db.engine.begin() as conn:
            row=self._row(conn,workflow_id)
            if row['status'] in TERMINAL or row['phase']!='verify':
                return row['phase']
            receipt=conn.execute(select(t.receipts).where(t.receipts.c.workflow_id==workflow_id)).mappings().first()
            if not receipt:
                raise ConflictError('Committed receipt unavailable; reconciliation required')
            payload=receipt['payload']
            credit=conn.execute(select(t.credits).where(t.credits.c.operation_id==receipt['operation_id'])).mappings().first()
            approved=self._approval(conn,row)
            valid=(digest(payload)==receipt['payload_hash'] and receipt['operation_id']==payload['operation_id']
                and approved['approved_hash']==receipt['payload_hash'] and approved['status']=='approved') and (
                (Decimal(payload['amount'])==0 and credit is None) or
                (credit is not None and credit['payload_hash']==receipt['payload_hash'] and credit['amount']==Decimal(payload['amount'])
                 and credit['currency']==payload['currency'] and credit['ticket_id']==row['ticket_id']
                 and credit['contract_id']==payload['contract_id'] and credit['organization_id']==row['organization_id']
                 and credit['workflow_id']==row['id'] and credit['approval_id']==approved['id']
                 and credit['period_start']==datetime.fromisoformat(payload['period_start'].replace('Z','+00:00'))
                 and credit['period_end']==datetime.fromisoformat(payload['period_end'].replace('Z','+00:00'))))
            outcome={'status':'completed' if valid else 'partial','verified':valid,'operation_id':receipt['operation_id'],
                **receipt['result'],'incident_resolved':False,'workflow_closed':valid,
                'note':'Sandbox review completed; technical incident resolution and customer delivery are separate.',
                'duration_ms':round((self.clock()-row['created_at']).total_seconds()*1000), 'inference_cost_usd':None}
            self._change(conn,row,status=outcome['status'],phase='end',outcome=outcome,
                error=None if valid else {'category':'unknown_outcome','message':'Receipt read-back mismatch; human reconciliation required'})
            self._event(conn,row,'closed' if valid else 'verification_failed',outcome)
        return 'end'

    def phase(self, workflow_id):
        with self.db.engine.connect() as conn:
            return self._row(conn,workflow_id,lock=False)

    def record_failure(self, workflow_id, category, message):
        with self.db.engine.begin() as conn:
            row=self._row(conn,workflow_id)
            if row['status'] in TERMINAL:
                return
            attempts=row['attempts']+1
            has_receipt=conn.execute(select(t.receipts.c.operation_id).where(t.receipts.c.workflow_id==workflow_id)).first()
            retry=category=='transient' and attempts<3
            status='retry_scheduled' if retry else ('partial' if has_receipt else 'failed')
            self._change(conn,row,status=status,attempts=attempts,error={'category':category,'message':message},
                retry_at=self.clock()+timedelta(seconds=2**attempts) if retry else None,
                phase=row['phase'] if retry else 'end')
            self._event(conn,row,'attempt_failed',{'category':category,'attempt':attempts,'retry_scheduled':retry})
