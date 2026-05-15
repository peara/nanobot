# Web Agent

Browser interaction with structured content extraction.

## Overview

NanoBot has two browser systems that work side by side:

1. **External Playwright MCP** — a standalone MCP server (`@playwright/mcp`) that provides raw browser tools (`playwright__navigate`, `playwright__click`, etc.). The LLM calls these directly for fine-grained control.

2. **Built-in web agent** — an MCP server (`nanobot.mcp_servers.web`) that wraps `BrowserInteractor` for structured page interaction, content extraction, and multi-step flows. The LLM calls `interact_page`, `read_page`, or `snapshot_page`.

They coexist. The LLM can use `playwright__*` for raw browser control or `web__*` for structured extraction. The external Playwright MCP is often disabled in production.

## Actions

The `interact_page` tool takes a URL and a list of steps. Each step has an `action` field:

| Action | Required fields | What it does |
|--------|----------------|--------------|
| `click` | `target` | Click an element. Auto-detects new tabs — if the click opens one, the old page is compressed to `{url, title}` and stored in background tabs |
| `type` | `target`, `text` | Fill an input field |
| `select` | `target`, `value` | Select a dropdown option |
| `scroll` | `amount` or `until_text` | Scroll by pixels or until text appears (max 8 iterations) |
| `wait_for` | `selector` or `text` or `network_idle` | Wait for an element, text, or network idle |
| `switch_tab` | `index` | Switch to a background tab by index |
| `snapshot` | — | Take a snapshot of the current page state (no action taken) |
| `screenshot` | — | Full-page screenshot saved to disk |

### Target resolution

The `target` field in `click`, `type`, `select` is a human-readable string, not a CSS selector. `BrowserInteractor.resolve_target()` tries 8 strategies in order:

1. CSS selector (if it looks like one: starts with `#`, `.`, `[`, or contains `>`)
2. Role: button (`get_by_role("button", name=target)`)
3. Role: link (`get_by_role("link", name=target)`)
4. Label (`get_by_label`)
5. Placeholder (`get_by_placeholder`)
6. Text (`get_by_text`)
7. ARIA label (`[aria-label*=target]`)
8. Placeholder attribute (`[placeholder*=target]`)

Returns the first locator that has at least one match.

### Multi-tab support

When `click()` opens a new tab (detected via `context.expect_page()` with a 2-second popup timeout):

1. The old page is compressed to `{url, title}` and stored in `_background_tabs`
2. `self.page` switches to the new tab automatically
3. `background_tabs` property returns the list of compressed background tab summaries
4. `switch_tab(index)` returns to a background tab by `context.pages` index

The `interact_page` response includes `background_tabs` (url + title for each) and `step_urls` (compact URL + title per step) so the LLM knows what tabs are available.

### Safety filtering

Every `click` and `select` call runs through `ensure_safe()`, which checks the target's text against `BLOCKED_ACTION_PATTERNS` — patterns like "pay", "purchase", "delete", "checkout", "confirm delete", etc. If a blocked pattern is found, the action raises `SafeActionError` and is not executed.

## Content extraction pipeline

When `interact_page` finishes executing steps, it doesn't just return the raw HTML. It runs a full extraction pipeline:

### 1. Fetch

`WebAgentTool.run_read_flow()` first tries `fetch_http()` via httpx. If the page is JS-heavy (detected by `<script>` tag count, low word count, or HTTP errors), it escalates to `fetch_browser()` via Playwright.

### 2. Classify

`classify_page(url, html)` uses heuristics to determine the page type:

| Type | Signals |
|------|---------|
| `listing` | URL patterns (`/search`, `/list`), many links in body, pagination links |
| `product` | Price elements, add-to-cart buttons, product schema |
| `profile` | URL patterns (`/user/`, `/profile/`), avatar images |
| `dashboard` | Many form inputs, nav-heavy DOM |
| `article` | Long text blocks, few links relative to text |
| `unknown` | Default |

### 3. Extract

Based on the page type, tries extraction strategies in order:

| Strategy | Method | Best for |
|----------|--------|----------|
| `selector` | CSS selectors (`article`, `main`, `.content`) | Well-structured sites |
| `heuristic` | selectolax scoring by text density vs link density | Generic pages |
| `readability` | trafilatura → readability-lxml fallback | Articles, blog posts |
| `metadata` | OG meta tags + description | Pages with good metadata |
| `crawl4ai` | Crawl4AI with PruningContentFilter | Complex pages |
| `listing` | Extracts repeating item structures | Search results, lists |

