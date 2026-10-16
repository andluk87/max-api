import asyncio,json,time
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.auto_relays import AutoRelays,AutoRelay
from app.service import Service


def event(s,mid,chat=10,stamp=None,text='new'):
    s.db.execute('INSERT OR IGNORE INTO events(chat,mid,payload) VALUES (?,?,?)',(str(chat),str(mid),json.dumps({'id':str(mid),'chat_id':str(chat),'text':text,'time':stamp if stamp is not None else int((time.time()+1)*1000),'attaches':[]})));s.db.commit()


def test_only_new_multiple_sources_dedup_and_pause(tmp_path):
    s=Service(str(tmp_path));a=AutoRelays(s)
    event(s,1)
    rule=a.save(AutoRelay(title='auto',destination=30,source_chat_ids=[10,20],enabled=True))
    event(s,2);event(s,3,20);event(s,4,40);event(s,5,10,stamp=1000)
    a.collect();a.collect()
    assert [r[0] for r in a.db.execute('SELECT mid FROM auto_jobs ORDER BY id')]==['2','3']
    a.toggle(rule['id'],False)
    assert {j['state'] for j in a.list()['jobs']}=={'cancelled'}
    event(s,6);a.toggle(rule['id'],True);a.collect()
    assert a.db.execute('SELECT COUNT(*) FROM auto_jobs').fetchone()[0]==2
    event(s,7);a.collect();assert a.list()['jobs'][0]['state']=='pending'
    asyncio.run(a.close());s.db.close()


def test_restart_pending_and_unknown_no_repeat(tmp_path,monkeypatch):
    import app.auto_relays as module
    sender=AsyncMock();monkeypatch.setattr(module,'send_copy',sender)
    async def run():
        s=Service(str(tmp_path));s.ready=lambda:True;a=AutoRelays(s)
        a.save(AutoRelay(title='auto',destination=30,source_chat_ids=[10],enabled=True))
        event(s,1);a.collect();await a.close()
        a=AutoRelays(s);await a.step()
        assert sender.await_count==1 and a.list()['jobs'][0]['state']=='sent'
        assert sender.call_args.args[2]['message']['text']=='new'
        assert 'web.max.ru' in sender.call_args.args[2]['url']
        event(s,2);await a.step();assert sender.await_count==1  # interval persists
        a.db.execute("UPDATE auto_jobs SET state='sending' WHERE mid='2'");a.db.commit();await a.close()
        a=AutoRelays(s);await a.step();assert a.list()['jobs'][0]['state']=='unknown';assert sender.await_count==1
        await a.close();s.db.close()
    asyncio.run(run())


def test_cycles_and_edit_cancel_pending(tmp_path):
    s=Service(str(tmp_path));a=AutoRelays(s)
    first=a.save(AutoRelay(title='a',destination=20,source_chat_ids=[10],enabled=True))
    second=a.save(AutoRelay(title='b',destination=10,source_chat_ids=[20],enabled=False))
    with pytest.raises(HTTPException):a.toggle(second['id'],True)
    with pytest.raises(ValueError):AutoRelay(title='self',destination=10,source_chat_ids=[10])
    event(s,1);a.collect()
    a.save(AutoRelay(title='changed',destination=30,source_chat_ids=[10],enabled=True),first['id'])
    assert a.list()['jobs'][0]['state']=='cancelled'
    asyncio.run(a.close());s.db.close()


def test_legacy_migration_never_enables(tmp_path):
    from app.relays import Relays,Relay,SourceRef
    s=Service(str(tmp_path));old=Relays(s)
    rule=old.save(Relay(title='old',destination=30,sources=[SourceRef(chat_id=10,url='https://max.ru/s/10/42')]))
    asyncio.run(old.close());a=AutoRelays(s)
    assert a.get(rule['id'])['source_chat_ids']==['10']
    assert a.get(rule['id'])['enabled'] is False
    asyncio.run(a.close());s.db.close()
