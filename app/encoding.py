"""JSON-safe MAX payloads with lossless binary values and 64-bit IDs."""
import base64
from enum import Enum

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

ID_FIELDS = {'id', 'chat_id', 'sender', 'user_id', 'contact_id', 'message_id', 'reply_to'}


def wire(value):
    # JSON mode tries to decode bytes as UTF-8 before we can handle them.
    if isinstance(value, BaseModel):
        return wire(value.model_dump(mode='python'))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'encoding': 'base64', 'data': base64.b64encode(bytes(value)).decode('ascii')}
    if isinstance(value, Enum):
        return wire(value.value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [wire(item) for item in value]
    if isinstance(value, dict):
        return {str(key): str(item) if key in ID_FIELDS and type(item) is int else wire(item)
                for key, item in value.items()}
    # Handles datetime, UUID and other ordinary JSON-compatible scalar types.
    return jsonable_encoder(value)
