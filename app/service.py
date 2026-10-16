import asyncio
import contextlib
import json
import sqlite3
from pathlib import Path

from pymax import ExtraConfig, QrAuthFlow
from .uploads import CompatibleWebClient as WebClient
from pymax.exceptions import ApiError
from .encoding import wire
from .diagnostics import emit, request_context


class Service:
    LOGIN_RETRY_DELAYS = (5, 10, 20, 40)

    def __init__(self, directory: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'events.sqlite', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT, mid TEXT, payload TEXT, UNIQUE(chat, mid))')
        self.db.execute('CREATE INDEX IF NOT EXISTS events_chat_seq ON events(chat,seq)')
        self.db.commit()
        self.client = None
        self.task = None
        self.state = 'disconnected'
        self.error = None
        self.qr = None
        self.hint = None
        self.passwords = asyncio.Queue(maxsize=1)
        self.lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()

    async def show_qr(self, qr_url: str):
        self.qr = qr_url
        self.state = 'qr_pending'
        emit(self.directory,'auth_qr_ready')

    async def get_password(self, hint=None):
        self.qr = None
        self.hint = hint
        self.state = 'password_required'
        emit(self.directory,'auth_password_required')
        password = await asyncio.wait_for(self.passwords.get(), timeout=180)
        self.state = 'authenticating'
        return password

    async def start(self):
        async with self.lock:
            if self.task and not self.task.done():
                return
            (self.directory / 'session-stopped').unlink(missing_ok=True)
            self.error = self.qr = self.hint = None
            self.passwords = asyncio.Queue(maxsize=1)
            self.state = 'connecting'
            emit(self.directory,'max_connecting')
            self.client = self.new_client()
            self.task = asyncio.create_task(self.run())

    def new_client(self):
        client = WebClient(
            work_dir=str(self.directory), session_name='max-session.db',
            auth_flow=QrAuthFlow(self, self),
            extra_config=ExtraConfig(log_level='CRITICAL', reconnect=True, reconnect_delay=5),
        )

        @client.on_start()
        async def ready(client):
            self.state = 'connected'
            emit(self.directory, 'max_connected')
            self.qr = self.hint = self.error = None

        @client.on_message()
        async def incoming(message, client):
            if message.chat_id is not None:
                self.store_message(message)

        return client

    async def close_client(self, client):
        try:
            await client.close()
        except Exception as exc:
            emit(self.directory, 'max_close_failed', 'ERROR', exception=exc)
            raise

    async def run(self):
        request_context.set(None)
        try:
            for attempt in range(len(self.LOGIN_RETRY_DELAYS) + 1):
                client = self.client
                retry = False
                try:
                    await client.start()
                    self.state = 'disconnected'
                    return
                except ApiError as exc:
                    # pymax reconnects transport failures, but treats API errors as fatal.
                    # Retry only the observed temporary login failure, never auth errors
                    # or message sends (which could cause duplicate deliveries).
                    if (exc.opcode != 19 or exc.error != 'service.unavailable'
                            or attempt == len(self.LOGIN_RETRY_DELAYS)):
                        raise
                    retry = True
                    self.state = 'reconnecting'
                    self.error = self.qr = self.hint = None
                    emit(self.directory, 'max_login_retry', 'WARNING', exception=exc)
                finally:
                    await self.close_client(client)
                if retry:
                    await asyncio.sleep(self.LOGIN_RETRY_DELAYS[attempt])
                    self.client = self.new_client()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = 'error'
            self.error = type(exc).__name__
            emit(self.directory, 'max_connection_failed', 'ERROR', exception=exc)
        finally:
            self.qr = self.hint = None

    def status(self):
        state = self.state
        if state == 'connected' and (self.client is None or not self.client.is_connected):
            state = 'reconnecting'
        return {'state': state, 'error': self.error, 'password_hint': self.hint,
                'qr_available': bool(self.qr)}

    def ready(self):
        return self.status()['state'] == 'connected'

    async def stop(self, *, persist=False):
        async with self.lock:
            if persist:
                (self.directory / 'session-stopped').touch(mode=0o600)
            await self._stop()

    async def _stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.task = None
        self.client = None
        self.error = self.qr = self.hint = None
        self.passwords = asyncio.Queue(maxsize=1)
        self.state = 'disconnected'
        emit(self.directory, 'max_stopped')

    async def logout(self):
        async with self.lock:
            async with self.send_lock:
                revoked = False
                if self.ready():
                    # A failed revocation leaves the saved session intact for retry.
                    await asyncio.wait_for(self.client.logout(), timeout=15)
                    revoked = True
                (self.directory / 'session-stopped').touch(mode=0o600)
                await self._stop()
                # Close the SQLite owner before removing its database and journals.
                for suffix in ('', '-wal', '-shm', '-journal'):
                    (self.directory / ('max-session.db' + suffix)).unlink(missing_ok=True)
                emit(self.directory, 'max_logged_out')
                return {'session_removed': True, 'remote_revoked': revoked}

    def store_message(self, message):
        payload = json.dumps(wire(message.model_dump(mode='python')), ensure_ascii=False)
        self.db.execute('INSERT OR IGNORE INTO events(chat,mid,payload) VALUES (?,?,?)',
                        (str(message.chat_id), str(message.id), payload))
        self.db.commit()

    def updates(self, after, limit):
        rows = self.db.execute('SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?', (after, limit)).fetchall()
        return {'updates': [{'update_id': row[0], 'message': wire(json.loads(row[1]))} for row in rows],
                'next_offset': rows[-1][0] if rows else after}

    def inbox_chats(self):
        rows = self.db.execute('SELECT chat, COUNT(*), MAX(seq) FROM events GROUP BY chat ORDER BY MAX(seq) DESC').fetchall()
        return {'chats': [{'chat_id': r[0], 'count': r[1]} for r in rows]}

    def inbox_messages(self, chat_id, before, limit):
        rows = self.db.execute('SELECT seq,payload FROM events WHERE chat=? AND (?=0 OR seq<?) ORDER BY seq DESC LIMIT ?',
                               (str(chat_id), before, before, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {'messages': [{'update_id': r[0], 'message': wire(json.loads(r[1]))} for r in rows],
                'next_before': rows[-1][0] if more else None}
