"""Provider billing safeguards using an in-process HTTP transport only."""
import asyncio

import httpx

from acc_engineer.providers import OpenRouter, ProviderError
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


LUNA_PRICING = {
    "prompt": "0.0000001",
    "completion": "0.0000005",
    "web_search": "0.01",
    "input_cache_read": "0.00000001",
    "input_cache_write": "0.000000125",
    "overrides": [{
        "min_prompt_tokens": 272000,
        "prompt": "0.0000002",
        "completion": "0.00000075",
        "input_cache_read": "0.00000002",
        "input_cache_write": "0.00000025",
    }],
}


def _provider_with_pricing(tmp_path, monkeypatch, pricing, model="openai/gpt-6-luna"):
    store = Store(tmp_path)
    provider = OpenRouter(store)
    provider.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []})),
        base_url=provider.BASE,
    )
    provider.catalogue[model] = {"pricing": pricing}
    monkeypatch.setattr(provider, "key", lambda: "sk-or-test-only")
    store.save_settings({"request_limit_usd": 1, "weekly_budget_usd": 10})
    return store, provider, model


def test_luna_catalogue_pricing_with_tiers_is_accepted_and_reserves_highest_rates(tmp_path, monkeypatch):
    store, provider, model = _provider_with_pricing(tmp_path, monkeypatch, LUNA_PRICING)
    payload = {"model": model, "messages": [{"role": "user", "content": "offline test"}]}

    asyncio.run(provider._post(model, "/chat/completions", payload, max_tokens=100))

    reservation = store.recent("usage")[0]
    bound = ((len(str(payload).encode()) + 512) * (0.0000002 + 0.00000025) + 100 * 0.00000075)
    assert reservation["cost_usd"] == bound
    assert reservation["outcome"] == "reserved"
    asyncio.run(provider.client.aclose())
    store.close()


def test_luna_optional_paid_search_is_rejected_when_request_enables_search(tmp_path, monkeypatch):
    store, provider, model = _provider_with_pricing(tmp_path, monkeypatch, LUNA_PRICING)
    payload = {"model": model, "plugins": [{"id": "web"}], "messages": []}

    try:
        asyncio.run(provider._post(model, "/chat/completions", payload))
    except ProviderError as exc:
        assert "Unverified pricing" in str(exc)
    else:
        raise AssertionError("paid web search must be blocked without a local cost bound")
    assert store.recent("usage") == []
    asyncio.run(provider.client.aclose())
    store.close()


def test_unknown_pricing_dimension_still_fails_closed(tmp_path, monkeypatch):
    pricing = {"prompt": "0.000001", "completion": "0.000002", "audio_generation": "0.01"}
    store, provider, model = _provider_with_pricing(tmp_path, monkeypatch, pricing)

    try:
        asyncio.run(provider._post(model, "/chat/completions", {"messages": []}))
    except ProviderError as exc:
        assert "Unverified pricing" in str(exc)
    else:
        raise AssertionError("unknown billing dimensions must remain blocked")
    assert store.recent("usage") == []
    asyncio.run(provider.client.aclose())
    store.close()
