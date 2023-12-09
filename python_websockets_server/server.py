from http.client import HTTPMessage
from http.client import parse_headers as http_parse_headers
from io import BytesIO
from dataclasses import dataclass
import asyncio
from email.utils import formatdate
import hashlib
import base64
from http import HTTPStatus

from .logger import logger
from .websockets import Handler, WebSockets


def _generate_accept_key(key: str):
    magic_string = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    sha1 = hashlib.sha1()
    sha1.update((key + magic_string).encode())
    data = sha1.digest()
    return base64.b64encode(data).decode()


_Headers = HTTPMessage


def _headers_from_dict(d: dict[str, str]) -> _Headers:
    h = _Headers()
    for k, v in d.items():
        h[k] = v
    return h


def parse_headers(data: bytes) -> _Headers:
    buf = BytesIO(data)
    headers = http_parse_headers(buf)

    # split comma separated headers (like `Sec-WebSocket-Extensions: foo, bar; baz=2`) into individual items
    for key in headers.keys():
        vals = ", ".join(headers.get_all(key, [])).split(", ")  # type: ignore
        del headers[key]
        for val in vals:
            headers[key] = val

    return headers


@dataclass(frozen=True)
class _Response:
    status: HTTPStatus
    headers: _Headers
    body: bytes = b""


class HttpException(Exception):
    def __init__(self, status: HTTPStatus, message: str = ""):
        super().__init__(status, message)
        self.status = status


async def _accept(headers: _Headers) -> _Response:
    # TODO: 'Origin', 'http://0.0.0.0:8000'

    if "Upgrade" not in headers.get_all("Connection", []):  # type: ignore
        raise HttpException(HTTPStatus.UPGRADE_REQUIRED, "invalid header: Connection")

    if headers.get("Upgrade") != "websocket":
        raise HttpException(HTTPStatus.UPGRADE_REQUIRED, "invalid header: Upgrade")

    if headers.get("Sec-WebSocket-Version") != "13":
        raise HttpException(
            HTTPStatus.BAD_REQUEST, "invalid header: Sec-WebSocket-Version"
        )

    key = headers.get("Sec-WebSocket-Key", "")
    try:
        assert len(base64.b64decode(key.encode(), validate=True)) == 16
    except Exception:
        raise HttpException(HTTPStatus.BAD_REQUEST, "invalid header: Sec-WebSocket-Key")

    if headers.get("Sec-WebSocket-Extensions", None):
        logger.warning("not implemented: Sec-WebSocket-Extensions")  # TODO

    accept_key = _generate_accept_key(key)
    return _accept_response(accept_key)


def _accept_response(accept_key: str):
    headers = _headers_from_dict(
        {
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Accept": accept_key,
        }
    )
    status = HTTPStatus.SWITCHING_PROTOCOLS
    return _Response(status, headers)


def _reject_response(status: HTTPStatus):
    headers = _headers_from_dict(
        {
            "Connection": "close",
            "Content-Type": "text/plain; charset=utf-8",
        }
    )
    return _Response(status, headers, status.phrase.encode())


def _error_response(status: HTTPStatus):
    headers = _headers_from_dict(
        {
            "Content-Type": "text/plain; charset=utf-8",
        }
    )
    return _Response(status, headers, status.phrase.encode())


async def _write_response(resp: _Response, writer: asyncio.StreamWriter):
    # write headers
    headers = {
        "Date": formatdate(usegmt=True),
        "Server": "Async Websockets Server",
    }

    for k, v in headers.items():
        resp.headers[k] = v

    if len(resp.body) > 0:
        resp.headers["Content-Length"] = str(len(resp.body))

    logger.debug(f"response: {vars(resp)}")

    # write first line
    writer.write(f"HTTP/1.1 {resp.status.value} {resp.status.phrase}\r\n".encode())

    # write headers
    for key, val in resp.headers.items():
        writer.write(f"{key}: {val}\r\n".encode())

    # write new line before body
    writer.write(f"\r\n".encode())

    # write body
    writer.write(resp.body)

    await writer.drain()


def _validate_protocol(protocol: str):
    if protocol != "HTTP/1.1":
        raise HttpException(HTTPStatus.BAD_REQUEST, "invalid protocol")


def _validate_path(path: str):
    if path != "/":
        raise HttpException(HTTPStatus.NOT_FOUND, "invalid path")


def _validate_method(method: str):
    if method != "GET":
        raise HttpException(HTTPStatus.BAD_REQUEST, "invalid method")


async def _parse_start_line(data: bytes) -> tuple[str, str, str]:
    method, path, protocol = data.decode("iso-8859-1").rstrip("\r\n").split(" ")
    return method, path, protocol


class DefaultHandler(Handler):
    async def on_open(self, ws: WebSockets):
        pass

    async def on_message(self, ws: WebSockets, message: str | bytes):
        pass

    async def on_error(self, ws: WebSockets, e: Exception):
        pass

    async def on_close(self, ws: WebSockets):
        pass


class Server:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        handler: Handler = DefaultHandler(),
    ):
        self._host = host
        self._port = port
        self._handler = handler

    async def start(self):
        server = await asyncio.start_server(
            self._client_connected_cb, self._host, self._port
        )
        logger.info(f"start server: {self._host}:{self._port}")
        async with server:
            await server.serve_forever()

    async def _client_connected_cb(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        try:
            method, path, protocol = await _parse_start_line(await reader.readline())
            _validate_method(method)
            _validate_path(path)
            _validate_protocol(protocol)
            logger.debug(dict(method=method, path=path, protocol=protocol))

            headers = parse_headers(await reader.readuntil(b"\r\n\r\n"))
            logger.debug(f"{vars(headers)}")

            response = await _accept(headers)
            logger.debug("accept")

            await _write_response(response, writer)
            await WebSockets.run(reader, writer, self._handler)
        except HttpException as e:
            logger.exception(e)
            response = _reject_response(e.status)
            logger.debug("reject")
            await _write_response(response, writer)
        except Exception as e:
            logger.exception(e)
            response = _error_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            await _write_response(response, writer)
        finally:
            logger.debug("close writer")
            writer.close()
