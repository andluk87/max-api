import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pymax.transport.websocket import WebSocketTransport
from app.transport import connect_websocket
from app.uploads import CompatibleWebClient


@pytest.mark.parametrize('proxy',[None,'http://localhost:8080'])
def test_websocket_honors_explicit_proxy(monkeypatch,proxy):
    async def run():
        fake = AsyncMock(return_value=object())
        monkeypatch.setattr('app.transport.connect',fake)
        transport = WebSocketTransport('wss://api.oneme.ru/websocket',proxy)
        await connect_websocket(transport)
        assert fake.call_args.kwargs['proxy'] == proxy
        assert fake.call_args.kwargs['origin'] == 'https://web.max.ru'
        assert fake.call_args.kwargs['open_timeout'] == 20
        assert transport.ws is fake.return_value
    asyncio.run(run())


def test_transport_adapter_is_applied_on_each_runtime(monkeypatch):
    transport = WebSocketTransport('wss://api.oneme.ru/websocket',None)
    app = SimpleNamespace(connection=SimpleNamespace(transport=transport),api=SimpleNamespace(uploads=SimpleNamespace()))
    monkeypatch.setattr('app.uploads.WebClient._build_app',lambda self:app)
    client = CompatibleWebClient()
    for _ in range(2):
        assert client._build_app().connection.transport.connect.__func__ is connect_websocket
