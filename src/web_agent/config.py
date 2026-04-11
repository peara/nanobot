from __future__ import annotations

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_QUALITY_THRESHOLD = 0.48
MAX_VISIBLE_TEXT_CHARS = 5000
MAX_LINKS = 50
MAX_BUTTONS = 30
MAX_INPUTS = 20
MAX_ITEMS = 25
EXPANSION_LABELS = (
    "read more",
    "show more",
    "view more",
    "load more",
    "see more",
    "next",
    "continue",
    "xem them",
    "xem thêm",
    "doc them",
    "đọc thêm",
)
BLOCKED_ACTION_PATTERNS = (
    "pay",
    "payment",
    "purchase",
    "buy now",
    "checkout",
    "delete",
    "remove account",
    "close account",
    "confirm order",
    "place order",
    "submit payment",
    "save card",
)
NON_CONTENT_SELECTORS = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
    "footer",
    "header",
    "nav",
    "aside",
    "form[action*='login']",
    "form[action*='signup']",
    "[role='navigation']",
    "[aria-label*='cookie' i]",
    "[class*='cookie' i]",
    "[id*='cookie' i]",
    "[class*='consent' i]",
    "[id*='consent' i]",
    "[class*='subscribe' i]",
    "[class*='newsletter' i]",
    "[class*='share' i]",
    "[class*='social' i]",
    "[class*='recommend' i]",
    "[class*='related' i]",
    "[class*='sidebar' i]",
    "[class*='promo' i]",
    "[class*='banner' i]",
    "[class*='ads' i]",
)
ARTICLE_SELECTORS = (
    "article",
    "main article",
    "main",
    "[role='main']",
    ".content",
    ".article",
    ".post-content",
    ".entry-content",
    ".markdown-body",
)
LISTING_SELECTORS = (
    "main",
    "[role='main']",
    ".listing",
    ".feed",
    ".posts",
    ".results",
    ".cards",
)
PRODUCT_HINTS = ("price", "add to cart", "buy now", "sku", "product details")
PROFILE_HINTS = ("followers", "following", "bio", "joined", "profile")
DASHBOARD_HINTS = ("dashboard", "analytics", "settings", "billing", "workspace")
LISTING_URL_HINTS = ("category", "categories", "tag", "tags", "topic", "topics", "listing", "search", "latest")
ARTICLE_URL_HINTS = ("article", "post", "story", "blog", "news")
JS_HEAVY_HINTS = (
    "__next",
    "__nuxt",
    "application/json",
    "id=\"root\"",
    "id=\"app\"",
    "data-reactroot",
    "ng-version",
)
