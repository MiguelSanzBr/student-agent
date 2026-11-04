"""Teto de corpo para a API inteira, pelo Content-Length (413 antes de ler o corpo)."""

from typing import Any

MAX_BODY_BYTES = 64 * 1024


class BodyLimit:
    # shortcut: só olha o cabeçalho; corpo chunked sem Content-Length passa — o proxy da frente
    # deve limitá-lo; upgrade para contar bytes no receive se a API for exposta direto.
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            for k, v in scope["headers"]:
                if k == b"content-length" and (not v.isdigit() or int(v) > MAX_BODY_BYTES):
                    body = b'{"detail":"corpo grande demais"}'
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 413,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
        await self.app(scope, receive, send)
