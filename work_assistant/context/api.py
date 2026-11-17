"""Human management endpoints. Model tools can propose memories but never confirm them."""
from fastapi import Depends,HTTPException,Query,Response
from sqlalchemy import select
from ..models import Employee
from ..db.store import ConflictError
from ..retrieval.ingestion import Ingestion,Upload
from ..memory.service import MemoryService,Proposal,Change
from .offload import OffloadStore
from .tables import contexts


def install_routes(app,db,knowledge,authorize):
    ingest=Ingestion(db);mem=MemoryService(db)
    def execute(fn):
        try:return fn()
        except LookupError:raise HTTPException(404,'Resource unavailable') from None
        except PermissionError as exc:raise HTTPException(403,str(exc)) from None
        except ConflictError as exc:raise HTTPException(409,str(exc)) from None
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
    @app.post('/v1/knowledge/uploads',status_code=201)
    def upload(body:Upload,actor:Employee=Depends(authorize)):return execute(lambda:ingest.save(actor.id,body))
    @app.put('/v1/knowledge/uploads/{document_id}')
    def revise(document_id:str,body:Upload,actor:Employee=Depends(authorize)):return execute(lambda:ingest.save(actor.id,body,document_id))
    @app.get('/v1/knowledge/uploads/{document_id}/revisions')
    def revisions(document_id:str,actor:Employee=Depends(authorize)):return execute(lambda:{'revisions':ingest.history(actor.id,document_id)})
    @app.get('/v1/knowledge/uploads/{document_id}/revisions/{version}/original')
    def original(document_id:str,version:int,actor:Employee=Depends(authorize)):
        def read():
            row=ingest.original(actor.id,document_id,version)
            return Response(row['original'],media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="original-document"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        return execute(read)
    @app.delete('/v1/knowledge/uploads/{document_id}')
    def remove(document_id:str,expected_version:int=Query(ge=1),actor:Employee=Depends(authorize)):
        return execute(lambda:ingest.remove(actor.id,document_id,expected_version))
    @app.get('/v1/memories')
    def memories(actor:Employee=Depends(authorize)):return execute(lambda:{'memories':mem.list(actor.id)})
    @app.post('/v1/memories',status_code=201)
    def propose(body:Proposal,actor:Employee=Depends(authorize)):return execute(lambda:mem.propose(actor.id,body))
    @app.patch('/v1/memories/{memory_id}')
    def change(memory_id:str,body:Change,actor:Employee=Depends(authorize)):return execute(lambda:mem.change(actor.id,memory_id,body))
    @app.get('/v1/context-artifacts/{ref}')
    def inspect(ref:str,cursor:int=Query(default=0,ge=0),limit:int=Query(default=8,ge=1,le=8),contains:str=Query(default='',max_length=100),actor:Employee=Depends(authorize)):
        return execute(lambda:OffloadStore(knowledge,actor.id).read(ref,cursor,limit,contains))
    @app.get('/v1/runs/{run_id}/context')
    def context(run_id:str,actor:Employee=Depends(authorize)):
        def read():
            from ..durable_api import evidence_available
            db.get_run(actor,run_id)
            if not evidence_available(db,knowledge,actor,run_id):raise LookupError('Context unavailable')
            with db.engine.connect() as c:row=c.execute(select(contexts.c.payload).where(contexts.c.run_id==run_id)).scalar_one_or_none()
            return {'context':row}
        return execute(read)
