"""Tests for src.cleaner: slugify, Markdown rendering, front matter, citations."""
from src.cleaner import (
    slugify,
    build_markdown,
    render_body,
    body_to_markdown,
    estimate_chunk_count,
)


class _Article:
    """Minimal stand-in for scraper.Article so tests stay independent of scraping."""

    def __init__(self, id, title, url, body, updated_at):
        self.id = id
        self.title = title
        self.html_url = url
        self.url = url
        self.body_html = body
        self.updated_at = updated_at
        self.draft = False


def test_slugify_basic_and_edge():
    assert slugify("How to Add a YouTube Video!") == "how-to-add-a-youtube-video"
    assert slugify("  Multiple   Spaces & Symbols!! ") == "multiple-spaces-symbols"
    assert slugify("") == "article"
    assert slugify("---") == "article"


def test_render_body_title_plus_citation():
    a = _Article(1, "My Title", "https://support.optisigns.com/x", "<p>Hello</p>", "2026-01-01T00:00:00Z")
    body = render_body(a, "https://support.optisigns.com")
    assert body.startswith("# My Title")
    assert "Hello" in body
    assert body.rstrip().endswith("Article URL: https://support.optisigns.com/x")


def test_render_body_no_duplicate_title_when_body_has_heading():
    a = _Article(1, "T", "https://x/y", "# Existing Heading\n\nText", "2026-01-01T00:00:00Z")
    body = render_body(a, "https://support.optisigns.com")
    # The synthetic "# T" title is suppressed because the body already leads with a heading.
    assert "\n# T\n" not in body
    assert "# Existing Heading" in body


def test_build_markdown_frontmatter_fields():
    a = _Article(42, "Title 42", "https://support.optisigns.com/a/42", "<p>Body</p>", "2026-07-01T00:00:00Z")
    body = render_body(a, "https://support.optisigns.com")
    md = build_markdown(a, "deadbeef", body)
    assert md.startswith("---\n")
    assert 'title: "Title 42"' in md
    assert 'article_id: "42"' in md
    assert 'source_url: "https://support.optisigns.com/a/42"' in md
    assert 'updated_at: "2026-07-01T00:00:00Z"' in md
    assert 'content_hash: "deadbeef"' in md
    assert "Article URL: https://support.optisigns.com/a/42" in md
    # Front matter comes before the body.
    assert md.index("---\ntitle:") < md.index("Body")


def test_body_to_markdown_drops_chrome_and_resolves_links():
    html = (
        "<nav>NAVCHROME</nav><footer>FOOTERCHROME</footer>"
        "<script>BADSCRIPT()</script><noscript>NOSCRIPTCHROME</noscript>"
        "<p>Keep me and <a href='/hc/en-us/articles/1'>a link</a>.</p>"
    )
    md = body_to_markdown(html, "https://support.optisigns.com")
    assert "NAVCHROME" not in md
    assert "FOOTERCHROME" not in md
    assert "BADSCRIPT" not in md
    assert "NOSCRIPTCHROME" not in md
    assert "Keep me" in md
    # Relative link resolved against the help-center origin.
    assert "https://support.optisigns.com/hc/en-us/articles/1" in md


def test_estimate_chunk_count():
    assert estimate_chunk_count("x" * (512 * 4), 512) == 1     # ~512 tokens -> 1 chunk
    assert estimate_chunk_count("x" * (1024 * 4), 512) == 2    # ~1024 tokens -> 2 chunks
    assert estimate_chunk_count("", 512) == 1
    assert estimate_chunk_count("x", 512) == 1
