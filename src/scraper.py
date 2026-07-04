"""Scrape OptiSigns Zendesk Help Center articles via the public API.

Endpoint: https://support.optisigns.com/api/v2/help_center/en-us/articles.json

The Zendesk Help Center API returns `{articles: [...], next_page: <url or null>}`.
Each article object already includes the full `body` HTML, so a single
paginated walk is enough — no browser scraping needed and no per-article fetch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Retries with capped exponential backoff for transient network/5xx failures.
_retry = retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, requests.HTTPError)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, max=30),
    reraise=True,
)


@dataclass
class Article:
    id: int
    title: str
    html_url: str
    body_html: str
    updated_at: str
    draft: bool

    @property
    def url(self) -> str:
        return self.html_url


@_retry
def _get(url: str, timeout: float = 30.0) -> dict:
    resp = requests.get(
        url,
        headers={
            # A descriptive UA helps Zendesk identify/track API traffic.
            "User-Agent": "kb-mini-agent/1.0 (+support.optisigns.com help-center pager)",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _article_list_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/api/v2/help_center/en-us/articles.json"


def _article_detail_url(base_url: str, article_id: int) -> str:
    base = base_url.rstrip("/")
    return f"{base}/api/v2/help_center/articles/{article_id}.json"


def _to_article(raw: dict) -> Article:
    return Article(
        id=int(raw["id"]),
        title=(raw.get("name") or raw.get("title") or "").strip(),
        html_url=raw.get("html_url", ""),
        body_html=raw.get("body") or "",
        updated_at=raw.get("updated_at") or "",
        draft=bool(raw.get("draft")),
    )


def _fetch_body(base_url: str, article_id: int, fallback_html: str) -> str:
    """The list endpoint sometimes returns a thin (metadata-only) record with an
    empty `body`. In that case, fetch the full article via the single-article
    endpoint so the Markdown is never blank. Falls back to the list value on
    any error so a detail-fetch failure doesn't drop the article entirely.
    """
    if fallback_html and fallback_html.strip():
        return fallback_html
    try:
        payload = _get(_article_detail_url(base_url, article_id))
        body = (payload.get("article") or {}).get("body") or ""
        if body.strip():
            return body
    except Exception:
        pass
    return fallback_html


def scrape_articles(
    base_url: str,
    limit: int,
    *,
    sample_article_ids: tuple[int, ...] | None = None,
) -> Iterator[Article]:
    """Yield up to `limit` published articles from the Help Center API.

    The Zendesk list endpoint returns articles newest-first by `edited_at`, so
    a small `limit` only covers the most recent articles. To guarantee the
    take-home's canonical sample question (`How do I add a YouTube video?`) is
    answerable from the corpus, articles whose IDs appear in
    `sample_article_ids` are fetched directly via the single-article detail
    endpoint *before* the listing walk. They take the first slots, and the
    listing fills the rest up to `limit`.

    The list endpoint occasionally returns a metadata-only record with an empty
    `body`; for any such record we transparently backfill it from the detail
    endpoint so no Markdown file is ever blank.
    """
    yielded = 0
    # 1) Pinned sample articles first (small, known set), so the corpus always
    #    contains the docs the sample question depends on regardless of recency.
    for aid in sample_article_ids or ():
        if yielded >= limit:
            break
        try:
            payload = _get(_article_detail_url(base_url, aid))
            raw = payload.get("article")
            if raw and not raw.get("draft") and int(raw["id"]) == aid:
                body = _fetch_body(base_url, aid, raw.get("body") or "")
                article = _to_article(raw)
                article.body_html = body
                yield article
                yielded += 1
        except Exception:
            continue

    # 2) Fill the rest from the paginated listing.
    url = _article_list_url(base_url)
    while url and yielded < limit:
        payload = _get(url)
        for raw in payload.get("articles", []):
            if yielded >= limit:
                break
            if raw.get("draft"):
                continue
            # Skip sample articles already yielded above; the list endpoint
            # surfaces them too but we don't want duplicates.
            if sample_article_ids and int(raw["id"]) in sample_article_ids:
                continue
            body = _fetch_body(base_url, int(raw["id"]), raw.get("body") or "")
            article = _to_article(raw)
            article.body_html = body
            yield article
            yielded += 1
        url = payload.get("next_page")
