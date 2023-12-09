import socketserver
import asyncio
from collections import deque
import json
import logging
import os
from http.server import SimpleHTTPRequestHandler

from python_websockets_server.server import Server
from python_websockets_server.websockets import Handler, WebSockets

logging.basicConfig(level=logging.INFO)
logging.disable(logging.DEBUG)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


Item = dict[str, str]


class ChatHistory:
    def __init__(self, items: list[Item], maxsize: int = 10):
        self.queue = deque(items, maxsize)

    def append(self, item: Item):
        self.queue.append(item)

    def to_list(self) -> list[Item]:
        return list(self.queue)


history = ChatHistory(
    [
        {"date": "2023-10-13T10:24:05.045Z", "message": "Hello!"},
        {"date": "2023-10-13T10:24:06.045Z", "message": "Hi"},
        {"date": "2023-10-13T10:24:07.045Z", "message": "How are you?"},
    ]
)


class WebChatHandler(Handler):
    def __init__(self):
        self.clients: dict[int, WebSockets] = {}

    async def on_open(self, ws: WebSockets):
        logger.info("handler.open")
        await ws.send(json.dumps(history.to_list()))
        self.clients[hash(ws)] = ws

    async def on_message(self, ws: "WebSockets", message: str | bytes):
        logger.info(f"handler.message: {message}")
        try:
            dec_message = json.loads(message)
        except:
            logger.info("handler.message: fail to decode message")
            return
        logger.info(f"handler.message.decoded: {dec_message}")
        history.append(dec_message)
        for client in self.clients.values():
            await client.send(json.dumps([dec_message]))

    async def on_error(self, ws: "WebSockets", e: Exception):
        logger.info("handler.error", e.args)

    async def on_close(self, ws: "WebSockets"):
        logger.info("handler.close")
        del self.clients[hash(ws)]


def serve_static(host: str, port: int):
    dir = os.path.dirname(__file__)
    static_directory = dir + "/static"
    os.chdir(static_directory)

    # Start the server
    with socketserver.TCPServer((host, port), SimpleHTTPRequestHandler) as server:
        logger.info(f"Serving static files http://{host}:{port}")
        server.serve_forever()


async def serve_ws(host: str, port: int):
    server = Server(host, port, handler=WebChatHandler())
    await server.start()


async def main():
    host = "127.0.0.1"
    port = 8000

    # if you have changed the address of the websocket server,
    # change it in the static/app.js too
    ws_host = "127.0.0.1"
    ws_port = 8080

    print(
        f"\nOpen http://{host}:{port} in different browser tabs or\n"
        "in different browsers and try to send something to the chat.\n"
    )
    await asyncio.gather(
        asyncio.to_thread(serve_static, host, port),
        serve_ws(ws_host, ws_port),
    )


if __name__ == "__main__":
    asyncio.run(main())
