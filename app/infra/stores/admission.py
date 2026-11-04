import math
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from app.config import AppSettings
from app.infra.redis import RedisClient
from core.clock import wall_now
from core.rkey import RKey, rkey


def _now() -> float:
    return wall_now().timestamp()


# Janela deslizante por contador: total = anterior x fração restante + atual (janelas de 60 s).
# `now` vem do relógio injetado (último ARGV). Reserva e acerto são atômicos.
# shortcut: retry_after = fim da janela atual (conservador); estimativa > capacidade nunca cabe
# e é recusada para sempre, o chamador deve limitar a estimativa.
_RESERVE = """
local now = tonumber(ARGV[#ARGV])
local win = math.floor(now / 60)
local cur = tonumber(redis.call('HGET', KEYS[1], 'win') or '-1')
local h = redis.call('HMGET', KEYS[1], 'req', 'tok', 'preq', 'ptok')
local req, tok = tonumber(h[1]) or 0, tonumber(h[2]) or 0
local preq, ptok = tonumber(h[3]) or 0, tonumber(h[4]) or 0
if cur == win - 1 then
  preq, ptok, req, tok = req, tok, 0, 0
elseif cur ~= win then
  preq, ptok, req, tok = 0, 0, 0, 0
end
local w = (60 - (now - win * 60)) / 60
local er, et = tonumber(ARGV[1]), tonumber(ARGV[2])
if preq * w + req + er > tonumber(ARGV[3]) or ptok * w + tok + et > tonumber(ARGV[4]) then
  redis.call('HSET', KEYS[1], 'win', win, 'req', req, 'tok', tok, 'preq', preq, 'ptok', ptok)
  redis.call('EXPIRE', KEYS[1], 180)
  return {0, math.max(1, math.ceil((win + 1) * 60 - now))}
end
redis.call('HSET', KEYS[1], 'win', win, 'req', req + er, 'tok', tok + et,
  'preq', preq, 'ptok', ptok)
redis.call('EXPIRE', KEYS[1], 180)
redis.call('SET', KEYS[2], win .. ':' .. er .. ':' .. et, 'EX', 180)
return {1, 0}
"""

_SETTLE = """
local v = redis.call('GET', KEYS[2])
if not v then return 0 end
redis.call('DEL', KEYS[2])
local win, er, et = string.match(v, '(%d+):(%d+):(%d+)')
local cur = redis.call('HGET', KEYS[1], 'win')
local dr, dt = tonumber(ARGV[1]) - tonumber(er), tonumber(ARGV[2]) - tonumber(et)
if cur == win then
  redis.call('HINCRBY', KEYS[1], 'req', dr)
  redis.call('HINCRBY', KEYS[1], 'tok', dt)
elseif cur == tostring(tonumber(win) + 1) then
  redis.call('HINCRBY', KEYS[1], 'preq', dr)
  redis.call('HINCRBY', KEYS[1], 'ptok', dt)
else
  return 0  -- janela já saiu do alcance: nada a acertar
end
return 1
"""

_UTIL = """
local now = tonumber(ARGV[#ARGV])
local win = math.floor(now / 60)
local cur = tonumber(redis.call('HGET', KEYS[1], 'win') or '-1')
local h = redis.call('HMGET', KEYS[1], 'req', 'tok', 'preq', 'ptok')
local req, tok = tonumber(h[1]) or 0, tonumber(h[2]) or 0
local preq, ptok = tonumber(h[3]) or 0, tonumber(h[4]) or 0
if cur == win - 1 then
  preq, ptok, req, tok = req, tok, 0, 0
elseif cur ~= win then
  preq, ptok, req, tok = 0, 0, 0, 0
end
local w = (60 - (now - win * 60)) / 60
local ur = (preq * w + req) / math.max(1, tonumber(ARGV[1]))
local ut = (ptok * w + tok) / math.max(1, tonumber(ARGV[2]))
local u = math.max(ur, ut)
return math.floor(u * 1000)
"""

_ACQUIRE = """
local now = tonumber(ARGV[#ARGV])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[1])
return 1
"""


@dataclass(frozen=True)
class Reservation:
    ok: bool
    retry_after_s: int | None
    ticket: str | None


class AdmissionBucket:
    """Balde global RPM+TPM de um modelo: capacidade = quota_fraction x cota."""

    def __init__(
        self, redis: RedisClient, settings: AppSettings, model: str,
        *, clock: Callable[[], float] = _now,
    ) -> None:  # fmt: skip
        self._clock = clock
        q = settings.slo.quotas[model]
        f = settings.slo.admission.quota_fraction
        self._cap = (math.floor(q.rpm * f), math.floor(q.tpm * f))
        self._redis = redis
        self._model = model

    def _ticket_key(self, ticket: str) -> RKey:
        return rkey("bucket", "global", "-", f"{self._model}:t:{ticket}")

    async def reserve(self, est_requests: int, est_tokens: int) -> Reservation:
        ticket = uuid4().hex
        keys = [rkey("bucket", "global", "-", self._model), self._ticket_key(ticket)]
        ok, wait = await self._redis.eval(
            _RESERVE, keys, [est_requests, est_tokens, *self._cap, self._clock()]
        )
        return Reservation(True, None, ticket) if ok else Reservation(False, int(wait), None)

    async def utilization(self) -> float:
        """Fração usada da capacidade (a maior entre RPM e TPM), sem reservar nada."""
        per_mille = await self._redis.eval(
            _UTIL, [rkey("bucket", "global", "-", self._model)], [*self._cap, self._clock()]
        )
        return int(per_mille) / 1000

    async def settle(self, ticket: str, real_requests: int, real_tokens: int) -> None:
        keys = [rkey("bucket", "global", "-", self._model), self._ticket_key(ticket)]
        await self._redis.eval(_SETTLE, keys, [real_requests, real_tokens])


class TurnSlots:
    """Vaga por turno: ZSET com score = expiração; o lease vencido some no próximo acquire."""

    def __init__(
        self, redis: RedisClient, settings: AppSettings, *, clock: Callable[[], float] = _now
    ) -> None:
        self._clock = clock
        self._redis = redis
        self._cap = settings.slo.turn_slots
        self._key = rkey("slots", "global", "-", "pool")

    async def acquire(self, ticket: str, ttl_s: float) -> bool:
        return bool(
            await self._redis.eval(_ACQUIRE, [self._key], [ticket, self._cap, ttl_s, self._clock()])
        )

    async def release(self, ticket: str) -> None:
        await self._redis.eval("return redis.call('ZREM', KEYS[1], ARGV[1])", [self._key], [ticket])
