"""Convert Zendesk article HTML into clean, citation-bearing Markdown.

Pipeline:
  raw HTML -> BeautifulSoup (drop nav/footer/ads) -> markdownify -> normalize -> front matter
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify as md


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "article"


def _ensure_absolute_links(soup: BeautifulSoup, base_url: str) -> None:
    """Resolve relative Zendesk links against the help-center origin so they
    survive once the surrounding page chrome is gone."""
    for tag in soup.find_all("a", href=True):
        tag["href"] = urljoin(base_url, tag["href"])
    for tag in soup.find_all("img", src=True):
        tag["src"] = urljoin(base_url, tag["src"])


def _drop_chrome(soup: BeautifulSoup) -> None:
    for sel in [
        "script", "style", "noscript",
        "nav", "header", "footer",
        "form", "button", "iframe",
    ]:
        for tag in soup.find_all(sel):
            tag.decompose()
    # Zendesk attaches comment/vote/widgets blocks with these classes.
    for cls in ["comment-list", "comment", "article-votes", "article-subscribe",
                "recent-articles", "related-articles", "article-attachments"]:
        for tag in soup.find_all(class_=cls):
            tag.decompose()


def body_to_markdown(body_html: str, base_url: str) -> str:
    """Return the cleaned Markdown body (no front matter)."""
    if not body_html or not body_html.strip():
        return ""
    soup = BeautifulSoup(body_html, "html.parser")
    _drop_chrome(soup)
    _ensure_absolute_links(soup, base_url)
    text = md(
        soup.decode_contents(),
        heading_style="ATX",
        strip=["script", "style"],
        code_language="",
    )
    return _normalize(text)


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Zendesk content occasionally includes NBSP or mojibake from NBSP that
    # renders as `Â ` in Markdown; normalize both to regular spaces.
    text = text.replace("\u00a0", " ").replace("Â ", " ").replace("Â\u00a0", " ")
    # Collapse 3+ blank lines to 2 (markdown paragraph boundary).
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Trim trailing whitespace on every line.
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip() + "\n"


def render_body(article, base_url: str) -> str:
    """The hashable, stable content: optional title H1 + cleaned body + citation.

    Front matter is intentionally excluded so the hash is over *content only*.
    The same article body always yields the same render_body output, hence the
    same hash, hence the same front-matter content_hash field.
    """
    body = body_to_markdown(article.body_html, base_url)
    title_line = "" if body.lstrip().startswith("#") else f"# {article.title}\n\n"
    return title_line + body + f"\n\nArticle URL: {article.url}\n"


def build_markdown(article, content_hash: str, body: str) -> str:
    """Assemble the full file: YAML front matter + pre-rendered body."""
    fm = (
        "---\n"
        f'title: "{article.title}"\n'
        f'source_url: "{article.url}"\n'
        f'article_id: "{article.id}"\n'
        f'updated_at: "{article.updated_at}"\n'
        f'content_hash: "{content_hash}"\n'
        "---\n\n"
    )
    return fm + body


def slugify(text: str) -> str:
    """Public slug helper (re-exports internal one for the storage layer)."""
    return _slugify(text)


def estimate_chunk_count(text: str, max_tokens_per_chunk: int) -> int:
    """Rough chunk estimate (≈4 chars/token) used for logging only.

    The provider does its own chunking; this is a sanity number for logs.
    """
    tokens = max(len(text) // 4, 1)
    return max(1, (tokens + max_tokens_per_chunk - 1) // max_tokens_per_chunk)
