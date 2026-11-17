"""Model-facing contracts; no generated code, identity, approval or arbitrary graph edges."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROLE_TOOLS = {
    'knowledge': {'search_knowledge', 'read_document', 'inspect_artifact'},
    'analyst': {'count_workspace_cases','list_sla_cases', 'get_sla_case', 'calculate_sla_impact', 'list_deals',
                'get_deal', 'calculate_deal_terms', 'inspect_artifact'},
    'action': {'draft_incident_memo', 'export_incident_memo', 'start_sla_workflow',
               'get_sla_workflow', 'resume_sla_workflow', 'start_deal_workflow',
               'get_deal_workflow', 'resume_deal_workflow', 'inspect_artifact'},
    'critic': set(),
}
ROLE_TOOLS['single'] = set.union(*(v for v in ROLE_TOOLS.values()))

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Intake(Contract):
    route: Literal['investigate', 'conversation', 'clarify']
    reply: str = Field(default='', max_length=2000)
    resolved_request: str = Field(default='', max_length=2000, description='Explicit task resolved from the latest message and recent context; never invent scope or approval.')

    @model_validator(mode='after')
    def reply_for_conversation(self):
        if self.route != 'investigate' and not self.reply:
            raise ValueError('A conversational reply or clarification question is required')
        if self.route == 'investigate' and self.reply:
            raise ValueError('Business answers must come from investigation')
        return self

class ConversationCheck(Contract):
    safe: bool
    reason: str = Field(min_length=1, max_length=400)

class Node(Contract):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    role: Literal['knowledge', 'analyst', 'action', 'critic']
    objective: str = Field(min_length=1, max_length=500)
    dependencies: list[str] = Field(default_factory=list, max_length=7)
    tools: list[str] = Field(default_factory=list, max_length=10)
    required_inputs: list[str] = Field(default_factory=list, max_length=7)
    output_schema: Literal['handoff_v1', 'claim_verdicts_v1']
    completion: Literal['evidence_read', 'calculation_checked', 'draft_ready', 'claims_verified']
    max_calls: int = Field(default=3, ge=1, le=5)

class Plan(Contract):
    decision: Literal['execute', 'clarify'] = 'execute'
    nodes: list[Node] = Field(default_factory=list, max_length=8)
    terminal: str = Field(default='', max_length=40)

    @model_validator(mode='after')
    def valid_dag(self):
        if self.decision == 'clarify':
            if self.nodes or self.terminal: raise ValueError('Clarification has no executable graph')
            return self
        by_id = {n.id: n for n in self.nodes}
        if len(by_id) != len(self.nodes) or not self.nodes: raise ValueError('Unique nonempty nodes required')
        if self.terminal not in by_id or by_id[self.terminal].role != 'critic': raise ValueError('Terminal must be the critic')
        visited = set()
        def visit(ident, stack):
            if ident in stack: raise ValueError('Plan must be acyclic')
            if ident in visited: return
            node = by_id[ident]
            if node.role!='critic' and node.max_calls<2:
                raise ValueError('Worker max_calls must be at least 2: one tool turn and one handoff turn')
            if len(set(node.dependencies)) != len(node.dependencies): raise ValueError('Duplicate dependency')
            if any(d not in by_id for d in node.dependencies): raise ValueError('Unknown dependency')
            if not set(node.required_inputs) <= set(node.dependencies): raise ValueError('Inputs must come from dependencies')
            if not set(node.tools) <= ROLE_TOOLS[node.role]: raise ValueError('Tool outside role capability')
            if node.role=='knowledge' and 'search_knowledge' in node.tools and 'read_document' not in node.tools:
                raise ValueError('Knowledge search requires read_document to inspect sources')
            for listing,detail in [('list_deals','get_deal'),('list_sla_cases','get_sla_case')]:
                if listing in node.tools and detail not in node.tools:
                    raise ValueError(f'{listing} requires {detail} so the worker can inspect discovered records')
            if node.role=='analyst' and not set(node.tools)&{'count_workspace_cases','list_sla_cases','list_deals','get_sla_case','calculate_sla_impact','get_deal','calculate_deal_terms'}:
                raise ValueError('Analyst needs a typed case/deal read, aggregate or calculation tool')
            expected = {'knowledge':'evidence_read', 'analyst':'calculation_checked', 'action':'draft_ready', 'critic':'claims_verified'}[node.role]
            if node.completion != expected: raise ValueError('Invalid completion condition')
            if (node.role == 'critic') != (node.output_schema == 'claim_verdicts_v1'): raise ValueError('Invalid output schema')
            if node.role == 'critic' and node.id != self.terminal: raise ValueError('One terminal critic required')
            if node.role in ('action','critic') and not node.dependencies: raise ValueError('Draft/critic need evidence handoffs')
            for dependency in node.dependencies: visit(dependency, stack | {ident})
            visited.add(ident)
        visit(self.terminal, set())
        if visited != set(by_id): raise ValueError('Every node must contribute to terminal verification')
        return self

class ToolCall(Contract):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)

class Support(Contract):
    source_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=2000)

class Claim(Contract):
    kind: Literal['fact', 'hypothesis', 'suggestion', 'unknown'] = 'fact'
    text: str = Field(min_length=1, max_length=1000)
    evidence: list[Support] = Field(default_factory=list, max_length=4)

class Handoff(Contract):
    status: Literal['complete', 'needs_input', 'not_found'] = 'complete'
    claims: list[Claim] = Field(default_factory=list, max_length=12)
    source_ids: list[str] = Field(default_factory=list, max_length=12)
    unresolved: list[str] = Field(default_factory=list, max_length=8)

class WorkerTurn(Contract):
    calls: list[ToolCall] = Field(default_factory=list, max_length=3)
    result: Handoff | None = None

    @model_validator(mode='after')
    def one_operation(self):
        if bool(self.calls) == (self.result is not None): raise ValueError('Return calls OR a result')
        return self

class Verdict(Contract):
    index: int = Field(ge=0, le=11)
    verdict: Literal['supported', 'unsupported', 'contradiction', 'uncertain']
    reason: str = Field(min_length=1, max_length=400)

class Review(Contract):
    verdicts: list[Verdict] = Field(max_length=12)
