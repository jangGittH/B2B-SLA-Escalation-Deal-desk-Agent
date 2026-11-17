from contextvars import ContextVar
# Safe prior context supplied only by the authorized durable conversation route.
prior_task=ContextVar('prior_task',default=None)
