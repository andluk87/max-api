"""Message-copy and native-forward transport helpers."""
import asyncio
import copy
import hashlib
import json
import sqlite3
import time
import uuid
from urllib.parse import urlsplit,parse_qs
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from pymax.api.messages.payloads import SendMessagePayload, SendMessagePayloadMessage
from pymax.protocol import Opcode
from .message_links import validate_link, native_source
from .source import read_source, source_photo, photo_attachments
from .media import make_photo


class SourceRef(BaseModel):
    chat_id: int
    message_id: int | None = Field(default=None,ge=1,le=18446744073709551615)
    url: str = Field(default='',max_length=2048)

    @model_validator(mode='after')
    def resolve(self):
        if not self.url.strip():
            if not self.message_id or self.message_id<0:raise ValueError('Выберите сообщение из истории или вставьте ссылку MAX')
            return self
        p=validate_link(self.url)
        self.url=self.url.strip()
        query=parse_qs(p.query)
        candidates=[query[k][0] for k in ('messageId','message_id','mid','message','post') if query.get(k)]
        parts=p.path.strip('/').split('/')
        if len(parts)>=2: candidates.append(parts[-1])
        numeric=next((int(x) for x in candidates if x.isdecimal()),None)
        if self.message_id is None: self.message_id=numeric
        if self.message_id is not None and self.message_id<=0:
            raise ValueError('Некорректное выбранное сообщение')
        if numeric is not None and self.message_id is not None and numeric!=self.message_id:
            raise ValueError('ID сообщения не совпадает со ссылкой')
        if len(parts)==3 and parts[0]=='s' and parts[1].lstrip('-').isdigit() and int(parts[1])!=self.chat_id:
            raise ValueError('Выбранный чат не совпадает со ссылкой')
        return self


class Relay(BaseModel):
    title: str = Field(min_length=1,max_length=150)
    destination: int
    sources: list[SourceRef] = Field(min_length=1,max_length=50)
    interval: int = Field(default=5,ge=5,le=30)

    @model_validator(mode='after')
    def unique(self):
        resolved=[s for s in self.sources if s.message_id is not None]
        if len({(s.chat_id,s.message_id) for s in resolved})!=len(resolved):
            raise ValueError('Одно сообщение указано несколько раз')
        return self


class DuplicateCopy(Exception):
    pass


def is_repost(message):
    link=message.get('link') or {}
    return str(link.get('type','')).upper()=='FORWARD'


