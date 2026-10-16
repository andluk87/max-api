from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
from app.history import history_page


class Message:
    def __init__(self, **data): self.data=data
    def model_dump(self, **kwargs): return self.data


def test_history_cursor_sort_and_rich_payload():
    asyncio.run(check_history())


async def check_history():
    client=SimpleNamespace(fetch_history=AsyncMock(return_value=[
        Message(id=9007199254740993,time=100,text='👋 text',elements=[{'type':'STRONG','from_':3,'length':4}],attaches=[{'type':'PHOTO','base_url':'https://example.com/photo','preview_data':b'abc'}]),
        Message(id=2,time=200,text='new'),Message(id=2,time=200,text='new')]))
    page=await history_page(client,-9007199254740993)
    assert len(page['messages'])==2
    assert page['messages'][0]['message']['text']=='new'
    old=page['messages'][1]['message']
    assert old['id']=='9007199254740993'
    assert old['chat_id']=='-9007199254740993'
    assert old['elements'][0]['from_']==3
    assert old['attaches'][0]['preview_data']['encoding']=='base64'
    assert page['next_before']==99
    client.fetch_history.assert_awaited_once_with(chat_id=-9007199254740993,backward=50,forward=0,from_time=None)
    client.fetch_history.reset_mock();client.fetch_history.return_value=[]
    assert (await history_page(client,1,99))['next_before'] is None
    assert client.fetch_history.call_args.kwargs['from_time']==99
