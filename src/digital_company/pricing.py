"""Transparent usage estimates; never presented as provider invoices."""
from __future__ import annotations
import os

MODEL_PRICES_USD = {
    "gpt-5.4-mini": {"input": .75, "cached": .075, "output": 4.50},
    "gpt-5.4": {"input": 2.50, "cached": .25, "output": 15.00},
}

def usage_payload(result, *, run_id: str, provider: str, model: str) -> dict | None:
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if usage is None:
        return None
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    cached = int(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0)
    reasoning = int(getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0)
    price = MODEL_PRICES_USD.get(model)
    usd = 0.0 if provider == "local" else None
    if provider != "local" and price:
        usd = round((max(0, inp-cached)*price["input"] + cached*price["cached"] + out*price["output"])/1_000_000, 8)
    rate = float(os.getenv("BILLING_USD_TO_BUDGET_RATE", "1.0"))
    return {"run_id": run_id, "provider": provider, "model": model,
            "requests": int(getattr(usage, "requests", 1) or 1), "input_tokens": inp,
            "cached_tokens": cached, "output_tokens": out, "reasoning_tokens": reasoning,
            "total_tokens": int(getattr(usage, "total_tokens", inp+out) or 0),
            "estimated_usd": usd, "estimated_budget_cost": round(usd*rate, 8) if usd is not None else None,
            "pricing_status": "priced" if usd is not None else "unknown_model"}
