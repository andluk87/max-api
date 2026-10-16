import asyncio,json,time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.auto_relays import AutoRelay,AutoRelays
from app.service import Service
from app.relays import copy_content,is_repost,send_copy,DuplicateCopy
from app.message_links import native_source
import pytest


def test_name_native_link_and_formatting():
    message={'text':'👋 hello','elements':[{'type':'STRONG','from_':3,'length':5},{'type':'UNDERLINE','from_':3,'length':5}]}
    text,elements=copy_content(message,'https://max.ru/news/AAAAAAAAACo','Группа 👋')
    assert text=='👋 hello\n\nГруппа 👋'
    assert elements[0]=={'type':'STRONG','from':3,'length':5}
    assert elements[1]['type']=='UNDERLINE'
    assert elements[-1]['length']==9 and elements[-1]['from']==10
    client=SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kw:{'title':'Группа','link':'https://max.ru/news'})))
    title,url=asyncio.run(native_source(client,-10,42,message))
    assert title=='Группа' and url=='https://max.ru/news/AAAAAAAAACo'
    client.get_chat.return_value=SimpleNamespace(model_dump=lambda **kw:{'title':'Private','link':'https://max.ru/join/abc'})
    assert asyncio.run(native_source(client,-10,42,message))[1]=='https://max.ru/c/-10/AAAAAAAAACo'


def test_reposts_replies_persistent_dedup(tmp_path):
    s=Service(str(tmp_path));a=AutoRelays(s)
    a.save(AutoRelay(title='a',destination=30,source_chat_ids=[10],enabled=True))
    for mid,kind in [(1,'FORWARD'),(2,'REPLY')]:
        m={'id':str(mid),'text':'text','time':int((time.time()+1)*1000),'link':{'type':kind}}
        s.db.execute('INSERT INTO events(chat,mid,payload) VALUES (?,?,?)',('10',str(mid),json.dumps(m)))
    s.db.commit();a.collect()
    assert [r[0] for r in a.db.execute('SELECT state FROM auto_jobs ORDER BY id')]==['skipped_repost','pending']
    assert a.claim(30,'same') and not a.claim(30,'same') and a.claim(40,'same')
    asyncio.run(a.close());a=AutoRelays(s);assert not a.claim(30,'same');asyncio.run(a.close());s.db.close()


def test_duplicate_never_invokes_send():
    api=SimpleNamespace(_upload_attachments=AsyncMock(return_value=[]),_next_cid=lambda:1)
    app=SimpleNamespace(api=SimpleNamespace(messages=api),invoke=AsyncMock())
    client=SimpleNamespace(_app=app,get_chat=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kw:{'title':'Chat','link':'https://max.ru/news'})))
    service=SimpleNamespace(client=client,send_lock=asyncio.Lock())
    source={'chat_id':10,'message_id':42,'message':{'text':'same'}}
    with pytest.raises(DuplicateCopy):asyncio.run(send_copy(service,30,source,claim=lambda _:False))
    app.invoke.assert_not_called()
    assert is_repost({'link':{'type':'FORWARD'}})
    assert not is_repost({'link':{'type':'REPLY'}})


def test_native_forward_and_copy_without_link():
    from app.relays import send_forward
    api=SimpleNamespace(_next_cid=lambda:123)
    client=SimpleNamespace(_app=SimpleNamespace(api=SimpleNamespace(messages=api),invoke=AsyncMock(return_value={'ok':True})))
    service=SimpleNamespace(client=client,send_lock=asyncio.Lock())
    source={'chat_id':10,'message_id':42,'message':{'text':'👋 hi','elements':[{'type':'STRONG','from_':3,'length':2}]}}
    asyncio.run(send_forward(service,30,source))
    client._app.invoke.assert_awaited_once()
    assert client._app.invoke.call_args.args[1]=={'chatId':30,'message':{'cid':-123,'link':{'type':'FORWARD','messageId':42,'chatId':10},'attaches':[]},'notify':True}
    with pytest.raises(DuplicateCopy):asyncio.run(send_forward(service,30,source,claim=lambda _:False))
    assert client._app.invoke.await_count==1
    api=SimpleNamespace(_upload_attachments=AsyncMock(return_value=[]),_next_cid=lambda:1)
    client._app=SimpleNamespace(api=SimpleNamespace(messages=api),invoke=AsyncMock())
    asyncio.run(send_copy(service,30,{**source,'add_source':False}))
    payload=client._app.invoke.call_args.args[1]
    assert payload['message']['text']=='👋 hi'
    assert len(payload['message']['elements'])==1


def test_mode_routing_and_optional_history(tmp_path,monkeypatch):
    import app.auto_relays as module
    forward=AsyncMock();copy=AsyncMock()
    monkeypatch.setattr(module,'send_forward',forward);monkeypatch.setattr(module,'send_copy',copy)
    async def run():
        s=Service(str(tmp_path));s.ready=lambda:True;a=AutoRelays(s)
        a.save(AutoRelay(title='forward',destination=30,source_chat_ids=[10],enabled=True,mode='forward'))
        a.save(AutoRelay(title='copy',destination=40,source_chat_ids=[10],enabled=True,add_source=False))
        s.db.execute('INSERT INTO events(chat,mid,payload) VALUES (?,?,?)',('10','1',json.dumps({'text':'test'})));s.db.commit()
        await a.step();await a.step()
        forward.assert_awaited_once();copy.assert_awaited_once()
        assert copy.call_args.args[2]['add_source'] is False
        assert a.list(include_jobs=False)['jobs']==[]
        assert len(a.list(limit=1)['jobs'])==1
        await a.close();s.db.close()
    asyncio.run(run())


def test_forward_wire_64bit_id_is_numeric():
    from app.relays import send_forward
    from pymax.protocol.ws.protocol import WsProtocol
    from pymax.protocol import OutboundFrame,Opcode
    numeric=9223372036854775806
    api=SimpleNamespace(_next_cid=lambda:1800000000000)
    async def invoke(opcode,payload):
        frame=OutboundFrame(ver=11,cmd=0,seq=1,opcode=opcode,payload=payload)
        encoded=WsProtocol().encode(frame)
        actual=json.loads(encoded)['payload']
        assert type(actual['message']['link']['messageId']) is int
        assert actual['message']['link']['messageId']==numeric
        assert actual['chatId']==-30 and actual['message']['link']['chatId']==-10
        assert actual['message']['link']['type']=='FORWARD'
        assert opcode==Opcode.MSG_SEND
        return SimpleNamespace(payload={'message':{'id':'new'}})
    client=SimpleNamespace(_app=SimpleNamespace(api=SimpleNamespace(messages=api),invoke=invoke))
    service=SimpleNamespace(client=client,send_lock=asyncio.Lock())
    asyncio.run(send_forward(service,-30,{'chat_id':'-10','message_id':str(numeric),'message':{'text':'original'}}))
