import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from pymax.protocol import Opcode
from app.message_links import resolve_link,message_link,web_message_link
from app.relays import Relay,SourceRef


def client(payload=None, message=None):
    return SimpleNamespace(_app=SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(payload=payload))),get_message=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kw:message or {'id':42,'text':'hi'})))


def test_web_permalink_matches_max_encoding():
    assert web_message_link(-10,117115811490104342)=='https://web.max.ru/-10/AaAUNcVzBBY'
    c=client()
    result=asyncio.run(message_link(c,-10,117115811490104342))
    assert result['url']=='https://web.max.ru/-10/AaAUNcVzBBY'
    result=asyncio.run(resolve_link(c,-10,result['url']))
    assert result['message_id']=='117115811490104342'
    c.get_message.assert_awaited_with(chat_id=-10,message_id=117115811490104342)
    c._app.invoke.assert_not_called()


def test_resolve_actual_public_link_via_max():
    c=client({'chat':{'id':-10},'message':{'id':117115811490104342,'text':'hi'}})
    result=asyncio.run(resolve_link(c,-10,'https://max.ru/ASGasparyan/AaAUNcVzBBY'))
    c._app.invoke.assert_awaited_once_with(Opcode.LINK_INFO,{'link':'ASGasparyan/AaAUNcVzBBY'})
    assert result['message_id']=='117115811490104342'
    with pytest.raises(HTTPException) as e:asyncio.run(resolve_link(c,-20,result['url']))
    assert e.value.status_code==422


def test_dont_use_forwarded_message_link_or_body_url():
    c=client(message={'id':42,'text':'https://max.ru/another/post','link':{'type':'FORWARD','chat_link':'https://max.ru/another'}})
    assert asyncio.run(message_link(c,10,42))['url']=='https://web.max.ru/10/AAAAAAAAACo'
    c=client(message={'id':42,'url':'https://max.ru/channel/AaAUNcVzBBY'})
    assert asyncio.run(message_link(c,10,42))['url']=='https://max.ru/channel/AaAUNcVzBBY'


def test_unresolved_links_no_manual_id_required():
    body=Relay(title='x',destination=2,sources=[SourceRef(chat_id=1,url='https://max.ru/name/opaque1'),SourceRef(chat_id=1,url='https://max.ru/name/opaque2')])
    assert all(s.message_id is None for s in body.sources)
    assert SourceRef(chat_id=1,message_id=42).url==''
    with pytest.raises(ValueError):SourceRef(chat_id=1)


def test_private_wrong_chat_and_invalid_link():
    c=client()
    for url in ['https://web.max.ru/20/AAAAAAAAACo','https://evil.test/name/id','https://web.max.ru/10/not-base64']:
        with pytest.raises(HTTPException):asyncio.run(resolve_link(c,10,url))
    c.get_message.assert_not_called()
