from pathlib import Path
from types import SimpleNamespace

from digital_company.pricing import usage_payload
from digital_company.store import CompanyStore


def test_extracts_sdk_usage_and_prices_known_model():
    usage = SimpleNamespace(requests=1, input_tokens=1000, output_tokens=100,
                            total_tokens=1100,
                            input_tokens_details=SimpleNamespace(cached_tokens=200),
                            output_tokens_details=SimpleNamespace(reasoning_tokens=25))
    result = SimpleNamespace(context_wrapper=SimpleNamespace(usage=usage))
    item = usage_payload(result, run_id="r1", provider="cloud", model="gpt-5.4-mini")
    assert item["cached_tokens"] == 200
    assert item["reasoning_tokens"] == 25
    assert item["estimated_usd"] == 0.001065


def test_usage_is_idempotent_and_reduces_remaining_budget(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Goal", 10)
    payload = {"run_id": "r1", "provider": "cloud", "model": "gpt-5.4-mini",
               "requests": 1, "input_tokens": 10, "cached_tokens": 0,
               "output_tokens": 2, "reasoning_tokens": 0, "total_tokens": 12,
               "estimated_usd": .01, "estimated_budget_cost": .01,
               "pricing_status": "priced"}
    store.record_model_usage(payload)
    store.record_model_usage(payload)
    assert store.operations_data()["model"]["token_usage"]["total_tokens"] == 12
    assert store.snapshot().remaining_budget_eur == 9.99
