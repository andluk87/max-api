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
