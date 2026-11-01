import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup
from app.news import News, Settings, article, format_post, photo_author


class Clock:
    now = 1000.0
    def __call__(self):
        return self.now


def seed(n, count=3, photos=False):
    n.put('settings',Settings(enabled=True,photos_allowed=photos).model_dump())
    for ident in range(1,count+1):
        data={'ai_title':'Заголовок '+str(ident),'summary':'Краткий пересказ','source':'МК на Чукотке'}
        if photos:
            data['image']='https://example.org/photo.jpg'
        n.db.execute("INSERT INTO articles VALUES (?,?,?,'draft',0,NULL)",(ident,f'https://example.org/{ident}.html',json.dumps(data)))
    n.db.execute("INSERT INTO destinations VALUES (123,'Канал',1,?)",(int(photos),))
    n.db.commit()


def test_photo_credit_comes_before_title_and_only_with_photo():
    soup=BeautifulSoup('<figcaption class="article__picture-caption"><span class="article__picture-description">Фото: администрация МО Певек</span></figcaption>','html.parser')
    assert photo_author(soup)=='администрация МО Певек'
    data={'ai_title':'Заголовок','summary':'Пересказ','photo_author':photo_author(soup),'hashtags':['#Россия']}
    text=format_post(data,'https://example.org/news.html',photo_attached=True)
    assert text.startswith('📸 Фото: администрация МО Певек\n\n**Заголовок**')
    assert 'Подробности на сайте: [МК на Чукотке](https://example.org/news.html)' in text
    assert 'Источник:' not in text
    assert '📸' not in format_post(data,'https://example.org/news.html')
    assert text.endswith('#Россия')


def test_article_photo_author_and_jsonld_fallback():
    text='На Чукотке ожидается снег. '*10
    raw='<h1>Погода</h1><div class="article__picture-description">Фото: Иван Иванов</div><article><p>'+text+'</p></article>'
    assert article(raw,'https://example.org/news.html')['photo_author']=='Иван Иванов'
    soup=BeautifulSoup('<script type="application/ld+json">{"@type":"NewsArticle","image":{"caption":"Фото: Редакция"},"author":{"name":"Автор статьи"}}</script>','html.parser')
    assert photo_author(soup)=='Редакция'
    assert photo_author(BeautifulSoup('<meta name="author" content="Автор статьи">','html.parser'))==''


def test_queue_fifo_and_15_minute_delay_survive_restart(tmp_path):
    async def run():
        clock=Clock()
        n=News(tmp_path,clock=clock);seed(n)
        n.send=AsyncMock(return_value={'id':'first'})
        for ident in (1,2,3):
            await n.publish(ident,[123])
        assert n.send.await_count==1
        assert [r[0] for r in n.db.execute('SELECT status FROM deliveries ORDER BY article_id')]==['sent','queued','queued']
        clock.now=1899
        await n.dispatch()
        assert n.send.await_count==1
        await n.close()
        n=News(tmp_path,clock=clock)
        n.send=AsyncMock(return_value={'id':'next'})
        await n.dispatch()
        n.send.assert_not_awaited()
        clock.now=1900
        await n.dispatch()
        assert n.send.await_count==1
        assert '**Заголовок 2**' in n.send.call_args.args[1]
        clock.now=1901
        await n.dispatch()
        assert n.send.await_count==1
        clock.now=2800
        await n.dispatch()
        assert n.send.await_count==2
        assert '**Заголовок 3**' in n.send.call_args.args[1]
        assert n.db.execute("SELECT count(*) FROM deliveries WHERE status='queued'").fetchone()[0]==0
        await n.close()
    asyncio.run(run())


def test_cooldown_is_per_recipient_and_starts_after_confirmation(tmp_path):
    async def run():
        clock=Clock();n=News(tmp_path,clock=clock);seed(n,count=2)
        n.db.execute("INSERT INTO destinations VALUES (124,'Группа',1,0)");n.db.commit()
        async def send(*args):
            clock.now+=10
            return {'id':str(clock.now)}
        n.send=AsyncMock(side_effect=send)
        await n.publish(1,[123,124])
        assert n.send.await_count==2
        assert n.db.execute('SELECT created FROM deliveries WHERE chat_id=123').fetchone()[0]==1010
        await n.publish(2,[123,124])
        clock.now=1900
        await n.dispatch()
        assert n.send.await_count==2
        clock.now=1910
        await n.dispatch()
        assert n.send.await_count==4
        await n.close()
    asyncio.run(run())


