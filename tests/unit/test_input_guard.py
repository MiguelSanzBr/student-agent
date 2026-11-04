import pytest

from app.guards.input import InputTooLong, normalize_input


def test_nfkc_and_invisible_removed():
    assert normalize_input("ｆａｔｕｒａ​ 12⁠") == "fatura 12"


def test_length_limit():
    assert normalize_input("a" * 2000) == "a" * 2000
    with pytest.raises(InputTooLong):
        normalize_input("a" * 2001)
