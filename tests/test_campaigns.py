import asyncio
import io
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app.campaigns import Draft, Store, Engine, next_occurrence
from app.main import create_app
from app.media import make_photo
from app.service import Service
from test_api import HEADERS, KEY


class Clock:
    value = 1704067200.0  # 2024-01-01 00:00 UTC
    def __call__(self):
        return self.value


def draft(**kwargs):
    return Draft(**dict({'title':'Новости','text':'**Привет** 👋','chat_ids':[11,22], 'interval_seconds':5}, **kwargs))


def fake_service(connected=True):
    return SimpleNamespace(ready=lambda: connected, send_lock=asyncio.Lock(),
        client=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(id=123))))


def image_bytes():
    out = io.BytesIO()
    Image.new('RGB',(2,2),'blue').save(out,format='PNG')
    return out.getvalue()


def test_validation_timezone_and_intervals():
    assert draft(chat_ids=[11,11,-22]).chat_ids == [11,-22]
    for kwargs in [{'interval_seconds':4},{'interval_seconds':31},{'cron':'* * * * * *'},
                   {'cron':'0 0 31 2 *'},{'timezone':'Mars/Olympus'},
                   {'enabled':True,'cron':''},{'enabled':True,'cron':'* * * * *','chat_ids':[]}]:
        with pytest.raises(ValidationError):
            draft(**kwargs)
    base=datetime(2024,1,1,tzinfo=timezone.utc).timestamp()
    result=next_occurrence('0 9 * * *','Europe/Moscow',base)
    assert datetime.fromtimestamp(result,timezone.utc).hour == 6


@pytest.mark.parametrize('interval',[5,30])
def test_send_order_interval_no_duplicates_and_snapshot(tmp_path,interval):
    async def scenario():
        clock=Clock(); store=Store(tmp_path,clock); s=fake_service(); engine=Engine(s,store)
        d=store.save(draft(interval_seconds=interval,chat_ids=[11,11,22]))
        rid=store.enqueue(d['id'])
        store.save(draft(text='Changed',chat_ids=[99]),draft_id=d['id'])
        await engine.step()
        assert s.client.send_message.call_args.kwargs['text'] == '**Привет** 👋'
        assert s.client.send_message.call_args.kwargs['chat_id'] == 11
        assert store.run_view(rid)['deliveries'][0]['state'] == 'sent'
        clock.value+=interval-0.01; await engine.step()
        assert s.client.send_message.await_count == 1
        clock.value+=0.02; await engine.step()
        assert s.client.send_message.await_count == 2
        assert s.client.send_message.call_args.kwargs['chat_id'] == 22
        assert store.run_view(rid)['state']=='completed'
        await engine.step(); assert s.client.send_message.await_count==2
        store.db.close()
    asyncio.run(scenario())


def test_photo_survives_edit_and_job_snapshot(tmp_path):
    async def scenario():
        store=Store(tmp_path); s=fake_service(); engine=Engine(s,store)
        photo=make_photo(image_bytes())
        d=store.save(draft(text='',chat_ids=[11],photo_action='replace'),photo)
        rid=store.enqueue(d['id'])
        store.save(draft(text='No photo',photo_action='remove'),draft_id=d['id'])
        await engine.step()
        assert s.client.send_message.call_args.kwargs['attachments'][0].raw == image_bytes()
        assert store.run_view(rid)['state']=='completed'
        assert store.db.execute('SELECT photo FROM runs WHERE id=?',(rid,)).fetchone()[0] is None
        store.db.close()
    asyncio.run(scenario())


def test_cron_idempotence_skip_overlap_and_offline(tmp_path):
    clock=Clock(); store=Store(tmp_path,clock)
    d=store.save(draft(cron='* * * * *',timezone='UTC',enabled=True))
    assert d['next_run']==clock.value+60
    clock.value+=60
    store.schedule_due(True); store.schedule_due(True)
    assert len(store.list_runs())==1
    clock.value+=60; store.schedule_due(True)
    assert len(store.list_runs())==1  # no overlapping run for same draft
    store.cancel(store.list_runs()[0]['id'])
    clock.value+=60; store.schedule_due(False)
    assert len(store.list_runs())==1
    assert 'MAX не подключён' in store.view(store.row(d['id']))['schedule_note']
    clock.value+=600; store.schedule_due(True)
    assert len(store.list_runs())==1
    assert store.row(d['id'])['next_run'] > clock.value
    store.db.close()


def test_edit_keeps_next_run_disable_stops_schedule(tmp_path):
    clock=Clock(); store=Store(tmp_path,clock)
    d=store.save(draft(cron='* * * * *',enabled=True))
    clock.value+=10
    changed=store.save(draft(text='new',cron='* * * * *',enabled=True),draft_id=d['id'])
    assert changed['next_run']==d['next_run']
    store.save(draft(cron='* * * * *',enabled=False),draft_id=d['id'])
    clock.value+=100; store.schedule_due(True)
    assert not store.list_runs()
    store.db.close()


def test_restart_does_not_resend_sent_or_uncertain(tmp_path):
    async def scenario():
        clock=Clock(); store=Store(tmp_path,clock)
        d=store.save(draft(chat_ids=[11,22,33])); rid=store.enqueue(d['id'])
        s=fake_service(); await Engine(s,store).step()
        with store.db:
            store.db.execute("UPDATE deliveries SET state='sending' WHERE run_id=? AND position=1",(rid,))
        store.db.close()
        store=Store(tmp_path,clock)
        assert [x['state'] for x in store.run_view(rid)['deliveries']]==['sent','unknown','pending']
        s2=fake_service(); engine=Engine(s2,store)
        await engine.step(); s2.client.send_message.assert_not_awaited()
        clock.value+=5; await engine.step()
        s2.client.send_message.assert_awaited_once()
        assert s2.client.send_message.call_args.kwargs['chat_id']==33
        assert store.run_view(rid)['state']=='partial'
        store.db.close()
    asyncio.run(scenario())


