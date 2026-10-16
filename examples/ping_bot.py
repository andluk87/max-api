"""python examples/ping_bot.py; API_KEY and BOT_CHAT_ID must be set."""
import json
import os
import time
import urllib.request
from pathlib import Path

BASE = os.environ.get('API_URL', 'http://127.0.0.1:8087')
KEY = os.environ['API_KEY']
CHAT = os.environ['BOT_CHAT_ID']  # Only reply in this explicitly chosen chat.
CURSOR = Path(os.environ.get('BOT_CURSOR', 'bot-cursor.txt'))


def request(path, body=None):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer ' + KEY, 'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=40) as response:
        return json.load(response)


offset = int(CURSOR.read_text()) if CURSOR.exists() else 0
while True:
    batch = request('/api/updates?after=' + str(offset))
    for event in batch['updates']:
        msg = event['message']
        # Commit before sending: avoids automatic duplicate replies on ambiguous failure.
        # A crash between commit and send can lose a reply (at-most-once attempt).
        offset = event['update_id']
        temp = CURSOR.with_suffix('.tmp')
        temp.write_text(str(offset))
        temp.replace(CURSOR)
        if str(msg.get('chat_id')) == CHAT and msg.get('text') == '/ping':
            request('/api/messages', {'chat_id': CHAT, 'text': 'pong', 'reply_to': msg['id']})
    time.sleep(2)
