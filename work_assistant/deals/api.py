from fastapi import Depends, HTTPException, Query
from ..models import Employee
from ..db.store import ConflictError
from .domain import CreateDeal, EditDeal, EditCost


def install_routes(app, service, authorize):
    def invoke(operation):
        try:
            return operation()
        except LookupError:
            raise HTTPException(404,'Record not found') from None
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from None
        except ConflictError as exc:
            raise HTTPException(409,str(exc)) from None

    @app.get('/v1/deals/options')
    def options(actor:Employee=Depends(authorize)):
        return invoke(lambda:service.options(actor.id))

    @app.get('/v1/deals')
    def listing(query:str=Query(default='',max_length=200),actor:Employee=Depends(authorize)):
        return invoke(lambda:{'deals':service.list(actor.id,query)})

    @app.post('/v1/deals',status_code=201)
    def create(body:CreateDeal,actor:Employee=Depends(authorize)):
        return invoke(lambda:service.create(actor.id,body))

    @app.get('/v1/deals/{deal_id}')
    def detail(deal_id:str,actor:Employee=Depends(authorize)):
        return invoke(lambda:service.detail(actor.id,deal_id))

    @app.patch('/v1/deals/{deal_id}')
    def edit(deal_id:str,body:EditDeal,actor:Employee=Depends(authorize)):
        return invoke(lambda:service.edit(actor.id,deal_id,body))

    @app.patch('/v1/pricing-policies/{policy_id}/cost')
    def cost(policy_id:str,body:EditCost,actor:Employee=Depends(authorize)):
        return invoke(lambda:service.edit_cost(actor.id,policy_id,body))
