from __future__ import annotations

from web_agent.cache import Cache
from web_agent.models import ExtractionResult, FetchResult, FlowState
from web_agent.service import DomainChromeCache, WebAgentTool


class TestCache:
    def test_get_returns_none_for_missing_key(self) -> None:
        cache = Cache()
        assert cache.get("missing") is None

    def test_set_and_get(self) -> None:
        cache = Cache()
        cache.set("k", {"items": [1, 2]})
        assert cache.get("k") == {"items": [1, 2]}

    def test_set_overwrites(self) -> None:
        cache = Cache()
        cache.set("k", "v1")
        cache.set("k", "v2")
        assert cache.get("k") == "v2"

    def test_delete_returns_true_when_present(self) -> None:
        cache = Cache()
        cache.set("k", "v")
        assert cache.delete("k") is True
        assert cache.get("k") is None

    def test_delete_returns_false_when_absent(self) -> None:
        cache = Cache()
        assert cache.delete("absent") is False

    def test_clear_removes_all(self) -> None:
        cache = Cache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.keys() == []

    def test_keys_returns_all_keys(self) -> None:
        cache = Cache()
        cache.set("x", 1)
        cache.set("y", 2)
        assert cache.keys() == ["x", "y"]

    def test_lru_eviction(self) -> None:
        cache = Cache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_lru_touch_on_get(self) -> None:
        cache = Cache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        _ = cache.get("a")  # touch "a", making "b" the eldest
        cache.set("c", 3)  # evicts "b"
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3

    def test_set_moves_to_end(self) -> None:
        cache = Cache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("a", 10)  # re-set "a", moves to end
        cache.set("c", 3)  # evicts "b" (eldest)
        assert cache.get("a") == 10
        assert cache.get("b") is None


class TestDomainChromeCache:
    def test_domain_from_url(self) -> None:
        assert DomainChromeCache.domain_from_url("https://auctions.yahoo.co.jp/search?p=test") == "auctions.yahoo.co.jp"

    def test_domain_from_url_with_port(self) -> None:
        assert DomainChromeCache.domain_from_url("https://example.com:8080/path") == "example.com:8080"

    def test_first_call_stores_baseline_and_returns_all_as_content(self) -> None:
        cache = DomainChromeCache()
        items = [
            {"title": "Home", "link": "https://example.com/"},
            {"title": "Product", "link": "https://example.com/p1"},
        ]
        links = [
            {"text": "Home", "href": "https://example.com/"},
            {"text": "Login", "href": "https://example.com/login"},
        ]
        content_items, chrome_items, content_links, chrome_links = cache.split_chrome("example.com", items, links)

        assert content_items == items
        assert chrome_items == []
        assert content_links == links
        assert chrome_links == []

        baseline = cache.get_baseline("example.com")
        assert baseline is not None
        assert len(baseline["items"]) == 2
        assert len(baseline["links"]) == 2

    def test_second_call_filters_chrome(self) -> None:
        cache = DomainChromeCache()
        items_1 = [
            {"title": "Home", "link": "https://example.com/"},
            {"title": "Product", "link": "https://example.com/p1"},
        ]
        links_1 = [
            {"text": "Home", "href": "https://example.com/"},
            {"text": "Login", "href": "https://example.com/login"},
        ]
        cache.split_chrome("example.com", items_1, links_1)

        items_2 = [
            {"title": "Home", "link": "https://example.com/"},
            {"title": "Product", "link": "https://example.com/p2"},
            {"title": "About", "link": "https://example.com/about"},
        ]
        links_2 = [
            {"text": "Home", "href": "https://example.com/"},
            {"text": "Login", "href": "https://example.com/login"},
            {"text": "New Link", "href": "https://example.com/new"},
        ]
        content_items, chrome_items, content_links, chrome_links = cache.split_chrome("example.com", items_2, links_2)

        assert len(chrome_items) == 1
        assert chrome_items[0]["title"] == "Home"
        assert len(content_items) == 2

        assert len(chrome_links) == 2
        assert len(content_links) == 1
        assert content_links[0]["text"] == "New Link"

    def test_different_domains_independent(self) -> None:
        cache = DomainChromeCache()
        items_a = [{"title": "Home", "link": "https://a.com/"}]
        cache.split_chrome("a.com", items_a, [])

        items_b = [{"title": "Home", "link": "https://b.com/"}]
        content_items, chrome_items, _, _ = cache.split_chrome("b.com", items_b, [])

        assert content_items == items_b
        assert chrome_items == []

    def test_clear_specific_domain(self) -> None:
        cache = DomainChromeCache()
        cache.split_chrome("a.com", [], [])
        cache.split_chrome("b.com", [], [])
        cache.clear("a.com")
        assert cache.get_baseline("a.com") is None
        assert cache.get_baseline("b.com") is not None

    def test_clear_all(self) -> None:
        cache = DomainChromeCache()
        cache.split_chrome("a.com", [], [])
        cache.clear()
        assert cache.cached_domains == []

    def test_uses_generic_cache(self) -> None:
        inner = Cache(max_entries=1)
        cache = DomainChromeCache(cache=inner)
        cache.split_chrome("a.com", [], [])
        cache.split_chrome("b.com", [], [])
        assert cache.get_baseline("a.com") is None
        assert cache.get_baseline("b.com") is not None


