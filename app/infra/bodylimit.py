"""Teto de corpo para a API inteira: pelo Content-Length (413 antes de ler) e, sem ele (chunked),
contando os bytes recebidos (413 assim que passa do teto; a rota não vê o resto do corpo)."""

from typing import Any

MAX_BODY_BYTES = 64 * 1024
_BODY = b'{"detail":"corpo grande demais"}'


async def _reject(send: Any) -> None:
    headers = [(b"content-type", b"application/json")]
    headers.append((b"content-length", str(len(_BODY)).encode()))
    await send({"type": "http.response.start", "status": 413, "headers": headers})
    await send({"type": "http.response.body", "body": _BODY})


class BodyLimit:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for k, v in scope["headers"]:
            if k == b"content-length" and (not v.isdigit() or int(v) > MAX_BODY_BYTES):
                await _reject(send)
                return
        seen, rejected, started = 0, False, False

        async def limited_receive() -> Any:
            nonlocal seen, rejected
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > MAX_BODY_BYTES and not rejected:
                    rejected = True
                    if not started:
                        await _reject(send)
                    return {"type": "http.disconnect"}  # a rota para de ler e não responde
            return msg

        async def guarded_send(msg: Any) -> None:
            nonlocal started
            if rejected:
                return  # o 413 já foi enviado
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not rejected:
                raise  # depois do 413, a rota só falha por ter perdido o corpo
