"""Chaves determinísticas e não reversíveis para identificadores de pessoa e matrícula."""

import hashlib
import hmac
from uuid import UUID


def hmac_uuid(key: bytes, value: str) -> UUID:
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
    return UUID(bytes=digest[:16])


def brand_family(brand: str) -> str:
    """Marca sem a modalidade: 'UniSaoPaulo_EAD' -> 'UniSaoPaulo'."""
    return brand.split("_", 1)[0]
