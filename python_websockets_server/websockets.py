import asyncio
from dataclasses import dataclass
import enum
import os
import struct
from typing import Protocol

from .logger import logger


# fmt: off
class _Mask(enum.IntEnum):
    FIN    = 0b10000000
    RSV1   = 0b01000000
    RSV2   = 0b00100000
    RSV3   = 0b00010000
    OPCODE = 0b00001111
    MASK   = 0b10000000
    P_LEN  = 0b01111111
# fmt: on


class CloseCode(enum.IntEnum):
    NORMAL_CLOSURE = 1000
    GOING_AWAY = 1001
    PROTOCOL_ERROR = 1002
    UNSUPPORTED_DATA = 1003
    NO_STATUS_RCVD = 1005
    ABNORMAL_CLOSURE = 1006
    INVALID_FRAME_PAYLOAD_DATA = 1007
    POLICY_VIOLATION = 1008
    MESSAGE_TOO_BIG = 1009
    MANDATORY_EXT = 1010
    INTERNAL_ERROR = 1011
    SERVICE_RESTART = 1012
    TRY_AGAIN_LATER = 1013
    BAD_GATEWAY = 1014
    TLS_HANDSHAKE = 1015


class Opcode(enum.IntEnum):
    CONTINUATION = 0x0  # TODO
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA


class WebSocketException(Exception):
    def __init__(self, code: CloseCode, message: str = ""):
        super().__init__(code, message)
        self.code = code


@dataclass(frozen=True)
class Frame:
    fin: bool
    rsv1: bool
    rsv2: bool
    rsv3: bool
    opcode: Opcode
    masked: bool
    masking_key: bytes
    payload: bytes

    def is_control(self) -> bool:
        # Currently defined opcodes for control frames
        # include 0x8 (Close), 0x9 (Ping), and 0xA (Pong).
        return self.opcode in (Opcode.CLOSE, Opcode.PING, Opcode.PONG)

    def is_fragmented(self) -> bool:
        return not self.fin

    def __post_init__(self):
        if self.opcode == Opcode.TEXT:
            # try to decode text data
            try:
                self.payload.decode()
            except UnicodeDecodeError:
                # raise ProtocolException("invalid text data")
                raise WebSocketException(
                    CloseCode.INVALID_FRAME_PAYLOAD_DATA, "invalid text data"
                )

        # All control frames MUST have a payload length of 125 bytes or less
        # and MUST NOT be fragmented.
        if self.is_control():
            if len(self.payload) > 125:
                raise WebSocketException(
                    CloseCode.PROTOCOL_ERROR,
                    "the control frames MUST have a payload length of 125 bytes or less",
                )
            if self.is_fragmented():
                raise WebSocketException(
                    CloseCode.PROTOCOL_ERROR,
                    "the control frames MUST NOT be fragmented",
                )

        if self.rsv1 or self.rsv2 or self.rsv3:
            raise WebSocketException(
                CloseCode.PROTOCOL_ERROR, "rsv1, rsv2, rsv3 must be 0"
            )


def create_frame(opcode: Opcode, payload: str | bytes) -> Frame:
    return Frame(
        fin=True,
        rsv1=False,
        rsv2=False,
        rsv3=False,
        opcode=opcode,
        masked=False,
        masking_key=b"",
        payload=payload if isinstance(payload, bytes) else payload.encode(),
    )


def encode_close_code(code: CloseCode) -> bytes:
    return struct.pack("!H", int(code))


def create_close_frame(code: CloseCode) -> Frame:
    return create_frame(Opcode.CLOSE, encode_close_code(code))


def _apply_mask(payload: bytes, masking_key: bytes, p_len: int) -> bytes:
    return bytes([payload[i] ^ masking_key[i % 4] for i in range(p_len)])


