import io
import json
import urllib.error

import pytest

from digital_company.browser_client import BrowserRuntimeError, browser_runtime_request
from digital_company.execution_client import ExecutionRuntimeClient, ExecutionRuntimeError
from digital_company.model_connections import ModelConnectionRegistry
from digital_company.network_policy import normalize_http_base_url, validate_https_resource_url


def test_normalize_http_base_url_accepts_expected_transports():
    assert (
        normalize_http_base_url(" https://models.example/v1/ ", allow_plain_http=False)
        == "https://models.example/v1"
    )
    assert (
        normalize_http_base_url("http://127.0.0.1:8430/", allow_plain_http=True)
        == "http://127.0.0.1:8430"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:password@models.example/v1",
        "https://models.example/v1?token=secret",
        "https://models.example/v1#fragment",
        "//models.example/v1",
    ],
)
def test_normalize_http_base_url_rejects_ambiguous_or_credentialed_urls(url):
    with pytest.raises(ValueError, match="without credentials, query, or fragment"):
        normalize_http_base_url(url, allow_plain_http=True)


def test_remote_model_connections_require_https(tmp_path):
    registry = ModelConnectionRegistry(tmp_path)

    with pytest.raises(ValueError, match="must be a HTTPS URL"):
        registry.save(
            {
                "name": "Insecure remote gateway",
                "adapter": "openai_compatible",
                "location": "cloud",
                "base_url": "http://models.example/v1",
                "model": "example-model",
            }
        )


def test_https_resource_url_allows_signed_query_but_rejects_credentials():
    signed = "https://cdn.example/image.png?signature=temporary"
    assert validate_https_resource_url(signed) == signed

    with pytest.raises(ValueError, match="without credentials"):
        validate_https_resource_url("https://user:secret@cdn.example/image.png")


def test_execution_runtime_rejects_non_http_transport():
    with pytest.raises(ValueError, match="Execution runtime URL"):
        ExecutionRuntimeClient("file:///tmp/runtime.sock")


class FakeResponse:
    def __init__(self, payload: bytes, content_type: str = "application/json"):
        self.payload = payload
        self.content_type = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.payload

    @property
    def headers(self):
        return self

    def get_content_type(self):
        return self.content_type


def test_execution_runtime_sends_authenticated_health_request(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"status": "ok"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ExecutionRuntimeClient("http://runtime:8440/", "runtime-token")

    assert client.health() == {"status": "ok"}
    assert captured["request"].full_url == "http://runtime:8440/health"
    assert captured["request"].headers["X-telekt-execution-token"] == "runtime-token"
    assert captured["timeout"] == 3


def test_execution_runtime_normalizes_http_errors(monkeypatch):
    error = urllib.error.HTTPError(
        "http://runtime:8440/health",
        503,
        "Unavailable",
        {},
        io.BytesIO(b"maintenance"),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(ExecutionRuntimeError, match="maintenance") as caught:
        ExecutionRuntimeClient("http://runtime:8440").health()

    assert caught.value.status_code == 503


def test_browser_runtime_returns_payload_and_content_type(monkeypatch):
    monkeypatch.setenv("BROWSER_RUNTIME_URL", "http://browser:8430/")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(b"screenshot", "image/png"),
    )

    assert browser_runtime_request("GET", "/screenshot", timeout=2) == (b"screenshot", "image/png")


def test_browser_runtime_normalizes_transport_errors(monkeypatch):
    monkeypatch.setenv("BROWSER_RUNTIME_URL", "http://browser:8430")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    with pytest.raises(BrowserRuntimeError, match="Browser runtime unavailable") as caught:
        browser_runtime_request("GET", "/health")

    assert caught.value.status_code == 503


def test_model_connection_registry_lists_gets_and_deletes_profiles(tmp_path):
    registry = ModelConnectionRegistry(tmp_path)
    saved = registry.save(
        {
            "id": "portfolio-model",
            "name": "Portfolio model",
            "adapter": "openai_responses",
            "location": "cloud",
            "model": "example-model",
        }
    )

    assert registry.list() == [saved]
    assert registry.get("portfolio-model") == saved
    assert registry.get("missing") is None
    assert registry.delete("missing") is False
    assert registry.delete("portfolio-model") is True
    assert registry.list() == []
