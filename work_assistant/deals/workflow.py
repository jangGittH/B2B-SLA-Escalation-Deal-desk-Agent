"""Commercial adapter for the shared review/checkpoint/decision lifecycle."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from sqlalchemy import select, insert, update
from ..workflows.service import WorkflowService, canonical, digest, employee, now, TERMINAL
from ..workflows import tables as shared
from ..db.models import entities
from ..db.store import ConflictError
from ..models import Document
from ..knowledge import can_read, view
from .service import DealService
from . import tables as t

REVIEW_POLICY = 'sandbox-deal-review-v1'


def memo_text(memo):
    s=memo['snapshot']; c=s['calculation']; d=s['deal']; zh=memo['language']=='zh'
    lines=[f"# {'交易审核备忘录' if zh else 'Deal review memo'} — {d['id']}",
        f"{'请求' if zh else 'Request'}: {d['title']} (v{d['version']})",
        f"{'客户' if zh else 'Account'}: {s['account']['name']}",
        f"{'价格策略' if zh else 'Pricing policy'}: {s['policy']['id']} v{s['policy']['version']}",
        f"{'月度标价 / 成本' if zh else 'Monthly list price / cost'}: {s['policy']['currency']} {s['policy']['monthly_list_price']} / {s['policy']['monthly_cost']}",
        f"{'成本依据' if zh else 'Cost attestation'}: {s['policy']['cost_evidence']}",
        f"{'申请折扣 / 毛利率' if zh else 'Requested discount / margin'}: {c['discount_fraction']} / {c['margin_fraction']}",
        f"{'沙盒月费变更' if zh else 'Sandbox monthly fee change'}: {s['sandbox_contract']['monthly_fee']} → {c['proposal_amount']}",
        f"{'沙盒到期日变更' if zh else 'Sandbox expiry change'}: {s['sandbox_contract']['ends_at']} → {d['renewal_end']}",
        c['formula'],f"{'请求说明（人工录入）' if zh else 'Request note (human supplied)'}: {d['evidence_note']}",
        'No real CRM change, signed amendment or customer delivery. / 不修改真实 CRM、不签署合同、不发送客户消息。',
        '\n## '+('来源版本' if zh else 'Source versions')]
    lines += [f"- {r['type']}:{r['id']} v{r['version']}" for r in s['provenance']]
    lines += [f"- {d['id']} v{d['version']}" for d in s.get('shared_evidence',[])]
    return '\n\n'.join(lines)


class DealWorkflowService(WorkflowService):
    kind='deal'
    subject='deal'
    modification_key='discount_fraction'
    title='Deal review'

    def __init__(self, database, *, clock=now):
        self.db=database
        self.clock=clock
        self.records=DealService(database,clock=clock)

    def _check_start(self, conn, actor, subject_id, expected_version):
        case=self.records._case(conn,actor,subject_id)
        if case['deal']['owner_id']!=actor.id: raise PermissionError('Only the deal owner may submit a review')
        if case['deal']['version']!=expected_version: raise ConflictError('Deal changed; reload')
        if conn.execute(select(t.effects.c.operation_id).where(t.effects.c.organization_id==actor.organization_id,t.effects.c.deal_id==subject_id)).first():
            raise ConflictError('This deal already updated the sandbox contract; inspect its receipt')

    def _snapshot(self, conn, row):
        actor=self._participants(conn,row)
        reviewer=employee(conn,row['reviewer_id'],row['organization_id'])
        if reviewer.id==actor.id or 'operations' not in reviewer.team_ids:
            raise PermissionError('A separate active Operations reviewer is required')
        s=self.records._case(conn,actor,row['deal_id'])
        if s['deal']['owner_id']!=actor.id: raise PermissionError('Deal ownership changed')
        docs=conn.execute(select(entities['documents'].c.payload).where(entities['documents'].c.organization_id==actor.organization_id,
            entities['documents'].c.id.in_([s['account']['id']+'-deal','credit-policy','operating-guide'])).order_by(entities['documents'].c.id).with_for_update(read=True)).scalars()
        s['shared_evidence']=[view(d,'en').model_dump(mode='json') for data in docs
            if can_read(actor,d:=Document.model_validate(data)) and can_read(reviewer,d)]
        s.update(reviewer_id=reviewer.id,review_policy=REVIEW_POLICY)
        return s

    def _memo(self, conn, row):
        return self.records._row(conn,t.memos,employee(conn,row['employee_id'],row['organization_id']),row['memo_id'])

    def _memo_text(self, memo):
        return memo_text(memo)

    def _payload(self, row, snapshot, memo_id, revision):
        s=snapshot; policy=s['policy']; old=s['sandbox_contract']; deal=s['deal']
        payload=dict(operation_id=f"deal:{row['id']}:{revision}",action='sandbox_contract_renewal',risk_tier=2,
            workflow_id=row['id'],organization_id=row['organization_id'],deal_id=row['deal_id'],contract_id=s['contract']['id'],
            currency=policy['currency'],snapshot_hash=digest(s),memo_id=memo_id,memo_version=1,memo_hash=digest({}),
            review_policy=REVIEW_POLICY,sources=s['provenance'],monthly_list_price=policy['monthly_list_price'],monthly_cost=policy['monthly_cost'],
            maximum_discount=deal['requested_discount'],policy_maximum_discount=policy['maximum_discount'],minimum_margin=policy['minimum_margin'],
            before={k:old[k] for k in ('version','monthly_fee','currency','ends_at','source_contract_version')},
            after={'version':old['version']+1,'monthly_fee':s['calculation']['proposal_amount'],'currency':old['currency'],
                   'ends_at':deal['renewal_end'],'source_contract_version':old['source_contract_version']},
            recipient_scope='internal sandbox contract only; no CRM or customer delivery')
        return self._modified_payload(payload,Decimal(deal['requested_discount']))

    def _create_proposal(self, conn, row, snapshot, mid, revision):
        memo=dict(id=mid,organization_id=row['organization_id'],version=1,deal_id=row['deal_id'],employee_id=row['employee_id'],
            language=row['language'],snapshot=snapshot,notes={},updated_at=self.clock())
        conn.execute(insert(t.memos).values(**memo))
        return self._payload(row,snapshot,mid,revision)

    def _valid_proposal(self, conn, row, approval):
        try:
            current=self._snapshot(conn,row)
            memo=self._memo(conn,row)
        except (LookupError,PermissionError):
            return 'source_or_participant_access_changed'
        if current!=row['snapshot'] or current['calculation']['missing_inputs']:
            return 'deal_contract_or_policy_changed'
        if memo['version']!=1 or memo['snapshot']!=current or memo['notes']!={}:
            return 'memo_changed'
        expected=self._payload(row,current,row['memo_id'],row['proposal_revision'])
        if approval['payload']!=expected or approval['payload_hash']!=digest(expected):
            return 'proposal_integrity_changed'
        if self.clock()>=approval['expires_at']: return 'approval_expired'
        return None

    def _default_modification(self, payload):
        return Decimal(payload['maximum_discount'])

    def _modified_payload(self, original, fraction):
        if not Decimal(0)<=fraction<=min(Decimal(original['maximum_discount']),Decimal(original['policy_maximum_discount'])):
            raise ConflictError('Discount may only be reduced within the reviewed policy')
        with localcontext() as ctx:
            ctx.prec=32
            fee=(Decimal(original['monthly_list_price'])*(1-fraction)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            cost=Decimal(original['monthly_cost'])
            if fee<=0 or fee-cost < fee*Decimal(original['minimum_margin']):
                raise ConflictError('Modified terms fail the minimum margin')
            margin=(fee-cost)/fee
        return original | {'amount':f'{fee:.2f}','discount_fraction':str(fraction),'margin_fraction':f'{margin:.6f}',
                           'after':original['after']|{'monthly_fee':f'{fee:.2f}'}}

    def execute(self, workflow_id):
        with self.db.engine.begin() as conn:
            row=self._row(conn,workflow_id)
            if row['status'] in TERMINAL: return 'end'
            if conn.execute(select(shared.receipts.c.operation_id).where(shared.receipts.c.workflow_id==workflow_id)).first():
                self._change(conn,row,status='verifying',phase='verify')
                return 'verify'
            if row['phase']!='execute' or row['status']!='approved': return row['phase']
            approval=self._approval(conn,row)
            if approval['status']!='approved' or not approval['approved_payload']:
                raise PermissionError('Persisted independent approval is required')
            conflict=self._valid_proposal(conn,row,approval)
            if conflict:
                self._invalidate(conn,row,conflict,'expired' if conflict=='approval_expired' else 'stale')
                return 'wait'
            payload=approval['approved_payload']
            expected=self._modified_payload(approval['payload'],Decimal(payload['discount_fraction']))
            if payload!=expected or approval['approved_hash']!=digest(expected):
                raise PermissionError('Approved contract change failed deterministic verification')
            if conn.execute(select(t.effects.c.operation_id).where(t.effects.c.organization_id==row['organization_id'],t.effects.c.deal_id==row['deal_id'])).first():
                self._invalidate(conn,row,'deal_already_applied')
                return 'wait'
            after=payload['after']
            result=conn.execute(update(t.sandbox_contracts).where(t.sandbox_contracts.c.organization_id==row['organization_id'],
                t.sandbox_contracts.c.id==payload['contract_id'],t.sandbox_contracts.c.version==payload['before']['version']).values(
                    version=after['version'],monthly_fee=Decimal(after['monthly_fee']),ends_at=datetime.fromisoformat(after['ends_at'])))
            if result.rowcount!=1: raise ConflictError('Sandbox contract changed')
            conn.execute(insert(t.effects).values(operation_id=payload['operation_id'],organization_id=row['organization_id'],deal_id=row['deal_id'],
                contract_id=payload['contract_id'],workflow_id=workflow_id,approval_id=approval['id'],payload_hash=approval['approved_hash'],
                before=payload['before'],after=after,created_at=self.clock()))
            receipt=dict(operation_id=payload['operation_id'],organization_id=row['organization_id'],workflow_id=workflow_id,approval_id=approval['id'],
                payload_hash=approval['approved_hash'],payload=payload,result={'effect':'sandbox_contract_updated','amount':payload['amount'],
                'currency':payload['currency'],'contract_id':payload['contract_id'],'contract_version':after['version'],
                'renewal_end':after['ends_at'],'customer_message_sent':False,'real_crm_updated':False},created_at=self.clock())
            conn.execute(insert(shared.receipts).values(**receipt))
            self._change(conn,row,status='verifying',phase='verify')
            self._event(conn,row,'effect_committed',{'operation_id':payload['operation_id'],**receipt['result']})
        return 'verify'

    def verify(self, workflow_id):
        with self.db.engine.begin() as conn:
            row=self._row(conn,workflow_id)
            if row['status'] in TERMINAL or row['phase']!='verify': return row['phase']
            receipt=conn.execute(select(shared.receipts).where(shared.receipts.c.workflow_id==workflow_id)).mappings().first()
            if not receipt: raise ConflictError('Committed receipt unavailable; reconciliation required')
            p=receipt['payload']; approval=self._approval(conn,row)
            effect=conn.execute(select(t.effects).where(t.effects.c.operation_id==receipt['operation_id'])).mappings().first()
            contract=conn.execute(select(t.sandbox_contracts).where(t.sandbox_contracts.c.organization_id==row['organization_id'],t.sandbox_contracts.c.id==p['contract_id'])).mappings().first()
            observed=canonical({k:contract[k] for k in p['after']}) if contract else None
            valid=(digest(p)==receipt['payload_hash']==approval['approved_hash'] and approval['status']=='approved'
                and p==approval['approved_payload'] and receipt['organization_id']==row['organization_id']
                and receipt['approval_id']==approval['id'] and receipt['operation_id']==p['operation_id'] and effect is not None
                and effect['organization_id']==row['organization_id'] and effect['deal_id']==row['deal_id']
                and effect['workflow_id']==row['id'] and effect['approval_id']==approval['id'] and effect['contract_id']==p['contract_id']
                and effect['payload_hash']==receipt['payload_hash'] and effect['before']==p['before'] and effect['after']==p['after']
                and observed==p['after'])
            outcome={'status':'completed' if valid else 'partial','verified':bool(valid),'workflow_closed':bool(valid),
                'operation_id':p['operation_id'],**receipt['result'],'duration_ms':round((self.clock()-row['created_at']).total_seconds()*1000),
                'inference_cost_usd':None,'note':'Sandbox contract renewal reviewed and recorded; no signed amendment or real CRM update.'}
            self._change(conn,row,status=outcome['status'],phase='end',outcome=outcome,
                error=None if valid else {'category':'unknown_outcome','message':'Contract read-back mismatch; human reconciliation required'})
            self._event(conn,row,'closed' if valid else 'verification_failed',outcome)
        return 'end'
