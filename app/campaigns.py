"""Persistent drafts, immutable send runs, and a single-process cron worker."""
import asyncio
import contextlib
import json
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from pymax import Photo
from .diagnostics import emit, request_context


def next_occurrence(expression, timezone, after):
    base = datetime.fromtimestamp(after, ZoneInfo(timezone))
    return croniter(expression, base, max_years_between_matches=10).get_next(datetime).timestamp()


class Draft(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    text: str = Field('', max_length=4000)
    chat_ids: list[int] = Field(default_factory=list, max_length=1000)
    interval_seconds: int = Field(5, ge=5, le=30)
    cron: str = Field('', max_length=100)
    timezone: str = Field('Asia/Anadyr', max_length=100)
    enabled: bool = False
    notify: bool = True
    photo_action: Literal['keep', 'replace', 'remove'] = 'keep'

    @field_validator('title')
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError('Введите название сообщения')
        return value.strip()

    @field_validator('chat_ids')
    @classmethod
    def unique_chats(cls, value):
        return list(dict.fromkeys(value))

    @model_validator(mode='after')
    def schedule_valid(self):
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError):
            raise ValueError('Неизвестный часовой пояс')
        self.cron = self.cron.strip()
        if self.cron:
            if len(self.cron.split()) != 5:
                raise ValueError('Cron должен содержать 5 полей: минута час день месяц день-недели')
            try:
                next_occurrence(self.cron, self.timezone, time.time())
            except (ValueError, KeyError, OverflowError):
                raise ValueError('Некорректное или невыполнимое расписание cron')
        if self.enabled and (not self.cron or not self.chat_ids):
            raise ValueError('Для включения cron задайте расписание и выберите чаты')
        return self


