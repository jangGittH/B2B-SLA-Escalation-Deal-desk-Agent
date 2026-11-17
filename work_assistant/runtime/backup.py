"""Quiesced local backup and new-database-only restore. Credentials stay out of argv."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from .queue import now

CHECK_TABLES=('organizations','employees','conversations','runs','artifacts','sla_workflows','sla_approvals',
    'sla_sandbox_credits','sla_action_receipts','deal_requests','deal_sandbox_contracts','deal_contract_effects','runtime_jobs','knowledge_revisions','knowledge_chunks','context_offloads','task_contexts','governed_memories','memory_events')


def command(tool,url,docker=False):
    u=make_url(url)
    env=os.environ|{'PGUSER':u.username or '', 'PGPASSWORD':u.password or '', 'PGDATABASE':u.database or '',
        'PGHOST':'127.0.0.1' if docker else (u.host or '127.0.0.1'),'PGPORT':'5432' if docker else str(u.port or 5432)}
    prefix=['docker','compose','exec','-T']
    if docker:
        for key in ('PGUSER','PGPASSWORD','PGDATABASE','PGHOST','PGPORT'): prefix+=['-e',key]
        prefix+=['postgres',tool]
    else: prefix=[tool]
    return prefix,env


def execute(tool,url,args,*,docker=False,stdin=None,stdout=None):
    cmd,env=command(tool,url,docker)
    result=subprocess.run(cmd+args,env=env,stdin=stdin,stdout=stdout or subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f'{tool} failed; check client/server version and database access (exit {result.returncode})')


def checksum(path):
    h=hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def snapshot_counts(url):
    engine=create_engine(url)
    try:
        with engine.connect() as c:
            return {name:c.execute(text('SELECT count(*) FROM '+name)).scalar_one() for name in CHECK_TABLES}
    finally: engine.dispose()


def validate_artifact_files(url, root):
    engine=create_engine(url)
    try:
        with engine.connect() as c:
            for artifact_id, in c.execute(text('SELECT id FROM artifacts')):
                if not re.fullmatch(r'[0-9a-f]{32}',artifact_id) or any(
                    not (Path(root)/artifact_id/name).is_file() for name in ('content','metadata.json')):
                    raise ValueError('Registered artifact files are missing; check the artifact root before backup or restore')
    finally: engine.dispose()


def backup(url,files,destination,*,quiesced=False,docker=False):
    if not quiesced: raise ValueError('Stop API and workers, then supply --quiesced for a consistent database/artifact backup')
    target=Path(destination);source=Path(files).resolve()
    if source.is_relative_to(target.resolve()) or target.resolve().is_relative_to(source):
        raise ValueError('Backup and artifact directories must be separate')
    validate_artifact_files(url,source)
    target.mkdir(mode=0o700,parents=True,exist_ok=False)
    if source.exists():
        if any(p.is_symlink() for p in source.rglob('*')): raise ValueError('Artifact directory must not contain symlinks')
        shutil.copytree(source,target/'files')
    else: (target/'files').mkdir()
    with (target/'database.dump').open('wb') as output:
        execute('pg_dump',url,['--format=custom','--no-owner','--no-acl'],docker=docker,stdout=output)
    manifest={'format':1,'created_at':now().isoformat(),'quiesced':True,'counts':snapshot_counts(url),
        'files':{str(p.relative_to(target)):checksum(p) for p in target.rglob('*') if p.is_file()}}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2))
    for p in target.rglob('*'): os.chmod(p,0o700 if p.is_dir() else 0o600)
    return manifest


def verify_bundle(directory):
    root=Path(directory).resolve()
    if any(p.is_symlink() for p in root.rglob('*')): raise ValueError('Backup must not contain symlinks')
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest.get('format')!=1 or 'database.dump' not in manifest.get('files',{}): raise ValueError('Unsupported backup manifest')
    for name,expected in manifest['files'].items():
        path=root/name
        if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file() or checksum(path)!=expected:
            raise ValueError('Backup integrity check failed')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name!='manifest.json'}
    if actual!=set(manifest['files']): raise ValueError('Unexpected backup files')
    return manifest


def restore(base_url,directory,name,files,*,docker=False):
    if not re.fullmatch(r'northstar_restore_[A-Za-z0-9_]+',name): raise ValueError('Use a new northstar_restore_* database name')
    destination=Path(files)
    if destination.exists(): raise ValueError('Restore artifact directory must not already exist')
    manifest=verify_bundle(directory)
    url=make_url(base_url).set(database=name).render_as_string(hide_password=False)
    admin=create_engine(base_url,isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as c: c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally: admin.dispose()
    with (Path(directory)/'database.dump').open('rb') as input_file:
        execute('pg_restore',url,['--exit-on-error','--single-transaction','--no-owner','--no-acl','--dbname',name],
            docker=docker,stdin=input_file)
    shutil.copytree(Path(directory)/'files',destination)
    validate_artifact_files(url,destination)
    if snapshot_counts(url)!=manifest['counts']: raise ValueError('Restored record counts differ from backup')
    # File integrity was checked before copying; compare the restored bytes as well.
    for name,expected in manifest['files'].items():
        if name.startswith('files/') and checksum(destination/name[6:])!=expected: raise ValueError('Restored artifact mismatch')
    return url,{'database':name,'counts_verified':True,'artifact_files':len(manifest['files'])-1}
