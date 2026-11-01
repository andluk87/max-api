import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.news import News, Settings, article


def test_settings_and_article():
    with pytest.raises(ValueError):
        Settings(interval=2)
    data = article('<h1>Погода на Чукотке</h1><meta property="og:image" content="/a.jpg"><article><nav>Реклама</nav><p>' + 'В воскресенье на Чукотке ожидается снег. ' * 8 + '</p></article>', 'https://example.org/n.html')
    assert data['image'] == 'https://example.org/a.jpg'
    assert 'Реклама' not in data['text']


def test_baseline_and_new_articles(tmp_path):
    async def run():
        n = News(tmp_path)
        n.put('settings', Settings(enabled=True).model_dump())
        n.request = AsyncMock(return_value=b'<a href="/weather/2026/01/01/first.html">news</a>')
        await n.scan()
        assert n.db.execute('SELECT status FROM articles').fetchone()[0] == 'baseline'
        n.request.return_value = b'<a href="/weather/2026/01/02/second.html">new</a>'
        await n.scan()
        await n.scan()
        assert n.db.execute("SELECT count(*) FROM articles WHERE status='new'").fetchone()[0] == 1
        await n.close()
    asyncio.run(run())


def test_send_deduplication_and_disable(tmp_path):
    async def run():
        n = News(tmp_path)
        n.put('settings', Settings(enabled=True).model_dump())
        n.db.execute("INSERT INTO articles VALUES (1, 'https://example.org/a.html', ?, 'draft', 0, NULL)", (json.dumps({'ai_title':'Погода','summary':'Будет снег','source':'МК','image':''}),))
        n.db.execute("INSERT INTO destinations VALUES (123, 'Канал', 1, 0)")
        n.db.commit()
        n.send = AsyncMock(return_value={'id': 42})
        await n.publish(1)
        await n.publish(1)
        assert n.send.await_count == 1
        assert n.db.execute('SELECT mid FROM deliveries').fetchone()[0] == '42'
        n.put('settings', Settings(enabled=False).model_dump())
        with pytest.raises(ValueError):
            await n.publish(1)
        await n.close()
    asyncio.run(run())


def test_uncertain_send_is_never_retried(tmp_path):
    async def run():
        n = News(tmp_path)
        n.put('settings', Settings(enabled=True).model_dump())
        n.db.execute("INSERT INTO articles VALUES (1, 'https://example.org/a.html', ?, 'draft', 0, NULL)", (json.dumps({'ai_title':'Погода','summary':'Будет снег','source':'МК'}),))
        n.db.execute("INSERT INTO destinations VALUES (123, 'Канал', 1, 0)")
        n.db.commit()
        n.send = AsyncMock(side_effect=asyncio.TimeoutError())
        await n.publish(1)
        await n.publish(1)
        assert n.send.await_count == 1
        assert n.db.execute('SELECT status FROM deliveries').fetchone()[0] == 'unknown'
        await n.close()
    asyncio.run(run())


def test_admin_only_and_settings_persist(tmp_path):
    from fastapi.testclient import TestClient
    from app.gateway import Gateway
    from app.main import create_app
    from test_api import KEY, HEADERS
    with TestClient(Gateway(create_app, tmp_path, KEY, run_scheduler=False)) as client:
        user = client.post('/api/admin/users', headers=HEADERS, json={'name': 'Reader'}).json()
        headers = {'Authorization': 'Bearer ' + user['api_key']}
        assert client.get('/api/admin/news', headers=headers).status_code == 403
        assert client.put('/api/admin/news/settings', headers=headers, json={'enabled': True}).status_code == 403
        assert client.get('/api/admin/news').status_code == 401
        assert client.put('/api/admin/news/settings', headers=HEADERS, json={'enabled': True, 'paused': True}).status_code == 200
        assert client.get('/ui/news.js').status_code == 200
    with TestClient(Gateway(create_app, tmp_path, KEY, run_scheduler=False)) as client:
        state = client.get('/api/admin/news', headers=HEADERS).json()
        assert state['settings']['enabled'] is True
        assert state['settings']['paused'] is True


def test_denied_test_send_has_actionable_error(tmp_path):
    from fastapi.testclient import TestClient
    from app.gateway import Gateway
    from app.main import create_app
    from pymax.exceptions import ApiError
    from test_api import KEY, HEADERS
    gateway = Gateway(create_app, tmp_path, KEY, run_scheduler=False)
    with TestClient(gateway) as client:
        import sqlite3
        with sqlite3.connect(tmp_path / 'news.sqlite') as db:
            db.execute("INSERT INTO destinations VALUES (123, 'Канал', 1, 0)")
        gateway.news.send = AsyncMock(side_effect=ApiError(opcode=64, error='chat.denied'))
        response = client.post('/api/admin/news/destination/123/test', headers=HEADERS)
        assert response.status_code == 403
        assert 'chat.denied' in response.json()['detail']
        assert 'аккаунт' in response.json()['detail']


