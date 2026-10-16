"""Command bot: /help, /ping, /time. One process per state database."""
import logging
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from client import MaxAPI

log = logging.getLogger('bot')


def reply_for(message, own_id, allowed):
    if str(message.get('chat_id')) not in allowed:
        return None
    if not message.get('sender') or str(message['sender']) == own_id:
        return None
    if (message.get('link') or {}).get('type') == 'FORWARD':
        return None
    command = (message.get('text') or '').strip()
    if command == '/help':
        return 'Команды: /ping — проверка; /time — время Анадыря; /help — помощь.'
    if command == '/ping':
        return 'pong 👋'
    if command == '/time':
        return datetime.now(ZoneInfo('Asia/Anadyr')).strftime('Анадырь: %d.%m.%Y %H:%M:%S')
    return None


def run():
    api = MaxAPI()
    own_id = str(int(os.environ['BOT_OWN_ID']))
    allowed = {str(int(x.strip())) for x in os.environ['BOT_CHAT_IDS'].split(',') if x.strip()}
    if not allowed:
        raise RuntimeError('Укажите разрешённые чаты BOT_CHAT_IDS')
    db = sqlite3.connect(os.environ.get('BOT_STATE', 'bot-state.sqlite'))
    db.executescript('CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY, offset INTEGER); CREATE TABLE IF NOT EXISTS attempts(update_id INTEGER PRIMARY KEY, state TEXT);')
    # Never retry an interrupted request automatically.
    db.execute("UPDATE attempts SET state='unknown' WHERE state='sending'");db.commit()
    if not db.execute('SELECT 1 FROM meta WHERE id=1').fetchone():
        # Initial bootstrap skips the existing local event log and sends nothing.
        offset = 0
        while True:
            page = api.request('GET', 'updates', params={'after': offset, 'limit': 500})
            offset = page['next_offset']
            if not page['updates']:
                break
        db.execute('INSERT INTO meta VALUES (1,?)', (offset,));db.commit()
        log.info('Начальная позиция сохранена. Ожидаются новые команды.')
    try:
        while True:
            offset = db.execute('SELECT offset FROM meta WHERE id=1').fetchone()[0]
            try:
                page = api.request('GET', 'updates', params={'after': offset, 'limit': 100})
            except Exception as exc:
                log.warning('Чтение событий: %s', type(exc).__name__)
                time.sleep(5)
                continue
            for update in page['updates']:
                uid = update['update_id']; message = update['message']
                reply = reply_for(message, own_id, allowed)
                if reply and message.get('id'):
                    with db:
                        claimed = db.execute("INSERT OR IGNORE INTO attempts VALUES (?,'sending')", (uid,)).rowcount
                    if claimed:
                        try:
                            api.send(message['chat_id'], reply, reply_to=message['id'])
                            state = 'sent'
                        except Exception as exc:
                            # Delivery could have succeeded before transport failure.
                            state = 'unknown'
                            log.warning('Отправка update=%s: %s; без повтора', uid, type(exc).__name__)
                        with db:
                            db.execute('UPDATE attempts SET state=? WHERE update_id=?', (state, uid))
                with db:
                    db.execute('UPDATE meta SET offset=? WHERE id=1', (uid,))
            time.sleep(2 if not page['updates'] else 0.1)
    finally:
        db.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run()
