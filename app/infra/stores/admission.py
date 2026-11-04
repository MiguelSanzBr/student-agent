import math
from dataclasses import dataclass
from uuid import uuid4

from app.config import AppSettings
from app.infra.redis import RedisClient
from core.rkey import RKey, rkey

# Janela fixa de 60 s (relógio do Redis). Reserva e acerto são atômicos.
# shortcut: janela fixa admite rajada de até 2x na virada do minuto; janela deslizante se apertar.
_RESERVE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1e6
local win = math.floor(now / 60)
local cur = tonumber(redis.call('HGET', KEYS[1], 'win') or '-1')
if cur ~= win then
  redis.call('DEL', KEYS[1])
  redis.call('HSET', KEYS[1], 'win', win, 'req', 0, 'tok', 0)
  redis.call('EXPIRE', KEYS[1], 120)
end
local req = tonumber(redis.call('HGET', KEYS[1], 'req'))
local tok = tonumber(redis.call('HGET', KEYS[1], 'tok'))
local er, et = tonumber(ARGV[1]), tonumber(ARGV[2])
if req + er > tonumber(ARGV[3]) or tok + et > tonumber(ARGV[4]) then
  return {0, math.max(1, math.ceil((win + 1) * 60 - now))}
end
redis.call('HINCRBY', KEYS[1], 'req', er)
redis.call('HINCRBY', KEYS[1], 'tok', et)
redis.call('SET', KEYS[2], win .. ':' .. er .. ':' .. et, 'EX', 120)
return {1, 0}
"""

_SETTLE = """
local v = redis.call('GET', KEYS[2])
if not v then return 0 end
redis.call('DEL', KEYS[2])
local win, er, et = string.match(v, '(%d+):(%d+):(%d+)')
if redis.call('HGET', KEYS[1], 'win') ~= win then return 0 end  -- janela já virou: nada a acertar
redis.call('HINCRBY', KEYS[1], 'req', tonumber(ARGV[1]) - tonumber(er))
redis.call('HINCRBY', KEYS[1], 'tok', tonumber(ARGV[2]) - tonumber(et))
return 1
"""

_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1e6
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

    def __init__(self, redis: RedisClient, settings: AppSettings, model: str) -> None:
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
        ok, wait = await self._redis.eval(_RESERVE, keys, [est_requests, est_tokens, *self._cap])
        return Reservation(True, None, ticket) if ok else Reservation(False, int(wait), None)

    async def settle(self, ticket: str, real_requests: int, real_tokens: int) -> None:
        keys = [rkey("bucket", "global", "-", self._model), self._ticket_key(ticket)]
        await self._redis.eval(_SETTLE, keys, [real_requests, real_tokens])


class TurnSlots:
    """Vaga por turno: ZSET com score = expiração; o lease vencido some no próximo acquire."""

    def __init__(self, redis: RedisClient, settings: AppSettings) -> None:
        self._redis = redis
        self._cap = settings.slo.turn_slots
        self._key = rkey("slots", "global", "-", "pool")

    async def acquire(self, ticket: str, ttl_s: float) -> bool:
        return bool(await self._redis.eval(_ACQUIRE, [self._key], [ticket, self._cap, ttl_s]))

    async def release(self, ticket: str) -> None:
        await self._redis.eval("return redis.call('ZREM', KEYS[1], ARGV[1])", [self._key], [ticket])