class TestFinalPayloadChromeDedup:
    def _build_result(self, items: list[dict], links: list[dict]) -> ExtractionResult:
        return ExtractionResult(
            phase="extract",
            strategy="listing",
            page_type="listing",
            content="Some content",
            markdown="# Title\n\nSome content",
            items=items,
            links=links,
            visible_text="Some content",
            quality_score=0.9,
            decision="accept",
            notes=[],
        )

    def _build_flow(self, url: str, items: list[dict], links: list[dict]) -> FlowState:
        return FlowState(
            fetch=FetchResult(
                strategy="browser_seed",
                url=url,
                final_url=url,
                status_code=200,
                html="",
                title="Test Page",
                used_browser=True,
                weak_content=False,
                errors=[],
            ),
            page_type="listing",
            steps=[],
            best_result=self._build_result(items, links),
            fallback_used=False,
        )

    def test_first_call_no_chrome_omitted(self) -> None:
        cache = DomainChromeCache()
        tool = WebAgentTool(chrome_cache=cache)
        items = [{"title": "Home", "link": "https://example.com/", "description": ""}]
        links = [{"text": "Home", "href": "https://example.com/"}]
        flow = self._build_flow("https://example.com/page1", items, links)

        payload = tool._final_payload(flow, actions_taken=[])

        assert "chrome_omitted" not in payload
        assert len(payload["items"]) == 1
        assert len(payload["links"]) == 1
        assert len(payload["_items_all"]) == 1
        assert len(payload["_links_all"]) == 1

    def test_second_call_filters_chrome(self) -> None:
        cache = DomainChromeCache()
        tool = WebAgentTool(chrome_cache=cache)

        items_1 = [
            {"title": "Home", "link": "https://example.com/", "description": ""},
            {"title": "Login", "link": "https://example.com/login", "description": ""},
        ]
        links_1 = [
            {"text": "Home", "href": "https://example.com/"},
            {"text": "Login", "href": "https://example.com/login"},
        ]
        flow_1 = self._build_flow("https://example.com/page1", items_1, links_1)
        tool._final_payload(flow_1, actions_taken=[])

        items_2 = [
            {"title": "Home", "link": "https://example.com/", "description": ""},
            {"title": "Product A", "link": "https://example.com/p1", "description": "Great product"},
        ]
        links_2 = [
            {"text": "Home", "href": "https://example.com/"},
            {"text": "Login", "href": "https://example.com/login"},
            {"text": "Product A", "href": "https://example.com/p1"},
        ]
        flow_2 = self._build_flow("https://example.com/page2", items_2, links_2)
        payload = tool._final_payload(flow_2, actions_taken=[])

        assert "chrome_omitted" in payload
        assert payload["chrome_omitted"]["items"] == 1
        assert payload["chrome_omitted"]["links"] == 2
        assert payload["chrome_omitted"]["domain"] == "example.com"
        assert payload["chrome_omitted"]["retrieve_with"] == "web__domain_chrome"

        assert len(payload["items"]) == 1
        assert payload["items"][0]["title"] == "Product A"
        assert len(payload["links"]) == 1
        assert payload["links"][0]["text"] == "Product A"

        assert len(payload["_items_all"]) == 2
        assert len(payload["_links_all"]) == 3

    def test_no_cache_behaves_as_before(self) -> None:
        tool = WebAgentTool(chrome_cache=None)
        items = [{"title": "Home", "link": "https://example.com/", "description": ""}]
        links = [{"text": "Home", "href": "https://example.com/"}]
        flow = self._build_flow("https://example.com/page1", items, links)

        payload = tool._final_payload(flow, actions_taken=[])

        assert "chrome_omitted" not in payload
        assert len(payload["items"]) == 1
        assert len(payload["links"]) == 1
        assert "_items_all" in payload
        assert "_links_all" in payload