async def parse_frame(reader: asyncio.StreamReader) -> Frame:
    # see https://datatracker.ietf.org/doc/html/rfc6455
    #
    # 0                   1                   2                   3
    # 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    # +-+-+-+-+-------+-+-------------+-------------------------------+
    # |F|R|R|R| opcode|M| Payload len |    Extended payload length    |
    # |I|S|S|S|  (4)  |A|     (7)     |             (16/64)           |
    # |N|V|V|V|       |S|             |   (if payload len==126/127)   |
    # | |1|2|3|       |K|             |                               |
    # +-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
    # |     Extended payload length continued, if payload len == 127  |
    # + - - - - - - - - - - - - - - - +-------------------------------+
    # |                               |Masking-key, if MASK set to 1  |
    # +-------------------------------+-------------------------------+
    # | Masking-key (continued)       |          Payload Data         |
    # +-------------------------------- - - - - - - - - - - - - - - - +
    # :                     Payload Data continued ...                :
    # + - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - +
    # |                     Payload Data continued ...                |
    # +---------------------------------------------------------------+
    data = await reader.read(2)
    h1, h2 = struct.unpack("!BB", data)

    # FIN:  1 bit
    #
    # Indicates that this is the final fragment in a message.  The first
    # fragment MAY also be the final fragment.
    # data = await reader.read(1)
    # (h1,) = struct.unpack("!B", data)
    # fin = bool(h1 & 0b10000000)
    fin = bool(h1 & _Mask.FIN)

    # RSV1, RSV2, RSV3:  1 bit each
    #
    # MUST be 0 unless an extension is negotiated that defines meanings
    # for non-zero values.  If a nonzero value is received and none of
    # the negotiated extensions defines the meaning of such a nonzero
    # value, the receiving endpoint MUST _Fail the WebSocket
    # Connection_.
    rsv1 = bool(h1 & _Mask.RSV1)
    rsv2 = bool(h1 & _Mask.RSV2)
    rsv3 = bool(h1 & _Mask.RSV3)

    # Opcode:  4 bits
    #
    # Defines the interpretation of the "Payload data".  If an unknown
    # opcode is received, the receiving endpoint MUST _Fail the
    # WebSocket Connection_.  The following values are defined.
    #
    # *  %x0 denotes a continuation frame
    # *  %x1 denotes a text frame
    # *  %x2 denotes a binary frame
    # *  %x3-7 are reserved for further non-control frames
    # *  %x8 denotes a connection close
    # *  %x9 denotes a ping
    # *  %xA denotes a pong
    # *  %xB-F are reserved for further control frames
    opcode = Opcode(h1 & _Mask.OPCODE)

    # is_continuation_frame = opcode == 0x0  # TODO
    # is_text_frame = opcode == 0x1
    # is_binary_frame = opcode == 0x2
    # is_connection_close = opcode == 0x8
    # is_ping = opcode == 0x9
    # is_pong = opcode == 0xA
    # print(
    #     "opcode",
    #     bin(opcode),
    #     is_continuation_frame,
    #     is_text_frame,
    #     is_binary_frame,
    #     is_connection_close,
    #     is_ping,
    #     is_pong,
    # )

    # Mask:  1 bit
    #
    # Defines whether the "Payload data" is masked.  If set to 1, a
    # masking key is present in masking-key, and this is used to unmask
    # the "Payload data" as per Section 5.3.  All frames sent from
    # client to server have this bit set to 1.
    mask = h2 & _Mask.MASK
    masked = bool(mask)

    # Payload length:  7 bits, 7+16 bits, or 7+64 bits
    #
    # The length of the "Payload data", in bytes: if 0-125, that is the
    # payload length.  If 126, the following 2 bytes interpreted as a
    # 16-bit unsigned integer are the payload length.  If 127, the
    # following 8 bytes interpreted as a 64-bit unsigned integer (the
    # most significant bit MUST be 0) are the payload length.  Multibyte
    # length quantities are expressed in network byte order.
    p_len = h2 & _Mask.P_LEN

    if p_len == 126:
        data = await reader.read(2)
        p_len = struct.unpack("!H", data)
        assert isinstance(p_len, int)
    elif p_len == 127:
        data = await reader.read(8)
        p_len = struct.unpack("!Q", data)
        assert isinstance(p_len, int)

    # Masking-key:  0 or 4 bytes
    #
    # All frames sent from the client to the server are masked by a
    # 32-bit value that is contained within the frame.  This field is
    # present if the mask bit is set to 1 and is absent if the mask bit
    # is set to 0.  See Section 5.3 for further information on client-
    # to-server masking.
    masking_key = b""
    if masked:
        masking_key = await reader.read(4)

    # Payload data:  (x+y) bytes
    #
    # The "Payload data" is defined as "Extension data" concatenated
    # with "Application data".
    payload = await reader.read(p_len)
    if masked:
        # To convert masked data into unmasked data, or vice versa, the following
        # algorithm is applied.  The same algorithm applies regardless of the
        # direction of the translation, e.g., the same steps are applied to
        # mask the data as to unmask the data.

        # Octet i of the transformed data ("transformed-octet-i") is the XOR of
        # octet i of the original data ("original-octet-i") with octet at index
        # i modulo 4 of the masking key ("masking-key-octet-j"):

        #     j                   = i MOD 4
        #     transformed-octet-i = original-octet-i XOR masking-key-octet-j

        # The payload length, indicated in the framing as frame-payload-length,
        # does NOT include the length of the masking key.  It is the length of
        # the "Payload data", e.g., the number of bytes following the masking
        # key.
        payload = _apply_mask(payload, masking_key, p_len)

    return Frame(
        fin=fin,
        rsv1=rsv1,
        rsv2=rsv2,
        rsv3=rsv3,
        opcode=opcode,
        masked=masked,
        masking_key=masking_key,
        payload=payload,
    )


