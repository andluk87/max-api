"""Persistent event-driven relay rules. No history backfill or automatic send retries."""
import asyncio
import contextlib
import json
import sqlite3
import time
import uuid
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,Field,model_validator
from .relays import send_copy, send_forward, is_repost, DuplicateCopy
from .message_links import web_message_link
from .diagnostics import emit, request_context, upstream_error
from .uploads import PhotoUploadError


class AutoRelay(BaseModel):
    title:str=Field(min_length=1,max_length=150)
    destination:int
    source_chat_ids:list[int]=Field(min_length=1,max_length=100)
    interval:int=Field(default=5,ge=5,le=30)
    enabled:bool=False
    mode:Literal['forward','copy']='copy'
    add_source:bool=True
    link_target:Literal['chat','message']='chat'

    @model_validator(mode='after')
    def check(self):
        self.source_chat_ids=list(dict.fromkeys(self.source_chat_ids))
        if self.destination in self.source_chat_ids:raise ValueError('Чат назначения не должен быть источником')
        return self


class AutoRelays:
    def __init__(self,service):
        self.service=service;self.task=None;self.error=None
        self.db=sqlite3.connect(service.directory/'relays.sqlite');self.db.row_factory=sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS auto_rules(id TEXT PRIMARY KEY,body TEXT,cursor INTEGER,cutoff REAL,next_send REAL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS auto_jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,rule_id TEXT,chat TEXT,mid TEXT,body TEXT,state TEXT,error TEXT,created REAL,UNIQUE(rule_id,chat,mid));
          CREATE TABLE IF NOT EXISTS auto_fingerprints(destination TEXT,fingerprint TEXT,created REAL,PRIMARY KEY(destination,fingerprint));
          CREATE TABLE IF NOT EXISTS auto_meta(key TEXT PRIMARY KEY);
        ''')
        # Existing manual lists become OFF rules, never silently activate sending.
        if not self.db.execute("SELECT 1 FROM auto_meta WHERE key='migrated'").fetchone():
            old=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='relays'").fetchone()
            if old:
                for row in self.db.execute('SELECT id,body FROM relays').fetchall():
                    data=json.loads(row['body']);sources=list(dict.fromkeys(str(x['chat_id']) for x in data['sources']))
                    sources=[x for x in sources if x!=str(data['destination'])]
                    if sources:
                        body=dict(title=data['title'],destination=str(data['destination']),source_chat_ids=sources,interval=data['interval'],enabled=False)
                        self.db.execute('INSERT OR IGNORE INTO auto_rules VALUES (?,?,?,?,0)',(row['id'],json.dumps(body),self.highwater(),time.time()))
            self.db.execute("INSERT INTO auto_meta VALUES ('migrated')")
        self.db.execute("UPDATE auto_jobs SET state='unknown',error='Отправка прервана перезапуском. Проверьте чат назначения.' WHERE state='sending'")
        self.db.commit()

    def highwater(self):return self.service.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    def get(self,rid):
        row=self.db.execute('SELECT * FROM auto_rules WHERE id=?',(rid,)).fetchone()
        if not row:raise HTTPException(404,'Правило не найдено')
        return {'id':rid,'mode':'copy','add_source':True,'link_target':'message',**json.loads(row['body'])}
    def rules(self):return [self.get(r[0]) for r in self.db.execute('SELECT id FROM auto_rules ORDER BY rowid DESC')]
    def check_cycles(self,rid,new):
        rules=[r for r in self.rules() if r['id']!=rid]+[new];graph={}
        for rule in rules:
            if not rule['enabled']:continue
            for src in rule['source_chat_ids']:graph.setdefault(str(src),set()).add(str(rule['destination']))
        done=set();visiting=set()
        def visit(node):
            if node in visiting:raise HTTPException(422,'Правила образуют круг пересылок. Измените источники или назначение.')
            if node in done:return
            visiting.add(node)
            for target in graph.get(node,()):visit(target)
            visiting.remove(node);done.add(node)
        for node in graph:visit(node)

    def save(self,body,rid=None):
        previous=self.get(rid) if rid else None
        rid=rid or uuid.uuid4().hex;data=body.model_dump();data['destination']=str(data['destination']);data['source_chat_ids']=[str(x) for x in data['source_chat_ids']]
        self.check_cycles(rid,data)
        changed=not previous or any(previous[k]!=data[k] for k in ('enabled','destination','source_chat_ids'))
        with self.db:
            if changed:
                self.db.execute("UPDATE auto_jobs SET state='cancelled' WHERE rule_id=? AND state='pending'",(rid,))
                self.db.execute('INSERT OR REPLACE INTO auto_rules VALUES (?,?,?,?,0)',(rid,json.dumps(data,ensure_ascii=False),self.highwater(),time.time()))
            else:self.db.execute('UPDATE auto_rules SET body=? WHERE id=?',(json.dumps(data,ensure_ascii=False),rid))
        return self.get(rid)
    def toggle(self,rid,enabled):
        data=self.get(rid);return self.save(AutoRelay(**{**data,'enabled':enabled}),rid)
    def delete(self,rid):
        self.get(rid)
        with self.db:
            self.db.execute("UPDATE auto_jobs SET state='cancelled' WHERE rule_id=? AND state='pending'",(rid,))
            self.db.execute('DELETE FROM auto_rules WHERE id=?',(rid,))
    def list(self,include_jobs=True,limit=100):
        jobs=[]
        for row in (self.db.execute('SELECT * FROM auto_jobs ORDER BY id DESC LIMIT ?', (limit,)) if include_jobs else []):
            data=json.loads(row['body']);jobs.append({'id':row['id'],'rule_id':row['rule_id'],'title':data['title'],'chat_id':row['chat'],'state':row['state'],'error':row['error'],'created':row['created']})
        return {'relays':self.rules(),'jobs':jobs,'connected':self.service.ready(),'error':self.error}

    def collect(self):
        for row in self.db.execute('SELECT * FROM auto_rules').fetchall():
            rule=json.loads(row['body'])
            if not rule['enabled']:continue
            events=self.service.db.execute('SELECT seq,chat,mid,payload FROM events WHERE seq>? ORDER BY seq LIMIT 500',(row['cursor'],)).fetchall()
            with self.db:
                for seq,chat,mid,payload in events:
                    if chat not in rule['source_chat_ids']:continue
                    message=json.loads(payload)
                    stamp=float(message.get('time') or 0)
                    if stamp>1e12:stamp/=1000
                    if stamp and stamp<row['cutoff']:continue
                    if not str(mid).isdigit():continue
                    # Service-only events without content are not messages to clone.
                    if not message.get('text') and not message.get('attaches') and not is_repost(message):continue
                    source={'chat_id':int(chat),'message_id':int(mid),'url':web_message_link(chat,int(mid)),'message':message}
                    body={**rule,'source':source}
                    self.db.execute('INSERT OR IGNORE INTO auto_jobs(rule_id,chat,mid,body,state,created) VALUES (?,?,?,?,?,?)',(row['id'],chat,mid,json.dumps(body,ensure_ascii=False),'skipped_repost' if is_repost(message) else 'pending',time.time()))
                if events:self.db.execute('UPDATE auto_rules SET cursor=? WHERE id=?',(events[-1][0],row['id']))

    async def step(self):
        self.collect()
        if not self.service.ready():return
        row=self.db.execute("SELECT j.* FROM auto_jobs j JOIN auto_rules r ON r.id=j.rule_id WHERE j.state='pending' AND r.next_send<=? ORDER BY j.id LIMIT 1",(time.time(),)).fetchone()
        if not row:return
        data=json.loads(row['body'])
        self.db.execute("UPDATE auto_jobs SET state='sending' WHERE id=?",(row['id'],));self.db.commit()
        state='unknown';error='Отправка прервана. Проверьте чат назначения.'
        emit(self.service.directory,'relay_attempt',operation=data.get('mode','copy'),rule_id=row['rule_id'],job_id=row['id'],state='sending')
        try:
            sender=send_forward if data.get('mode','copy')=='forward' else send_copy
            options={**data['source'],'add_source':data.get('add_source',True),'link_target':data.get('link_target','message')}
            await asyncio.wait_for(sender(self.service,int(data['destination']),options,claim=lambda fingerprint:self.claim(data['destination'],fingerprint)),180)
            state='sent';error=None
        except DuplicateCopy as e:state='skipped_duplicate';error=str(e)
        except HTTPException as e:
            state='failed';error=str(e.detail)
            emit(self.service.directory,'relay_failed','ERROR',exception=e,operation=data.get('mode','copy'),rule_id=row['rule_id'],job_id=row['id'])
        except PhotoUploadError as e:
            state = 'failed'
            error = 'Фото не загружено; сообщение не отправлено. Проверьте Логи. Автоматического повтора нет.'
            emit(self.service.directory, 'relay_upload_failed', 'ERROR', exception=e, operation=data.get('mode','copy'), rule_id=row['rule_id'], job_id=row['id'])
        except asyncio.CancelledError:raise
        except Exception as e:
            code=upstream_error(e).get('max_error_code')
            if code == 'not.found' and getattr(e, 'opcode', None) == 64:
                state = 'failed'
                error = 'MAX: not.found. Сообщение или чат не найдены либо недоступны аккаунту. Проверьте исходное сообщение, чат назначения и права доступа. Автоматического повтора нет.'
            else:
                error=('MAX: '+code+'. ' if code else '')+'Результат неизвестен. Автоматического повтора нет.'
            emit(self.service.directory,'relay_failed' if state == 'failed' else 'relay_unknown','ERROR',exception=e,operation=data.get('mode','copy'),rule_id=row['rule_id'],job_id=row['id'])
        finally:
            emit(self.service.directory,'relay_result',state=state,rule_id=row['rule_id'],job_id=row['id'])
            with self.db:
                self.db.execute('UPDATE auto_jobs SET state=?,error=? WHERE id=?',(state,error,row['id']))
                self.db.execute('UPDATE auto_rules SET next_send=? WHERE id=?',(time.time()+data['interval'],row['rule_id']))
    def claim(self,destination,fingerprint):
        with self.db:
            result=self.db.execute('INSERT OR IGNORE INTO auto_fingerprints VALUES (?,?,?)',(str(destination),fingerprint,time.time()))
        return result.rowcount==1
    def start(self):self.task=asyncio.create_task(self.loop())
    async def loop(self):
        request_context.set(None)
        while True:
            try:await self.step();self.error=None
            except Exception as e:
                self.error='Ошибка обработки новых сообщений'
                emit(self.service.directory,'relay_worker_failed','ERROR',exception=e)
            await asyncio.sleep(.5)
    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await self.task
        self.db.close()