def copy_content(message, url, label='Источник'):
    text=message.get('text') or ''
    elements=copy.deepcopy(message.get('elements') or [])
    suffix=(('\n\n' if text else '')+label) if url else ''
    offset=len((text+('\n\n' if text else '')).encode('utf-16-le'))//2
    text+=suffix
    if len(text)>4000: raise HTTPException(422,'Текст со ссылкой превышает 4000 символов')
    for e in elements:
        if 'from_' in e: e['from']=e.pop('from_')
    if url:elements.append({'type':'LINK','from':offset,'length':len(label.encode('utf-16-le'))//2,'attributes':{'url':url}})
    return text,elements


async def send_copy(service, destination, source, claim=None):
    message=source.get('message') or await read_source(service.client,source['chat_id'],source['message_id'])
    if is_repost(message):raise DuplicateCopy('Перепост из другого чата пропущен')
    attachments=message.get('attaches') or []
    photos=photo_attachments(message)
    if len(photos)!=len(attachments):
        raise HTTPException(422,'В сообщении есть вложения кроме фото. Копия не отправлена, чтобы не потерять содержимое')
    if len(photos)>10: raise HTTPException(422,'В сообщении больше 10 фото')
    title,url=await native_source(service.client,source['chat_id'],source['message_id'],message,source.get('link_target','message')) if source.get('add_source',True) else ('',None)
    text,elements=copy_content(message,url,title)
    raw_images=[await source_photo(message,i) for i in range(len(photos))]
    images=[make_photo(raw) for raw in raw_images]
    fingerprint=hashlib.sha256(json.dumps([message.get('text') or '',[hashlib.sha256(raw).hexdigest() for raw in raw_images]],ensure_ascii=False).encode()).hexdigest()
    # All source reads and image validation precede any send attempt.
    async with service.send_lock:
        api=service.client._app.api.messages
        uploaded=await api._upload_attachments(images)
        if claim is not None and not claim(fingerprint):raise DuplicateCopy('Такое содержимое уже отправлялось в этот чат или результат предыдущей отправки неизвестен')
        frame=SendMessagePayload(chat_id=destination,notify=True,message=SendMessagePayloadMessage(
            text=text,cid=api._next_cid(),elements=elements,attaches=uploaded))
        return await service.client._app.invoke(Opcode.MSG_SEND,frame.to_payload())


async def send_forward(service,destination,source,claim=None):
    message=source.get('message') or await read_source(service.client,source['chat_id'],source['message_id'])
    if is_repost(message):raise DuplicateCopy('Перепост пропущен')
    attaches=message.get('attaches') or []
    photos=photo_attachments(message)
    if len(photos)==len(attaches):
        raw_images=[await source_photo(message,i) for i in range(len(photos))]
        fingerprint=hashlib.sha256(json.dumps([message.get('text') or '',[hashlib.sha256(raw).hexdigest() for raw in raw_images]],ensure_ascii=False).encode()).hexdigest()
    else:
        # No file re-upload for native forwarding. Compare the same MAX media IDs.
        identity=[{k:v for k,v in item.items() if k in ('type','_type','file_id','video_id','audio_id','sticker_id','photo_id')} for item in attaches]
        fingerprint=hashlib.sha256(json.dumps([message.get('text') or '',identity],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if any(not any(k.endswith('_id') for k in item) for item in identity):
            fingerprint=hashlib.sha256(f"{source['chat_id']}:{source['message_id']}".encode()).hexdigest()
    async with service.send_lock:
        if claim is not None and not claim(fingerprint):raise DuplicateCopy('Дубль пропущен')
        # PyMax 2.4.1 ForwardLink coerces messageId to str. Preserve MAX's numeric
        # ID type in the wire request (public web client MSG_SEND/FORWARD shape).
        # Do not run through wire(), which stringifies IDs for browser responses.
        api=service.client._app.api.messages
        payload={'chatId':int(destination),'message':{
            'cid':-api._next_cid(),
            'link':{'type':'FORWARD','messageId':int(source['message_id']),
                    'chatId':int(source['chat_id'])},'attaches':[]},'notify':True}
        return await service.client._app.invoke(Opcode.MSG_SEND,payload)


class Relays:
    def __init__(self,service):
        self.service=service
        self.db=sqlite3.connect(service.directory/'relays.sqlite')
        self.db.execute('CREATE TABLE IF NOT EXISTS relays (id TEXT PRIMARY KEY, body TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS relay_runs (id TEXT PRIMARY KEY, rule_id TEXT, body TEXT, created REAL)')
        self.tasks={}
        for rid,raw in self.db.execute('SELECT id,body FROM relay_runs').fetchall():
            run=json.loads(raw)
            if run['state']=='running':
                run['state']='interrupted'
                for item in run['items']:
                    if item['state']=='sending': item['state']='unknown'
                    elif item['state']=='pending': item['state']='cancelled'
                self.put_run(run)
        self.db.commit()

    def get(self,rid):
        row=self.db.execute('SELECT body FROM relays WHERE id=?',(rid,)).fetchone()
        if not row: raise HTTPException(404,'Пересылка не найдена')
        return {'id':rid,**json.loads(row[0])}

    def active(self,rid):
        return any(not task.done() and rule==rid for task,rule in self.tasks.values())

    def save(self,body,rid=None):
        if rid:
            self.get(rid)
            if self.active(rid): raise HTTPException(409,'Дождитесь завершения пересылки')
        rid=rid or uuid.uuid4().hex
        # Decimal IDs stay strings for browser precision; convert at transport boundary.
        data=body.model_dump();data['destination']=str(data['destination'])
        for x in data['sources']:
            x['chat_id']=str(x['chat_id']);x['message_id']=str(x['message_id'])
        self.db.execute('INSERT OR REPLACE INTO relays VALUES (?,?)',(rid,json.dumps(data,ensure_ascii=False)));self.db.commit()
        return self.get(rid)

    def delete(self,rid):
        self.get(rid)
        if self.active(rid): raise HTTPException(409,'Сначала остановите пересылку и дождитесь завершения текущего сообщения')
        self.db.execute('DELETE FROM relays WHERE id=?',(rid,));self.db.commit()

    def list(self):
        return {'relays':[self.get(r[0]) for r in self.db.execute('SELECT id FROM relays ORDER BY rowid DESC')],
                'runs':[json.loads(r[0]) for r in self.db.execute('SELECT body FROM relay_runs ORDER BY created DESC LIMIT 30')]}

    def put_run(self,run):
        self.db.execute('INSERT OR REPLACE INTO relay_runs VALUES (?,?,?,?)',(run['id'],run['rule_id'],json.dumps(run,ensure_ascii=False),run['created']));self.db.commit()

    def start(self,rid):
        if self.active(rid): raise HTTPException(409,'Пересылка уже выполняется')
        if not self.service.ready(): raise HTTPException(409,'Подключите MAX')
        rule=self.get(rid)
        run={'id':uuid.uuid4().hex,'rule_id':rid,'title':rule['title'],'created':time.time(),'state':'running','items':[{'source':s,'state':'pending'} for s in rule['sources']]}
        self.put_run(run)
        task=asyncio.create_task(self.work(run,rule))
        self.tasks[run['id']]=(task,rid)
        task.add_done_callback(lambda t:self.tasks.pop(run['id'],None))
        return run

    async def work(self,run,rule):
        try:
            for index,item in enumerate(run['items']):
                item['state']='sending';self.put_run(run)
                try:
                    source={**item['source'],'chat_id':int(item['source']['chat_id']),'message_id':int(item['source']['message_id'])}
                    await asyncio.wait_for(send_copy(self.service,int(rule['destination']),source),180)
                    item['state']='sent'
                except HTTPException as e:
                    item['state']='failed';item['error']=str(e.detail)
                except Exception:
                    item['state']='unknown';item['error']='Результат неизвестен. Проверьте чат назначения перед повтором.'
                self.put_run(run)
                if index+1<len(run['items']): await asyncio.sleep(rule['interval'])
            run['state']='completed' if all(x['state']=='sent' for x in run['items']) else 'partial'
        except asyncio.CancelledError:
            run['state']='cancelled'
            for item in run['items']:
                if item['state']=='sending':item['state']='unknown'
                elif item['state']=='pending':item['state']='cancelled'
        finally:self.put_run(run)

    def cancel(self,run_id):
        if run_id not in self.tasks: raise HTTPException(409,'Нет активного запуска')
        self.tasks[run_id][0].cancel()

    async def close(self):
        tasks=[x[0] for x in self.tasks.values()]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.db.close()
