from digital_company.model_adapters import ModelAdapterFactory


def test_default_cloud_binding_preserves_hosted_tool_capability(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    binding = ModelAdapterFactory().build(
        None, default_model="configured-model", default_cloud=True,
    )
    assert binding.model == "configured-model"
    assert binding.ready is False
    assert binding.supports_hosted_tools is True


def test_keyless_compatible_transport_uses_sdk_placeholder_credential():
    binding = ModelAdapterFactory(secret_reader=lambda _name: None).build({
        "id": "self-hosted",
        "adapter": "openai_compatible",
        "location": "local",
        "base_url": "http://model-runtime:11434/v1",
        "model": "custom-model",
        "requires_api_key": False,
    }, default_model="unused", default_cloud=False)

    assert binding.model.model == "custom-model"
    assert binding.ready is True
    assert binding.supports_hosted_tools is False
