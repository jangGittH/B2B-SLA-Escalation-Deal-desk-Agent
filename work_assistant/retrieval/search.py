"""Authorized BM25 + multilingual dense retrieval, RRF and local cross-encoder ranking."""
import json
import re
import threading
import time
from pathlib import Path
import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import update
from ..context.tables import chunks as table
from ..telemetry import event
from .index import split,reconcile,fingerprint

_lock=threading.RLock();_models=None
EMBEDDER='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
RERANKER='cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'

def models():
    global _models
    with _lock:
        if _models is None:
            from sentence_transformers import SentenceTransformer,CrossEncoder
            import torch
            torch.set_num_threads(2)
            pins=json.loads(Path(__file__).with_name('models.json').read_text())
            dense=SentenceTransformer(EMBEDDER,revision=pins[EMBEDDER],device='cpu',local_files_only=True,trust_remote_code=False)
            ranker=CrossEncoder(RERANKER,revision=pins[RERANKER],device='cpu',local_files_only=True,trust_remote_code=False,max_length=384)
            _models=(dense,ranker,pins)
        return _models

def tokens(value):
    import unicodedata
    value=unicodedata.normalize('NFKC',value).casefold()
    result=re.findall(r'[a-z0-9]+',value)
    for sequence in re.findall(r'[\u3400-\u9fff]+',value):
        result.extend(sequence[i:i+2] for i in range(max(1,len(sequence)-1)))
    return result


def rank(knowledge,actor,query,language,limit,mode):
    from ..knowledge import can_read,view,SearchHit,SearchResponse
    started=time.monotonic()
    # ACL filtering is before chunk selection, embeddings, BM25 and cross-encoder input.
    docs=[d for d in knowledge.repository.documents() if can_read(actor,d)]
    items=[]
    for doc in docs:
        items.extend(reconcile(knowledge.repository,doc) if knowledge.cases else split(doc))
    if len(items)>4000:raise ValueError('Search corpus exceeds local index budget; narrow or archive the corpus')
    method='bm25';ranked=[];scores={};degraded=False
    if items:
        strings=[r['section']+'\n'+r['content'] for r in items]
        corpus=[tokens(s) for s in strings];qt=tokens(query)
        bm=BM25Okapi(corpus).get_scores(qt)
        lexical=[i for i,row in enumerate(corpus) if set(qt)&set(row)]
        lexical.sort(key=lambda i:(-float(bm[i]),items[i]['id']))
        candidates={i:1/(60+rank) for rank,i in enumerate(lexical[:40],1)}
        if mode=='hybrid':
            try:
                with _lock:
                    encoder,reranker,pins=models()
                    model_key=EMBEDDER+'@'+pins[EMBEDDER]
                    missing=[i for i,r in enumerate(items) if r['embedding_model']!=model_key or not r['embedding']]
                    if missing:
                        vectors=encoder.encode([strings[i] for i in missing],normalize_embeddings=True,show_progress_bar=False,batch_size=32)
                        for i,vector in zip(missing,vectors):items[i]['embedding']=vector.tolist();items[i]['embedding_model']=model_key
                        if knowledge.cases:
                            with knowledge.repository.engine.begin() as c:
                                for i in missing:c.execute(update(table).where(table.c.id==items[i]['id'],table.c.fingerprint==items[i]['fingerprint']).values(embedding=items[i]['embedding'],embedding_model=model_key))
                    qvector=encoder.encode([query],normalize_embeddings=True,show_progress_bar=False)[0]
                    similarity=np.asarray([r['embedding'] for r in items])@qvector
                    dense=sorted((i for i in range(len(items)) if similarity[i]>=.25),key=lambda i:-similarity[i])[:40]
                    for position,i in enumerate(dense,1):candidates[i]=candidates.get(i,0)+1/(60+position)
                    fused=sorted(candidates,key=lambda i:(-candidates[i],items[i]['id']))[:30]
                    if fused:
                        reranked=reranker.predict([(query,strings[i]) for i in fused],show_progress_bar=False,batch_size=16)
                        scores={i:float(score) for i,score in zip(fused,reranked)}
                        ranked=sorted(fused,key=lambda i:(-scores[i],-candidates[i],items[i]['id']))
                    method='bm25_dense_rrf_cross_encoder'
            except (ImportError,OSError,ValueError):
                degraded=True;method='bm25_models_unavailable'
        if not ranked:ranked=lexical[:40];scores={i:float(bm[i]) for i in ranked}
    hits=[];seen=set()
    # Exact identifiers remain decisive even if a semantic model prefers similar prose.
    exact=re.findall(r'\b[A-Z][A-Z0-9]*-\d+[\w-]*\b',query)
    if exact:ranked.sort(key=lambda i:not any(x.casefold() in (items[i]['content']+' '+items[i]['document_id']).casefold() for x in exact))
    current_actor=knowledge._actor(actor.id)
    for i in ranked:
        r=items[i];ident=r['document_id']
        if ident in seen:continue
        doc=knowledge.repository.document(ident)
        if doc is None or not can_read(current_actor,doc) or fingerprint(doc)!=r['fingerprint']:continue
        seen.add(ident)
        hits.append(SearchHit(document=view(doc,language),score=scores.get(i,0),chunk_id=r['id'],section=r['section'],excerpt=r['content'][:900]))
        if len(hits)>=limit:break
    event('retrieval',{'method':method,'candidate_chunks':len(items),'returned':len(hits),'duration_ms':round((time.monotonic()-started)*1000),'degraded':degraded})
    return SearchResponse(query=query,language=language,method=method,hits=tuple(hits))
