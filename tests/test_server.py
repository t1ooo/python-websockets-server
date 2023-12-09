import io
import pytest # type: ignore
from python_websockets_server.server import Server, parse_headers
from .shared import StreamWriterMockHelper, stream_reader, timeouted


# TODO: add unhappy path test cases


def prepare_headers(s: str) -> bytes:
    lines = filter(len, map(str.strip, s.split("\n")))
    s = "\r\n".join(lines) + "\r\n\r\n"
    return s.encode()


@pytest.mark.asyncio  # type: ignore
async def test_server_response():
    reader = stream_reader(
        prepare_headers(
            """
GET / HTTP/1.1
Host: localhost:8000
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: owW3TyUBC/ZYqlQ0O23rWg==
Sec-WebSocket-Version: 13
"""
        )
    )
    writer_h = StreamWriterMockHelper()

    server = Server()
    await timeouted(server._client_connected_cb(reader, writer_h.writer))  # type: ignore

    buf = io.BytesIO(writer_h.data())
    assert buf.readline() == b"HTTP/1.1 101 Switching Protocols\r\n"
    headers = parse_headers(buf.read())
    assert headers.get("Upgrade") == "websocket"
    assert headers.get("Connection") == "Upgrade"
    assert headers.get("Server") == "Async Websockets Server"
    assert headers.get("Sec-WebSocket-Accept") != None
    assert headers.get("Date") != None