Each strategy returns extracted text. If the quality score meets the threshold, extraction stops.

### 4. Score

`score_content()` evaluates extracted content with a weighted formula:

- 42% — length (word count)
- 24% — sentence quality (complete sentences ratio)
- 20% — uniqueness (distinct word ratio)
- 14% — low link ratio (fewer links = more content-like)
- Penalty — repetition detection

Default quality threshold is 0.48 (configurable via `quality_threshold`). Content scoring below the threshold with fewer than 60 words triggers a fallback to the next strategy.

### 5. Chrome dedup

First call to a domain stores all navigation elements (headers, footers, repeated sidebar links) as a baseline. Subsequent calls split items and links into `content_*` (new/dynamic) vs `chrome_*` (matching baseline). Chrome items are moved to a separate field and excluded from the main payload, reducing token waste on repeated navigation elements.

## MCP tools

The web MCP server (`python -m nanobot.mcp_servers.web.server`) exposes these tools:

| Tool | Type | Description |
|------|------|-------------|
| `web_health` | sync | Dependency readiness check + runtime settings |
| `web__search_web` | async | Search via Tavily or Exa (requires API key) |
| `web__read_page` | async | Fetch + extract content from a URL |
| `web__snapshot_page` | async | Open browser, take one snapshot, return structured result |
| `web__interact_page` | async | Full interaction: open URL, execute steps, extract content |
| `web__domain_chrome` | sync | Retrieve stored navigation chrome for a domain |
| `web__create_script` | sync | Create a NanoScript (sandboxed Python extraction script) |
| `web__search_scripts` | sync | Semantic search for existing scripts |
| `web__invoke_script` | async | Run a NanoScript in a sandbox |

### Search providers

`web__search_web` supports two providers:

| Provider | Env var | Features |
|----------|---------|----------|
| Tavily | `TAVILY_API_KEY` | Auto-detects finance queries, `topic=news` for freshness |
| Exa | `EXA_API_KEY` | Domain filtering, date range filtering |

With `provider="auto"` (default), Tavily is tried first, then Exa as fallback.

## Snapshots

`browser.snapshot()` returns a `SnapshotResult` dataclass with:

| Field | Type | Description |
|-------|------|-------------|
| `url` | str | Current page URL |
| `title` | str | Page title |
| `visible_text` | str | Body inner text, normalized and truncated to 5000 chars |
| `buttons` | list[dict] | Clickable buttons: `{text, type, aria_label}` (max 30) |
| `links` | list[dict] | Links: `{text, href}` (max 50) |
| `inputs` | list[dict] | Form inputs: `{name, type, placeholder, label}` (max 20) |
| `candidate_actions` | list[str] | Suggested next actions, e.g. `["click:Next", "click:Search"]` (max 15) |

The `candidate_actions` list is built from filtered buttons and links — blocked patterns are excluded, and common action hints (login, search, expansion labels) are prioritized.

## Hook integration

Two hooks track browser-related tool events:

### BrowseEventRecorderHook

Records `playwright__*` tool calls to the context store under `browse_history`. Each entry stores:
- Page URL and title (extracted from MCP result text)
- Whether the page was blocked (detected by title/URL heuristics)
- Timestamp

Keeps the last 40 browse events per scope.

### ToolResultRecorderHook

Records all tool call events (not just browser) to `tool_results`. Each entry:
- Tool name, args, ok/error status
- Result preview (clipped)
- Timestamp

Keeps the last 60 events per scope.

## Configuration

```yaml
# config.yaml
mcp_servers:
  web:
    command: python
    args: ["-m", "nanobot.mcp_servers.web.server"]
    env:
      TAVILY_API_KEY: "${TAVILY_API_KEY}"
      EXA_API_KEY: "${EXA_API_KEY}"
      WEB_AGENT_HEADLESS: "true"
      WEB_AGENT_QUALITY_THRESHOLD: "0.48"

  # Optional: raw Playwright MCP for direct browser control
  # playwright:
  #   command: npx
  #   args: ["-y", "@playwright/mcp@latest", "--browser", "chrome", "--headless"]
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_AGENT_HEADLESS` | `true` | Run browser in headless mode |
| `WEB_AGENT_SAVE_OUTPUTS` | `false` | Save extraction results to `data/outputs/` |
| `WEB_AGENT_QUALITY_THRESHOLD` | `0.48` | Minimum content quality score to accept |