import asyncio
from typing import Any, Awaitable
from unittest.mock import MagicMock


def stream_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    return reader


async def timeouted(fn: Awaitable[Any], t: float = 0.1):
    try:
        await asyncio.wait_for(fn, t)
    except asyncio.exceptions.TimeoutError:
        pass


class StreamWriterMockHelper:
    def __init__(self):
        self.writer = MagicMock(spec=asyncio.StreamWriter)

    def data(self) -> bytes:
        data = b"".join([v.args[0] for v in self.writer.write.call_args_list])  # type: ignore
        self.writer.reset_mock()
        return data
