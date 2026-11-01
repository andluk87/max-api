"""Yandex AI Studio Text Generation API; credentials never enter article logs."""
import asyncio
import json
import os
import tempfile
from pathlib import Path

import aiohttp

ENDPOINT = 'https://ai.api.cloud.yandex.net/foundationModels/v1/completion'


class KeyStore:
    def __init__(self, directory):
        self.path = Path(directory) / 'news-yandex.key'

    def configured(self):
        return self.path.is_file() and self.path.stat().st_size > 0

    def save(self, key):
        key = key.strip()
        if not 1 <= len(key) <= 1024 or any(ch.isspace() for ch in key):
            raise ValueError('Укажите API-ключ Яндекс без пробелов, не ID ключа')
        fd, temporary = tempfile.mkstemp(prefix='.news-key-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(key)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read(self):
        if not self.configured():
            raise ValueError('Сначала сохраните API-ключ Яндекс в настройках ИИ')
        return self.path.read_text().strip()

    def delete(self):
        self.path.unlink(missing_ok=True)


def normalize_response(payload):
    if not isinstance(payload, dict):
        raise ValueError('Яндекс вернул ответ неверного формата')
    result = payload.get('result', payload)
    alternatives = result.get('alternatives', []) if isinstance(result, dict) else []
    if not isinstance(alternatives, list) or not alternatives or not isinstance(alternatives[0], dict):
        raise ValueError('Яндекс не вернул вариант ответа модели')
    alternative = alternatives[0]
    status = alternative.get('status')
    if status == 'ALTERNATIVE_STATUS_CONTENT_FILTER':
        raise ValueError('Яндекс остановил генерацию фильтром содержимого; публикация не подготовлена')
    if status not in ('ALTERNATIVE_STATUS_FINAL', 'ALTERNATIVE_STATUS_TRUNCATED_FINAL'):
        raise ValueError('Яндекс не подтвердил завершение генерации')
    message = alternative.get('message')
    text = message.get('text') if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Яндекс вернул пустой ответ модели')
    return {'provider': 'yandex', 'response': text,
            'done_reason': 'length' if status == 'ALTERNATIVE_STATUS_TRUNCATED_FINAL' else 'stop'}


async def complete(key, folder_id, model, prompt, schema, max_tokens):
    if not folder_id:
        raise ValueError('Укажите Folder ID каталога Yandex Cloud в настройках ИИ')
    body = {
        'modelUri': f'gpt://{folder_id}/{model}',
        'completionOptions': {'stream': False, 'temperature': 0.1, 'maxTokens': str(max_tokens)},
        'messages': [{'role': 'system', 'text': 'Ты редактор новостей. Не выдумывай факты. Отвечай только JSON по указанной схеме.'},
                     {'role': 'user', 'text': prompt}],
        'jsonSchema': {'schema': schema},
    }
    headers = {'Authorization': 'Api-Key ' + key, 'Content-Type': 'application/json',
               'x-folder-id': folder_id, 'x-data-logging-enabled': 'false'}
    errors = {400:'Проверьте Folder ID, модель и параметры запроса.',
              401:'API-ключ недействителен или истёк.',
              403:'Проверьте права сервисного аккаунта, каталог и биллинг Yandex Cloud.',
              404:'Модель не найдена или недоступна в указанном каталоге.',
              429:'Превышена квота или частота запросов. Повторите позже.'}
    try:
        async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=120)) as session:
            async with session.post(ENDPOINT, headers=headers, json=body, allow_redirects=False) as response:
                # Never echo upstream bodies/headers: they may include credentials.
                if response.status != 200:
                    raise ValueError(f'Яндекс API: HTTP {response.status}. ' + errors.get(response.status,'Сервис временно недоступен. Повторите позже.'))
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024:
                        raise ValueError('Яндекс вернул слишком большой ответ')
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError('Яндекс вернул некорректный JSON') from exc
                return normalize_response(payload)
    except asyncio.TimeoutError as exc:
        raise ValueError('Яндекс не ответил за 120 секунд. Повторите позже.') from exc
    except aiohttp.ClientError as exc:
        raise ValueError('Не удалось подключиться к Яндекс API. Проверьте DNS, HTTPS и доступ сервера к ai.api.cloud.yandex.net.') from exc
