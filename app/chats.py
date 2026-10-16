"""Paginated MAX chat directory. PyMax 2.4.1 drops the server cursor in fetch_chats,
so this small adapter uses the same opcode and preserves the response marker.
"""
import asyncio
import time

from pymax.protocol import Opcode
from pymax.types import Chat

from .encoding import wire


async def chat_page(client, marker=None):
    current = marker if marker is not None else int(time.time() * 1000)
    response = await asyncio.wait_for(
        client._app.invoke(Opcode.CHATS_LIST, {'marker': current, 'count': 100}),
        timeout=25,
    )
    payload = response.payload
    if not isinstance(payload, dict) or not isinstance(payload.get('chats'), list):
        raise ValueError('Invalid chat page')
    chats = [Chat.model_validate(item) for item in payload['chats']]
    # Do not calculate a cursor from timestamps: equal timestamps can skip dialogs.
    next_marker = payload.get('marker')
    warning = None
    if next_marker is not None:
        next_marker = int(next_marker)
        if next_marker <= 0:
            next_marker = None
    complete = next_marker is None
    if next_marker is not None and next_marker >= current:
        complete = False
        next_marker = None
        warning = 'MAX вернул повторяющийся маркер. Полнота списка не подтверждена.'
    if not chats:
        complete, next_marker, warning = True, None, None
    if 'marker' not in payload and chats:
        complete = False
        warning = 'MAX не вернул маркер следующей страницы. Полнота списка не подтверждена.'

    me = client.me
    own_id = me.contact.id if me else None
    peers = {}
    for chat in chats:
        if chat.type == 'DIALOG':
            candidates = [uid for uid in chat.participants if uid != own_id]
            if len(candidates) == 1:
                peers[chat.id] = candidates[0]
    users = {}
    missing = sorted({peers[c.id] for c in chats if c.id in peers and not c.title})
    for start in range(0, len(missing), 100):
        try:
            result = await asyncio.wait_for(client.get_users(missing[start:start+100]), timeout=10)
            users.update({user.id: user for user in result if user is not None})
        except Exception:
            # Names are optional; a failed profile lookup must not hide a dialog.
            warning = (warning + ' ' if warning else '') + 'Часть имён недоступна; диалоги показаны по ID.'
            break
    output = []
    for chat in chats:
        item = wire(chat)
        name = chat.title
        peer_id = peers.get(chat.id)
        user = users.get(peer_id)
        if not name and user:
            for entry in user.names:
                name = entry.name or ' '.join(x for x in [entry.first_name, entry.last_name] if x)
                if name:
                    break
        item['display_name'] = name or ('Личная переписка' if chat.type == 'DIALOG' else 'Чат') + ' ' + str(peer_id or chat.id)
        item['peer_id'] = str(peer_id) if peer_id is not None else None
        output.append(item)
    return {'chats': output, 'next_marker': str(next_marker) if next_marker is not None else None,
            'complete': complete, 'warning': warning}
