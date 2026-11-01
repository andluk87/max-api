"""Honor the configured MAX proxy explicitly on every WebSocket connection."""
from websockets.asyncio.client import connect


async def connect_websocket(self):
    # websockets otherwise auto-detects system proxies, unlike explicit None.
    self.ws = await connect(
        self.url,
        origin='https://web.max.ru',
        proxy=self.proxy,
        max_size=10 * 1024 * 1024,
        open_timeout=20,
    )
