from core.keys import hmac_uuid


def test_hmac_uuid() -> None:
    assert hmac_uuid(b"a" * 32, "x") == hmac_uuid(b"a" * 32, "x") != hmac_uuid(b"b" * 32, "x")
