import pytest

from digital_company.model_connections import ModelConnectionRegistry


def test_connection_registry_accepts_arbitrary_models_and_adapters(tmp_path):
    registry = ModelConnectionRegistry(tmp_path)
    item = registry.save({
        "name": "My gateway", "adapter": "openai_compatible", "location": "cloud",
        "base_url": "https://models.example/v1/", "model": "future-model-99",
        "requires_api_key": False,
    })
    assert item["model"] == "future-model-99"
    assert item["base_url"] == "https://models.example/v1"
    assert item["requires_api_key"] is False


def test_connection_registry_requires_url_for_compatible_transport(tmp_path):
    registry = ModelConnectionRegistry(tmp_path)
    with pytest.raises(ValueError, match="base URL"):
        registry.save({"name": "Broken", "adapter": "openai_compatible",
                       "location": "cloud", "model": "anything"})


def test_defaults_are_transport_profiles_not_vendor_model_catalog(tmp_path):
    connections = ModelConnectionRegistry(tmp_path).ensure_defaults("local-x", "remote-y")
    assert {(item["adapter"], item["model"]) for item in connections} == {
        ("openai_compatible", "local-x"), ("openai_responses", "remote-y")
    }
