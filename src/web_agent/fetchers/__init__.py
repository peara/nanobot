from .browser_fetcher import fetch_browser
from .http_fetcher import fetch_http, should_escalate_fetch

__all__ = ["fetch_browser", "fetch_http", "should_escalate_fetch"]
