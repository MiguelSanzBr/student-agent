from evals.redteam.adaptive import refine_template, run_adaptive


async def test_stops_at_first_success_and_feeds_answer_back() -> None:
    prompts: list[str] = []
    executed: list[str] = []

    async def attacker(p: str) -> str:
        prompts.append(p)
        return f"v{len(prompts)}"

    async def execute(text: str) -> tuple[bool, str]:
        executed.append(text)
        return text == "v2", f"resposta a {text}"

    r = await refine_template("base", execute, attacker)
    assert r.success and executed == ["base", "v1", "v2"] and r.refinements == 2
    assert "resposta a base" in prompts[0] and "base" in prompts[0]
    assert "resposta a v1" in prompts[1]


async def test_three_refinements_max_when_agent_holds() -> None:
    async def attacker(_: str) -> str:
        return "novo"

    async def execute(_: str) -> tuple[bool, str]:
        return False, "Não posso ajudar com isso."

    r = await refine_template("base", execute, attacker)
    assert not r.success and r.refinements == 3 and len(r.attempts) == 4


async def test_run_adaptive_unit_is_template() -> None:
    async def attacker(_: str) -> str:
        return "x"

    async def execute(t: str) -> tuple[bool, str]:
        return False, "ok"

    out = await run_adaptive({"t1": "a", "t2": "b"}, execute, attacker)
    assert set(out) == {"t1", "t2"} and not any(r.success for r in out.values())
