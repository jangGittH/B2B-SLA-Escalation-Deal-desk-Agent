"""Short, tenant-scoped transactions. Only human case edits and private drafts mutate state."""
from datetime import datetime, timezone
import json
from uuid import uuid4

from sqlalchemy import select, insert, update
from sqlalchemy.exc import IntegrityError

from ..db.store import ConflictError
from ..models import Employee
from ..telemetry import current_run, event
from . import tables as t
from .domain import Account, Contract, Policy, Ticket, CreateTicket, EditTicket, MemoNotes, dump
from .calculator import calculate
from .seed import values


def now():
    return datetime.now(timezone.utc)


class SlaService:
    def __init__(self, database):
        self.db = database

    def actor(self, employee_id):
        actor = self.db.employee(employee_id)
        if not actor or not actor.active:
            raise PermissionError('Employee unavailable')
        return actor

    def _row(self, conn, table, actor, record_id, lock=False):
        stmt = select(table).where(table.c.organization_id == actor.organization_id, table.c.id == record_id)
        row = conn.execute(stmt.with_for_update() if lock else stmt).mappings().first()
        if row is None:
            raise LookupError('Record not found')
        return dict(row)

    def _case(self, conn, actor, ticket_id):
        # Contract lock serializes case writes/calculations for overlapping incident detection.
        ticket = Ticket.model_validate(self._row(conn, t.tickets, actor, ticket_id))
        contract = Contract.model_validate(self._row(conn, t.contracts, actor, ticket.contract_id, lock=True))
        ticket = Ticket.model_validate(self._row(conn, t.tickets, actor, ticket_id))
        policy = Policy.model_validate(self._row(conn, t.policies, actor, contract.policy_id, lock=True))
        account = Account.model_validate(self._row(conn, t.accounts, actor, contract.account_id, lock=True))
        result = calculate(ticket, contract, policy)
        # No double counting across cases: require consolidation before proposing a credit.
        related = []
        if ticket.incident_started_at and ticket.incident_ended_at:
            related = conn.execute(select(t.tickets.c.id).where(
                t.tickets.c.organization_id == actor.organization_id, t.tickets.c.contract_id == contract.id,
                t.tickets.c.id != ticket.id, t.tickets.c.incident_started_at < ticket.incident_ended_at,
                t.tickets.c.incident_ended_at > ticket.incident_started_at).order_by(t.tickets.c.id)).scalars().all()
        if related:
            result.update(status='missing_inputs', proposal_amount=None)
            result['missing_inputs'].append('overlapping_incidents_require_consolidation')
        return {'ticket': dump(ticket), 'account': dump(account), 'contract': dump(contract), 'policy': dump(policy),
                'calculation': result, 'overlapping_ticket_ids': related,
                'provenance': [{'type': kind, 'id': record.id, 'version': record.version}
                               for kind, record in [('ticket', ticket), ('account', account), ('contract', contract), ('policy', policy)]],
                'data_classification': 'fictional_demo; incident fields are human attestations, not independently verified'}

    def _audit(self, conn, actor, ticket_id, kind, payload):
        context = current_run.get()
        conn.execute(insert(t.audit).values(organization_id=actor.organization_id, ticket_id=ticket_id,
            employee_id=actor.id, run_id=context[1]['id'] if context else None, kind=kind,
            payload=payload, created_at=now()))

    def list_tickets(self, employee_id, status=None, account_id=None, query=''):
        actor = self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn, actor)
            stmt = select(t.tickets).join(t.contracts,
                (t.tickets.c.organization_id == t.contracts.c.organization_id) &
                (t.tickets.c.contract_id == t.contracts.c.id)).where(t.tickets.c.organization_id == actor.organization_id)
            if status:
                stmt = stmt.where(t.tickets.c.status == status)
            if account_id:
                stmt = stmt.where(t.contracts.c.account_id == account_id)
            if query:
                # Escape wildcard characters; filters are bounded parameters, never raw SQL.
                pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                stmt = stmt.where(t.tickets.c.id.ilike(pattern) | t.tickets.c.title.ilike(pattern))
            return [Ticket.model_validate(row).model_dump(mode='json', include={'id','title','status','severity','assignee_id','contract_id','version','updated_at'}) for row in conn.execute(stmt.order_by(
                t.tickets.c.updated_at.desc(), t.tickets.c.id).limit(100)).mappings()]

    def options(self, employee_id):
        actor = self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn, actor)
            data = {name: [dump(model.model_validate(row)) for row in conn.execute(select(table).where(
                table.c.organization_id == actor.organization_id).order_by(table.c.id).limit(100)).mappings()]
                for name, table, model in [('accounts', t.accounts, Account), ('contracts', t.contracts, Contract)]}
        data['assignees'] = [{'id': e.id, 'name': e.name.model_dump()} for e in self.db.employees()
                             if e.active and e.organization_id == actor.organization_id]
        return data

    def detail(self, employee_id, ticket_id):
        actor = self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn, actor)
            result = self._case(conn, actor, ticket_id)
        event('sla_calculated', {'ticket_id': ticket_id, 'ticket_version': result['ticket']['version'],
              'formula_version': result['calculation']['formula_version'],
              'outcome': result['calculation']['status'], 'missing_inputs': result['calculation']['missing_inputs']})
        return result

    def create_ticket(self, employee_id, body: CreateTicket):
        actor = self.actor(employee_id)
        if body.assignee_id != actor.id:
            raise PermissionError('New cases must be assigned to yourself')
        ticket = Ticket(**body.model_dump(), organization_id=actor.organization_id, updated_at=now())
        try:
            with self.db.engine.begin() as conn:
                self.db._lock_actor(conn, actor)
                self._row(conn, t.contracts, actor, body.contract_id, lock=True)
                conn.execute(insert(t.tickets).values(**values(ticket)))
                self._audit(conn, actor, ticket.id, 'case_created', {'version': 1, 'risk_tier': 1})
        except IntegrityError:
            raise ConflictError('Case ID already exists or a reference changed') from None
        return dump(ticket)

    def edit_ticket(self, employee_id, ticket_id, body: EditTicket):
        actor = self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn, actor)
            previous = self._case(conn, actor, ticket_id)['ticket']
            if previous['assignee_id'] != actor.id:
                raise PermissionError('Only the current case assignee may edit incident facts')
            if previous['version'] != body.expected_version:
                raise ConflictError('Case changed; reload before editing')
            from ..db.models import entities
            candidate = conn.execute(select(entities['employees'].c.payload).where(
                entities['employees'].c.id == body.assignee_id,
                entities['employees'].c.organization_id == actor.organization_id)).scalar_one_or_none()
            if not candidate or not Employee.model_validate(candidate).active:
                raise LookupError('Assignee not found')
            ticket = Ticket.model_validate(previous | body.model_dump(exclude={'expected_version', 'change_reason'}) |
                                           {'version': body.expected_version + 1, 'updated_at': now()})
            conn.execute(update(t.tickets).where(t.tickets.c.organization_id == actor.organization_id,
                t.tickets.c.id == ticket_id, t.tickets.c.version == body.expected_version).values(**values(ticket)))
            self._audit(conn, actor, ticket_id, 'case_edited', {'before': previous, 'after': dump(ticket),
                        'reason': body.change_reason, 'risk_tier': 1})
        return dump(ticket)

    def list_memos(self, employee_id, ticket_id):
        actor = self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn, actor)
            self._row(conn, t.tickets, actor, ticket_id)
            return [dict(row) for row in conn.execute(select(t.memos.c.id, t.memos.c.version, t.memos.c.updated_at).where(
                t.memos.c.organization_id == actor.organization_id, t.memos.c.ticket_id == ticket_id,
                t.memos.c.employee_id == actor.id).order_by(t.memos.c.updated_at.desc()).limit(100)).mappings()]

    def save_memo(self, employee_id, ticket_id, body, memo_id=None):
        actor = self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn, actor)
            snapshot = self._case(conn, actor, ticket_id)
            if snapshot['ticket']['version'] != body.expected_ticket_version:
                raise ConflictError('Case changed; reload before drafting')
            previous = None
            if memo_id:
                previous = self._memo(conn, actor, memo_id)
                snapshot = self._memo_snapshot(conn, actor, previous)
                if previous['version'] != body.expected_version or previous['ticket_id'] != ticket_id:
                    raise ConflictError('Memo changed; reload before editing')
            context = current_run.get()
            row = dict(id=memo_id or uuid4().hex, organization_id=actor.organization_id,
                version=previous['version']+1 if previous else 1, ticket_id=ticket_id, employee_id=actor.id,
                run_id=context[1]['id'] if context else None,
                language=previous['language'] if previous else body.language, snapshot=snapshot,
                notes=MemoNotes.model_validate(body.model_dump(include=set(MemoNotes.model_fields))).model_dump(), updated_at=now())
            if previous:
                conn.execute(update(t.memos).where(t.memos.c.organization_id == actor.organization_id,
                    t.memos.c.id == memo_id).values(**row))
            else:
                conn.execute(insert(t.memos).values(**row))
            conn.execute(insert(t.revisions).values(organization_id=actor.organization_id, memo_id=row['id'],
                version=row['version'], payload=json.loads(json.dumps(row, default=str))))
            self._audit(conn, actor, ticket_id, 'memo_saved', {'memo_id': row['id'], 'version': row['version'],
                        'risk_tier': 1, 'calculation_status': snapshot['calculation']['status']})
        event('sla_memo_saved', {'ticket_id': ticket_id, 'memo_id': row['id'], 'version': row['version']})
        return row | {'stale': False, 'content': memo_text(row)}

    def _memo(self, conn, actor, memo_id):
        row = self._row(conn, t.memos, actor, memo_id)
        if row['employee_id'] != actor.id:
            raise LookupError('Memo not found')
        return row

    def _memo_snapshot(self, conn, actor, row):
        if 'ledger' in row['snapshot']:
            from ..workflows.service import workflow_snapshot
            return workflow_snapshot(self, conn, actor, row['ticket_id'], row['snapshot']['reviewer_id'])
        return self._case(conn, actor, row['ticket_id'])

    def memo(self, employee_id, memo_id):
        actor = self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn, actor)
            row = self._memo(conn, actor, memo_id)
            current = self._memo_snapshot(conn, actor, row)
            versions = conn.execute(select(t.revisions.c.version).where(
                t.revisions.c.organization_id == actor.organization_id, t.revisions.c.memo_id == memo_id
            ).order_by(t.revisions.c.version)).scalars().all()
        return row | {'stale': current != row['snapshot'], 'revisions': versions, 'content': memo_text(row)}

    def source(self, employee_id, source_id, language):
        from ..knowledge import DocumentView
        if source_id.startswith('case:'):
            ticket_id = source_id[5:]
            snapshot = self.detail(employee_id, ticket_id)
            title = ('SLA 案例' if language == 'zh' else 'SLA case') + ' ' + ticket_id
            version, updated = snapshot['ticket']['version'], snapshot['ticket']['updated_at']
            content = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2)
        else:
            row = self.memo(employee_id, source_id[5:])
            if row['stale']:
                raise LookupError('Memo inputs changed; refresh the memo')
            title, version, updated, content = 'SLA memo ' + row['ticket_id'], row['version'], row['updated_at'], row['content']
        return DocumentView(id=source_id, project_id=None, title=title, content=content, language=language,
            version=version, updated_at=updated, source_url=f'/v1/documents/{source_id}?language={language}')

    def link_export(self, employee_id, memo_id, version, artifact):
        actor = self.actor(employee_id)
        with self.db.engine.begin() as conn:
            self.db._lock_actor(conn, actor)
            row = self._memo(conn, actor, memo_id)
            if row['version'] != version or self._memo_snapshot(conn, actor, row) != row['snapshot']:
                raise ConflictError('Memo changed; export again')
            conn.execute(insert(t.exports).values(artifact_id=artifact.id, organization_id=actor.organization_id,
                memo_id=memo_id, memo_version=version))
            self._audit(conn, actor, row['ticket_id'], 'memo_exported', {'memo_id': memo_id,
                        'version': version, 'artifact_id': artifact.id, 'risk_tier': 1})
        self.db.register_artifact(employee_id, artifact, row['run_id'])

    def check_export(self, employee_id, artifact_id):
        actor = self.actor(employee_id)
        with self.db.engine.connect() as conn:
            row = conn.execute(select(t.exports).where(t.exports.c.artifact_id == artifact_id)).mappings().first()
        if row:
            if row['organization_id'] != actor.organization_id:
                raise LookupError('File not found')
            memo = self.memo(employee_id, row['memo_id'])
            if memo['stale'] or row['memo_version'] != memo['version']:
                raise LookupError('File inputs changed; export the current memo')


