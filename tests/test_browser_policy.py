import pytest

from digital_company.browser_policy import contains_human_checkpoint, validate_browser_url


def test_browser_url_requires_https_and_explicit_domain_allowlist():
    assert validate_browser_url(
        "https://accounts.example.com/login", ["example.com"]
    ) == "https://accounts.example.com/login"

    with pytest.raises(ValueError, match="HTTPS"):
        validate_browser_url("http://example.com", ["example.com"])
    with pytest.raises(ValueError, match="outside"):
        validate_browser_url("https://evil.example.net", ["example.com"])
    with pytest.raises(ValueError, match="embedded credentials"):
        validate_browser_url("https://user:pass@example.com", ["example.com"])


@pytest.mark.parametrize("target", ["localhost", "127.0.0.1", "10.0.0.5", "169.254.169.254"])
def test_browser_blocks_private_and_metadata_targets(target: str):
    with pytest.raises(ValueError, match="Private and loopback"):
        validate_browser_url(f"https://{target}/", [target])


def test_human_checkpoint_detection_is_conservative():
    assert contains_human_checkpoint(
        "https://example.com/login", "Security challenge", "Enter verification code"
    )
    assert not contains_human_checkpoint(
        "https://example.com/catalog", "Products", "Browse the current collection"
    )
