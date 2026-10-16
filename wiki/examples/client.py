"""Python 3.10+, standard library only. No automatic retries for writes."""
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class APIError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(f'HTTP {status}: {detail}')


class MaxAPI:
    def __init__(self, base=None, key=None):
        self.base = (base or os.environ['MAX_API_URL']).rstrip('/')
        self.key = key or os.environ['MAX_API_KEY']

    def request(self, method, path, body=None, *, raw=None, content_type=None, binary=False, params=None):
        url = self.base + '/api/' + path.lstrip('/')
        if params:
            url += '?' + urlencode(params)
        headers = {'Authorization': 'Bearer ' + self.key}
        data = raw
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            content_type = 'application/json'
        if content_type:
            headers['Content-Type'] = content_type
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=150) as response:
                result = response.read()
                return result if binary else (json.loads(result) if result else None)
        except HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise APIError(exc.code, detail) from exc

    def form(self, method, path, fields, photo=None):
        boundary = 'MaxWiki' + secrets.token_hex(16)
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        if photo:
            source = Path(photo)
            mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}[source.suffix.lower()]
            # Fixed wire filename: avoid injecting a user-supplied filename into headers.
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="photo{source.suffix.lower()}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
            parts.extend([source.read_bytes(), b'\r\n'])
        parts.append(f'--{boundary}--\r\n'.encode())
        return self.request(method, path, raw=b''.join(parts), content_type='multipart/form-data; boundary=' + boundary)

    def chats(self):
        marker = None
        seen = set()
        while True:
            page = self.request('GET', 'chats', params={'marker': marker} if marker else None)
            yield from page['chats']
            marker = page['next_marker']
            if not marker:
                if not page['complete']:
                    raise RuntimeError(page.get('warning') or 'Полнота списка не подтверждена')
                return
            if marker in seen:
                raise RuntimeError('Повторяющийся маркер')
            seen.add(marker)

    def send(self, chat_id, text, reply_to=None):
        return self.request('POST', 'messages', {'chat_id': str(chat_id), 'text': text, 'reply_to': str(reply_to) if reply_to is not None else None})

    def photo(self, chat_id, filename, text=''):
        return self.form('POST', 'messages/photo', {'chat_id': str(chat_id), 'text': text}, filename)

    def save(self, payload, photo=None, identifier=None):
        return self.form('PUT' if identifier else 'POST', 'saved-messages' + ('/' + identifier if identifier else ''), {'payload': json.dumps(payload, ensure_ascii=False)}, photo)


if __name__ == '__main__':
    api = MaxAPI()
    print(api.request('GET', 'status'))
    for chat in api.chats():
        print(chat['id'], chat['type'], chat.get('display_name', chat.get('title')))