def memo_text(row):
    """Server-rendered fields and explicit provenance; authored notes remain unverified."""
    zh = row['language'] == 'zh'
    s, notes = row['snapshot'], row['notes']
    choose = lambda en, cn: cn if zh else en
    ticket, account, contract, policy, calc = (s[k] for k in ('ticket', 'account', 'contract', 'policy', 'calculation'))
    unknown = choose('Unknown', '未知')
    def line(en, cn, value):
        return f"{choose(en, cn)}: {unknown if value is None else value}"
    facts = [
        line('Case', '案例', ticket['id'] + ' — ' + ticket['title']),
        line('Account', '客户', account['name']),
        line('Assignee', '负责人', ticket['assignee_id']),
        line('Severity / status', '等级 / 状态', ticket['severity'] + ' / ' + ticket['status']),
        line('Reported at', '报告时间', ticket['reported_at']),
        line('First response', '首次响应', ticket['first_response_at']),
        line('Outage start', '故障开始', ticket['incident_started_at']),
        line('Outage end', '故障结束', ticket['incident_ended_at']),
        line('Billing period start', '计费周期开始', ticket['billing_period_start']),
        line('Billing period end', '计费周期结束', ticket['billing_period_end']),
        line('Eligibility confirmed', '资格已确认', ticket['eligibility_confirmed']),
        line('Prior credits this period', '本期已抵扣金额', s.get('ledger', {}).get('total', ticket['prior_credit_amount'])),
        line('Supporting attestation', '支持性确认说明', ticket['evidence_note'] or None),
    ]
    exclusions = unknown if ticket['exclusions'] is None else ('; '.join(
        w['start'] + ' → ' + w['end'] for w in ticket['exclusions']) or choose('Confirmed none', '已确认无排除时段'))
    facts.append(line('Excluded intervals', '排除时段', exclusions))
    terms = [line('Contract', '合同', contract['id']),
             line('Demo signed terms', '演示已签条款', contract['signed']),
             line('Monthly fee', '月费', contract['currency'] + ' ' + contract['monthly_fee']),
             line('Contract period', '合同期限', contract['starts_at'] + ' → ' + contract['ends_at']),
             line('Policy', '政策', policy['id']),
             line('Rate per eligible minute', '每有效分钟费率', policy['credit_rate_per_minute']),
             line('Monthly cap fraction', '月度上限比例', policy['monthly_cap_fraction'])]
    result = [line('Outcome', '计算状态', calc['status']),
              line('Proposed credit', '抵扣款建议', (calc['currency'] + ' ' + calc['proposal_amount']) if calc['proposal_amount'] is not None else None),
              line('Eligible minutes', '有效分钟', calc.get('eligible_minutes')),
              line('Excluded minutes', '排除分钟', calc.get('excluded_minutes')),
              line('Remaining monthly cap', '月度剩余上限', calc.get('remaining_cap')),
              line('Response deadline', '响应截止时间', calc['response_deadline']),
              line('Response status', '响应状态', calc['response_status']),
              line('Missing inputs', '缺失输入', ', '.join(calc['missing_inputs']) or choose('None', '无')),
              line('Formula', '公式', calc['formula']), line('Formula version', '公式版本', calc['formula_version']),
              line('Calendar', '日历规则', calc['calendar']), line('Rounding', '舍入规则', calc['rounding'])]
    return '\n\n'.join([
        '# ' + choose('Private incident memo — draft only', '私人事件备忘录 — 仅草稿'),
        choose('No credit issued. No customer response sent. Human approval is required.', '未发放抵扣款，未发送客户回复。需要人工审批。'),
        choose('Fictional demo data. Incident inputs are human attestations, not independently verified facts.', '虚构演示数据。事件输入为人工确认值，并非已独立验证的事实。'),
        '## ' + choose('Recorded incident inputs', '记录的事件输入'), '\n'.join(facts),
        '## ' + choose('Contract and policy', '合同与政策'), '\n'.join(terms),
        '## ' + choose('Deterministic calculation / missing inputs', '确定性计算 / 缺失输入'), '\n'.join(result),
        line('Credit basis', '抵扣款依据', 'sandbox ledger' if 'ledger' in s else 'human attestation'),
        '## ' + choose('Input provenance', '输入来源'),
        '\n'.join(f"{r['type']} / {r['id']} / v{r['version']}" for r in s['provenance']),
        '\n'.join(f"Evidence / {d['id']} / v{d['version']}" for d in s.get('shared_evidence', [])),
        '## ' + choose('Hypotheses — unverified author notes, not confirmed root cause', '假设 — 未验证的作者备注，不是已确认根因'),
        notes['hypotheses'] or choose('Root cause unknown.', '根因未知。'),
        '## ' + choose('Proposed next steps — unverified author notes', '建议下一步 — 未验证的作者备注'),
        notes['next_steps'] or choose('Collect missing evidence; review before any action.', '收集缺失证据；执行前审核。'),
        '## ' + choose('Customer response draft — unverified, not sent', '客户回复草稿 — 未验证，未发送'),
        notes['customer_response_draft'] or choose('Not drafted.', '尚未起草。'),
    ])
