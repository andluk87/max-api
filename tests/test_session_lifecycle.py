import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pymax.exceptions import ApiError

from app.main import create_app
from app.service import Service

KEY = 'session-test-' * 4
HEADERS = {'Authorization': 'Bearer ' + KEY}


def fake_client(monkeypatch, outcomes):
    clients = []

    class Client:
        def __init__(self, **kwargs):
            self.is_connected = False
            self.close = AsyncMock()
            clients.append(self)

        def on_start(self):
            def register(fn):
                self.ready = fn
                return fn
            return register

        def on_message(self):
            return lambda fn: fn

        async def start(self):
            outcome = outcomes.pop(0)
            if outcome:
                raise outcome
            self.is_connected = True
            await self.ready(self)
            await asyncio.Future()

    monkeypatch.setattr('app.service.WebClient', Client)
    monkeypatch.setattr(Service, 'LOGIN_RETRY_DELAYS', (0, 0))
    return clients


def test_temporary_login_failure_recovers_without_losing_session(tmp_path, monkeypatch):
    clients = fake_client(monkeypatch, [ApiError(opcode=19, error='service.unavailable'), None])

    async def scenario():
        service = Service(str(tmp_path))
        session = tmp_path / 'max-session.db'
        session.write_bytes(b'saved-session')
        await service.start()
        for _ in range(30):
            if service.ready():
                break
            await asyncio.sleep(0)
        assert service.ready()
        assert len(clients) == 2
        assert session.read_bytes() == b'saved-session'
        clients[0].close.assert_awaited_once()
        await service.stop()
        clients[1].close.assert_awaited_once()
        assert service.client is None
        assert service.error is None
        service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize('code,attempts', [('service.unavailable', 3), ('auth.invalid', 1)])
def test_login_retry_is_bounded_and_only_for_transient_error(tmp_path, monkeypatch, code, attempts):
    clients = fake_client(monkeypatch, [ApiError(opcode=19, error=code) for _ in range(3)])

    async def scenario():
        service = Service(str(tmp_path))
        await service.start()
        await service.task
        assert len(clients) == attempts
        assert service.status()['state'] == 'error'
        assert service.error == 'ApiError'
        for client in clients:
            client.close.assert_awaited_once()
        await service.stop()
        service.db.close()

    asyncio.run(scenario())


def test_stop_cancels_login_retry_delay(tmp_path, monkeypatch):
    clients = fake_client(monkeypatch, [ApiError(opcode=19, error='service.unavailable')])
    monkeypatch.setattr(Service, 'LOGIN_RETRY_DELAYS', (3600,))

    async def scenario():
        service = Service(str(tmp_path))
        await service.start()
        for _ in range(20):
            if service.state == 'reconnecting':
                break
            await asyncio.sleep(0)
        assert service.state == 'reconnecting'
        await asyncio.wait_for(service.stop(persist=True), 1)
        assert service.status()['state'] == 'disconnected'
        assert len(clients) == 1
        assert (tmp_path / 'session-stopped').exists()
        service.db.close()

    asyncio.run(scenario())


def test_api_stop_prevents_restore_but_explicit_start_resumes(tmp_path, monkeypatch):
    clients = fake_client(monkeypatch, [None, None])
    (tmp_path / 'max-session.db').touch()
    for first in (True, False):
        service = Service(str(tmp_path))
        with TestClient(create_app(service, KEY, run_scheduler=False)) as c:
            if first:
                assert c.post('/api/auth/stop', headers=HEADERS).json()['state'] == 'disconnected'
            else:
                assert c.get('/api/status', headers=HEADERS).json()['state'] == 'disconnected'
                assert len(clients) == 1
                assert c.post('/api/auth/start', headers=HEADERS).status_code == 200
                assert not (tmp_path / 'session-stopped').exists()
                assert len(clients) == 2


