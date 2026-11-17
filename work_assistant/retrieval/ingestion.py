"""Bounded upload extraction and versioned originals, with explicit human ACL changes."""
import base64
import hashlib
import io
import time
import zipfile
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,insert,update,delete
from ..models import Document,BilingualText
from ..knowledge import KnowledgeService,can_read
from ..db.models import entities
from ..context.tables import revisions,chunks
from ..db.store import ConflictError
from .index import reconcile

class Upload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=160)
    filename:str=Field(min_length=1,max_length=150)
    content_base64:str=Field(min_length=1,max_length=2800000)
    language:Literal['en','zh']='en'
    scope:Literal['user','team','tenant']='user'
    team_id:str|None=None
    expected_version:int|None=Field(default=None,ge=1)


def extract(filename,raw):
    if len(raw)>2000000:raise ValueError('Upload exceeds 2 MB')
    ext=Path(filename).suffix.lower()
    if ext in ('.txt','.md'):result=raw.decode('utf-8-sig')
    elif ext=='.docx':
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist())>10000000 or len(z.infolist())>2000:raise ValueError('Expanded document is too large')
        from docx import Document as Word
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        doc=Word(io.BytesIO(raw));lines=[]
        for block in doc.iter_inner_content():
            if isinstance(block,Paragraph):
                heading=block.style.name if block.style else ''
                lines.append(('# '+block.text) if heading.startswith('Heading') else block.text)
            elif isinstance(block,Table):lines.extend(' | '.join(cell.text for cell in row.cells) for row in block.rows)
        result='\n'.join(lines)
    elif ext=='.pdf':
        from pypdf import PdfReader
        pdf=PdfReader(io.BytesIO(raw))
        if pdf.is_encrypted or len(pdf.pages)>50:raise ValueError('Use an unencrypted text PDF with at most 50 pages')
        result='\n\n'.join(f'# Page {i+1}\n{p.extract_text() or ""}' for i,p in enumerate(pdf.pages))
        if not any((p.extract_text() or '').strip() for p in pdf.pages):raise ValueError('Scanned PDFs require OCR, which is not supported')
    else:raise ValueError('Use TXT, Markdown, DOCX or a text PDF')
    if not result.strip() or len(result)>100000:raise ValueError('Extracted text must contain 1–100,000 characters')
    return result

class Ingestion:
    def __init__(self,db):self.db=db;self.knowledge=KnowledgeService(db)
    def save(self,actor_id,body,document_id=None):
        actor=self.knowledge._actor(actor_id)
        try:raw=base64.b64decode(body.content_base64,validate=True);content=extract(body.filename,raw)
        except Exception as exc:raise ValueError(str(exc) if isinstance(exc,ValueError) else 'Document could not be extracted') from None
        if body.scope!='user' and 'operations' not in actor.team_ids:raise PermissionError('Only Operations can publish shared evidence')
        if body.scope=='team' and body.team_id not in actor.team_ids:raise PermissionError('Choose one of your teams')
        if body.scope!='team' and body.team_id:raise ValueError('team_id is only valid for team scope')
        stamp=time.time()
        with self.db.engine.begin() as c:
            self.db._lock_actor(c,actor)
            version=1
            if document_id:
                old=c.execute(select(entities['documents'].c.payload).where(entities['documents'].c.id==document_id).with_for_update()).scalar_one_or_none()
                owner=c.execute(select(revisions.c.owner_id).where(revisions.c.document_id==document_id).order_by(revisions.c.version.desc()).limit(1)).scalar_one_or_none()
                if not old or not can_read(actor,Document.model_validate(old)) or not owner:raise LookupError('Uploaded document not found')
                if owner!=actor.id and 'operations' not in actor.team_ids:raise PermissionError('Only the owner or Operations can revise evidence')
                if body.expected_version!=old['version']:raise ConflictError('Document changed; reload before revising')
                version=old['version']+1
            elif body.expected_version is not None:raise ValueError('New documents have no expected_version')
            ident=document_id or 'upload-'+uuid4().hex
            # Both fields retain original language; no fabricated translation.
            doc=Document(id=ident,organization_id=actor.organization_id,title=BilingualText(en=body.title,zh=body.title),
                content=BilingualText(en=content,zh=content),version=version,updated_at=datetime.now(timezone.utc),
                visibility='organization' if body.scope=='tenant' else 'restricted',
                reader_employee_ids=(actor.id,) if body.scope=='user' else (),reader_team_ids=(body.team_id,) if body.scope=='team' else ())
            if document_id:c.execute(update(entities['documents']).where(entities['documents'].c.id==ident).values(payload=doc.model_dump(mode='json')))
            else:c.execute(insert(entities['documents']).values(id=ident,organization_id=actor.organization_id,payload=doc.model_dump(mode='json')))
            c.execute(insert(revisions).values(id=uuid4().hex,document_id=ident,organization_id=actor.organization_id,owner_id=actor.id,
                version=version,filename=Path(body.filename).name,original=raw,checksum=hashlib.sha256(raw).hexdigest(),
                payload={'document':doc.model_dump(mode='json'),'language':body.language,'scope':body.scope},created_at=stamp))
            c.execute(delete(chunks).where(chunks.c.document_id==ident))
        # Search also reconciles lazily if the process stops between commit and indexing.
        reconcile(self.db,doc)
        return {'id':ident,'version':version,'characters':len(content),'language':body.language,'scope':body.scope}
    def history(self,actor_id,ident):
        self.knowledge.read(actor_id,ident,'en')
        with self.db.engine.connect() as c:
            rows=c.execute(select(revisions).where(revisions.c.document_id==ident).order_by(revisions.c.version.desc())).mappings().all()
        actor=self.knowledge._actor(actor_id)
        return [{k:r[k] for k in ('version','filename','checksum','created_at')} for r in rows if can_read(actor,Document.model_validate(r['payload']['document']))]
    def original(self,actor_id,ident,version):
        self.knowledge.read(actor_id,ident,'en')
        with self.db.engine.connect() as c:
            row=c.execute(select(revisions).where(revisions.c.document_id==ident,revisions.c.version==version)).mappings().first()
        if not row:raise LookupError('Revision not found')
        # Historical versions may contain text whose former ACL was narrower.
        if not can_read(self.knowledge._actor(actor_id),Document.model_validate(row['payload']['document'])):raise LookupError('Revision not found')
        return row
    def remove(self,actor_id,ident,version):
        actor=self.knowledge._actor(actor_id)
        with self.db.engine.begin() as c:
            self.db._lock_actor(c,actor)
            raw=c.execute(select(entities['documents'].c.payload).where(entities['documents'].c.id==ident).with_for_update()).scalar_one_or_none()
            owner=c.execute(select(revisions.c.owner_id).where(revisions.c.document_id==ident).limit(1)).scalar_one_or_none()
            if not raw or not owner or not can_read(actor,Document.model_validate(raw)):raise LookupError('Uploaded document not found')
            if actor.id!=owner and 'operations' not in actor.team_ids:raise PermissionError('Only owner or Operations may delete')
            if raw['version']!=version:raise ConflictError('Document changed')
            c.execute(delete(chunks).where(chunks.c.document_id==ident));c.execute(delete(revisions).where(revisions.c.document_id==ident))
            c.execute(delete(entities['documents']).where(entities['documents'].c.id==ident))
