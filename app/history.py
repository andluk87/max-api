import asyncio
from .encoding import wire


async def history_page(client, chat_id, before=0, limit=50):
    messages = await asyncio.wait_for(client.fetch_history(
        chat_id=chat_id, backward=limit, forward=0, from_time=before or None), timeout=30)
    result = []
    seen = set()
    for message in messages or []:
        value = wire(message.model_dump(mode='python'))
        identity = str(value.get('id'))
        if identity in seen:
            continue
        seen.add(identity)
        value['chat_id'] = str(chat_id)
        result.append({'message': value})
    result.sort(key=lambda x: int(x['message'].get('time', 0)), reverse=True)
    oldest = min((int(x['message'].get('time', 0)) for x in result), default=0)
    cursor = oldest - 1 if oldest > 1 and (not before or oldest <= before) else None
    return {'messages': result, 'next_before': cursor, 'source': 'max'}