def test_logout_api_revokes_current_session_and_preserves_other_data(tmp_path):
    service = Service(str(tmp_path))
    with TestClient(create_app(service, KEY, run_scheduler=False, restore_session=False)) as c:
        for suffix in ('', '-wal', '-shm', '-journal'):
            (tmp_path / ('max-session.db' + suffix)).touch()
        service.client = SimpleNamespace(is_connected=True, logout=AsyncMock(return_value=True))
        client = service.client
        service.state = 'connected'
        assert c.post('/api/auth/logout').status_code == 401
        response = c.post('/api/auth/logout', headers=HEADERS)
        assert response.status_code == 200
        assert response.json()['remote_revoked'] is True
        client.logout.assert_awaited_once()
        assert not list(tmp_path.glob('max-session.db*'))
        assert (tmp_path / 'events.sqlite').exists()
        assert c.post('/api/auth/logout', headers=HEADERS).json()['remote_revoked'] is False


def test_logout_failure_retains_session_without_leaking_error(tmp_path):
    service = Service(str(tmp_path))
    with TestClient(create_app(service, KEY, run_scheduler=False, restore_session=False)) as c:
        (tmp_path / 'max-session.db').write_bytes(b'saved')
        service.client = SimpleNamespace(is_connected=True, logout=AsyncMock(side_effect=RuntimeError('secret-token')))
        service.state = 'connected'
        response = c.post('/api/auth/logout', headers=HEADERS)
        assert response.status_code == 502
        assert 'secret-token' not in response.text
        assert (tmp_path / 'max-session.db').read_bytes() == b'saved'
        assert service.state == 'connected'


def test_logout_cancels_running_client_before_removing_session(tmp_path, monkeypatch):
    clients = fake_client(monkeypatch, [None])

    async def scenario():
        service = Service(str(tmp_path))
        (tmp_path / 'max-session.db').touch()
        await service.start()
        for _ in range(20):
            if service.ready():
                break
            await asyncio.sleep(0)
        clients[0].logout = AsyncMock(return_value=True)
        result = await service.logout()
        assert result == {'session_removed': True, 'remote_revoked': True}
        clients[0].logout.assert_awaited_once()
        clients[0].close.assert_awaited_once()
        assert service.task is None
        assert not (tmp_path / 'max-session.db').exists()
        assert service.state == 'disconnected'
        service.db.close()

    asyncio.run(scenario())


def test_logout_is_isolated_between_tenants(tmp_path):
    from app.gateway import Gateway
    with TestClient(Gateway(create_app, tmp_path, KEY, run_scheduler=False)) as c:
        user = c.post('/api/admin/users', headers=HEADERS, json={'name': 'User'}).json()
        tenant = tmp_path / 'users' / user['user']['id']
        (tmp_path / 'max-session.db').write_bytes(b'admin-session')
        (tenant / 'max-session.db').write_bytes(b'user-session')
        response = c.post('/api/auth/logout', headers={'Authorization': 'Bearer ' + user['api_key']})
        assert response.status_code == 200
        assert response.json()['session_removed'] is True
        assert not (tenant / 'max-session.db').exists()
        assert (tmp_path / 'max-session.db').read_bytes() == b'admin-session'
        assert c.post('/api/auth/logout', headers=HEADERS).status_code == 200
        assert not (tmp_path / 'max-session.db').exists()


def test_logout_timeout_keeps_saved_session(tmp_path):
    service = Service(str(tmp_path))
    with TestClient(create_app(service, KEY, run_scheduler=False, restore_session=False)) as c:
        (tmp_path / 'max-session.db').write_bytes(b'saved')
        service.client = SimpleNamespace(is_connected=True, logout=AsyncMock(side_effect=asyncio.TimeoutError))
        service.state = 'connected'
        assert c.post('/api/auth/logout', headers=HEADERS).status_code == 504
        assert (tmp_path / 'max-session.db').read_bytes() == b'saved'
