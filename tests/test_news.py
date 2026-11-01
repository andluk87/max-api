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
        for method, path, body in [('GET','/api/news',None),('GET','/api/news/archive?day=2026-10-30',None),('POST','/api/news/check',None),('POST','/api/news/import-latest',None),('PUT','/api/news/settings',{'enabled':True})]:
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


def test_ollama_error_reason_and_memory_hint():
    from app.news import ollama_failure
    error = ollama_failure(500, 'model requires more system memory (4.0 GiB) than is available (1.0 GiB)')
    assert 'Ollama' in error and 'HTTP 500' in error
    assert '4.0 GiB' in error and 'Не хватает памяти' in error
    assert 'ollama list' in ollama_failure(404, 'model not found')


def test_ollama_http_failure_is_explained(tmp_path):
    from aiohttp import web
    async def run():
        async def generate(request):
            return web.json_response({'error':'model requires more system memory'},status=500)
        app = web.Application()
        app.router.add_post('/api/generate',generate)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner,'127.0.0.1',0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        n = News(tmp_path)
        n.put('settings',Settings(ollama=f'http://127.0.0.1:{port}').model_dump())
        try:
            with pytest.raises(ValueError,match='Ollama.*Не хватает памяти'):
                await n.generate({'title':'Погода','text':'Будет снег'})
        finally:
            await n.close()
            await runner.cleanup()
    asyncio.run(run())


def test_ai_corrects_overlong_summary_without_truncating(tmp_path):
    async def run():
        n = News(tmp_path)
        text = 'В воскресенье на Чукотке ожидается снег. ' * 8
        n.request = AsyncMock(side_effect=[{'response':json.dumps({'title':'Погода','summary':'а'*650})}, {'response':json.dumps({'title':'Погода','summary':text})}])
        data = await n.generate({'title':'Погода на Чукотке','text':text})
        assert data['summary'] == text.strip()
        assert n.request.await_count == 2
        second_prompt = n.request.call_args.kwargs['json']['prompt']
        assert '650' in second_prompt and '600' in second_prompt
        assert n.request.call_args.kwargs['json']['format']['required'] == ['title','summary']
        await n.close()
    asyncio.run(run())


def test_ai_invalid_second_result_has_precise_error(tmp_path):
    async def run():
        n = News(tmp_path)
        n.request = AsyncMock(return_value={'response':json.dumps({'title':'Погода','summary':'а'*650})})
        with pytest.raises(ValueError, match='650 символов, максимум 600'):
            await n.generate({'title':'Погода','text':'Будет снег'})
        assert n.request.await_count == 2
        await n.close()
    asyncio.run(run())


@pytest.mark.parametrize('result,reason', [
    ({'response':'{"title":"Погода","summary":null}'},'пустой пересказ'),
    ({'response':'[]'},'неверного формата'),
    ({'response':'{"title":'},'некорректный JSON'),
    ({'response':'{}','done_reason':'length'},'лимиту токенов'),
])
def test_ai_rejects_incomplete_or_invalid_output(result,reason):
    from app.news import validate_summary
    with pytest.raises(ValueError,match=reason):
        validate_summary(result,Settings(),'Погода')


def test_source_hashtags_and_clickable_source_in_max():
    from app.news import extract_hashtags, format_post
    from bs4 import BeautifulSoup
    from pymax.formatting.markdown import Formatter
    soup = BeautifulSoup('<meta name="keywords" content="Новости, Погода, Прогноз погоды"><meta property="article:tag" content="Погода"><a class="article__tag-item">Погода</a><a class="article__tag-item">#Прогноз погоды</a><a class="article__tag-item">Погода</a>', 'html.parser')
    tags = extract_hashtags(soup)
    assert tags == ['#Погода', '#Прогнозпогоды']
    url = 'https://www.mk-chukotka.ru/social/2026/11/01/news.html'
    data = {'ai_title':'Погода на Чукотке','summary':'В воскресенье ожидается снег.','source':'МК на Чукотке','hashtags':tags}
    text = format_post(data,url)
    assert text.endswith('#Погода #Прогнозпогоды')
    plain, entities = Formatter.format_markdown(text)
    links = [e for e in entities if e.type == 'LINK']
    assert len(links) == 1
    assert links[0].attributes.url == url
    label = plain.encode('utf-16-le')[links[0].from_*2:(links[0].from_+links[0].length)*2].decode('utf-16-le')
    assert label == 'МК на Чукотке'
    assert url not in plain


def test_hashtags_extracted_with_article():
    text = 'В воскресенье на Чукотке ожидается снег. ' * 8
    raw = '<h1>Погода</h1><meta name="keywords" content="Погода, Чукотка"><article><p>'+text+'</p></article><a class="article__tag-item">Чукотка</a>'
    assert article(raw,'https://example.org/news.html')['hashtags'] == ['#Чукотка']


@pytest.mark.parametrize('status', ['draft', 'published'])
def test_refresh_tags_keeps_ready_summary_without_ai(tmp_path,status):
    import sqlite3
    from fastapi.testclient import TestClient
    from app.gateway import Gateway
    from app.main import create_app
    from test_api import KEY, HEADERS
    gateway = Gateway(create_app,tmp_path,KEY,run_scheduler=False)
    with TestClient(gateway) as client:
        client.put('/api/news/settings',headers=HEADERS,json={'enabled':True})
        original = {'title':'Оригинал','ai_title':'Заголовок ИИ','summary':'Готовый пересказ','manual_only':True,'source':'МК на Чукотке'}
        with sqlite3.connect(tmp_path/'news.sqlite') as db:
            db.execute("INSERT INTO articles VALUES (1,'https://example.org/article.html',?,?,0,NULL)",(json.dumps(original),status))
        gateway.news.request = AsyncMock(return_value=b'<meta name="keywords" content="Economy"><a class="article__tag-item">Weather</a>')
        gateway.news.generate = AsyncMock(side_effect=AssertionError('AI must not run'))
        response = client.post('/api/news/article/1/refresh-tags',headers=HEADERS)
        assert response.status_code == 200
        data = client.get('/api/news',headers=HEADERS).json()['articles'][0]['data']
        assert data == dict(original,hashtags=['#Weather'])
        record = client.get('/api/news',headers=HEADERS).json()['articles'][0]
        assert record['status'] == status
        assert client.get('/api/news',headers=HEADERS).json()['deliveries'] == []
        if status == 'published':
            assert client.post('/api/news/article/1/regenerate',headers=HEADERS).status_code == 422
        gateway.news.generate.assert_not_awaited()


def test_keywords_and_section_do_not_become_hashtags():
    from app.news import extract_hashtags
    from bs4 import BeautifulSoup
    soup = BeautifulSoup('<meta name="keywords" content="Экономика, Россия"><meta property="article:section" content="Экономика"><meta property="article:tag" content="Россия"><a rel="tag">Навигация</a>', 'html.parser')
    assert extract_hashtags(soup) == []
