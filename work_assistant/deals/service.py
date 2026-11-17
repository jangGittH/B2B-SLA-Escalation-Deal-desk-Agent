from datetime import datetime, timezone
from sqlalchemy import select, insert, update
from sqlalchemy.exc import IntegrityError
from ..db.store import ConflictError
from ..sla.service import SlaService
from ..sla import tables as sla
from . import tables as t
from .domain import Deal, PricingPolicy
from .calculator import calculate


def now():
    return datetime.now(timezone.utc)


def json_value(value):
    from ..workflows.service import canonical
    return canonical(value)


class DealService:
    def __init__(self, db, *, clock=now):
        self.db = db
        self.clock = clock
        self.sla = SlaService(db)

    def actor(self, employee_id):
        return self.sla.actor(employee_id)

    def _row(self, conn, table, actor, rid, lock=True):
        return self.sla._row(conn,table,actor,rid,lock=lock)

    def _case(self, conn, actor, deal_id):
        draft = self._row(conn,t.requests,actor,deal_id,lock=False)
        contract = self._row(conn,sla.contracts,actor,draft['contract_id'])
        sandbox = self._row(conn,t.sandbox_contracts,actor,draft['contract_id'])
        deal = self._row(conn,t.requests,actor,deal_id)
        policy = self._row(conn,t.policies,actor,deal['pricing_policy_id'])
        account = self._row(conn,sla.accounts,actor,contract['account_id'])
        result = calculate(Deal.model_validate(deal),PricingPolicy.model_validate(policy),contract,sandbox,self.clock())
        return json_value(dict(deal=deal,contract=contract,sandbox_contract=sandbox,policy=policy,account=account,
            calculation=result,provenance=[{'type':kind,'id':row['id'],'version':row['version']} for kind,row in
                [('deal',deal),('contract',contract),('sandbox_contract',sandbox),('pricing_policy',policy),('account',account)]]))

    def detail(self, employee_id, deal_id):
        actor=self.actor(employee_id)
        with self.db.engine.begin() as conn:
            result=self._case(conn,actor,deal_id)
            applied=conn.execute(select(t.effects).where(t.effects.c.organization_id==actor.organization_id,t.effects.c.deal_id==deal_id)).mappings().first()
        return result | {'can_edit':result['deal']['owner_id']==actor.id and not applied,'applied_operation_id':applied['operation_id'] if applied else None,
                         'applied_terms':applied['after'] if applied else None,
                         'can_edit_cost':'operations' in actor.team_ids}

    def list(self, employee_id, query=''):
        actor=self.actor(employee_id)
        with self.db.engine.connect() as conn:
            stmt=select(t.requests).where(t.requests.c.organization_id==actor.organization_id)
            if query:
                stmt=stmt.where(t.requests.c.id.ilike('%'+query+'%') | t.requests.c.title.ilike('%'+query+'%'))
            return json_value([dict(row) for row in conn.execute(stmt.order_by(t.requests.c.updated_at.desc()).limit(50)).mappings()])

    def options(self, employee_id):
        actor=self.actor(employee_id)
        with self.db.engine.connect() as conn:
            return json_value({key:[dict(row) for row in conn.execute(select(table).where(table.c.organization_id==actor.organization_id)).mappings()]
                for key,table in [('contracts',sla.contracts),('policies',t.policies)]})

    def _audit(self, conn, actor, rid, kind, payload):
        conn.execute(insert(t.audit).values(organization_id=actor.organization_id,employee_id=actor.id,record_id=rid,
            kind=kind,payload=json_value(payload),created_at=self.clock()))

    def create(self, employee_id, body):
        actor=self.actor(employee_id)
        try:
            with self.db.engine.begin() as conn:
                self.db._lock_actor(conn,actor)
                contract=self._row(conn,sla.contracts,actor,body.contract_id)
                self._row(conn,t.policies,actor,body.pricing_policy_id)
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                conn.execute(pg_insert(t.sandbox_contracts).values(organization_id=actor.organization_id,id=contract['id'],version=1,
                    source_contract_version=contract['version'],monthly_fee=contract['monthly_fee'],currency=contract['currency'],ends_at=contract['ends_at']).on_conflict_do_nothing())
                deal=Deal(**body.model_dump(),organization_id=actor.organization_id,owner_id=actor.id,updated_at=self.clock())
                conn.execute(insert(t.requests).values(**deal.model_dump()))
                self._audit(conn,actor,deal.id,'deal_created',deal.model_dump(mode='json'))
        except IntegrityError:
            raise ConflictError('Deal ID already exists or a linked record changed') from None
        return self.detail(employee_id,deal.id)

    def edit(self, employee_id, deal_id, body):
        actor=self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn,actor)
            before=self._case(conn,actor,deal_id)['deal']
            if before['owner_id']!=actor.id: raise PermissionError('Only the deal owner may edit the request')
            if before['version']!=body.expected_version: raise ConflictError('Deal changed; reload')
            if conn.execute(select(t.effects.c.operation_id).where(t.effects.c.organization_id==actor.organization_id,t.effects.c.deal_id==deal_id)).first():
                raise ConflictError('Applied deal is immutable; create a new request for another renewal')
            changes=body.model_dump(exclude={'expected_version','change_reason'})
            changes.update(version=before['version']+1,updated_at=self.clock())
            conn.execute(update(t.requests).where(t.requests.c.organization_id==actor.organization_id,t.requests.c.id==deal_id).values(**changes))
            self._audit(conn,actor,deal_id,'deal_edited',{'before':before,'after':changes,'reason':body.change_reason})
        return self.detail(employee_id,deal_id)

    def edit_cost(self, employee_id, policy_id, body):
        actor=self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn,actor)
            from ..workflows.service import employee
            actor = employee(conn, actor.id, actor.organization_id)
            if 'operations' not in actor.team_ids: raise PermissionError('Only Operations may attest policy costs')
            before=self._row(conn,t.policies,actor,policy_id)
            if before['version']!=body.expected_version: raise ConflictError('Pricing policy changed; reload')
            changes=body.model_dump(exclude={'expected_version','change_reason'})
            changes['version']=before['version']+1
            PricingPolicy.model_validate(before | changes)
            conn.execute(update(t.policies).where(t.policies.c.organization_id==actor.organization_id,t.policies.c.id==policy_id).values(**changes))
            self._audit(conn,actor,policy_id,'policy_cost_attested',{'before':before,'after':before|changes,'reason':body.change_reason})
        return json_value(before|changes)
