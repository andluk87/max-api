from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.campaigns import Store, Draft, next_occurrence
from app.main import create_app
from app.service import Service
from test_api import HEADERS, KEY


def test_favorites_persist_exact_ids_and_do_not_change_recipients(tmp_path):
    store=Store(tmp_path)
    d=store.save(Draft(title='test',text='hi',chat_ids=[-9007199254740993]))
    store.favorite(-9007199254740993,'Личный чат','DIALOG')
    store.db.close()
    store=Store(tmp_path)
    assert store.favorites()[0]['chat_id']=='-9007199254740993'
    store.favorite(-9007199254740993,'Новое имя','DIALOG')
    assert len(store.favorites())==1
    store.remove_favorite(-9007199254740993)
    assert store.favorites()==[]
    assert store.view(store.row(d['id']))['chat_ids']==['-9007199254740993']
    store.db.close()


def test_api_favorite_crud_and_schedule_toggle(tmp_path):
    with TestClient(create_app(Service(str(tmp_path)),KEY,run_scheduler=False)) as c:
        assert c.get('/api/favorite-chats').status_code==401
        assert c.put('/api/favorite-chats/123',json={'title':'A'}).status_code==401
        assert c.put('/api/favorite-chats/123',headers=HEADERS,json={'title':'A','type':'DIALOG'}).status_code==200
        assert c.get('/api/favorite-chats',headers=HEADERS).json()['chats'][0]['title']=='A'
        d=c.post('/api/saved-messages',headers=HEADERS,data={'payload':Draft(title='Draft',text='unchanged',chat_ids=[123],cron='0 9 * * *',timezone='Asia/Anadyr').model_dump_json()}).json()
        r=c.patch('/api/saved-messages/'+d['id']+'/schedule',headers=HEADERS,json={'enabled':True})
        assert r.status_code==200 and r.json()['next_run'] is not None
        assert r.json()['text']=='unchanged' and r.json()['timezone']=='Asia/Anadyr'
        r=c.patch('/api/saved-messages/'+d['id']+'/schedule',headers=HEADERS,json={'enabled':False})
        assert r.json()['next_run'] is None
        assert c.delete('/api/favorite-chats/123',headers=HEADERS).status_code==200
        assert c.get('/api/favorite-chats',headers=HEADERS).json()['chats']==[]
        preview=c.post('/api/schedule/preview',headers=HEADERS,json={'cron':'0 9 * * *','timezone':'Asia/Anadyr'})
        assert preview.status_code==200


def test_anadyr_schedule_utc_conversion():
    # 09:00 Anadyr follows 00:00 UTC at 21:00 UTC of the same day.
    start=datetime(2026,1,1,0,0,tzinfo=timezone.utc).timestamp()
    next_time=next_occurrence('0 9 * * *','Asia/Anadyr',start)
    assert datetime.fromtimestamp(next_time,timezone.utc)==datetime(2026,1,1,21,0,tzinfo=timezone.utc)
