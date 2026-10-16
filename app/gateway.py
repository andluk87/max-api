"""Multi-tenant boundary. User credentials never reach another tenant app."""
import asyncio
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from .service import Service
from .diagnostics import emit, request_context


class UserChange(BaseModel):
    name:str=Field(min_length=1,max_length=100)


class Enabled(BaseModel):
    enabled:bool


class Gateway:
    def __init__(self,factory,directory=None,admin_key=None,run_scheduler=True):
        self.factory=factory;self.root=Path(directory or os.environ.get('DATA_DIR','./data'))
        self.admin_key=admin_key if admin_key is not None else os.environ.get('API_KEY','')
        self.internal=secrets.token_urlsafe(48);self.run_scheduler=run_scheduler
        self.apps={};self.locks={};self.db=None;self.started=time.time()
        @asynccontextmanager
        async def lifespan(app):
            if len(self.admin_key)<32:raise RuntimeError('API_KEY must have at least 32 characters')
            self.root.mkdir(parents=True,exist_ok=True);os.umask(0o077)
            self.db=sqlite3.connect(self.root/'users.sqlite');self.db.row_factory=sqlite3.Row
            self.db.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT,key_hash TEXT UNIQUE,enabled INTEGER,created REAL,last_seen REAL);
            CREATE TABLE IF NOT EXISTS request_stats(uid TEXT,day TEXT,requests INTEGER,errors INTEGER,PRIMARY KEY(uid,day));
            CREATE TABLE IF NOT EXISTS direct_stats(uid TEXT,kind TEXT,count INTEGER,PRIMARY KEY(uid,kind));
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,created REAL,action TEXT,uid TEXT);
            ''');self.db.commit()
            emit(self.root,'server_started')
            await self.tenant('admin')
            for row in self.db.execute('SELECT id FROM users WHERE enabled=1').fetchall():await self.tenant(row['id'])
            try:yield
            finally:
                for uid in list(self.apps):await self.stop(uid)
                self.db.close()
        self.control=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None)
        self.routes()

    def digest(self,key):return hashlib.sha256(key.encode()).hexdigest()
    def lock(self,uid):return self.locks.setdefault(uid,asyncio.Lock())
    def user(self,uid):
        if uid=='admin':return {'id':'admin','name':'Администратор','enabled':1,'created':None,'last_seen':None}
        row=self.db.execute('SELECT id,name,enabled,created,last_seen FROM users WHERE id=?',(uid,)).fetchone()
        if not row:raise HTTPException(404,'Пользователь не найден')
        return dict(row)
    def record(self,action,uid):
        self.db.execute('INSERT INTO audit(created,action,uid) VALUES (?,?,?)',(time.time(),action,uid));self.db.commit()
    async def tenant(self,uid):
        if uid not in self.apps:
            user=self.user(uid);directory=self.root if uid=='admin' else self.root/'users'/uid
            enabled=bool(user['enabled'])
            app=self.factory(Service(str(directory)),self.internal,run_scheduler=self.run_scheduler and enabled,restore_session=enabled)
            context=app.router.lifespan_context(app);await context.__aenter__();self.apps[uid]=(app,context)
        return self.apps[uid][0]
    async def stop(self,uid):
        pair=self.apps.pop(uid,None)
        if pair:await pair[1].__aexit__(None,None,None)
    def summary(self,uid):
        user=self.user(uid);pair=self.apps.get(uid);directory=self.root if uid=='admin' else self.root/'users'/uid
        result={**user,'enabled':bool(user['enabled']),'max_state':pair[0].state.service.status()['state'] if pair else 'disconnected'}
        def scalar(db,sql):return db.execute(sql).fetchone()[0]
        stats={}
        for filename in ('events.sqlite','campaigns.sqlite','relays.sqlite'):
            path=directory/filename
            if not path.exists():continue
            db=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
            try:
                if filename=='events.sqlite':stats['messages_received']=scalar(db,'SELECT COUNT(*) FROM events')
                elif filename=='campaigns.sqlite':
                    stats['saved_messages']=scalar(db,'SELECT COUNT(*) FROM drafts')
                    stats['favorites']=scalar(db,'SELECT COUNT(*) FROM favorite_chats')
                    stats['campaign_deliveries']={r[0]:r[1] for r in db.execute('SELECT state,COUNT(*) FROM deliveries GROUP BY state')}
                    stats['schedules_enabled']=scalar(db,"SELECT COUNT(*) FROM drafts WHERE json_extract(config,'$.enabled')=1")
                else:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='auto_rules'").fetchone():
                        stats['relay_rules']=scalar(db,'SELECT COUNT(*) FROM auto_rules')
                        stats['relays_enabled']=scalar(db,"SELECT COUNT(*) FROM auto_rules WHERE json_extract(body,'$.enabled')=1")
                        stats['relay_deliveries']={r[0]:r[1] for r in db.execute('SELECT state,COUNT(*) FROM auto_jobs GROUP BY state')}
            finally:db.close()
        # Admin directory also contains tenant subdirectories; count only own files.
        stats['disk_bytes']=sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) if directory.exists() else 0
        row=self.db.execute('SELECT COALESCE(SUM(requests),0),COALESCE(SUM(errors),0) FROM request_stats WHERE uid=?',(uid,)).fetchone()
        stats['api_requests'],stats['api_errors']=row
        stats.update({r[0]:r[1] for r in self.db.execute('SELECT kind,count FROM direct_stats WHERE uid=?',(uid,))})
        result['stats']=stats;return result

    def routes(self):
        api=self.control
        @api.get('/api/admin/users')
        async def users():
            users=[self.summary('admin')]+[self.summary(r[0]) for r in self.db.execute('SELECT id FROM users ORDER BY created')]
            totals={key:sum(u['stats'].get(key,0) for u in users) for key in ('messages_received','saved_messages','schedules_enabled','relay_rules','relays_enabled','api_requests','api_errors','disk_bytes','direct_sent','direct_unknown')}
            daily=[dict(r) for r in self.db.execute('SELECT day,SUM(requests) AS requests,SUM(errors) AS errors FROM request_stats GROUP BY day ORDER BY day DESC LIMIT 14')]
            return {'users':users,'totals':totals,'daily':daily,'uptime_seconds':int(time.time()-self.started),'audit':[dict(r) for r in self.db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]}
        @api.post('/api/admin/users',status_code=201)
        async def create(body:UserChange):
            uid=uuid.uuid4().hex;key='maxu_'+secrets.token_urlsafe(40)
            self.db.execute('INSERT INTO users VALUES (?,?,?,?,?,NULL)',(uid,body.name.strip(),self.digest(key),1,time.time()));self.db.commit()
            await self.tenant(uid);self.record('create',uid)
            return {'user':self.user(uid),'api_key':key}
        @api.patch('/api/admin/users/{uid}')
        async def rename(uid:str,body:UserChange):
            if uid=='admin':raise HTTPException(422,'Администратор задан в .env')
            self.user(uid);self.db.execute('UPDATE users SET name=? WHERE id=?',(body.name.strip(),uid));self.db.commit();self.record('rename',uid);return self.user(uid)
        @api.post('/api/admin/users/{uid}/key')
        async def rotate(uid:str):
            if uid=='admin':raise HTTPException(422,'Ключ администратора меняется в .env')
            async with self.lock(uid):
                self.user(uid);key='maxu_'+secrets.token_urlsafe(40);self.db.execute('UPDATE users SET key_hash=? WHERE id=?',(self.digest(key),uid));self.db.commit();self.record('rotate_key',uid)
            return {'api_key':key}
        @api.patch('/api/admin/users/{uid}/enabled')
        async def enabled(uid:str,body:Enabled):
            if uid=='admin':raise HTTPException(422,'Нельзя заблокировать администратора')
            async with self.lock(uid):
                self.user(uid);self.db.execute('UPDATE users SET enabled=? WHERE id=?',(int(body.enabled),uid));self.db.commit();await self.stop(uid)
                if body.enabled:await self.tenant(uid)
                self.record('enable' if body.enabled else 'disable',uid)
            return self.user(uid)
        @api.delete('/api/admin/users/{uid}')
        async def delete(uid:str):
            if uid=='admin':raise HTTPException(422,'Нельзя удалить администратора')
            async with self.lock(uid):
                self.user(uid);await self.stop(uid)
                path=self.root/'users'/uid
                if path.exists():
                    trash=self.root/'deleted';trash.mkdir(exist_ok=True);path.rename(trash/(uid+'-'+uuid.uuid4().hex))
                self.db.execute('DELETE FROM users WHERE id=?',(uid,));self.db.commit();self.record('delete',uid)
            return {'deleted':True}

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.dispatch(scope,receive,send)
        request_id=uuid.uuid4().hex;context_token=request_context.set(request_id);started=time.monotonic();status=500;sent=False;exc=None
        scope=dict(scope);scope['_diagnostics_dir']=self.root
        route='unmatched'
        for app in [self.control]+[p[0] for p in list(self.apps.values())[:1]]:
            for candidate in app.routes:
                if getattr(candidate,'path_regex',None) and candidate.path_regex.fullmatch(scope['path']):route=candidate.path;break
        async def tracked(message):
            nonlocal status,sent
            if message['type']=='http.response.start':
                sent=True;status=message['status'];message=dict(message)
                message['headers']=list(message.get('headers',[]))+[(b'x-request-id',request_id.encode())]
            await send(message)
        try:await self.dispatch(scope,receive,tracked)
        except Exception as error:
            exc=error
            if not sent:await JSONResponse({'detail':'Внутренняя ошибка. Проверьте Логи.','request_id':request_id},status_code=500)(scope,receive,tracked)
        finally:
            if status>=400 or scope['method'] not in ('GET','HEAD','OPTIONS'):
                emit(scope['_diagnostics_dir'],'http_request','ERROR' if status>=500 else 'WARNING' if status>=400 else 'INFO',exception=exc,request_id=request_id,method=scope['method'],route=route,status=status,duration_ms=round((time.monotonic()-started)*1000))
            request_context.reset(context_token)

    async def dispatch(self,scope,receive,send):
        if scope['type']=='lifespan':return await self.control(scope,receive,send)
        if scope['type']!='http':return
        path=scope['path'];headers=dict(scope.get('headers',[]));token=headers.get(b'authorization',b'').decode('latin1')
        key=token[7:] if token.startswith('Bearer ') else ''
        is_admin=bool(key) and hmac.compare_digest(key.encode(),self.admin_key.encode())
        uid=None
        if is_admin:uid=headers.get(b'x-user-id',b'admin').decode('latin1') or 'admin'
        elif key:
            row=self.db.execute('SELECT id,enabled FROM users WHERE key_hash=?',(self.digest(key),)).fetchone()
            if row and row['enabled']:uid=row['id']
        async def error(status,detail):
            await JSONResponse({'detail':detail},status_code=status,headers={'Cache-Control':'no-store'})(scope,receive,send)
        if path.startswith('/api/'):
            if uid is None:return await error(401,'Неверный ключ или пользователь заблокирован')
            if path.startswith('/api/admin/'):
                if not is_admin:return await error(403,'Требуется ключ администратора')
                return await self.control(scope,receive,self.no_cache(send))
            if not is_admin and b'x-user-id' in headers:return await error(403,'Выбор другого пользователя запрещён')
            async with self.lock(uid):
                # Recheck after acquiring lock: disable/delete/rotate can race a request.
                try:user=self.user(uid)
                except HTTPException:return await error(404,'Пользователь не найден')
                if not is_admin:
                    row=self.db.execute('SELECT key_hash,enabled FROM users WHERE id=?',(uid,)).fetchone()
                    if not row or not row['enabled'] or not hmac.compare_digest(row['key_hash'],self.digest(key)):return await error(401,'Ключ отозван или пользователь заблокирован')
                if path=='/api/whoami':
                    return await JSONResponse({'role':'admin' if is_admin else 'user','user':user})(scope,receive,self.no_cache(send))
                app=await self.tenant(uid)
                scope['_diagnostics_dir']=app.state.service.directory
                rewritten=dict(scope);rewritten['headers']=[(k,v) for k,v in scope['headers'] if k.lower() not in (b'authorization',b'x-user-id')]+[(b'authorization',('Bearer '+self.internal).encode())]
                status=500
                async def track(message):
                    nonlocal status
                    if message['type']=='http.response.start':status=message['status']
                    await send(message)
                try:await app(rewritten,receive,track)
                finally:
                    day=time.strftime('%Y-%m-%d',time.gmtime())
                    self.db.execute('INSERT INTO request_stats VALUES (?,?,1,?) ON CONFLICT(uid,day) DO UPDATE SET requests=requests+1,errors=errors+excluded.errors',(uid,day,int(status>=400)))
                    if uid!='admin':self.db.execute('UPDATE users SET last_seen=? WHERE id=?',(time.time(),uid))
                    if path in ('/api/messages','/api/messages/photo') and scope['method']=='POST':
                        kind='direct_sent' if 200<=status<300 else 'direct_unknown' if status>=500 else None
                        if kind:self.db.execute('INSERT INTO direct_stats VALUES (?,?,1) ON CONFLICT(uid,kind) DO UPDATE SET count=count+1',(uid,kind))
                    self.db.commit()
                    if is_admin and uid!='admin' and scope['method'] not in ('GET','HEAD','OPTIONS'):
                        self.record('account:'+scope['method']+' '+path,uid)
            return
        # UI/assets/schema contain no account data; administrator's app serves common files.
        return await self.apps['admin'][0](scope,receive,send)

    def no_cache(self,send):
        async def wrapped(message):
            if message['type']=='http.response.start':message['headers']=list(message.get('headers',[]))+[(b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff')]
            await send(message)
        return wrapped
