import asyncio
import json
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from fastapi.testclient import TestClient

from app.yandex_ai import KeyStore, complete, normalize_response
from app.news import News, Settings
from app.gateway import Gateway
from app.main import create_app
from test_api import KEY, HEADERS


def response(text, status='ALTERNATIVE_STATUS_FINAL'):
    return {'result': {'alternatives': [{'status':status,'message':{'role':'assistant','text':text}}]}}


def test_key_store_private_and_replaceable(tmp_path):
    store = KeyStore(tmp_path)
    assert not store.configured()
    with pytest.raises(ValueError):
        store.read()
    store.save('test-private-api-key')
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.read() == 'test-private-api-key'
    store.save('second-key')
    assert store.read() == 'second-key'
    store.delete()
    assert not store.configured()


@pytest.mark.parametrize('wrapped',[True,False])
def test_normalize_cloud_response(wrapped):
    payload = response('{"title":"Погода","summary":"Снег"}')
    result = normalize_response(payload if wrapped else payload['result'])
    assert result['provider'] == 'yandex' and result['done_reason'] == 'stop'
    assert json.loads(result['response'])['title'] == 'Погода'


def test_cloud_rejects_partial_and_filtered_outputs():
    with pytest.raises(ValueError,match='завершение'):
        normalize_response(response('partial','ALTERNATIVE_STATUS_PARTIAL'))
    with pytest.raises(ValueError,match='фильтром'):
        normalize_response(response('','ALTERNATIVE_STATUS_CONTENT_FILTER'))
    assert normalize_response(response('{}','ALTERNATIVE_STATUS_TRUNCATED_FINAL'))['done_reason'] == 'length'


def test_yandex_request_auth_schema_and_no_error_secret_leak(tmp_path,monkeypatch):
    async def run():
        received = []
        async def handler(request):
            received.append((dict(request.headers),await request.json()))
            if len(received) == 2:
                return web.json_response({'message':'sensitive-test-key'},status=403)
            return web.json_response(response('{"status":"ok"}'))
        app = web.Application()
        app.router.add_post('/completion',handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner,'127.0.0.1',0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr('app.yandex_ai.ENDPOINT',f'http://127.0.0.1:{port}/completion')
        try:
            schema = {'type':'object'}
            result = await complete('sensitive-test-key','folder-1','aliceai-llm','Верни JSON',schema,64)
            assert result['response'] == '{"status":"ok"}'
            headers, body = received[0]
            assert headers['Authorization'] == 'Api-Key sensitive-test-key'
            assert headers['x-folder-id'] == 'folder-1'
            assert headers['x-data-logging-enabled'] == 'false'
            assert body['modelUri'] == 'gpt://folder-1/aliceai-llm'
            assert body['jsonSchema']['schema'] == schema
            assert body['completionOptions']['maxTokens'] == '64'
            assert 'sensitive-test-key' not in json.dumps(body)
            with pytest.raises(ValueError,match='HTTP 403') as caught:
                await complete('sensitive-test-key','folder-1','aliceai-llm','test',schema,64)
            assert 'sensitive-test-key' not in str(caught.value)
        finally:
            await runner.cleanup()
    asyncio.run(run())


def test_cloud_summary_uses_selected_provider_without_ollama(tmp_path,monkeypatch):
    async def run():
        n = News(tmp_path)
        n.put('settings',Settings(provider='yandex',yandex_folder_id='folder-1').model_dump())
        n.yandex_key.save('test-key')
        text = 'В воскресенье на Чукотке ожидается снег. '*8
        cloud = AsyncMock(return_value={'provider':'yandex','response':json.dumps({'title':'Погода','summary':text})})
        monkeypatch.setattr('app.news.yandex_complete',cloud)
        n.request = AsyncMock(side_effect=AssertionError('Ollama should not be called'))
        result = await n.generate({'title':'Погода','text':text,'hashtags':['#Погода']})
        assert result['summary'] == text.strip()
        assert result['hashtags'] == ['#Погода']
        assert cloud.call_args.args[:3] == ('test-key','folder-1','aliceai-llm')
        cloud.assert_awaited_once()
        n.request.assert_not_awaited()
        await n.close()
    asyncio.run(run())


def test_yandex_credentials_are_tenant_isolated_and_never_returned(tmp_path,monkeypatch):
    gateway = Gateway(create_app,tmp_path,KEY,run_scheduler=False)
    with TestClient(gateway) as client:
        user = client.post('/api/admin/users',headers=HEADERS,json={'name':'Cloud reader'}).json()
        uid = user['user']['id']
        uh = {'Authorization':'Bearer '+user['api_key']}
        assert client.put('/api/news/yandex-key',headers=uh,json={'key':'user-test-secret'}).status_code == 403
        client.patch('/api/admin/users/'+uid+'/modules/news',headers=HEADERS,json={'enabled':True})
        assert client.put('/api/news/yandex-key',headers=uh,json={'key':'user-test-secret'}).json() == {'configured':True}
        assert client.get('/api/news',headers=uh).json()['yandex_key_configured'] is True
        assert client.get('/api/news',headers=HEADERS).json()['yandex_key_configured'] is False
        client.put('/api/news/settings',headers=uh,json={'provider':'yandex','yandex_folder_id':'user-folder'})
        fake = AsyncMock(return_value={'provider':'yandex','response':'{"status":"ok"}','done_reason':'stop'})
        monkeypatch.setattr('app.news.yandex_complete',fake)
        tested = client.post('/api/news/ai',headers=uh)
        assert tested.status_code == 200 and tested.json()['provider'] == 'yandex'
        assert fake.call_args.args[:3] == ('user-test-secret','user-folder','aliceai-llm')
        for path in ['/api/news','/api/logs']:
            assert 'user-test-secret' not in client.get(path,headers=uh).text
        assert 'user-test-secret' not in client.get('/api/admin/users',headers=HEADERS).text
        client.delete('/api/news/yandex-key',headers=uh)
        assert client.get('/api/news',headers=uh).json()['yandex_key_configured'] is False
        assert client.post('/api/news/ai',headers=uh).status_code == 422
        assert fake.await_count == 1


def test_provider_and_folder_validation():
    with pytest.raises(ValueError):
        Settings(provider='unknown')
    with pytest.raises(ValueError):
        Settings(yandex_folder_id='folder\r\nheader: value')
