from types import SimpleNamespace
from unittest.mock import AsyncMock

from pymax.types import User
from pymax.protocol import Opcode

from test_api import api, HEADERS


def chat(id, type='DIALOG', **fields):
    return {'id':id,'type':type,'status':'ACTIVE','owner':1, **fields}


def connect(service, pages):
    invoke = AsyncMock(side_effect=[SimpleNamespace(payload=page) for page in pages])
    service.client = SimpleNamespace(is_connected=True,
        me=SimpleNamespace(contact=SimpleNamespace(id=1)),
        _app=SimpleNamespace(invoke=invoke),
        get_users=AsyncMock(return_value=[User(id=2, names=[{'firstName':'Иван','lastName':'Петров'}])]))
    service.state = 'connected'
    return invoke


def test_all_pages_include_dialogs_groups_channels_and_names(api):
    c, s = api
    invoke = connect(s, [
        {'chats':[chat(10,'CHAT',title='Группа')], 'marker':100},
        {'chats':[chat(20, participants={'1':0,'2':0}), chat(30,'CHANNEL',title='Канал')], 'marker':0},
    ])
    first = c.get('/api/chats',headers=HEADERS).json()
    assert first['complete'] is False and first['next_marker'] == '100'
    second = c.get('/api/chats?marker=100',headers=HEADERS).json()
    assert second['complete'] is True and second['next_marker'] is None
    assert [x['type'] for x in first['chats'] + second['chats']] == ['CHAT','DIALOG','CHANNEL']
    assert second['chats'][0]['display_name'] == 'Иван Петров'
    assert second['chats'][0]['peer_id'] == '2'
    assert invoke.call_args.args == (Opcode.CHATS_LIST, {'marker':100,'count':100})
    s.client.get_users.assert_awaited_once_with([2])


def test_repeated_marker_is_not_reported_as_complete(api):
    c, s = api
    connect(s, [{'chats':[chat(1)],'marker':100}])
    data = c.get('/api/chats?marker=100',headers=HEADERS).json()
    assert not data['complete'] and data['next_marker'] is None and data['warning']


def test_missing_cursor_with_nonempty_page_is_explicitly_incomplete(api):
    c, s = api
    connect(s, [{'chats':[chat(1)]}])
    data = c.get('/api/chats',headers=HEADERS).json()
    assert not data['complete'] and data['warning']


def test_name_failure_does_not_hide_personal_chat(api):
    c, s = api
    connect(s, [{'chats':[chat(20,participants={'1':0,'2':0})], 'marker':None}])
    s.client.get_users.side_effect = RuntimeError('sensitive upstream details')
    data = c.get('/api/chats',headers=HEADERS).json()
    assert data['complete'] and len(data['chats']) == 1
    assert data['chats'][0]['display_name'] == 'Личная переписка 2'
    assert 'sensitive' not in str(data)


def test_upstream_failure_is_explicit_and_sanitized(api):
    c, s = api
    connect(s, [])
    s.client._app.invoke.side_effect = RuntimeError('secret')
    result = c.get('/api/chats',headers=HEADERS)
    assert result.status_code == 502 and 'secret' not in result.text


def test_empty_page_finishes_even_if_server_echoes_cursor(api):
    c, s = api
    connect(s, [{'chats':[], 'marker':100}])
    data = c.get('/api/chats?marker=100',headers=HEADERS).json()
    assert data['complete'] and data['next_marker'] is None
