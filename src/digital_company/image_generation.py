"""Bounded image-generation transport using an agent's configured model connection."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from digital_company.network_policy import normalize_http_base_url, validate_https_resource_url
from digital_company.runtime_secrets import get_secret


class ImageGenerationRuntime:
    """Generate one bitmap without exposing provider credentials to company state."""

    def __init__(
        self, connection: dict, config: dict, budget_check=None, on_generated=None
    ) -> None:
        if connection.get("location") != "cloud":
            raise RuntimeError(
                "Featured-image generation requires a cloud image-capable connection"
            )
        self.connection = connection
        self.config = config
        self.budget_check = budget_check or (lambda _estimate: None)
        self.on_generated = on_generated or (lambda: None)

    def generate(self, prompt: str) -> tuple[bytes, str]:
        self.budget_check(float(self.config.get("estimated_cost_eur", 0.25)))
        base_url = normalize_http_base_url(
            self.connection.get("base_url") or "https://api.openai.com/v1",
            allow_plain_http=False,
            field_name="Image model base URL",
        )
        api_key = get_secret(f"MODEL_CONNECTION_{self.connection['id']}") or os.getenv(
            "OPENAI_API_KEY", ""
        )
        if not api_key:
            raise RuntimeError("Image model connection is missing its API key")
        style = str(self.config.get("brand_style", "")).strip()
        final_prompt = prompt if not style else f"{prompt}\n\nBrand and visual direction: {style}"
        payload = {
            "model": self.config.get("model", "gpt-image-2"),
            "prompt": final_prompt,
            "size": self.config.get("size", "1536x1024"),
            "quality": self.config.get("quality", "medium"),
        }
        request = urllib.request.Request(
            base_url + "/images/generations",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        )
        try:
            # URL is restricted to a validated HTTPS model endpoint above.
            with urllib.request.urlopen(request, timeout=240) as response:  # nosec
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Image generation returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Image generation connection failed") from exc
        item = (body.get("data") or [{}])[0]
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"], validate=True)
        elif item.get("url"):
            try:
                url = validate_https_resource_url(
                    str(item["url"]), field_name="Generated image URL"
                )
            except ValueError as exc:
                raise RuntimeError("Image provider returned an unsafe URL") from exc
            # Signed query parameters are common for short-lived image downloads.
            with urllib.request.urlopen(url, timeout=60) as response:  # nosec
                data = response.read(15_000_001)
        else:
            raise RuntimeError("Image provider returned no image data")
        if not data or len(data) > 15_000_000:
            raise RuntimeError("Generated image is empty or larger than 15 MB")
        self.on_generated()
        return data, "image/png"
