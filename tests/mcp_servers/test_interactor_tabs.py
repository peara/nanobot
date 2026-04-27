from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from web_agent.browser.interactor import BrowserInteractor, SafeActionError


@pytest.fixture
def interactor():
    bi = BrowserInteractor(headless=True)
    bi._context = MagicMock()
    bi.page = MagicMock()
    bi.page.url = "https://example.com/search"
    bi.page.is_closed.return_value = False
    bi.page.title = AsyncMock(return_value="Search Results")
    bi.page.set_default_timeout = MagicMock()
    bi.page.bring_to_front = AsyncMock()
    bi._background_tabs = []
    return bi


class TestBackgroundTabs:
    def test_background_tabs_initially_empty(self, interactor):
        assert interactor.background_tabs == []

    def test_background_tabs_returns_copy(self, interactor):
        interactor._background_tabs = [{"url": "https://example.com", "title": "Example"}]
        tabs = interactor.background_tabs
        tabs.append({"url": "https://other.com", "title": "Other"})
        assert len(interactor._background_tabs) == 1

    @pytest.mark.asyncio
    async def test_compress_page_extracts_url_and_title(self, interactor):
        mock_page = MagicMock()
        mock_page.url = "https://example.com/search?q=test"
        mock_page.is_closed.return_value = False
        mock_page.title = AsyncMock(return_value="Search Results")

        result = await interactor._compress_page(mock_page)

        assert result == {"url": "https://example.com/search?q=test", "title": "Search Results"}

    @pytest.mark.asyncio
    async def test_compress_page_handles_closed_page(self, interactor):
        mock_page = MagicMock()
        mock_page.url = "https://example.com/closed"
        mock_page.is_closed.return_value = True

        result = await interactor._compress_page(mock_page)

        assert result == {"url": "https://example.com/closed", "title": ""}

    @pytest.mark.asyncio
    async def test_compress_page_handles_playwright_error(self, interactor):
        from playwright.async_api import Error as PlaywrightError

        mock_page = MagicMock()
        mock_page.url = "https://example.com/error"
        mock_page.is_closed.return_value = False
        mock_page.title = AsyncMock(side_effect=PlaywrightError("page crashed"))

        result = await interactor._compress_page(mock_page)

        assert result == {"url": "https://example.com/error", "title": ""}


class TestSwitchTab:
    @pytest.mark.asyncio
    async def test_switch_tab_to_valid_index(self, interactor):
        page0 = interactor.page
        page1 = MagicMock()
        page1.url = "https://example.com/detail"
        page1.title = AsyncMock(return_value="Detail Page")
        page1.set_default_timeout = MagicMock()
        page1.bring_to_front = AsyncMock()
        interactor._context.pages = [page0, page1]

        result = await interactor.switch_tab(1)

        assert interactor.page is page1
        assert "switch_tab:1" in result
        assert "https://example.com/detail" in result
        page1.set_default_timeout.assert_called_once_with(12000)
        page1.bring_to_front.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_tab_saves_current_to_background(self, interactor):
        page0 = interactor.page
        page1 = MagicMock()
        page1.url = "https://example.com/detail"
        page1.set_default_timeout = MagicMock()
        page1.bring_to_front = AsyncMock()
        interactor._context.pages = [page0, page1]

        await interactor.switch_tab(1)

        assert len(interactor._background_tabs) == 1
        assert interactor._background_tabs[0]["url"] == "https://example.com/search"

    @pytest.mark.asyncio
    async def test_switch_tab_to_already_active_returns_early(self, interactor):
        interactor._context.pages = [interactor.page]

        result = await interactor.switch_tab(0)

        assert "already active" in result
        assert len(interactor._background_tabs) == 0

    @pytest.mark.asyncio
    async def test_switch_tab_out_of_range_raises(self, interactor):
        interactor._context.pages = [interactor.page]

        with pytest.raises(LookupError, match="out of range"):
            await interactor.switch_tab(5)

    @pytest.mark.asyncio
    async def test_switch_tab_negative_index_raises(self, interactor):
        interactor._context.pages = [interactor.page]

        with pytest.raises(LookupError, match="out of range"):
            await interactor.switch_tab(-1)

    @pytest.mark.asyncio
    async def test_switch_tab_back_and_forth(self, interactor):
        page0 = interactor.page
        page1 = MagicMock()
        page1.url = "https://example.com/detail"
        page1.set_default_timeout = MagicMock()
        page1.bring_to_front = AsyncMock()
        interactor._context.pages = [page0, page1]

        await interactor.switch_tab(1)
        assert interactor.page is page1
        assert len(interactor._background_tabs) == 1

        await interactor.switch_tab(0)
        assert interactor.page is page0
        assert len(interactor._background_tabs) == 2
        assert interactor._background_tabs[1]["url"] == "https://example.com/detail"


class TestClickWithPopupDetection:
    @pytest.mark.asyncio
    async def test_click_same_page_no_popup(self, interactor):
        from playwright.async_api import Error as PlaywrightError

        mock_locator = MagicMock()
        mock_locator.click = AsyncMock()
        interactor.resolve_target = AsyncMock(return_value=mock_locator)
        interactor.ensure_safe = AsyncMock()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=PlaywrightError("timeout"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        interactor._context.expect_page = MagicMock(return_value=mock_ctx)

        result = await interactor.click("Submit")

        assert result == "click:Submit"
        mock_locator.click.assert_called_once()


class TestSafeActionError:
    def test_safe_action_error_is_runtime_error(self):
        err = SafeActionError("blocked")
        assert isinstance(err, RuntimeError)
        assert str(err) == "blocked"

    def test_browser_unavailable_error_is_runtime_error(self):
        from web_agent.browser.interactor import BrowserUnavailableError

        err = BrowserUnavailableError("no browser")
        assert isinstance(err, RuntimeError)
        assert str(err) == "no browser"
