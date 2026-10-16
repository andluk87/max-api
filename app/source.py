"""Authenticated source-message reads and bounded public-only image downloads."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urljoin
import aiohttp
from aiohttp.resolver import ThreadedResolver
from fastapi import HTTPException
from .encoding import wire
from .media import make_photo, MAX_PHOTO_BYTES


class PublicResolver(ThreadedResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await super().resolve(host, port, family)
        if not results or any(not ipaddress.ip_address(r['host']).is_global for r in results):
            raise ValueError('Non-public image host')
        return results


def image_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise ValueError('Invalid image URL')
    try: address = ipaddress.ip_address(p.hostname)
    except ValueError: address = None
    if address is not None and not address.is_global:
        raise ValueError('Non-public image host')
    return url


async def download_photo(url):
    try:
        connector = aiohttp.TCPConnector(resolver=PublicResolver())
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30), trust_env=False) as session:
            for _ in range(4):
                async with session.get(image_url(url), allow_redirects=False) as response:
                    if response.status in (301,302,303,307,308):
                        url=urljoin(url,response.headers.get('Location',''));continue
                    response.raise_for_status()
                    raw=bytearray()
                    async for part in response.content.iter_chunked(65536):
                        raw.extend(part)
                        if len(raw)>MAX_PHOTO_BYTES: raise HTTPException(413,'Фото больше 10 МиБ')
                    make_photo(bytes(raw))
                    return bytes(raw)
            raise ValueError('Too many redirects')
    except HTTPException: raise
    except Exception:
        raise HTTPException(502,'Не удалось скачать оригинал фото из MAX. Обновите историю и повторите.')


async def read_source(client, chat_id, message_id):
    try:
        message=await asyncio.wait_for(client.get_message(chat_id=chat_id,message_id=message_id),30)
    except Exception:
        raise HTTPException(502,'MAX не вернул исходное сообщение')
    if message is None: raise HTTPException(404,'Исходное сообщение не найдено')
    return wire(message.model_dump(mode='python'))


def photo_attachments(message):
    return [a for a in message.get('attaches',[]) if str(a.get('type',a.get('_type',''))).upper()=='PHOTO']


async def source_photo(message, index=0):
    photos=photo_attachments(message)
    if index<0 or index>=len(photos): raise HTTPException(404,'Фото не найдено')
    photo=photos[index]
    return await download_photo(photo.get('base_url') or photo.get('baseUrl') or '')