def test_offline_manual_queue_resumes_and_cancel_prevents_send(tmp_path):
    async def scenario():
        store=Store(tmp_path); d=store.save(draft()); rid=store.enqueue(d['id'])
        with pytest.raises(HTTPException) as exc: store.enqueue(d['id'])
        assert exc.value.status_code==409
        s=fake_service(False); engine=Engine(s,store); await engine.step()
        s.client.send_message.assert_not_awaited()
        s.ready=lambda:True
        await engine.step(); s.client.send_message.assert_awaited_once()
        store.cancel(rid); await engine.step()
        assert store.run_view(rid)['state']=='cancelled'
        assert s.client.send_message.await_count==1
        store.db.close()
    asyncio.run(scenario())


def test_cancel_and_delete_during_network_request(tmp_path):
    async def scenario():
        store=Store(tmp_path); d=store.save(draft()); rid=store.enqueue(d['id'])
        started,release=asyncio.Event(),asyncio.Event()
        async def send(**kwargs):
            started.set(); await release.wait(); return SimpleNamespace(id=123)
        s=fake_service(); s.client.send_message=send
        task=asyncio.create_task(Engine(s,store).step()); await started.wait()
        store.delete(d['id']); release.set(); await task
        result=store.run_view(rid)
        assert result['draft_id'] is None
        assert result['state']=='cancelled'
        assert [x['state'] for x in result['deliveries']]==['sent','cancelled']
        store.db.close()
    asyncio.run(scenario())


def test_timeout_not_retried_and_shutdown_is_uncertain(tmp_path):
    async def scenario():
        clock=Clock(); store=Store(tmp_path,clock); s=fake_service()
        s.client.send_message.side_effect=asyncio.TimeoutError
        d=store.save(draft(chat_ids=[11])); rid=store.enqueue(d['id'])
        engine=Engine(s,store); await engine.step(); await engine.step()
        assert s.client.send_message.await_count==1
        assert store.run_view(rid)['deliveries'][0]['state']=='unknown'
        store.db.close()
    asyncio.run(scenario())


def test_api_crud_photos_queue_and_preview(tmp_path):
    service=Service(str(tmp_path))
    with TestClient(create_app(service=service,api_key=KEY,run_scheduler=False)) as c:
        assert c.get('/api/saved-messages').status_code==401
        payload=draft(photo_action='replace',cron='0 9 * * 1-5').model_dump_json()
        r=c.post('/api/saved-messages',headers=HEADERS,data={'payload':payload},files={'photo':('x.png',image_bytes())})
        assert r.status_code==201
        data=r.json(); identifier=data['id']
        assert data['has_photo'] and data['chat_ids']==['11','22']
        assert c.get('/api/saved-messages/'+identifier+'/photo',headers=HEADERS).content==image_bytes()
        r=c.put('/api/saved-messages/'+identifier,headers=HEADERS,data={'payload':draft(title='Правка').model_dump_json()})
        assert r.status_code==200 and r.json()['has_photo']
        r=c.post('/api/saved-messages/'+identifier+'/send',headers=HEADERS)
        assert r.status_code==202
        rid=r.json()['id']
        assert c.post('/api/saved-messages/'+identifier+'/send',headers=HEADERS).status_code==409
        assert c.get('/api/send-runs',headers=HEADERS).json()['runs'][0]['id']==rid
        preview=c.post('/api/schedule/preview',headers=HEADERS,json={'cron':'0 9 * * *','timezone':'Europe/Moscow'})
        assert preview.status_code==200 and len(preview.json()['upcoming'])==5
        assert c.post('/api/schedule/preview',headers=HEADERS,json={'cron':'bad'}).status_code==422
        assert c.delete('/api/saved-messages/'+identifier,headers=HEADERS).status_code==200
        assert c.get('/api/saved-messages/'+identifier,headers=HEADERS).status_code==404
        assert c.get('/api/send-runs',headers=HEADERS).json()['runs'][0]['state']=='cancelled'
        assert not c.get('/api/saved-messages',headers=HEADERS).json()['messages']


def test_shutdown_marks_inflight_unknown_and_keeps_remaining(tmp_path):
    async def scenario():
        clock=Clock(); store=Store(tmp_path,clock)
        d=store.save(draft()); rid=store.enqueue(d['id'])
        started=asyncio.Event()
        async def send(**kwargs):
            started.set(); await asyncio.Future()
        s=fake_service(); s.client.send_message=send
        task=asyncio.create_task(Engine(s,store).step()); await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        result=store.run_view(rid)
        assert [r['state'] for r in result['deliveries']]==['unknown','pending']
        store.db.close()
        restored=Store(tmp_path,clock)
        assert restored.view(restored.row(d['id']))['title']=='Новости'
        assert restored.run_view(rid)['deliveries'][1]['state']=='pending'
        restored.db.close()
    asyncio.run(scenario())


def test_invalid_empty_message_removal_is_atomic(tmp_path):
    store=Store(tmp_path)
    d=store.save(draft(text='',photo_action='replace'),make_photo(image_bytes()))
    with pytest.raises(HTTPException):
        store.save(draft(text='',photo_action='remove'),draft_id=d['id'])
    assert store.row(d['id'])['photo']==image_bytes()
    store.db.close()
