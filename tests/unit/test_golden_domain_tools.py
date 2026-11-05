from evals.golden.domain_tools import drop_domain_tools

ROW = {"status": "fail", "cause": "roteamento", "reasons": []}


def test_domain_tool_reason_dropped_and_item_passes() -> None:
    r = drop_domain_tools(ROW | {"reasons": ["t1: ferramenta esperada não chamada: financeiro"]})
    assert (r["status"], r["reasons"], r["cause"]) == ("pass", [], None)


def test_other_reasons_keep_the_failure() -> None:
    reasons = ["t1: ferramenta esperada não chamada: lms", "t1: fato ausente: 3"]
    r = drop_domain_tools(ROW | {"reasons": reasons})
    assert (r["status"], r["reasons"]) == ("fail", ["t1: fato ausente: 3"])


def test_real_tool_name_untouched() -> None:
    row = ROW | {"reasons": ["t1: ferramenta esperada não chamada: get_lms_activity"]}
    assert drop_domain_tools(row) is row
