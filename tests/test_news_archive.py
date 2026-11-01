import asyncio
import json
import time
from datetime import date
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from app.news import News, Settings, original_url
from app.gateway import Gateway
from app.main import create_app
from test_api import KEY, HEADERS


def listing():
    return b'''<a class="news-listing__item-link" href="https://www.mk-chukotka.ru/social/2026/10/30/first.html"><span>12:10</span><h3 class="news-listing__item-title">First news</h3></a>
    <a class="news-listing__item-link" href="https://www.mk-chukotka.ru/social/2026/10/29/other.html">Wrong date</a>
    <a class="news-listing__item-link" href="https://other.org/social/2026/10/30/evil.html">Wrong host</a>'''


def body():
    return ('<h1>Погода</h1><article><p>'+'В воскресенье на Чукотке ожидается снег. '*8+'</p></article>').encode()


def test_browse_date_filters_links_and_does_not_generate(tmp_path):
    async def run():
        n = News(tmp_path)
        n.request = AsyncMock(return_value=listing())
        n.generate = AsyncMock()
        found = await n.browse_date(date(2026,10,30))
        assert len(found) == 1
        assert found[0]['title'] == 'First news'
        assert found[0]['status'] == 'not_loaded'
        n.request.assert_awaited_once_with('GET','https://www.mk-chukotka.ru/news/2026/10/30/')
        n.generate.assert_not_awaited()
        assert n.db.execute('SELECT count(*) FROM articles').fetchone()[0] == 0
        await n.close()
    asyncio.run(run())


def test_published_article_can_have_manual_copy_without_resetting_history(tmp_path):
    async def run():
        n = News(tmp_path)
        n.put('settings',Settings(enabled=True,automatic=True).model_dump())
        url = 'https://www.mk-chukotka.ru/social/2026/10/30/first.html'
        old = {'ai_title':'Ранее опубликовано','summary':'Старый пересказ','source':'МК на Чукотке'}
        n.db.execute("INSERT INTO articles VALUES (1,?,?,'published',0,NULL)",(url,json.dumps(old)))
        n.db.execute("INSERT INTO destinations VALUES (123,'Тест',1,0)")
        n.db.execute("INSERT INTO deliveries VALUES (1,123,'sent','old-mid',0,NULL)")
        n.db.commit()
        n.request = AsyncMock(return_value=body())
        n.generate = AsyncMock(side_effect=lambda data:dict(data,ai_title='Новый заголовок',summary='Новый пересказ'))
        ident = await n.create_manual_draft(url)
        copy = n.db.execute('SELECT * FROM articles WHERE id=?',(ident,)).fetchone()
        assert ident != 1
        assert original_url(copy) == url
        assert copy['url'] != url
        assert json.loads(copy['data'])['manual_only'] is True
        n.last = time.time()
        n.send = AsyncMock(return_value={'id':'new-mid'})
        await n.tick()
        n.send.assert_not_awaited()
        await n.publish(ident,[123])
        n.send.assert_awaited_once()
        assert '#manual-' not in n.send.call_args.args[1]
        assert '('+url+')' in n.send.call_args.args[1]
        assert n.db.execute('SELECT mid FROM deliveries WHERE article_id=1').fetchone()[0] == 'old-mid'
        assert n.db.execute('SELECT mid FROM deliveries WHERE article_id=?',(ident,)).fetchone()[0] == 'new-mid'
        assert json.loads(n.db.execute('SELECT data FROM articles WHERE id=1').fetchone()[0]) == old
        second = await n.create_manual_draft(url)
        assert second != ident
        await n.close()
    asyncio.run(run())


def test_archive_import_verifies_membership_and_returns_canonical_url(tmp_path):
    gateway = Gateway(create_app,tmp_path,KEY,run_scheduler=False)
    with TestClient(gateway) as client:
        client.put('/api/news/settings',headers=HEADERS,json={'enabled':True})
        gateway.news.request = AsyncMock(return_value=listing())
        gateway.news.generate = AsyncMock()
        found = client.get('/api/news/archive?day=2026-10-30',headers=HEADERS)
        assert found.status_code == 200 and len(found.json()['articles']) == 1
        gateway.news.generate.assert_not_awaited()
        rejected = client.post('/api/news/archive/import',headers=HEADERS,json={'day':'2026-10-30','url':'http://127.0.0.1/secret'})
        assert rejected.status_code == 422
        assert all(call.args[1].startswith('https://www.mk-chukotka.ru/news/') for call in gateway.news.request.call_args_list)
        gateway.news.request = AsyncMock(side_effect=[listing(),body()])
        gateway.news.generate = AsyncMock(side_effect=lambda data:dict(data,ai_title='Погода',summary='Готовый пересказ'))
        created = client.post('/api/news/archive/import',headers=HEADERS,json={'day':'2026-10-30','url':found.json()['articles'][0]['url']})
        assert created.status_code == 200
        state = client.get('/api/news',headers=HEADERS).json()
        draft = state['articles'][0]
        assert draft['id'] == created.json()['id']
        assert draft['status'] == 'draft'
        assert '#manual-' not in draft['url']
        assert draft['data']['manual_only'] is True
        assert state['deliveries'] == []
        assert client.get('/api/news/archive?day=bad-date',headers=HEADERS).status_code == 422
