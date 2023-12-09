import asyncio
from unittest.mock import MagicMock, call
import pytest  # type: ignore
from python_websockets_server.websockets import (
    WebSockets,
    Opcode,
    CloseCode,
    Frame,
    Handler,
    create_frame,
    encode_frame,
    parse_frame,
    create_close_frame,
    encode_close_code,
)
from .shared import StreamWriterMockHelper, stream_reader, timeouted


# TODO: add unhappy path test cases


async def parse_frame_from_bytes(data: bytes) -> Frame:
    return await parse_frame(stream_reader(data))


class EchoHandler(Handler):
    async def on_open(self, ws: "WebSockets"):
        print("handler.open")

    async def on_message(self, ws: "WebSockets", message: str | bytes):
        await ws.send(message)

    async def on_error(self, ws: "WebSockets", e: Exception):
        print("handler.error", e.args)

    async def on_close(self, ws: "WebSockets"):
        print("handler.close")


async def ws_req_resp(frame: Frame) -> Frame:
    reader = stream_reader(encode_frame(frame))
    writer_h = StreamWriterMockHelper()

    await timeouted(WebSockets.run(reader, writer_h.writer, EchoHandler()))

    response = writer_h.data()
    return await parse_frame_from_bytes(response)


@pytest.mark.asyncio  # type: ignore
async def test_ws_response():
    payload = "hello"
    frame = await ws_req_resp(create_frame(Opcode.TEXT, payload))
    assert frame == Frame(
        fin=True,
        rsv1=False,
        rsv2=False,
        rsv3=False,
        opcode=Opcode.TEXT,
        masked=False,
        masking_key=b"",
        payload=payload.encode(),
    )

    payload = b"hello"
    frame = await ws_req_resp(create_frame(Opcode.BINARY, payload))
    assert frame == Frame(
        fin=True,
        rsv1=False,
        rsv2=False,
        rsv3=False,
        opcode=Opcode.BINARY,
        masked=False,
        masking_key=b"",
        payload=payload,
    )

    payload = b"\1 \2 \3 \4"
    frame = await ws_req_resp(create_frame(Opcode.PING, payload))
    assert frame == Frame(
        fin=True,
        rsv1=False,
        rsv2=False,
        rsv3=False,
        opcode=Opcode.PONG,
        masked=False,
        masking_key=b"",
        payload=payload,
    )

    frame = await ws_req_resp(create_close_frame(CloseCode.NORMAL_CLOSURE))
    assert frame == Frame(
        fin=True,
        rsv1=False,
        rsv2=False,
        rsv3=False,
        opcode=Opcode.CLOSE,
        masked=False,
        masking_key=b"",
        payload=encode_close_code(CloseCode.NORMAL_CLOSURE),
    )


@pytest.mark.asyncio  # type: ignore
async def test_ws_methods():
    handler = MagicMock(spec=EchoHandler)
    writer_h = StreamWriterMockHelper()
    reader = asyncio.StreamReader()

    ws = WebSockets(reader, writer_h.writer, handler)

    await ws.send("Hello")
    assert writer_h.data() == encode_frame(create_frame(Opcode.TEXT, "Hello"))

    await ws.ping("Hello")
    assert writer_h.data() == encode_frame(create_frame(Opcode.PING, "Hello"))

    await ws.close(CloseCode.ABNORMAL_CLOSURE)
    assert writer_h.data() == encode_frame(
        create_close_frame(CloseCode.ABNORMAL_CLOSURE)
    )


@pytest.mark.asyncio  # type: ignore
async def test_ws_handler():
    handler = MagicMock(spec=EchoHandler)

    writer_h = StreamWriterMockHelper()

    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame(create_frame(Opcode.TEXT, "hello")))
    reader.feed_data(encode_frame(create_frame(Opcode.BINARY, b"hello")))
    reader.feed_data(encode_frame(create_close_frame(CloseCode.NORMAL_CLOSURE)))

    ws = WebSockets(reader, writer_h.writer, handler)
    await timeouted(ws._handle())  # type: ignore

    assert handler.on_open.call_count == 1  # type: ignore
    assert handler.on_message.call_count == 2  # type: ignore
    assert handler.on_close.call_count == 1  # type: ignore
    assert handler.on_error.call_count == 0  # type: ignore

    assert handler.on_open.call_args_list == [call(ws)]  # type: ignore
    assert handler.on_message.call_args_list == [call(ws, "hello"), call(ws, b"hello")]  # type: ignore
    assert handler.on_close.call_args_list == [call(ws)]  # type: ignore


@pytest.mark.asyncio  # type: ignore
async def test_ws_handler_on_error():
    handler = MagicMock(spec=EchoHandler)

    writer_h = StreamWriterMockHelper()

    reader = asyncio.StreamReader()
    reader.feed_data(b"123123132")  # write invalid data

    ws = WebSockets(reader, writer_h.writer, handler)
    exc: Exception | None = None
    try:
        await timeouted(ws._handle())  # type: ignore
    except Exception as e:
        exc = e

    assert handler.on_open.call_count == 1  # type: ignore
    assert handler.on_message.call_count == 0  # type: ignore
    assert handler.on_close.call_count == 1  # type: ignore
    assert handler.on_error.call_count == 1  # type: ignore

    assert handler.on_error.call_args_list == [call(ws, exc)]  # type: ignore