# TODO: parse code reason
def parse_close_frame_payload(payload: bytes) -> CloseCode | None:
    if len(payload) < 2:
        raise WebSocketException(CloseCode.PROTOCOL_ERROR, "invalid close payload")
    if len(payload) == 0:
        return None

    (code,) = struct.unpack("!H", payload[:2])
    assert isinstance(code, int), type(code)
    return CloseCode(code)


def encode_frame(frame: Frame) -> bytes:
    buf = bytearray()

    h1 = (
        (_Mask.FIN if frame.fin else 0)
        | (_Mask.RSV1 if frame.rsv1 else 0)
        | (_Mask.RSV2 if frame.rsv2 else 0)
        | (_Mask.RSV3 if frame.rsv3 else 0)
        | int(frame.opcode)
    )

    buf.extend(struct.pack("!B", h1))

    h2 = _Mask.MASK if frame.masked else 0

    p_len = len(frame.payload)
    if p_len < 126:
        buf.extend(struct.pack("!B", h2 | p_len))
    elif p_len < 65536:
        buf.extend(struct.pack("!B", h2 | 126))
        buf.extend(struct.pack("!H", p_len))
    else:
        buf.extend(struct.pack("!B", h2 | 127))
        buf.extend(struct.pack("!Q", p_len))

    if frame.masked:
        masking_key = os.urandom(4)
        buf.extend(masking_key)
        payload = masking_key + _apply_mask(frame.payload, masking_key, p_len)
    else:
        payload = frame.payload

    buf += payload

    return bytes(buf)


class Handler(Protocol):
    async def on_open(self, ws: "WebSockets"):
        ...

    async def on_message(self, ws: "WebSockets", message: str | bytes):
        ...

    async def on_error(self, ws: "WebSockets", e: Exception):
        ...

    async def on_close(self, ws: "WebSockets"):
        ...


class WebSockets:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handler: Handler,
    ):
        # self._handler = handler
        self._reader = reader
        self._writer = writer
        self._handler = handler
        self._client_handlers = {
            Opcode.CONTINUATION: self._on_continuation,
            Opcode.TEXT: self._on_text,
            Opcode.BINARY: self._on_binary,
            Opcode.PING: self._on_ping,
            Opcode.PONG: self._on_pong,
            Opcode.CLOSE: self._on_close,
        }

    @classmethod
    async def run(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handler: Handler,
    ):
        obj = cls(reader, writer, handler)
        await obj._handle()

    async def _on_continuation(self, frame: Frame):
        logger.debug("continuation")
        logger.warning("not implemented: on_continuation opcode")  # TODO

    async def _on_text(self, frame: Frame):
        logger.debug("text")
        await self._handler.on_message(self, frame.payload.decode())  # ON_MESSAGE

    async def _on_binary(self, frame: Frame):
        logger.debug("binary")
        await self._handler.on_message(self, frame.payload)  # ON_MESSAGE

    async def _on_ping(self, frame: Frame):
        logger.debug("ping")
        await self._send_frame(create_frame(Opcode.PONG, frame.payload))

    async def _on_pong(self, frame: Frame):
        logger.debug("pong")
        # do nothing? # TODO

    async def _on_close(self, frame: Frame):
        try:
            close_code = parse_close_frame_payload(frame.payload)
            logger.debug(f"close {close_code}")
        except Exception as e:
            logger.exception(e)

        await self.close()

    async def _send_frame(self, frame: Frame):
        logger.debug(f"resp frame {vars(frame)}")
        data = encode_frame(frame)
        logger.debug(f"resp data {data}")
        self._writer.write(data)
        await self._writer.drain()

    async def send(self, message: str | bytes):
        frame = create_frame(
            Opcode.BINARY if isinstance(message, bytes) else Opcode.TEXT, message
        )
        await self._send_frame(frame)

    async def ping(self, message: str | bytes):
        await self._send_frame(create_frame(Opcode.PING, message))

    async def close(self, code: CloseCode = CloseCode.NORMAL_CLOSURE):
        frame = create_close_frame(code)
        await self._send_frame(frame)
        await self._handler.on_close(self)  # OP_CLOSE

    async def _handle(self):
        await self._handler.on_open(self)  # OP_OPEN

        while True:
            try:
                frame = await parse_frame(self._reader)
                logger.debug(f"req frame {vars(frame)}")

                await self._client_handlers[frame.opcode](frame)
                if frame.opcode == Opcode.CLOSE:
                    break

            except WebSocketException as e:
                await self._handler.on_error(self, e)  # OP_ERROR
                await self.close(e.code)
                raise
            except Exception as e:
                await self._handler.on_error(self, e)  # OP_ERROR
                await self.close(CloseCode.INTERNAL_ERROR)
                raise