class Store:
    def __init__(self, directory, clock=time.time):
        self.directory=Path(directory)
        self.clock = clock
        self.db = sqlite3.connect(Path(directory) / 'campaigns.sqlite', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY, config TEXT NOT NULL, photo BLOB, photo_name TEXT,
            next_run REAL, schedule_note TEXT, created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, draft_id TEXT REFERENCES drafts(id) ON DELETE SET NULL,
            config TEXT NOT NULL, photo BLOB, photo_name TEXT, source TEXT NOT NULL,
            state TEXT NOT NULL, created REAL NOT NULL, next_send REAL NOT NULL,
            cancel_requested INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            run_id TEXT REFERENCES runs(id), position INTEGER, chat_id TEXT NOT NULL,
            state TEXT NOT NULL, message_id TEXT, error TEXT, updated REAL,
            PRIMARY KEY(run_id, position)
        );
        CREATE TABLE IF NOT EXISTS favorite_chats (
            chat_id TEXT PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL, updated REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS deliveries_pending ON deliveries(run_id,state,position);
        CREATE INDEX IF NOT EXISTS runs_state ON runs(state,created);
        ''')
        # Never retry a send whose outcome was lost at shutdown/crash.
        now = self.clock()
        with self.db:
            self.db.execute("UPDATE deliveries SET state='unknown',error='Перезапуск во время отправки; результат неизвестен',updated=? WHERE state='sending'", (now,))
            for row in self.db.execute("SELECT id,config FROM runs WHERE state='running'").fetchall():
                interval = json.loads(row['config'])['interval_seconds']
                self.db.execute('UPDATE runs SET next_send=MAX(next_send,?) WHERE id=?', (now+interval, row['id']))

    def row(self, draft_id):
        row = self.db.execute('SELECT * FROM drafts WHERE id=?', (draft_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Сообщение не найдено')
        return row

    def view(self, row):
        data = json.loads(row['config'])
        data['chat_ids'] = [str(x) for x in data['chat_ids']]
        return dict(data, id=row['id'], has_photo=row['photo'] is not None,
                    next_run=row['next_run'], schedule_note=row['schedule_note'],
                    created=row['created'], updated=row['updated'])

    def list_drafts(self):
        return [self.view(row) for row in self.db.execute('SELECT * FROM drafts ORDER BY updated DESC')]

    def save(self, draft, photo=None, draft_id=None):
        old = self.row(draft_id) if draft_id else None
        raw, name = (old['photo'], old['photo_name']) if old else (None, None)
        if draft.photo_action == 'remove':
            raw, name = None, None
        elif draft.photo_action == 'replace':
            if photo is None:
                raise HTTPException(422, 'Выберите картинку для замены')
            raw, name = photo.raw, photo.name
        elif photo is not None:
            raise HTTPException(422, 'Для новой картинки используйте photo_action=replace')
        if not draft.text.strip() and raw is None:
            raise HTTPException(422, 'Введите текст или добавьте картинку')
        config = draft.model_dump(exclude={'photo_action'})
        now = self.clock()
        schedule = next_occurrence(draft.cron, draft.timezone, now) if draft.enabled else None
        # Editing text/recipients must not postpone an already scheduled occurrence.
        if old:
            previous = json.loads(old['config'])
            if all(config[k] == previous[k] for k in ('enabled','cron','timezone')):
                schedule = old['next_run']
        identifier = draft_id or uuid.uuid4().hex
        with self.db:
            self.db.execute('''INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET config=excluded.config,photo=excluded.photo,
                photo_name=excluded.photo_name,next_run=excluded.next_run,
                schedule_note=NULL,updated=excluded.updated''',
                (identifier,json.dumps(config,ensure_ascii=False),raw,name,schedule,None,
                 old['created'] if old else now,now))
        return self.view(self.row(identifier))

    def favorites(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM favorite_chats ORDER BY title COLLATE NOCASE,chat_id')]

    def favorite(self, chat_id, title, chat_type):
        identifier = str(chat_id)
        with self.db:
            self.db.execute('INSERT INTO favorite_chats VALUES (?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,type=excluded.type,updated=excluded.updated',
                            (identifier,title.strip() or 'Чат '+identifier,chat_type,self.clock()))
        return dict(self.db.execute('SELECT * FROM favorite_chats WHERE chat_id=?',(identifier,)).fetchone())

    def remove_favorite(self, chat_id):
        with self.db:
            self.db.execute('DELETE FROM favorite_chats WHERE chat_id=?',(str(chat_id),))

    def set_schedule_enabled(self, draft_id, enabled):
        config = json.loads(self.row(draft_id)['config'])
        config['enabled'] = enabled
        return self.save(Draft(**config), draft_id=draft_id)

    def delete(self, draft_id):
        self.row(draft_id)
        with self.db:
            for row in self.db.execute("SELECT id FROM runs WHERE draft_id=? AND state IN ('queued','running')", (draft_id,)).fetchall():
                self._cancel(row['id'])
            self.db.execute('DELETE FROM drafts WHERE id=?', (draft_id,))

    def enqueue(self, draft_id, source='manual'):
        with self.db:
            return self._enqueue(draft_id, source)

    def _enqueue(self, draft_id, source):
        row = self.row(draft_id)
        config = json.loads(row['config'])
        if not config['chat_ids']:
            raise HTTPException(422, 'Выберите хотя бы один чат')
        if self.db.execute("SELECT 1 FROM runs WHERE draft_id=? AND state IN ('queued','running')", (draft_id,)).fetchone():
            raise HTTPException(409, 'Для этого сообщения уже есть активная отправка')
        identifier = uuid.uuid4().hex
        now = self.clock()
        self.db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,0)',
            (identifier,draft_id,row['config'],row['photo'],row['photo_name'],source,'queued',now,now))
        self.db.executemany('INSERT INTO deliveries(run_id,position,chat_id,state) VALUES (?,?,?,?)',
            [(identifier,i,str(chat),'pending') for i,chat in enumerate(config['chat_ids'])])
        return identifier

    def schedule_due(self, connected):
        now = self.clock()
        with self.db:
            for row in self.db.execute('SELECT * FROM drafts WHERE next_run<=?', (now,)).fetchall():
                config = json.loads(row['config'])
                note = None
                # One-minute grace; no burst replay of missed cron occurrences.
                if now-row['next_run'] > 60:
                    note = 'Пропущен запуск во время простоя'
                elif not connected:
                    note = 'Пропущен запуск: MAX не подключён'
                else:
                    try:
                        self._enqueue(row['id'], 'cron')
                        note = 'Отправка по расписанию поставлена в очередь'
                    except HTTPException as exc:
                        note = exc.detail
                next_run = next_occurrence(config['cron'], config['timezone'], now)
                self.db.execute('UPDATE drafts SET next_run=?,schedule_note=? WHERE id=?', (next_run,note,row['id']))

    def _cancel(self, run_id):
        self.db.execute("UPDATE deliveries SET state='cancelled' WHERE run_id=? AND state='pending'", (run_id,))
        self.db.execute('UPDATE runs SET cancel_requested=1 WHERE id=?', (run_id,))
        # In-flight requests may still finish; don't claim that they were revoked.
        if not self.db.execute("SELECT 1 FROM deliveries WHERE run_id=? AND state='sending'", (run_id,)).fetchone():
            self.finish(run_id)

    def cancel(self, run_id):
        current = self.run_view(run_id)
        if current['state'] not in ('queued','running'):
            return current
        with self.db:
            self._cancel(run_id)
        return self.run_view(run_id)

    def finish(self, run_id):
        states = [r[0] for r in self.db.execute('SELECT state FROM deliveries WHERE run_id=?', (run_id,))]
        if any(s in ('pending','sending') for s in states):
            return
        cancelled = self.db.execute('SELECT cancel_requested FROM runs WHERE id=?', (run_id,)).fetchone()[0]
        state = 'cancelled' if cancelled else ('completed' if all(s == 'sent' for s in states) else 'partial')
        self.db.execute('UPDATE runs SET state=?,photo=NULL WHERE id=?', (state,run_id))

    def run_view(self, run_id):
        row = self.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Отправка не найдена')
        config = json.loads(row['config'])
        deliveries = [dict(item) for item in self.db.execute('SELECT position,chat_id,state,message_id,error,updated FROM deliveries WHERE run_id=? ORDER BY position', (run_id,))]
        return {'id':row['id'],'draft_id':row['draft_id'],'title':config['title'],
                'source':row['source'],'state':row['state'],'created':row['created'],
                'next_send':row['next_send'],'interval_seconds':config['interval_seconds'],
                'cancel_requested':bool(row['cancel_requested']),'deliveries':deliveries}

    def list_runs(self, limit=30):
        return [self.run_view(row[0]) for row in self.db.execute('SELECT id FROM runs ORDER BY created DESC,rowid DESC LIMIT ?', (limit,))]


class Engine:
    def __init__(self, service, store):
        self.service, self.store = service, store
        self.task = None
        self.error = None

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def loop(self):
        request_context.set(None)
        while True:
            try:
                self.store.schedule_due(self.service.ready())
                await self.step()
                self.error = None
            except Exception as exc:
                self.error = type(exc).__name__
                emit(self.store.directory,'scheduler_failed','ERROR',exception=exc)
            await asyncio.sleep(0.5)

    async def step(self):
        db, s = self.store.db, self.service
        # FIFO across runs, and a persistent cooldown within each run.
        run = db.execute("SELECT * FROM runs WHERE state IN ('queued','running') ORDER BY created,rowid LIMIT 1").fetchone()
        if run is None:
            return
        item = db.execute("SELECT * FROM deliveries WHERE run_id=? AND state='pending' ORDER BY position LIMIT 1", (run['id'],)).fetchone()
        if item is None:
            with db:
                self.store.finish(run['id'])
            return
        if run['next_send'] > self.store.clock() or not s.ready():
            return
        config = json.loads(run['config'])
        async with s.send_lock:
            latest = db.execute('SELECT cancel_requested FROM runs WHERE id=?', (run['id'],)).fetchone()
            if latest['cancel_requested'] or not s.ready():
                return
            with db:
                db.execute("UPDATE runs SET state='running' WHERE id=?", (run['id'],))
                db.execute("UPDATE deliveries SET state='sending',updated=? WHERE run_id=? AND position=?", (self.store.clock(),run['id'],item['position']))
            state, message_id, error = 'sent', None, None
            try:
                kwargs = dict(chat_id=int(item['chat_id']), text=config['text'] or None, notify=config['notify'])
                if run['photo'] is not None:
                    kwargs['attachments'] = [Photo(raw=run['photo'],name=run['photo_name'])]
                result = await asyncio.wait_for(s.client.send_message(**kwargs), timeout=120 if run['photo'] is not None else 30)
                message_id = str(result.id)
            except asyncio.CancelledError:
                state, error = 'unknown', 'Остановка сервера во время отправки; результат неизвестен'
                raise
            except Exception as exc:
                emit(self.store.directory,'campaign_send_failed','ERROR',exception=exc,run_id=run['id'])
                state, error = 'unknown', 'MAX не подтвердил результат; автоматического повтора нет'
            finally:
                emit(self.store.directory,'campaign_result',run_id=run['id'],state=state)
                now = self.store.clock()
                with db:
                    db.execute('UPDATE deliveries SET state=?,message_id=?,error=?,updated=? WHERE run_id=? AND position=?',
                               (state,message_id,error,now,run['id'],item['position']))
                    db.execute('UPDATE runs SET next_send=? WHERE id=?', (now+config['interval_seconds'],run['id']))
                    self.store.finish(run['id'])
