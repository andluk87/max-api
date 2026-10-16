"""Bounded per-account diagnostic journal; never stores exception messages or payloads."""
import json
import re
from pymax.exceptions import ApiError
import sqlite3
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime,timezone
from pathlib import Path

request_context=ContextVar("diagnostic_request",default=None)
LIMIT=5000
FIELDS={'request_id','method','route','status','duration_ms','operation','run_id','rule_id','job_id','state'}


def upstream_error(exception):
    """Only structured error code/opcode; no free-form server messages or payload."""
    if not isinstance(exception,ApiError):return {}
    result={}
    if isinstance(exception.opcode,int):result['max_opcode']=int(exception.opcode)
    code=exception.error
    if isinstance(code,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}',code):
        result['max_error_code']=code
    elif code is not None:result['max_error_code']='[non-symbolic code omitted]'
    return result


def connect(directory):
    db=sqlite3.connect(Path(directory)/'diagnostics.sqlite',timeout=5)
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT,level TEXT,event TEXT,body TEXT)')
    return db


def emit(directory,event,level='INFO',exception=None,**fields):
    if request_context.get():fields.setdefault('request_id',request_context.get())
    record={'time':datetime.now(timezone.utc).isoformat(),'level':level,'event':event}
    record.update({k:v for k,v in fields.items() if k in FIELDS and isinstance(v,(str,int,float,bool,type(None)))})
    if exception is not None:
        record['exception_type']=type(exception).__name__
        record.update(upstream_error(exception))
        # Only code locations, no exception values, source lines, locals or full paths.
        record['stack']=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(exception.__traceback__)[-12:]]
    try:
        with connect(directory) as db:
            db.execute('INSERT INTO logs(time,level,event,body) VALUES (?,?,?,?)',(record['time'],level,event,json.dumps(record,ensure_ascii=False)))
            db.execute('DELETE FROM logs WHERE id <= (SELECT COALESCE(MAX(id),0)-? FROM logs)',(LIMIT,))
        db.close()
        print(json.dumps(record,ensure_ascii=False),file=sys.stdout,flush=True)
    except Exception:
        # Diagnostics must not change send results or reveal data in a logging failure.
        print('{"level":"ERROR","event":"diagnostics_write_failed"}',file=sys.stderr,flush=True)


def read(directory,level=None,before=0,limit=100):
    db=connect(directory)
    try:
        rows=db.execute('SELECT id,body FROM logs WHERE (? IS NULL OR level=?) AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?', (level,level,before,before,limit+1)).fetchall()
        more=len(rows)>limit;rows=rows[:limit]
        return {'logs':[{'id':r['id'],**json.loads(r['body'])} for r in rows],'next_before':rows[-1]['id'] if more else None,'retention':LIMIT,'timezone':'UTC'}
    finally:db.close()
