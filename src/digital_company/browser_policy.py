"""Deterministic URL and domain rules shared by the browser runtime and tests."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse


DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if not DOMAIN_RE.fullmatch(domain) or ".." in domain:
        raise ValueError(f"Invalid allowed domain: {value}")
    try:
        address = ipaddress.ip_address(domain)
    except ValueError:
        address = None
    if domain == "localhost" or domain.endswith(".localhost") or (address and not address.is_global):
        raise ValueError("Private and loopback browser targets are blocked")
    return domain


def validate_browser_url(url: str, allowed_domains: list[str]) -> str:
    """Require HTTPS and an explicitly allowlisted public hostname."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Browser URL must be an HTTPS URL without embedded credentials")
    hostname = normalize_domain(parsed.hostname)
    allowed = [normalize_domain(value) for value in allowed_domains]
    if not any(hostname == domain or hostname.endswith("." + domain) for domain in allowed):
        raise ValueError(f"Browser target is outside the allowed domains: {hostname}")
    return parsed.geturl()


def contains_human_checkpoint(url: str, title: str, text: str) -> bool:
    sample = f"{url} {title} {text[:5000]}".lower()
    markers = (
        "captcha", "recaptcha", "hcaptcha", "two-factor", "two factor", "2fa",
        "verification code", "verify your identity", "security challenge",
    )
    return any(marker in sample for marker in markers)