def test_quota_and_pause_hold_queue_instead_of_losing_posts(tmp_path):
    async def run():
        clock=Clock();n=News(tmp_path,clock=clock);seed(n,count=2)
        n.put('settings',Settings(enabled=True,hourly=1).model_dump())
        n.send=AsyncMock(return_value={'id':'mid'})
        await n.publish(1,[123]);await n.publish(2,[123])
        clock.now=1900
        await n.dispatch()
        assert n.send.await_count==1
        n.put('settings',Settings(enabled=True,paused=True,hourly=1).model_dump())
        clock.now=4600
        await n.dispatch()
        assert n.send.await_count==1
        n.put('settings',Settings(enabled=True,hourly=1).model_dump())
        await n.dispatch()
        assert n.send.await_count==2
        await n.close()
    asyncio.run(run())


def test_scans_every_15_minutes_and_migrates_old_setting(tmp_path):
    async def run():
        clock=Clock();n=News(tmp_path,clock=clock)
        n.put('settings',{'enabled':True,'interval':5})
        assert n.settings().interval==15
        n.last=1000;n.scan=AsyncMock();n.process=AsyncMock();n.dispatch=AsyncMock()
        await n.tick()
        clock.now=1899;await n.tick();n.scan.assert_not_awaited()
        clock.now=1900;await n.tick();assert n.scan.await_count==1
        clock.now=2799;await n.tick();assert n.scan.await_count==1
        clock.now=2800;await n.tick();assert n.scan.await_count==2
        await n.close()
    asyncio.run(run())
    with pytest.raises(ValueError):
        Settings(interval=5)


def test_old_photo_draft_loads_credit_before_sending_without_ai(tmp_path):
    async def run():
        clock=Clock();n=News(tmp_path,clock=clock);seed(n,count=1,photos=True)
        n.request=AsyncMock(side_effect=[b'<meta property="og:image" content="/new.jpg"><span class="article__picture-description">Photo author</span>',b'photo-bytes'])
        n.generate=AsyncMock();n.send=AsyncMock(return_value={'id':'photo-mid'})
        await n.publish(1,[123])
        assert n.send.call_args.args[1].startswith('📸 Фото: Photo author\n\n**Заголовок 1**')
        assert n.send.call_args.args[2]==b'photo-bytes'
        assert n.request.call_args.args==('GET','https://example.org/new.jpg')
        n.generate.assert_not_awaited()
        assert json.loads(n.db.execute('SELECT data FROM articles').fetchone()[0])['photo_author']=='Photo author'
        await n.close()
    asyncio.run(run())


def test_disconnected_max_keeps_jobs_pending_until_login(tmp_path):
    from types import SimpleNamespace
    async def run():
        clock=Clock();connected=[False]
        service=SimpleNamespace(ready=lambda:connected[0])
        n=News(tmp_path,lambda:service,clock=clock);seed(n,count=1)
        n.send=AsyncMock(return_value={'id':'after-login'})
        await n.publish(1,[123])
        n.send.assert_not_awaited()
        assert n.db.execute('SELECT status FROM deliveries').fetchone()[0]=='queued'
        connected[0]=True
        await n.dispatch()
        n.send.assert_awaited_once()
        assert n.db.execute('SELECT status FROM deliveries').fetchone()[0]=='sent'
        await n.close()
    asyncio.run(run())


def test_interrupted_send_is_unknown_and_not_a_stuck_queue_job(tmp_path):
    async def run():
        clock=Clock();n=News(tmp_path,clock=clock);seed(n,count=1)
        n.db.execute("UPDATE articles SET status='queued' WHERE id=1")
        n.db.execute("INSERT INTO deliveries VALUES (1,123,'sending',NULL,0,NULL)");n.db.commit()
        await n.close()
        n=News(tmp_path,clock=clock)
        assert n.db.execute('SELECT status FROM deliveries').fetchone()[0]=='unknown'
        assert n.db.execute('SELECT status FROM articles').fetchone()[0]=='draft'
        n.send=AsyncMock(return_value={'id':'must-not-send'})
        clock.now=5000
        await n.publish(1,[123])
        n.send.assert_not_awaited()
        await n.close()
    asyncio.run(run())
