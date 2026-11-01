"""Shared message invocation for editor campaigns and news publishing.

Caller owns service.send_lock and persistent delivery state.
"""
import asyncio
from .diagnostics import emit


async def send_message(service, chat_id, text, *, notify=True, attachments=None, operation='campaign'):
    me = getattr(service.client, 'me', None)
    contact = getattr(me, 'contact', None)
    account_id = getattr(contact, 'id', None)
    directory = getattr(service, 'directory', None)
    if directory is not None:
        emit(directory, 'max_send_target', operation=operation,
             account_id=str(account_id) if account_id is not None else None,
             chat_id=str(int(chat_id)))
    kwargs = dict(chat_id=int(chat_id), text=text or None, notify=notify)
    if attachments:
        kwargs['attachments'] = attachments
    return await asyncio.wait_for(service.client.send_message(**kwargs),
                                  timeout=120 if attachments else 30)
