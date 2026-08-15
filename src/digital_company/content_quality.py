"""Deterministic pre-publication checks for structured content packages."""

from __future__ import annotations

import re

from digital_company.models import ContentPackage


def score_content(package: ContentPackage, minimum_words: int = 700) -> dict:
    """Return an explainable Telekt SEO QA score; this is not an AIOSEO score."""
    plain = re.sub(r"<[^>]+>", " ", package.html_content)
    plain = re.sub(r"\s+", " ", plain).strip()
    words = re.findall(r"\b[\wČĆŽŠĐčćžšđ-]+\b", plain, flags=re.UNICODE)
    haystack = plain.casefold()
    keyword = package.focus_keyword.casefold()
    checks = [
        ("clean_title", not re.search(r"\b(draft|nacrt|seo nacrt)\b", package.title, re.I), 8),
        ("seo_title_length", 30 <= len(package.seo_title) <= 70, 8),
        ("meta_description_length", 120 <= len(package.meta_description) <= 165, 8),
        ("keyword_in_title", keyword in package.title.casefold(), 10),
        ("keyword_in_seo_title", keyword in package.seo_title.casefold(), 8),
        ("keyword_in_meta", keyword in package.meta_description.casefold(), 7),
        ("keyword_in_opening", keyword in haystack[:900], 7),
        ("useful_length", len(words) >= max(300, minimum_words), 12),
        ("heading_structure", bool(re.search(r"<h2\b", package.html_content, re.I)), 8),
        ("category", bool(package.categories), 6),
        ("tags", len(package.tags) >= 2, 5),
        ("internal_links", bool(package.internal_links), 5),
        ("verified_sources", len(package.source_urls) >= 2, 5),
        (
            "featured_image",
            package.featured_image is not None
            and bool(package.featured_image.alt_text.strip())
            and bool(package.featured_image.prompt.strip()),
            5,
        ),
    ]
    score = sum(weight for _name, passed, weight in checks if passed)
    return {
        "name": "Telekt SEO QA",
        "score": score,
        "maximum": sum(weight for _name, _passed, weight in checks),
        "word_count": len(words),
        "checks": [{"name": name, "passed": passed, "weight": weight} for name, passed, weight in checks],
        "issues": [name for name, passed, _weight in checks if not passed],
    }
