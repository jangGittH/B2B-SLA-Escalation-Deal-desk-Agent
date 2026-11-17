"""A bounded worker can run embedded for local use or as independent processes."""
import logging
import os
import signal
import threading
import time
from uuid import uuid4
from .queue import Queue
from ..workflows.service import WorkflowService
from ..deals.workflow import DealWorkflowService
from ..workflows.engine import WorkflowEngine

log=logging.getLogger('work_assistant.worker')


class Worker:
    def __init__(self,db,*,slots=2,engine_factory=WorkflowEngine):
        self.db=db; self.queue=Queue(db); self.slots=slots; self.engine_factory=engine_factory
        self.id=f'{os.getpid()}-{uuid4().hex[:12]}'; self.stop=threading.Event(); self.threads=[]

    def once(self):
        job=self.queue.claim(self.id)
        if job is None: return False
        finished=threading.Event()
        def pulse():
            while not finished.wait(job['lease_seconds']/3):
                try:
                    if not self.queue.heartbeat(job): return
                except Exception:
                    log.warning('lease_heartbeat_unavailable',extra={'workflow_id':job['workflow_id']})
        thread=threading.Thread(target=pulse,daemon=True);thread.start()
        error=None
        try:
            service=(DealWorkflowService if job['kind']=='deal' else WorkflowService)(self.db)
            self.engine_factory(service).drive(job['workflow_id'])
        except Exception:
            error='Worker storage or dispatch failure'
            log.warning('delivery_failed',extra={'workflow_id':job['workflow_id'],'worker_id':self.id})
        finally:
            finished.set();thread.join()
        self.queue.finish(job,error)
        return True

    def _loop(self,slot):
        last=0
        while not self.stop.is_set():
            try:
                if slot==0 and time.monotonic()-last>1:
                    self.queue.worker_heartbeat(self.id)
                    self.queue.reconcile()
                    from ..memory.service import MemoryService
                    MemoryService(self.db).consolidate_completed()
                    last=time.monotonic()
                if not self.once(): self.stop.wait(.15)
            except Exception:
                # Storage outages leave the unacknowledged lease recoverable; do not log credentials.
                log.warning('worker_storage_unavailable',extra={'worker_id':self.id})
                self.stop.wait(1)

    def start(self):
        for slot in range(self.slots):
            thread=threading.Thread(target=self._loop,args=(slot,),daemon=True)
            thread.start();self.threads.append(thread)
        return self

    def close(self):
        self.stop.set()
        try: self.queue.worker_heartbeat(self.id,True)
        except Exception: pass
        for thread in self.threads: thread.join()


def run(db,slots=2):
    worker=Worker(db,slots=slots)
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:worker.stop.set())
    worker.start()
    try:
        while not worker.stop.wait(.5): pass
    finally: worker.close()
