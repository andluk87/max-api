import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymax.api.messages.service import MessageService
from pymax.protocol import Opcode

from app.media import make_photo
from app.uploads import CompatibleWebClient, PhotoUploadError, select_photo_token, upload_photo
from app.diagnostics import emit, read
from test_media import png
from test_api import api


@pytest.mark.parametrize('url,photos,expected', [
    ('https://upload.example/photo?signature=secret', {'new-id': {'token': 'test-token'}}, 'test-token'),
    ('https://upload.example/photo?photoIds=42', {'42': {'token': 'expected'}, '43': {'token': 'other'}}, 'expected'),
])
def test_select_token_with_and_without_query_id(url, photos, expected):
    assert select_photo_token({'photos': photos}, url) == expected


@pytest.mark.parametrize('url,photos', [
    ('https://upload.example/photo', {}),
    ('https://upload.example/photo', {'42': {'token': 'a'}, '43': {'token': 'b'}}),
    ('https://upload.example/photo?photoIds=42', {'43': {'token': 'b'}}),
    ('https://upload.example/photo', {'42': {'token': ''}}),
])
def test_missing_or_ambiguous_tokens_are_rejected(url, photos):
    with pytest.raises((KeyError, ValueError)):
        select_photo_token({'photos': photos}, url)


def test_real_pymax_send_uploads_without_photoids_and_sends_token(monkeypatch):
    calls = []
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {'photos': {'new-id': {'token': 'uploaded-token'}}}
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()
    monkeypatch.setattr('app.uploads.aiohttp.ClientSession', Session)
    async def scenario():
        app = SimpleNamespace(config=SimpleNamespace(proxy=None), api=SimpleNamespace())
        async def invoke(opcode, payload=None):
            if opcode == Opcode.PHOTO_UPLOAD:
                return SimpleNamespace(payload={'url': 'https://upload.example/photo?signature=secret'})
            assert opcode == Opcode.MSG_SEND
            assert payload['message']['attaches'][0]['photoToken'] == 'uploaded-token'
            return SimpleNamespace(payload={'id': 1, 'chatId': 2, 'text': 'sent', 'time': 0, 'type': 'TEXT'})
        app.invoke = AsyncMock(side_effect=invoke)
        app.api.uploads = SimpleNamespace(upload_photo=lambda photo: upload_photo(SimpleNamespace(app=app), photo))
        app.api.messages = MessageService(app)
        message = await app.api.messages.send_message(chat_id=2, text='caption', attachments=[make_photo(png())])
        assert message.id == 1
        assert app.invoke.await_count == 2
        assert len(calls) == 1
        assert calls[0][1]['allow_redirects'] is False
    asyncio.run(scenario())


def test_bad_upload_does_not_send_and_diagnostics_hide_url_and_token(tmp_path, monkeypatch):
    async def scenario():
        app = SimpleNamespace(config=SimpleNamespace(proxy=None))
        app.invoke = AsyncMock(return_value=SimpleNamespace(payload={'url': 'http://secret-url/?token=secret-token'}))
        app.api = SimpleNamespace(uploads=SimpleNamespace(upload_photo=lambda photo: upload_photo(SimpleNamespace(app=app), photo)))
        with pytest.raises(PhotoUploadError) as caught:
            await MessageService(app).send_message(chat_id=2, attachments=[make_photo(png())])
        assert app.invoke.await_count == 1  # PHOTO_UPLOAD only; no MSG_SEND
        emit(tmp_path, 'test_upload_failed', 'ERROR', exception=caught.value)
        record = read(tmp_path)['logs'][0]
        assert record['upload_stage'] == 'parse_url'
        assert 'secret-url' not in str(record) and 'secret-token' not in str(record)
    asyncio.run(scenario())


def test_adapter_is_installed_for_every_client_runtime(monkeypatch):
    app = SimpleNamespace(api=SimpleNamespace(uploads=SimpleNamespace()))
    monkeypatch.setattr('app.uploads.WebClient._build_app', lambda self: app)
    client = CompatibleWebClient()
    for _ in range(2):
        assert client._build_app().api.uploads.upload_photo.__func__ is upload_photo


def test_direct_photo_upload_failure_has_safe_api_error(api):
    from test_api import HEADERS
    c, service = api
    service.state = 'connected'
    service.client = SimpleNamespace(is_connected=True, send_message=AsyncMock(side_effect=PhotoUploadError('http_upload')))
    response = c.post('/api/messages/photo', headers=HEADERS, data={'chat_id': '1'}, files={'photo': ('photo.png', png())})
    assert response.status_code == 502
    assert 'сообщение не отправлено' in response.json()['detail']
