"""Versioned section chunks. Current document ACLs are always authoritative."""
import hashlib
import json
import re
from sqlalchemy import select,delete,insert,text
from ..context.tables import chunks


def fingerprint(document):
    # Changing chunk semantics must rebuild existing rows even without a source edit.
    return hashlib.sha256(('hierarchical-chunks-v2:'+document.model_dump_json()).encode()).hexdigest()


def split(document):
    result=[];fp=fingerprint(document)
    for language in ('en','zh'):
        section=document.title.localized(language);parts=[];headings=[]
        def flush():
            if not parts:return
            content='\n'.join(parts);parts.clear()
            # Overlap retains context across unusually long paragraphs; no inferred facts.
            for offset in range(0,len(content),700):
                piece=content[offset:offset+900]
                ordinal=len(result)
                ident=hashlib.sha256(f'{document.id}:{document.version}:{fp}:{language}:{ordinal}'.encode()).hexdigest()
                result.append(dict(id=ident,document_id=document.id,version=document.version,fingerprint=fp,
                    language=language,ordinal=ordinal,section=section[:500],content=piece,embedding_model=None,embedding=None))
        for line in document.content.localized(language).splitlines():
            heading=re.match(r'^(#{1,6})\s+(.+)',line)
            if heading:
                flush()
                level=len(heading.group(1))
                headings=[item for item in headings if item[0]<level]
                headings.append((level,heading.group(2).strip()))
                section=' / '.join(title for _,title in headings)
            if sum(map(len,parts))+len(line)>800:flush()
            parts.append(line)
        flush()
    return result


def reconcile(db,document):
    expected=split(document)
    with db.engine.begin() as c:
        c.execute(text('SELECT pg_advisory_xact_lock(hashtext(:id))'),{'id':'index:'+document.id})
        old=c.execute(select(chunks.c.fingerprint).where(chunks.c.document_id==document.id).limit(1)).scalar_one_or_none()
        if old!=fingerprint(document):
            c.execute(delete(chunks).where(chunks.c.document_id==document.id))
            if expected:c.execute(insert(chunks),expected)
        return [dict(r) for r in c.execute(select(chunks).where(chunks.c.document_id==document.id).order_by(chunks.c.ordinal)).mappings()]
