import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import Service

KEY = 'test-key-' * 8
HEADERS = {'Authorization': 'Bearer ' + KEY}


@pytest.fixture
def api(tmp_path):
    service = Service(str(tmp_path))
    app = create_app(service=service, api_key=KEY)
    with TestClient(app) as client:
        yield client, service


def test_protected_endpoints_and_public_gui(api):
    c, s = api
    for path in ['/api/status', '/api/auth/qr', '/api/chats', '/api/updates']:
        assert c.get(path).status_code == 401
    assert c.post('/api/auth/start').status_code == 401
    assert c.get('/').status_code == 200
    assert c.get('/healthz').json() == {'ok': True}
    assert c.get('/api/status', headers=HEADERS).json()['state'] == 'disconnected'


def test_qr_and_password_not_exposed_in_status(api):
    c, s = api
    s.qr = 'https://example.org/secret-qr'
    assert 'secret-qr' not in c.get('/api/status', headers=HEADERS).text
    r = c.get('/api/auth/qr', headers=HEADERS)
    assert r.content.startswith(b'\x89PNG')
    assert r.headers['cache-control'] == 'no-store'
    assert c.post('/api/auth/password', headers=HEADERS, json={'password':'secret'}).status_code == 409
    s.state = 'password_required'
    assert c.post('/api/auth/password', headers=HEADERS, json={'password':'secret'}).status_code == 200
    assert c.post('/api/auth/password', headers=HEADERS, json={'password':'secret'}).status_code == 409


def test_send_validation_and_delivery_errors(api):
    c, s = api
    body = {'chat_id':'-9007199254740993', 'text':'Hello'}
    assert c.post('/api/messages', headers=HEADERS, json=body).status_code == 409
    assert c.post('/api/messages', headers=HEADERS, json={**body, 'text':' '}).status_code == 422
    message = SimpleNamespace(model_dump=lambda **kw: {'id':9007199254740993,'text':'Hello'})
    send = AsyncMock(return_value=message)
    s.client = SimpleNamespace(is_connected=True, send_message=send)
    s.state = 'connected'
    r = c.post('/api/messages', headers=HEADERS, json=body)
    assert r.status_code == 200
    assert r.json()['id'] == '9007199254740993'
    assert send.call_args.kwargs['chat_id'] == -9007199254740993
    send.side_effect = asyncio.TimeoutError
    assert c.post('/api/messages', headers=HEADERS, json=body).status_code == 504
    send.side_effect = RuntimeError('secret-token')
    r = c.post('/api/messages', headers=HEADERS, json=body)
    assert r.status_code == 502
    assert 'secret-token' not in r.text


def test_updates_cursor_and_deduplication(api):
    c, s = api
    for _ in range(2):
        s.db.execute('INSERT OR IGNORE INTO events(chat,mid,payload) VALUES (?,?,?)', ('1','2','{"id":2,"chat_id":1,"text":"ping"}'))
    s.db.commit()
    result = c.get('/api/updates', headers=HEADERS).json()
    assert len(result['updates']) == 1
    assert result['updates'][0]['message']['chat_id'] == '1'
    cursor = result['next_offset']
    assert c.get('/api/updates', headers=HEADERS, params={'after':cursor}).json()['updates'] == []
    assert c.get('/api/updates?after=-1', headers=HEADERS).status_code == 422


def test_client_wiring_and_qr_expiry(tmp_path, monkeypatch):
    class FakeWebClient:
        def __init__(self, **kwargs):
            self.flow = kwargs['auth_flow']
        def on_start(self):
            return lambda fn: fn
        def on_message(self):
            return lambda fn: fn
        async def start(self):
            await self.flow.qr_provider.show_qr('secret')
            raise RuntimeError('QR authentication expired')
        async def close(self):
            pass
    monkeypatch.setattr('app.service.WebClient', FakeWebClient)
    async def scenario():
        service = Service(str(tmp_path))
        await service.start()
        await service.task
        assert service.status()['state'] == 'error'
        assert service.qr is None
        await service.start()
        await service.task
        await service.stop()
        service.db.close()
    asyncio.run(scenario())
