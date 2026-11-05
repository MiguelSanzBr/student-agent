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


def test_with_domains_adds_the_domain_of_each_called_tool() -> None:
    from evals.golden.run import with_domains
    from evals.golden.score import TurnRecord

    rec = with_domains(TurnRecord("x", ("get_financial_summary", "get_lms_activity")))
    assert rec.tools_called == ("get_financial_summary", "get_lms_activity", "financeiro", "lms")


def test_check_expected_tools_flags_domains_unless_allowed() -> None:
    from evals.golden.run import check_expected_tools
    from evals.golden.score import Check, DatasetItem

    items = [
        DatasetItem("T-1@s", "T-1", "financeiro", "test_only", (Check("q", ("financeiro",)),)),
        DatasetItem("a@s", "a", "lms", "shared", (Check("q", ("get_lms_activity",)),)),
    ]
    assert check_expected_tools(items, allow_domains=False) == ["T-1: financeiro"]
    assert check_expected_tools(items, allow_domains=True) == []
