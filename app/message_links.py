"""MAX link adapter, matching LINK_INFO (89) and MAX Web's uint64 URL encoding.
Reference: https://web.max.ru/ (public client, inspected 2026-09-25).
"""
import asyncio
import base64
from urllib.parse import urlsplit
from fastapi import HTTPException
from pymax.protocol import Opcode
from .source import read_source
from .encoding import wire


def validate_link(url):
    p=urlsplit(url.strip())
    if p.scheme!='https' or p.hostname not in {'max.ru','web.max.ru'} or p.username or p.password or p.port not in (None,443):
        raise ValueError('Нужна HTTPS-ссылка на сообщение MAX')
    return p


def web_message_link(chat_id,message_id):
    token=base64.urlsafe_b64encode(int(message_id).to_bytes(8,'big',signed=False)).decode().rstrip('=')
    return f'https://web.max.ru/{int(chat_id)}/{token}'


async def message_link(client,chat_id,message_id):
    message=await read_source(client,chat_id,message_id)
    # Only own message URL fields: message.link is a reply/forward, not its permalink.
    for key in ('url','message_link','messageLink','public_link','publicLink'):
        value=message.get(key)
        if isinstance(value,str):
            try:validate_link(value)
            except ValueError:continue
            return {'chat_id':str(chat_id),'message_id':str(message_id),'url':value,'text':message.get('text') or ''}
    # Numeric chat route is supported by MAX Web for personal and private chats too.
    return {'chat_id':str(chat_id),'message_id':str(message_id),'url':web_message_link(chat_id,message_id),'text':message.get('text') or ''}


async def resolve_link(client,chat_id,url):
    try:p=validate_link(url)
    except ValueError as e:raise HTTPException(422,str(e))
    parts=p.path.strip('/').split('/')
    # Web routes for private chats contain a decimal chat and a uint64 base64url message.
    if p.hostname=='web.max.ru' and len(parts)==2 and parts[0].lstrip('-').isdigit():
        try:
            raw=base64.b64decode(parts[1]+'='*((-len(parts[1]))%4),altchars=b'-_',validate=True)
            if len(raw)!=8:raise ValueError()
            mid=int.from_bytes(raw,'big')
        except ValueError:raise HTTPException(422,'Некорректная ссылка на сообщение')
        if int(parts[0])!=chat_id:raise HTTPException(422,'Ссылка относится к другому чату')
        result=await message_link(client,chat_id,mid)
        result['url']=url.strip();return result
    # Resolve public/short links through MAX, never guess an opaque message ID.
    link=p.path if p.path.startswith('/c/') else p.path.lstrip('/')
    try:
        frame=await asyncio.wait_for(client._app.invoke(Opcode.LINK_INFO,{'link':link}),30)
        payload=wire(frame.payload)
    except Exception:raise HTTPException(502,'MAX не смог распознать ссылку. Выберите сообщение из истории чата.')
    chat=payload.get('chat') or {};message=payload.get('message') or {}
    if payload.get('post'):raise HTTPException(422,'Ссылки на комментарии пока не поддерживаются. Выберите сообщение из истории.')
    if str(chat.get('id'))!=str(chat_id):raise HTTPException(422,'Ссылка относится к другому чату либо недоступна аккаунту')
    if not str(message.get('id','')).isdigit():raise HTTPException(422,'MAX не вернул сообщение по этой ссылке')
    return {'chat_id':str(chat_id),'message_id':str(message['id']),'url':url.strip(),'text':message.get('text') or ''}

async def native_source(client,chat_id,message_id,message,target='message'):
    """Use the source chat's own metadata; never use forwarded chat metadata."""
    try:
        chat=await asyncio.wait_for(client.get_chat(chat_id),30)
        data=wire(chat.model_dump(mode='python'))
    except Exception:
        raise HTTPException(502,'Не удалось получить название исходного чата MAX')
    title=data.get('title') or data.get('display_name')
    if not title:
        raise HTTPException(422,'У исходного чата нет доступного названия')
    if target=='chat':
        link=data.get('link')
        if isinstance(link,str):
            try:
                p=validate_link(link)
                if p.hostname=='max.ru':return title,link
            except ValueError:pass
        return title,f'https://max.ru/c/{int(chat_id)}'
    for key in ('url','message_link','messageLink','public_link','publicLink'):
        value=message.get(key)
        if isinstance(value,str):
            try:p=validate_link(value)
            except ValueError:continue
            if p.hostname=='max.ru':return title,value
    token=web_message_link(chat_id,message_id).rsplit('/',1)[-1]
    link=data.get('link')
    if isinstance(link,str):
        try:
            p=validate_link(link);parts=p.path.strip('/').split('/')
            if len(parts)==1 and parts[0] and parts[0] not in ('join','joinchannel','c'):
                return title,'https://max.ru/'+parts[0]+'/'+token
        except ValueError:pass
    return title,f'https://max.ru/c/{int(chat_id)}/{token}'
