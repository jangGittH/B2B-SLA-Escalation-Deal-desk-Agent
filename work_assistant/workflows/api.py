from fastapi import Depends, HTTPException, Query
from ..db.store import ConflictError
from ..runtime.queue import CapacityError
from ..models import Employee
from .domain import StartWorkflow, ResumeWorkflow, DecideApproval, PreviewDecision


def install_routes(app, service, engine, authorize, *, prefix="/v1/sla-workflows", StartSchema=StartWorkflow, DecisionSchema=DecideApproval, PreviewSchema=PreviewDecision):
    def invoke(operation):
        try:
            return operation()
        except LookupError:
            raise HTTPException(404,'Workflow not found') from None
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from None
        except CapacityError as exc:
            raise HTTPException(429,str(exc),headers={'Retry-After':'5'}) from None
        except ConflictError as exc:
            raise HTTPException(409,str(exc)) from None

    @app.post(prefix,status_code=202)
    def start(body:StartSchema, actor:Employee=Depends(authorize)):
        row=invoke(lambda:service.start(actor.id,body))
        return invoke(lambda:service.get(actor.id,row['id']))

    @app.get(prefix)
    def listing(ticket_id:str|None=Query(default=None,max_length=100,alias=service.subject_key),actor:Employee=Depends(authorize)):
        return invoke(lambda:{'workflows':service.list(actor.id,ticket_id)})

    @app.get(prefix+'/{workflow_id}')
    def get(workflow_id:str,actor:Employee=Depends(authorize)):
        return invoke(lambda:service.get(actor.id,workflow_id))

    @app.post(prefix+'/{workflow_id}/resume',status_code=202)
    def resume(workflow_id:str,body:ResumeWorkflow,actor:Employee=Depends(authorize)):
        invoke(lambda:service.resume(actor.id,workflow_id,body.expected_version))
        return invoke(lambda:service.get(actor.id,workflow_id))

    @app.post(prefix+'/{workflow_id}/preview')
    def preview(workflow_id:str,body:PreviewSchema,actor:Employee=Depends(authorize)):
        return invoke(lambda:service.preview(actor.id,workflow_id,body.approval_id,body.expected_version,getattr(body,service.modification_key)))

    @app.post(prefix+'/{workflow_id}/decision',status_code=202)
    def decide(workflow_id:str,body:DecisionSchema,actor:Employee=Depends(authorize)):
        invoke(lambda:service.decide(actor.id,workflow_id,body))
        return invoke(lambda:service.get(actor.id,workflow_id))

    @app.post(prefix+'/{workflow_id}/cancel')
    def cancel(workflow_id:str,body:ResumeWorkflow,actor:Employee=Depends(authorize)):
        invoke(lambda:service.cancel(actor.id,workflow_id,body.expected_version))
        return invoke(lambda:service.get(actor.id,workflow_id))
