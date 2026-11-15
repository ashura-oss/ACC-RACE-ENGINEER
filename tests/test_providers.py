"""Provider billing safeguards using an in-process HTTP transport only."""
import asyncio

import httpx

from acc_engineer.providers import OpenRouter
from acc_engineer.storage import Store


def test_canceled_provider_dispatch_keeps_cost_reservation(tmp_path, monkeypatch):
    store = Store(tmp_path)
    provider = OpenRouter(store)
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.Event().wait()

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.BASE)
    provider.catalogue["test/model"] = {"pricing": {"prompt": "0.000001", "completion": "0.000002"}}
    monkeypatch.setattr(provider, "key", lambda: "sk-or-test-only")
    store.save_settings({"request_limit_usd": 1, "weekly_budget_usd": 10})

    async def exercise():
        task = asyncio.create_task(provider._post("test/model", "/chat/completions", {"prompt": "offline test"}, max_tokens=10))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(exercise())
    reservations = store.recent("usage")
    assert len(reservations) == 1
    assert reservations[0]["outcome"] == "reserved"
    assert reservations[0]["estimated"] is True
    assert reservations[0]["cost_usd"] > 0
    assert store.recent("usage_detail") == []
    asyncio.run(provider.client.aclose())
    store.close()
