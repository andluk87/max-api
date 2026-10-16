import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pymax.types.domain.attachments.enums import AttachmentType
from app.relays import Relay,SourceRef,Relays,copy_content,send_copy
from app.source import image_url,read_source,photo_attachments
from app.service import Service
from app.main import create_app
from test_api import KEY,HEADERS


def test_source_links_and_ids():
    assert SourceRef(chat_id=-10,url='https://max.ru/s/-10/9007199254740993').message_id==9007199254740993
    assert SourceRef(chat_id=10,url='https://max.ru/channel?mid=42').message_id==42
    assert SourceRef(chat_id=10,message_id=42,url='https://max.ru/channel/opaque').message_id==42
    for args in [dict(chat_id=1,url='https://evil.test/42'),dict(chat_id=1,url='https://max.ru/s/2/42'),dict(chat_id=1,message_id=43,url='https://max.ru/c/42')]:
        with pytest.raises(ValueError):SourceRef(**args)


def test_public_photo_urls():
    assert image_url('https://example.com/photo')
    for url in ['http://example.com/p','https://127.0.0.1/p','https://[::1]/p','https://169.254.169.254/','https://user:pw@example.com/p','https://example.com:8080/p']:
        with pytest.raises(ValueError):image_url(url)


def test_exact_content_and_link_utf16():
    message={'text':'👋 **literal** text','elements':[{'type':'STRONG','from_':15,'length':4}]}
    text,elements=copy_content(message,'https://max.ru/s/1/2')
    assert text=='👋 **literal** text\n\nИсточник'
    assert elements[0]['from']==15
    assert elements[-1]['from']==len('👋 **literal** text\n\n'.encode('utf-16-le'))//2
    assert elements[-1]['attributes']['url']=='https://max.ru/s/1/2'
    assert 'from_' in message['elements'][0]


def test_copy_transport_is_new_message_with_photo(monkeypatch):
    import app.relays as module
    raw=Path('tests/fixtures/photo.png').read_bytes()
    monkeypatch.setattr(module,'source_photo',AsyncMock(return_value=raw))
    message=SimpleNamespace(model_dump=lambda **kw:{'id':42,'text':'hi','elements':[{'type':'STRONG','from_':0,'length':2}],'attaches':[{'type':AttachmentType.PHOTO,'base_url':'https://example.com/p'}]})
    internal=SimpleNamespace(_upload_attachments=AsyncMock(return_value=[]),_next_cid=lambda:123)
    app=SimpleNamespace(api=SimpleNamespace(messages=internal),invoke=AsyncMock())
    client=SimpleNamespace(get_message=AsyncMock(return_value=message),get_chat=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kw:{'title':'Новости','link':'https://max.ru/news'})),_app=app)
    service=SimpleNamespace(client=client,send_lock=asyncio.Lock())
    asyncio.run(send_copy(service,9,{'chat_id':1,'message_id':42,'url':'https://max.ru/s/1/42'}))
    assert len(internal._upload_attachments.call_args.args[0])==1
    payload=app.invoke.call_args.args[1]
    assert payload['chatId']==9
    assert payload['message'].get('link') is None
    assert payload['message']['text']=='hi\n\nНовости'
    assert payload['message']['elements'][-1]['attributes']['url']=='https://max.ru/news/AAAAAAAAACo'
    assert payload['message']['elements'][-1]['type']=='LINK'


def test_photo_endpoint_and_relay_crud(tmp_path,monkeypatch):
    import app.main as module
    raw=Path('tests/fixtures/photo.png').read_bytes()
    monkeypatch.setattr(module,'source_photo',AsyncMock(return_value=raw))
    service=Service(str(tmp_path))
    service.state='connected';service.client=SimpleNamespace(is_connected=True,get_message=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kw:{'text':'x','attaches':[]})))
    body={'title':'Copies','destination':'9007199254740993','source_chat_ids':['-10','-20']}
    with TestClient(create_app(service,KEY,run_scheduler=False)) as c:
        assert c.get('/api/source/1/42/photo/0').status_code==401
        assert c.get('/api/relays').status_code==401
        response=c.get('/api/source/1/42/photo/0',headers=HEADERS)
        assert response.content==raw and response.headers['content-type']=='image/png'
        rule=c.post('/api/relays',headers=HEADERS,json=body).json()
        assert rule['destination']=='9007199254740993' and len(rule['source_chat_ids'])==2
        assert c.put('/api/relays/'+rule['id'],headers=HEADERS,json={**body,'title':'Updated'}).status_code==200
        assert c.get('/api/relays',headers=HEADERS).json()['relays'][0]['title']=='Updated'
        assert c.delete('/api/relays/'+rule['id'],headers=HEADERS).status_code==200


def test_relay_failure_not_retried_and_restart(tmp_path,monkeypatch):
    import app.relays as module
    sender=AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(module,'send_copy',sender)
    async def scenario():
        service=Service(str(tmp_path));service.ready=lambda:True
        manager=Relays(service)
        rule=manager.save(Relay(title='one',destination=9,sources=[SourceRef(chat_id=1,url='https://max.ru/s/1/42')]))
        run=manager.start(rule['id']);await manager.tasks[run['id']][0]
        assert manager.list()['runs'][0]['items'][0]['state']=='unknown'
        assert sender.await_count==1
        await manager.close()
        manager=Relays(service)
        assert manager.list()['relays'][0]['id']==rule['id']
        assert manager.list()['runs'][0]['items'][0]['state']=='unknown'
        await manager.close();service.db.close()
    asyncio.run(scenario())
