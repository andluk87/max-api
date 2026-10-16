import asyncio
import io
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator, ValidationError

from .service import Service
from .uploads import PhotoUploadError
from .diagnostics import emit, read as read_logs
from typing import Literal
from .encoding import wire
from .chats import chat_page
from .media import make_photo, MAX_PHOTO_BYTES
from .campaigns import Store, Engine, Draft, next_occurrence
import time
from .relays import Relay, SourceRef
from .auto_relays import AutoRelays, AutoRelay
from .message_links import resolve_link, message_link
from .source import read_source, source_photo, photo_attachments

bearer = HTTPBearer(auto_error=False)


def create_app(service=None, api_key=None, run_scheduler=True, restore_session=True):
    key = api_key if api_key is not None else os.environ.get('API_KEY', '')

    @asynccontextmanager
    async def lifespan(app):
        if len(key) < 32:
            raise RuntimeError('API_KEY must have at least 32 characters; run setup.py')
        os.umask(0o077)
        app.state.service = service or Service(os.environ.get('DATA_DIR', './data'))
        s = app.state.service
        app.state.campaigns = Store(s.directory)
        app.state.engine = Engine(s, app.state.campaigns)
        app.state.relays = AutoRelays(s)
        if run_scheduler:
            app.state.engine.start()
            app.state.relays.start()
        # Restore only an existing session; first-time QR login is explicit.
        if (restore_session and (s.directory / 'max-session.db').exists()
                and not (s.directory / 'session-stopped').exists()):
            await s.start()
        try:
            yield
        finally:
            await app.state.relays.close()
            await app.state.engine.close()
            app.state.campaigns.db.close()
            await s.stop()
            s.db.close()

    app = FastAPI(title='MAX User Account API', version='1.11.2', lifespan=lifespan, docs_url=None, redoc_url=None)

    async def auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
        if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), key.encode()):
            raise HTTPException(401, 'Invalid API key', headers={'WWW-Authenticate': 'Bearer'})

    def svc():
        return app.state.service

    def connected():
        s = svc()
        if not s.ready():
            raise HTTPException(409, 'MAX account is not connected')
        return s

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob: data: https:; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        return response

    @app.get('/healthz')
    async def health():
        return {'ok': True}

    @app.get('/', include_in_schema=False)
    async def index():
        return FileResponse(Path(__file__).parent / 'static/index.html')

    @app.get('/ui/{name}', include_in_schema=False)
    async def assets(name: str):
        if name not in {'app.js', 'style.css', 'schedule.js', 'rich.js', 'relays.js', 'admin.js', 'theme.js', 'bootstrap.min.css'}:
            raise HTTPException(404)
        return FileResponse(Path(__file__).parent / 'static' / name)

    protected = [Depends(auth)]

    @app.get('/api/logs', dependencies=protected)
    async def logs(level: Literal['INFO','WARNING','ERROR'] | None = None, before: int = Query(0,ge=0), limit: int = Query(100,ge=1,le=500)):
        return read_logs(svc().directory,level,before,limit)

    @app.get('/api/status', dependencies=protected)
    async def status():
        return svc().status()

    @app.post('/api/auth/start', dependencies=protected)
    async def start():
        await svc().start()
        return svc().status()

    @app.post('/api/auth/stop', dependencies=protected)
    async def stop():
        await svc().stop(persist=True)
        return svc().status()

    @app.post('/api/auth/logout', dependencies=protected)
    async def logout():
        try:
            result = await svc().logout()
        except asyncio.TimeoutError:
            raise HTTPException(504, 'MAX logout timed out; session retained, retry logout')
        except Exception as exc:
            emit(svc().directory, 'max_logout_failed', 'ERROR', exception=exc)
            raise HTTPException(502, 'MAX logout failed; check logs and retry')
        return {**svc().status(), **result}

    @app.get('/api/auth/qr', dependencies=protected)
    async def qr():
        if not svc().qr:
            raise HTTPException(409, 'QR is not available; check status')
        out = io.BytesIO()
        qrcode.make(svc().qr).save(out, format='PNG')
        return Response(out.getvalue(), media_type='image/png')

    @app.post('/api/auth/password', dependencies=protected)
    async def password(body: Password):
        s = svc()
        if s.state != 'password_required' or s.passwords.full():
            raise HTTPException(409, 'Password is not currently requested')
        s.passwords.put_nowait(body.password)
        return {'accepted': True}

    @app.get('/api/chats', dependencies=protected)
    async def chats(marker: int | None = Query(None, ge=1), cached: bool = False):
        client = connected().client
        if cached:
            return {'chats': [wire(c.model_dump(mode='python')) for c in client.chats or []],
                    'next_marker': None, 'complete': False, 'warning': 'Только кеш начальной синхронизации'}
        try:
            return await chat_page(client, marker)
        except asyncio.TimeoutError:
            raise HTTPException(504, 'MAX не ответил при загрузке чатов. Повторите загрузку.')
        except Exception as exc:
            emit(svc().directory,'max_read_failed','ERROR',exception=exc)
            raise HTTPException(502, 'Не удалось загрузить страницу чатов MAX. Повторите загрузку.')

    async def deliver(s, chat_id, text, reply_to, notify, attachments=None):
        async with s.send_lock:
            try:
                kwargs = dict(chat_id=chat_id, text=text, reply_to=reply_to, notify=notify)
                if attachments:
                    kwargs['attachments'] = attachments
                message = await asyncio.wait_for(s.client.send_message(**kwargs),
                                                 timeout=120 if attachments else 30)
                emit(s.directory,'direct_send_complete',state='sent')
                return wire(message.model_dump(mode='python'))
            except PhotoUploadError as exc:
                emit(s.directory, 'direct_upload_failed', 'ERROR', exception=exc, state='failed')
                raise HTTPException(502, 'Фото не загружено; сообщение не отправлено. Проверьте Логи.')
            except asyncio.TimeoutError as exc:
                emit(s.directory,'direct_send_failed','ERROR',exception=exc,state='unknown')
                raise HTTPException(504, 'Delivery unknown: do not automatically retry')
            except Exception as exc:
                emit(s.directory,'direct_send_failed','ERROR',exception=exc,state='unknown')
                raise HTTPException(502, 'MAX request failed; delivery may be unknown')

    @app.post('/api/messages', dependencies=protected)
    async def send(body: Send):
        return await deliver(connected(), body.chat_id, body.text, body.reply_to, body.notify)

    @app.post('/api/messages/photo', dependencies=protected)
    async def send_photo(
        chat_id: int = Form(...),
        photo: UploadFile = File(...),
        text: str = Form('', max_length=4000),
        reply_to: int | None = Form(None),
        notify: bool = Form(True),
    ):
        s = connected()
        try:
            raw = await photo.read(MAX_PHOTO_BYTES + 1)
        finally:
            await photo.close()
        attachment = await asyncio.to_thread(make_photo, raw)
        return await deliver(s, chat_id, text if text.strip() else None,
                             reply_to, notify, [attachment])

    def campaigns():
        return app.state.campaigns

    async def save_draft(payload, photo, identifier=None):
        try:
            draft = Draft.model_validate_json(payload)
        except ValidationError as exc:
            raise HTTPException(422, '; '.join(e['msg'] for e in exc.errors()))
        attachment = None
        if photo is not None:
            try:
                raw = await photo.read(MAX_PHOTO_BYTES + 1)
            finally:
                await photo.close()
            attachment = await asyncio.to_thread(make_photo, raw)
        return campaigns().save(draft, attachment, identifier)

    @app.get('/api/favorite-chats', dependencies=protected)
    async def list_favorites():
        return {'chats': campaigns().favorites()}

    @app.put('/api/favorite-chats/{chat_id}', dependencies=protected)
    async def favorite_chat(chat_id: int, body: Favorite):
        return campaigns().favorite(chat_id, body.title, body.type)

    @app.delete('/api/favorite-chats/{chat_id}', dependencies=protected)
    async def unfavorite_chat(chat_id: int):
        campaigns().remove_favorite(chat_id)
        return {'deleted': True}

    @app.patch('/api/saved-messages/{identifier}/schedule', dependencies=protected)
    async def toggle_schedule(identifier: str, body: ScheduleEnabled):
        try:
            return campaigns().set_schedule_enabled(identifier, body.enabled)
        except ValidationError as exc:
            raise HTTPException(422, '; '.join(e['msg'] for e in exc.errors()))

    @app.get('/api/saved-messages', dependencies=protected)
    async def list_saved():
        return {'messages': campaigns().list_drafts()}

    @app.post('/api/saved-messages', dependencies=protected, status_code=201)
    async def create_saved(payload: str = Form(...), photo: UploadFile | None = File(None)):
        return await save_draft(payload, photo)

    @app.get('/api/saved-messages/{identifier}', dependencies=protected)
    async def read_saved(identifier: str):
        return campaigns().view(campaigns().row(identifier))

    @app.put('/api/saved-messages/{identifier}', dependencies=protected)
    async def edit_saved(identifier: str, payload: str = Form(...), photo: UploadFile | None = File(None)):
        return await save_draft(payload, photo, identifier)

    @app.delete('/api/saved-messages/{identifier}', dependencies=protected)
    async def delete_saved(identifier: str):
        campaigns().delete(identifier)
        return {'deleted': True}

    @app.get('/api/saved-messages/{identifier}/photo', dependencies=protected)
    async def saved_photo(identifier: str):
        row = campaigns().row(identifier)
        if row['photo'] is None:
            raise HTTPException(404, 'Картинки нет')
        extension = row['photo_name'].rsplit('.', 1)[-1]
        return Response(row['photo'], media_type={'jpg':'image/jpeg','png':'image/png','webp':'image/webp'}[extension])

    @app.post('/api/saved-messages/{identifier}/send', dependencies=protected, status_code=202)
    async def enqueue_saved(identifier: str):
        run_id = campaigns().enqueue(identifier)
        return campaigns().run_view(run_id)

    @app.get('/api/send-runs', dependencies=protected)
    async def list_send_runs(limit: int = Query(30, ge=1, le=100)):
        return {'runs': campaigns().list_runs(limit), 'scheduler_error': app.state.engine.error,
                'connected': svc().ready()}

    @app.post('/api/send-runs/{identifier}/cancel', dependencies=protected)
    async def cancel_run(identifier: str):
        return campaigns().cancel(identifier)

    @app.post('/api/schedule/preview', dependencies=protected)
    async def preview_schedule(body: Schedule):
        try:
            Draft(title='preview', cron=body.cron, timezone=body.timezone, enabled=True, chat_ids=[0])
        except ValidationError as exc:
            raise HTTPException(422, '; '.join(e['msg'] for e in exc.errors()))
        current = time.time()
        upcoming = []
        for _ in range(5):
            current = next_occurrence(body.cron, body.timezone, current)
            upcoming.append(current)
        return {'timezone':body.timezone, 'upcoming':upcoming}

    @app.get('/api/updates', dependencies=protected)
    async def updates(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return svc().updates(after, limit)

    @app.get('/api/source/{chat_id}/{message_id}/photo/{index}', dependencies=protected)
    async def import_photo(chat_id: int, message_id: int, index: int):
        message = await read_source(connected().client, chat_id, message_id)
        raw = await source_photo(message, index)
        photo = make_photo(raw)
        mime = {'jpg':'image/jpeg','png':'image/png','webp':'image/webp'}[photo.name.rsplit('.',1)[-1]]
        return Response(raw, media_type=mime)

    @app.post('/api/source/resolve', dependencies=protected)
    async def resolve_source(body: SourceRef):
        client = connected().client
        if body.url:
            return await resolve_link(client, body.chat_id, body.url)
        return await message_link(client, body.chat_id, body.message_id)

    async def normalize_relay(body):
        for ref in body.sources:
            if ref.message_id is None or not ref.url:
                result = await resolve_source(ref)
                ref.message_id = int(result['message_id'])
                ref.url = result['url']
        try:
            return Relay.model_validate(body.model_dump())
        except ValidationError:
            raise HTTPException(422, 'Одно сообщение выбрано несколько раз')

    @app.get('/api/relays', dependencies=protected)
    async def list_relays(include_jobs: bool = False, limit: int = Query(100, ge=1, le=500)):
        return app.state.relays.list(include_jobs=include_jobs,limit=limit)

    @app.post('/api/relays', dependencies=protected)
    async def create_relay(body: AutoRelay):
        return app.state.relays.save(body)

    @app.put('/api/relays/{identifier}', dependencies=protected)
    async def update_relay(identifier: str, body: AutoRelay):
        return app.state.relays.save(body, identifier)

    @app.delete('/api/relays/{identifier}', dependencies=protected)
    async def delete_relay(identifier: str):
        app.state.relays.delete(identifier)
        return {'deleted': True}

    @app.patch('/api/relays/{identifier}/enabled', dependencies=protected)
    async def toggle_relay(identifier: str, body: ScheduleEnabled):
        return app.state.relays.toggle(identifier, body.enabled)

    @app.get('/api/history/{chat_id}', dependencies=protected)
    async def history(chat_id: int, before: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        from .history import history_page
        client = connected().client
        try:
            return await history_page(client, chat_id, before, limit)
        except asyncio.TimeoutError:
            raise HTTPException(504, 'MAX не ответил при загрузке истории. Повторите запрос.')
        except Exception as exc:
            emit(svc().directory,'max_read_failed','ERROR',exception=exc)
            raise HTTPException(502, 'Не удалось загрузить историю этого чата из MAX.')

    @app.get('/api/inbox/chats', dependencies=protected)
    async def inbox_chats():
        return svc().inbox_chats()

    @app.get('/api/inbox/{chat_id}', dependencies=protected)
    async def inbox_messages(chat_id: int, before: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return svc().inbox_messages(chat_id, before, limit)

    return app


class Send(BaseModel):
    chat_id: int
    text: str = Field(min_length=1, max_length=4000)
    reply_to: int | None = None
    notify: bool = True

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('text must not be blank')
        return value


class Favorite(BaseModel):
    title: str = Field('', max_length=500)
    type: str = Field('CHAT', max_length=40)


class ScheduleEnabled(BaseModel):
    enabled: bool


class Schedule(BaseModel):
    cron: str = Field(min_length=1, max_length=100)
    timezone: str = Field('Asia/Anadyr', max_length=100)


class Password(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


from .gateway import Gateway
app = Gateway(create_app)
