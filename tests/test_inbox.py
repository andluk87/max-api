import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.service import Service
from test_api import HEADERS, KEY


def test_inbox_isolated_pagination_and_auth(tmp_path):
    svc=Service(str(tmp_path))
    for chat, mid in [('9007199254740993',1),('10',2),('9007199254740993',3),('9007199254740993',4)]:
        svc.db.execute('INSERT INTO events(chat,mid,payload) VALUES (?,?,?)',(chat,str(mid),json.dumps({'chat_id':chat,'text':f'Привет 👋 {mid}'})))
    svc.db.commit()
    with TestClient(create_app(svc,KEY,run_scheduler=False)) as c:
        assert c.get('/api/inbox/chats').status_code==401
        assert c.get('/api/inbox/10').status_code==401
        chats=c.get('/api/inbox/chats',headers=HEADERS).json()['chats']
        assert chats==[{'chat_id':'9007199254740993','count':3},{'chat_id':'10','count':1}]
        first=c.get('/api/inbox/9007199254740993?limit=2',headers=HEADERS).json()
        assert [x['update_id'] for x in first['messages']]==[4,3]
        second=c.get('/api/inbox/9007199254740993?limit=2&before='+str(first['next_before']),headers=HEADERS).json()
        assert [x['update_id'] for x in second['messages']]==[1]
        assert second['next_before'] is None
        assert c.get('/api/inbox/999',headers=HEADERS).json()['messages']==[]
