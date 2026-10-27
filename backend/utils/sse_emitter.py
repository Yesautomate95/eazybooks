# Add SSE Emitter class
import asyncio
import json
from typing import Dict, Any, AsyncGenerator


class SSEEmitter:
    def __init__(self):
        self._queue = asyncio.Queue()
        self._active = True

    async def emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an SSE event"""
        if self._active:
            await self._queue.put({
                "event": event_type,
                "data": json.dumps(data)
            })

    async def stream(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream events from the queue"""
        while self._active:
            try:
                event = await self._queue.get()
                yield event
            except asyncio.CancelledError:
                break

    def stop(self):
        """Stop the emitter"""
        self._active = False