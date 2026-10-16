"""PyMax 2.4.1 photo upload adapter: signed URLs need not contain photoIds."""
from types import MethodType
from urllib.parse import parse_qs, urlsplit

import aiohttp
from pymax import WebClient
from pymax.api.response import payload_item
from pymax.api.uploads.models import PhotoUploadResponse
from pymax.api.uploads.payloads import AttachPhotoPayload, UploadPayload
from pymax.exceptions import UploadError
from pymax.protocol import Opcode


class PhotoUploadError(UploadError):
    def __init__(self, stage):
        # Fixed labels only: URLs and upstream response bodies contain credentials.
        self.upload_stage = stage
        super().__init__('Photo upload failed')


def select_photo_token(result, url):
    model = PhotoUploadResponse.model_validate(result)
    ids = parse_qs(urlsplit(url).query).get('photoIds', [])
    if ids:
        # Preserve exact matching when the server supplied an ID. Never pick a
        # different photo if the upload response disagrees with that ID.
        token = model.photos[ids[0]].token
    elif len(model.photos) == 1:
        token = next(iter(model.photos.values())).token
    else:
        raise ValueError('Ambiguous photo upload response')
    if not token:
        raise ValueError('Empty photo token')
    return token


async def upload_photo(self, photo, profile=False):
    stage = 'request_url'
    try:
        frame = await self.app.invoke(Opcode.PHOTO_UPLOAD, payload=UploadPayload(profile=profile).to_payload())
        stage = 'parse_url'
        url = payload_item(frame, 'url', str)
        parsed = urlsplit(url or '')
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Invalid upload URL')
        stage = 'read_photo'
        kind = photo.validate_photo()
        if not kind:
            raise ValueError('Invalid photo')
        raw = await photo.read()
        form = aiohttp.FormData()
        form.add_field('file', raw, filename='image.' + kind[0], content_type=kind[1])
        stage = 'http_upload'
        async with aiohttp.ClientSession(proxy=self.app.config.proxy, timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(url, data=form, allow_redirects=False) as response:
                if response.status != 200:
                    raise PhotoUploadError('http_status')
                stage = 'decode_response'
                result = await response.json()
        stage = 'select_token'
        return AttachPhotoPayload(photo_token=select_photo_token(result, url))
    except PhotoUploadError:
        raise
    except Exception as exc:
        raise PhotoUploadError(stage) from exc


class CompatibleWebClient(WebClient):
    def _build_app(self):
        app = super()._build_app()
        # Apply on every runtime creation, including reconnects. Other upload
        # methods and their registered event handlers remain PyMax's originals.
        app.api.uploads.upload_photo = MethodType(upload_photo, app.api.uploads)
        return app
