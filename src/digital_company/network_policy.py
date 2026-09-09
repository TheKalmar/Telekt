"""Small, deterministic URL checks for configured service boundaries."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_http_base_url(
    value: str,
    *,
    allow_plain_http: bool,
    field_name: str = "Base URL",
) -> str:
    """Return a normalized HTTP(S) base URL or reject ambiguous transports.

    Provider endpoints are configuration, never request-controlled redirect
    targets. Restricting the scheme here prevents ``urllib`` from unexpectedly
    accepting local files or custom URL handlers.
    """

    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    schemes = {"http", "https"} if allow_plain_http else {"https"}
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        transport = "HTTP(S)" if allow_plain_http else "HTTPS"
        raise ValueError(
            f"{field_name} must be a {transport} URL without credentials, query, or fragment"
        )
    return normalized


def validate_https_resource_url(value: str, *, field_name: str = "Resource URL") -> str:
    """Validate a provider-returned HTTPS resource URL while allowing signed queries."""

    normalized = value.strip()
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} must be an HTTPS URL without credentials or fragment")
    return normalized
