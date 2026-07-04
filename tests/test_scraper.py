"""Tests for src.scraper: Zendesk pagination, draft skipping, limit, blank-body
backfill via the single-article endpoint — via mock API."""
from src import scraper


def _art(id, name, url, body, draft):
    return {
        "id": id,
        "name": name,
        "html_url": url,
        "body": body,
        "updated_at": "2026-01-01T00:00:00Z",
        "draft": draft,
    }


def test_scrape_articles_pagination_drafts_and_limit(monkeypatch):
    page1 = {
        "articles": [
            _art(1, "A One", "https://support.optisigns.com/a1", "<p>1</p>", False),
            _art(2, "A Two (draft)", "https://support.optisigns.com/a2", "<p>2</p>", True),
            _art(3, "A Three", "https://support.optisigns.com/a3", "<p>3</p>", False),
        ],
        "next_page": "https://support.optisigns.com/api/v2/help_center/en-us/articles.json?page=2",
    }
    page2 = {
        "articles": [
            _art(4, "A Four", "https://support.optisigns.com/a4", "<p>4</p>", False),
        ],
        "next_page": None,
    }
    pages = iter([page1, page2])
    monkeypatch.setattr(scraper, "_get", lambda url, timeout=30.0: next(pages))

    arts = list(scraper.scrape_articles("https://support.optisigns.com", limit=10))
    ids = [a.id for a in arts]
    assert ids == [1, 3, 4]  # draft id=2 skipped, next_page followed
    assert arts[0].url == "https://support.optisigns.com/a1"
    assert arts[0].title == "A One"
    assert arts[0].body_html == "<p>1</p>"


def test_scrape_articles_respects_limit(monkeypatch):
    page = {
        "articles": [_art(i, f"N{i}", f"https://x/{i}", "<p/>", False) for i in range(5)],
        "next_page": None,
    }
    monkeypatch.setattr(scraper, "_get", lambda url, timeout=30.0: page)
    arts = list(scraper.scrape_articles("https://support.optisigns.com", limit=3))
    assert [a.id for a in arts] == [0, 1, 2]


def test_scrape_articles_stops_without_next_page(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, timeout=30.0):
        calls["n"] += 1
        return {"articles": [_art(1, "Only", "https://x/1", "<p/>", False)], "next_page": None}

    monkeypatch.setattr(scraper, "_get", fake_get)
    arts = list(scraper.scrape_articles("https://support.optisigns.com", limit=10))
    assert [a.id for a in arts] == [1]
    assert calls["n"] == 1  # no second page fetched once next_page is null


def test_scrape_articles_backfills_blank_body_from_detail(monkeypatch):
    """A list record with an empty body should trigger a single-article fetch."""
    list_page = {
        "articles": [_art(5, "Blank In List", "https://x/5", "", False)],
        "next_page": None,
    }
    detail = {"article": _art(5, "Blank In List", "https://x/5", "<p>real body</p>", False)}

    def fake_get(url, timeout=30.0):
        if "articles/5.json" in url:
            return detail
        return list_page

    monkeypatch.setattr(scraper, "_get", fake_get)
    arts = list(scraper.scrape_articles("https://support.optisigns.com", limit=5))
    assert len(arts) == 1
    assert arts[0].body_html == "<p>real body</p>"


def test_scrape_articles_keeps_body_when_list_has_it(monkeypatch):
    """If the list endpoint already provides a body, no detail fetch happens."""
    list_page = {
        "articles": [_art(6, "Has Body", "https://x/6", "<p>list body</p>", False)],
        "next_page": None,
    }
    calls = []

    def fake_get(url, timeout=30.0):
        calls.append(url)
        return list_page

    monkeypatch.setattr(scraper, "_get", fake_get)
    arts = list(scraper.scrape_articles("https://support.optisigns.com", limit=5))
    assert arts[0].body_html == "<p>list body</p>"
    # Only the list URL was hit; no detail fetch.
    assert all("articles/6.json" not in u for u in calls)


def test_scrape_articles_pins_sample_ids_first(monkeypatch):
    """Pinned sample articles are fetched via the detail endpoint first, ahead
    of the listing, and are not duplicated when they reappear in the listing."""
    detail = {"article": _art(7, "Pinned YT", "https://x/7", "<p>pinned body</p>", False)}
    list_page = {
        "articles": [
            _art(8, "Listed One", "https://x/8", "<p>8</p>", False),
            _art(7, "Pinned YT", "https://x/7", "<p>should be skipped here</p>", False),
            _art(9, "Listed Two", "https://x/9", "<p>9</p>", False),
        ],
        "next_page": None,
    }

    def fake_get(url, timeout=30.0):
        if "articles/7.json" in url:
            return detail
        return list_page

    monkeypatch.setattr(scraper, "_get", fake_get)
    arts = list(scraper.scrape_articles(
        "https://support.optisigns.com", limit=10, sample_article_ids=(7,)
    ))
    ids = [a.id for a in arts]
    # Pinned 7 first, then 8 and 9 from the listing; 7 not duplicated.
    assert ids == [7, 8, 9]
    assert arts[0].body_html == "<p>pinned body</p>"  # came from detail endpoint


def test_scrape_articles_no_pin_by_default(monkeypatch):
    """Without sample_article_ids the scraper walks the listing only."""
    list_page = {"articles": [_art(1, "A", "https://x/1", "<p/>", False)], "next_page": None}
    calls = []

    def fake_get(url, timeout=30.0):
        calls.append(url)
        return list_page

    monkeypatch.setattr(scraper, "_get", fake_get)
    arts = list(scraper.scrape_articles("https://support.optisigns.com", limit=5))
    assert [a.id for a in arts] == [1]
    # No detail-style URLs hit when nothing is pinned.
    assert all("/articles/1.json" not in u for u in calls)
