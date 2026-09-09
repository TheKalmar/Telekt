"""Safe rendering of model-authored article HTML in email and approval pages."""

from __future__ import annotations

import html
from html.parser import HTMLParser
from urllib.parse import urlparse

ALLOWED_TAGS = {
    "article",
    "section",
    "header",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "p",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "b",
    "i",
    "blockquote",
    "a",
    "br",
    "hr",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
VOID_TAGS = {"br", "hr"}


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form"}:
            self.skipping += 1
            return
        if self.skipping or tag not in ALLOWED_TAGS:
            return
        safe_attrs = ""
        if tag == "a":
            href = next((value for name, value in attrs if name.lower() == "href"), "") or ""
            parsed = urlparse(href)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                safe_attrs = f' href="{html.escape(href, quote=True)}" rel="noopener noreferrer"'
        self.parts.append(f"<{tag}{safe_attrs}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form"}:
            self.skipping = max(0, self.skipping - 1)
            return
        if not self.skipping and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(html.escape(data))


def sanitize_article_html(value: str, maximum: int = 150_000) -> str:
    parser = _Sanitizer()
    parser.feed((value or "")[:maximum])
    parser.close()
    return "".join(parser.parts)


def render_content_review(review: dict | None) -> str:
    """Render a complete, portable review card without executable markup."""
    if not review:
        return ""
    package = review.get("content_package") or {}
    content = package.get("html_content") or review.get("content") or ""
    if not content:
        return ""
    quality = review.get("quality_report") or {}
    publication = review.get("publication_state") or {}
    image = publication.get("featured_image") or {}
    image_url = str(image.get("source_url") or "")
    image_html = ""
    if image_url.startswith("https://"):
        image_html = (
            f'<img src="{html.escape(image_url, quote=True)}" alt="{html.escape(str(image.get("alt_text") or ""), quote=True)}" '
            'style="display:block;width:100%;max-height:420px;object-fit:cover;border-radius:10px;margin:16px 0">'
        )
    facts = [
        ("Focus keyword", package.get("focus_keyword")),
        ("SEO title", package.get("seo_title")),
        ("Meta description", package.get("meta_description")),
        ("Categories", ", ".join(package.get("categories") or [])),
        ("Tags", ", ".join(package.get("tags") or [])),
        (
            "Telekt SEO QA",
            f"{quality.get('score')}/{quality.get('maximum')} (target {quality.get('target')})"
            if quality.get("score") is not None
            else "",
        ),
        ("WordPress draft", publication.get("link")),
    ]
    rows = "".join(
        f"<tr><td style='padding:5px 10px;color:#667386'>{html.escape(label)}</td>"
        f"<td style='padding:5px 10px'>{html.escape(str(value))}</td></tr>"
        for label, value in facts
        if value
    )
    sources = "".join(
        f'<li><a style="color:#0b62c4" href="{html.escape(str(url), quote=True)}">{html.escape(str(url))}</a></li>'
        for url in package.get("source_urls") or review.get("sources") or []
        if str(url).startswith(("http://", "https://"))
    )
    return (
        '<div style="margin:16px 0;padding:18px;background:#fff;color:#172033;'
        'border:1px solid #d8e0ea;border-radius:10px">'
        '<h2 style="margin-top:0">Complete draft for review</h2>'
        f"<table style='width:100%;border-collapse:collapse'>{rows}</table>{image_html}"
        f'<article style="font:16px/1.65 Georgia,serif">{sanitize_article_html(content)}</article>'
        + (f"<h3>Sources</h3><ul>{sources}</ul>" if sources else "")
        + "</div>"
    )
