"""One atomic event-loop budget shared by supervisor, every worker, retries and critic."""
from dataclasses import dataclass, field
import time

class BudgetExceeded(Exception): pass

@dataclass
class Budget:
    max_model_calls: int = 24
    max_tool_calls: int = 24
    max_replans: int = 2
    max_repairs: int = 2
    deadline_seconds: float = 75
    per_call_bytes: int = 48000
    max_input_bytes: int = 500000
    max_token_reservation: int = 550000
    output_tokens: int = 2048
    model_calls: int = 0
    tool_calls: int = 0
    input_bytes: int = 0
    token_reservation: int = 0
    reported_tokens: int = 0
    unknown_usage_calls: int = 0
    replans: int = 0
    repairs: int = 0
    started: float = field(default_factory=time.monotonic)

    def check(self):
        if time.monotonic()-self.started >= self.deadline_seconds: raise BudgetExceeded('deadline')

    def model(self, size):
        self.check()
        reservation=size+self.output_tokens
        if (self.model_calls>=self.max_model_calls or size>self.per_call_bytes or
            self.input_bytes+size>self.max_input_bytes or self.token_reservation+reservation>self.max_token_reservation):
            raise BudgetExceeded('model/context budget')
        self.model_calls+=1; self.input_bytes+=size; self.token_reservation+=reservation

    def tool(self):
        self.check()
        if self.tool_calls>=self.max_tool_calls: raise BudgetExceeded('tool budget')
        self.tool_calls+=1

    def replan(self):
        self.check()
        if self.replans>=self.max_replans: raise BudgetExceeded('replan budget')
        self.replans+=1

    def repair(self):
        self.check()
        if self.repairs>=self.max_repairs: raise BudgetExceeded('repair budget')
        self.repairs+=1

    def usage(self, usage):
        if usage is None or usage.get('total_token_count') is None: self.unknown_usage_calls+=1
        else:
            self.reported_tokens+=usage['total_token_count']
            if self.reported_tokens>self.max_token_reservation: raise BudgetExceeded('reported token budget')

    def snapshot(self):
        return {key:getattr(self,key) for key in ('model_calls','tool_calls','input_bytes','token_reservation',
            'reported_tokens','unknown_usage_calls','replans','repairs')} | {
            'elapsed_ms':round((time.monotonic()-self.started)*1000),
            'max_model_calls':self.max_model_calls,'max_tool_calls':self.max_tool_calls,
            'max_input_bytes':self.max_input_bytes,'max_token_reservation':self.max_token_reservation,
            'token_measure':'UTF-8 input bytes plus reserved maximum output tokens; conservative proxy, not exact tokenizer billing'}
