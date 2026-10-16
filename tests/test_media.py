import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pymax import Photo
from pymax.api.messages.service import MessageService
from pymax.api.uploads.payloads import AttachPhotoPayload

from test_api import api, HEADERS


def png():
    out = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(out, format='PNG')
    return out.getvalue()


def ready(s):
    result = SimpleNamespace(model_dump=lambda **kw: {'id':9007199254740993, 'text':'sent'})
    send = AsyncMock(return_value=result)
    s.client = SimpleNamespace(is_connected=True, send_message=send)
    s.state = 'connected'
    return send


def test_image_with_caption_reply_and_emoji(api):
    c, s = api
    send = ready(s)
    caption = '👋 **Привет ❤️** _курсив_ [ссылка](https://example.com)'
    response = c.post('/api/messages/photo', headers=HEADERS,
        data={'chat_id':'-9007199254740993','text':caption,'reply_to':'42','notify':'false'},
        files={'photo':('../../wrong.jpg',png(),'text/plain')})
    assert response.status_code == 200
    assert response.json()['id'] == '9007199254740993'
    kwargs = send.call_args.kwargs
    assert kwargs['text'] == caption
    assert kwargs['chat_id'] == -9007199254740993
    assert kwargs['reply_to'] == 42 and kwargs['notify'] is False
    assert isinstance(kwargs['attachments'][0], Photo)
    assert kwargs['attachments'][0].raw == png()
    assert kwargs['attachments'][0].name == 'image.png'


def test_image_without_caption(api):
    c, s = api
    send = ready(s)
    r = c.post('/api/messages/photo', headers=HEADERS,
               data={'chat_id':'1'}, files={'photo':('photo.png',png(),'image/png')})
    assert r.status_code == 200
    assert send.call_args.kwargs['text'] is None


def test_bad_image_and_missing_file_do_not_send(api):
    c, s = api
    send = ready(s)
    r = c.post('/api/messages/photo', headers=HEADERS,
               data={'chat_id':'1'}, files={'photo':('photo.png',b'not an image','image/png')})
    assert r.status_code == 415
    assert c.post('/api/messages/photo',headers=HEADERS,data={'chat_id':'1'}).status_code == 422
    send.assert_not_awaited()


def test_image_limits_and_caption_length(api, monkeypatch):
    c, s = api
    send = ready(s)
    monkeypatch.setattr('app.media.MAX_PHOTO_BYTES',10)
    r = c.post('/api/messages/photo',headers=HEADERS,data={'chat_id':'1'},files={'photo':('x.png',png())})
    assert r.status_code == 413
    r = c.post('/api/messages/photo',headers=HEADERS,data={'chat_id':'1','text':'a'*4001},files={'photo':('x.png',png())})
    assert r.status_code == 422
    send.assert_not_awaited()


def test_photo_requires_authorization_and_connection(api):
    c, s = api
    kwargs = dict(data={'chat_id':'1'},files={'photo':('x.png',png())})
    assert c.post('/api/messages/photo',**kwargs).status_code == 401
    assert c.post('/api/messages/photo',headers=HEADERS,**kwargs).status_code == 409


def test_actual_pymax_payload_formats_unicode_and_attaches_photo(api):
    c, s = api
    # Use the installed PyMax serializer; only the network and upload are replaced.
    upstream = SimpleNamespace(api=SimpleNamespace())
    upstream.invoke = AsyncMock(return_value=SimpleNamespace(payload={
        'id':99,'chatId':1,'text':'sent','time':0,'type':'TEXT'}))
    upload = AsyncMock(return_value=AttachPhotoPayload(photo_token='test-photo-token'))
    upstream.api.uploads = SimpleNamespace(upload_photo=upload)
    messages = MessageService(upstream)
    upstream.api.messages = messages
    s.client = SimpleNamespace(is_connected=True, send_message=messages.send_message)
    s.state = 'connected'
    text = '😀 **Привет 👩‍💻** _мир_ __да__ ~~нет~~ `код` [сайт](https://example.com)'
    r = c.post('/api/messages/photo',headers=HEADERS,data={'chat_id':'1','text':text},files={'photo':('x.png',png())})
    assert r.status_code == 200
    payload = upstream.invoke.call_args.args[1]['message']
    assert '**' not in payload['text']
    assert '😀 Привет 👩‍💻' in payload['text']
    entities = {e['type']:e for e in payload['elements']}
    assert {'STRONG','EMPHASIZED','UNDERLINE','STRIKETHROUGH','MONOSPACED','LINK'} <= entities.keys()
    assert entities['STRONG']['from'] == 3  # emoji: 2 UTF-16 units, space: 1
    assert entities['STRONG']['length'] == len('Привет 👩‍💻'.encode('utf-16-le')) // 2
    assert entities['LINK']['attributes']['url'] == 'https://example.com'
    assert payload['attaches'][0]['photoToken'] == 'test-photo-token'
    upload.assert_awaited_once()
