import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymax.types import Chat, Message

from test_api import api, HEADERS

BINARY = b'\x9d\xff\x00\x81'


def message():
    return Message(id=9007199254740993, chat_id=123, sender=456,
                   text='Привет', time=0, type='TEXT', stats={'binary': BINARY})


def test_chats_with_binary_nested_message(api):
    c, s = api
    chat = Chat(id=123, type='DIALOG', status='ACTIVE', owner=456,
                title='Тест', last_message=message())
    # This is the original failure from the user's server log.
    with pytest.raises(UnicodeDecodeError):
        chat.model_dump(mode='json')
    s.client = SimpleNamespace(is_connected=True, me=None,
        _app=SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(
            payload={'chats':[chat.model_dump(mode='python')], 'marker':0}))))
    s.state = 'connected'
    result = c.get('/api/chats', headers=HEADERS)
    assert result.status_code == 200
    payload = result.json()['chats'][0]['last_message']
    assert payload['text'] == 'Привет'
    assert payload['id'] == '9007199254740993'
    assert base64.b64decode(payload['stats']['binary']['data']) == BINARY


def test_sent_message_with_binary_fields(api):
    c, s = api
    s.client = SimpleNamespace(is_connected=True, send_message=AsyncMock(return_value=message()))
    s.state = 'connected'
    result = c.post('/api/messages', headers=HEADERS, json={'chat_id':123,'text':'Привет'})
    assert result.status_code == 200
    assert result.json()['stats']['binary']['encoding'] == 'base64'


def test_incoming_binary_message_persists_and_can_be_polled(api):
    c, s = api
    s.store_message(message())
    s.store_message(message())
    result = c.get('/api/updates', headers=HEADERS).json()
    assert len(result['updates']) == 1
    payload = result['updates'][0]['message']
    assert payload['id'] == '9007199254740993'
    assert base64.b64decode(payload['stats']['binary']['data']) == BINARY
    cursor = result['next_offset']
    assert c.get('/api/updates', headers=HEADERS, params={'after':cursor}).json()['updates'] == []