def test_news_uses_editor_transport_parameters(tmp_path):
    from app.delivery import send_message
    async def run():
        message = SimpleNamespace(model_dump=lambda **kwargs: {'id': 42})
        client = SimpleNamespace(send_message=AsyncMock(return_value=message), me=SimpleNamespace(contact=SimpleNamespace(id=9001)))
        service = SimpleNamespace(client=client, directory=tmp_path, send_lock=asyncio.Lock(), ready=lambda: True)
        n = News(tmp_path, lambda: service)
        await send_message(service, -79387375605133, 'Тест')
        editor_kwargs = client.send_message.call_args.kwargs
        await n.send(-79387375605133, 'Тест')
        assert client.send_message.call_args.kwargs == editor_kwargs
        assert editor_kwargs == {'chat_id': -79387375605133, 'text': 'Тест', 'notify': True}
        await n.close()
    asyncio.run(run())


def test_news_module_grants_isolation_revocation_and_persistence(tmp_path):
    from fastapi.testclient import TestClient
    from app.gateway import Gateway
    from app.main import create_app
    from test_api import KEY, HEADERS
    gateway = Gateway(create_app, tmp_path, KEY, run_scheduler=False)
    with TestClient(gateway) as client:
        a = client.post('/api/admin/users', headers=HEADERS, json={'name':'Alice'}).json()
        b = client.post('/api/admin/users', headers=HEADERS, json={'name':'Bob'}).json()
        uid, bid = a['user']['id'], b['user']['id']
        ah = {'Authorization':'Bearer '+a['api_key']}
        bh = {'Authorization':'Bearer '+b['api_key']}
        assert client.get('/api/whoami', headers=ah).json()['modules']['news'] is False
        for method, path, body in [('GET','/api/news',None),('POST','/api/news/check',None),('POST','/api/news/import-latest',None),('PUT','/api/news/settings',{'enabled':True})]:
            assert client.request(method,path,headers=ah,json=body).status_code == 403
        path = '/api/admin/users/'+uid+'/modules/news'
        assert client.patch(path,headers=ah,json={'enabled':True}).status_code == 403
        assert client.patch(path,headers=HEADERS,json={'enabled':True}).status_code == 200
        assert client.get('/api/whoami',headers=ah).json()['modules']['news'] is True
        assert client.get('/api/news',headers=ah).status_code == 200
        assert client.get('/api/news',headers=bh).status_code == 403
        assert client.get('/api/news',headers={**ah,'X-User-ID':bid}).status_code == 403
        assert client.put('/api/news/settings',headers=ah,json={'model':'alice-model'}).status_code == 200
        assert client.get('/api/news',headers=HEADERS).json()['settings']['model'] != 'alice-model'
        assert client.get('/api/news',headers={**HEADERS,'X-User-ID':uid}).json()['settings']['model'] == 'alice-model'
        assert gateway.apps[uid][0].state.news.service_provider() is gateway.apps[uid][0].state.service
        assert client.patch(path,headers=HEADERS,json={'enabled':False}).status_code == 200
        assert client.get('/api/news',headers=ah).status_code == 403
        assert client.patch(path,headers=HEADERS,json={'enabled':True}).status_code == 200
    with TestClient(Gateway(create_app,tmp_path,KEY,run_scheduler=False)) as client:
        assert client.get('/api/whoami',headers=ah).json()['modules']['news'] is True
        assert client.get('/api/whoami',headers=bh).json()['modules']['news'] is False
        assert client.get('/api/news',headers=ah).json()['settings']['model'] == 'alice-model'


def test_revoking_news_stops_worker(tmp_path):
    from fastapi.testclient import TestClient
    from app.gateway import Gateway
    from app.main import create_app
    from test_api import KEY, HEADERS
    gateway = Gateway(create_app,tmp_path,KEY,run_scheduler=True)
    with TestClient(gateway) as client:
        user = client.post('/api/admin/users',headers=HEADERS,json={'name':'Alice'}).json()
        uid = user['user']['id']
        assert gateway.apps[uid][0].state.news.task is None
        path = '/api/admin/users/'+uid+'/modules/news'
        client.patch(path,headers=HEADERS,json={'enabled':True})
        task = gateway.apps[uid][0].state.news.task
        assert task is not None
        client.patch(path,headers=HEADERS,json={'enabled':False})
        assert task.done()
        assert gateway.apps[uid][0].state.news.task is None


def test_manual_latest_draft_never_auto_publishes(tmp_path):
    import time
    async def run():
        n = News(tmp_path)
        n.put('settings',Settings(enabled=True,automatic=True).model_dump())
        raw = ('<h1>Новости Чукотки</h1><article><p>'+'Ожидается снег и сильный ветер в воскресенье. '*8+'</p></article>').encode()
        n.request = AsyncMock(side_effect=[b'<a href="/weather/2026/01/01/latest.html">latest</a>',raw])
        n.generate = AsyncMock(side_effect=lambda data: dict(data, ai_title='Погода',summary='Прогноз погоды'))
        ident = await n.import_latest()
        row = n.db.execute('SELECT * FROM articles WHERE id=?',(ident,)).fetchone()
        assert row['status'] == 'draft'
        assert json.loads(row['data'])['manual_only'] is True
        n.last = time.time()
        n.publish = AsyncMock()
        await n.tick()
        n.publish.assert_not_awaited()
        await n.close()
    asyncio.run(run())
