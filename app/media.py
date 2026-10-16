"""Validate uploaded image bytes without trusting extension or MIME header."""
import io
import warnings

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pymax import Photo

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000
FORMATS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}


def make_photo(raw: bytes) -> Photo:
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(413, 'Картинка должна быть не больше 10 МиБ')
    if not raw:
        raise HTTPException(422, 'Выберите непустую картинку')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                extension = FORMATS.get(image.format)
                if extension is None:
                    raise HTTPException(415, 'Поддерживаются JPEG, PNG и WebP')
                if image.width * image.height > MAX_PHOTO_PIXELS:
                    raise HTTPException(413, 'Разрешение картинки должно быть не больше 20 мегапикселей')
                image.verify()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, 'Слишком большое разрешение картинки')
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(415, 'Файл не является корректной картинкой JPEG, PNG или WebP')
    # Canonical name avoids trusting user-provided paths and extension.
    return Photo(raw=raw, name='image.' + extension)
