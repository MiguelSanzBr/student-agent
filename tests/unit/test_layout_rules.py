import importlib
import inspect
import pkgutil

import app.api
import app.infra.stores as stores
from app.main import app as fastapi_app


def test_routers_registered_by_list() -> None:
    mods = [m.name for m in pkgutil.iter_modules(app.api.__path__) if m.name != "auth"]
    assert len(mods) == 8
    routers = [importlib.import_module(f"app.api.{n}").router for n in mods]
    assert all(r in app.api.ROUTERS for r in routers) and len(app.api.ROUTERS) == len(routers)
    paths = set(fastapi_app.openapi()["paths"])
    for r in routers:
        assert r.routes and all(getattr(x, "path", None) in paths for x in r.routes)


def test_one_module_per_store() -> None:
    classes = {
        "admission": {"AdmissionBucket", "TurnSlots", "Reservation"},
        "rate_limit": {"RateLimit"},
        "budget": {"TokenBudget"},
        "spend": {"GlobalSpend"},
        "history": {"HistoryStore", "Conversation"},
        "drafts": {"DraftStore"},
        "tickets": {"LoginTickets"},
        "labels": {"LabelCache"},
        "a2a_tasks": {"RedisTaskStore"},
        "turn_outputs": {"TurnOutputCache"},
    }
    found = {m.name for m in pkgutil.iter_modules(stores.__path__)}
    assert found == set(classes)
    for name, expected in classes.items():
        mod = importlib.import_module(f"app.infra.stores.{name}")
        own = {
            n for n, c in inspect.getmembers(mod, inspect.isclass) if c.__module__ == mod.__name__
        }
        assert own == expected
