# Plugin System

Scavengarr uses a plugin-driven architecture. Each plugin defines how to scrape
a specific source site. Plugins are either **YAML** (declarative) or **Python**
(imperative).

## Plugin Directory

Plugins are stored in the directory configured by `plugin_dir` (default: `./plugins`).
The registry scans for `.yaml`, `.yml`, and `.py` files at startup.

## Plugin Discovery

1. **Startup:** The registry indexes all files in `plugin_dir` (file scan only, no parsing)
2. **First access:** A plugin is parsed/loaded when `registry.get(name)` is called
3. **Caching:** Loaded plugins are cached in memory for the process lifetime

## YAML Plugins

YAML plugins define scraping rules declaratively using CSS selectors and URL templates.

### Minimal Example

```yaml
name: "example-site"
version: "1.0.0"
base_url: "https://example.com"

scraping:
  mode: "scrapy"
  start_stage: "search_results"

  stages:
    - name: "search_results"
      type: "list"
      url_pattern: "/search/{query}"
      selectors:
        link: "a.result-title"
        title: "a.result-title::text"
      field_attributes:
        link: ["href"]
      next_stage: "detail_page"

    - name: "detail_page"
      type: "detail"
      selectors:
        title: "h1.title"
        download_link: "a.download-btn"
      field_attributes:
        download_link: ["href"]

auth:
  type: "none"
```

### Full Plugin Schema

```yaml
# --- Plugin Metadata ---
name: "plugin-name"              # Unique identifier (required)
version: "1.0.0"                 # Semantic version (required)
base_url: "https://example.com"  # Root URL (required)

# --- Scraping Configuration ---
scraping:
  mode: "scrapy"                 # "scrapy" (static HTML) or "playwright" (JS-heavy)
  start_stage: "search_results"  # Entry point stage name
  max_depth: 4                   # Maximum cascading depth
  delay_seconds: 2.0             # Delay between requests (seconds)

  stages:
    - name: "search_results"     # Stage identifier
      type: "list"               # "list" (intermediate) or "detail" (terminal)
      url_pattern: "/search/{query}"  # URL template ({query} is substituted)

      selectors:
        # --- Simple Selectors (CSS) ---
        link: "a[href*='/detail/']"        # Link to next stage (list type)
        title: "h2.title"                   # Text content extraction
        download_link: "a.download"          # Terminal download URL
        seeders: "span.seeds"                # Numeric values
        leechers: "span.leeches"
        size: "span.size"
        release_name: "span.release"
        description: "p.description"

        # --- Nested Extraction ---
        download_links:
          container: "div.downloads"         # Outer container selector
          item_group: "ul"                   # Group selector within container
          items: "li"                        # Individual item selector
          fields:
            hoster_name: "p.host"            # Named fields within each item
            link: "a.button"
          field_attributes:
            link: ["href", "data-url"]       # Extract attributes instead of text
          multi_value_fields: ["link"]        # Fields with multiple values

      # --- Field Attribute Extraction ---
      field_attributes:
        link: ["href"]                       # Extract href instead of text content

      # --- Pagination ---
      pagination:
        enabled: true
        selector: "a.next-page"              # Next page link selector
        max_pages: 5                         # Maximum pages to follow

      next_stage: "detail_page"              # Next stage name (list type only)

# --- Authentication ---
auth:
  type: "none"                   # "none", "basic", "form", "cookie"
  username: ""
  password: ""
  login_url: ""
  username_field: ""
  password_field: ""
  submit_selector: ""

# --- HTTP Overrides (per-plugin) ---
http:
  timeout_seconds: 15.0
  follow_redirects: true
  user_agent: "CustomAgent/1.0"
```

### Stage Types

| Type | Purpose | Required Selector | Output |
|------|---------|-------------------|--------|
| `list` | Intermediate stage | `link` | URLs for `next_stage` |
| `detail` | Terminal stage | `download_link` or `download_links` | `SearchResult` objects |

### Selector Extraction

By default, selectors extract **text content**. To extract an HTML attribute instead,
use `field_attributes`:

```yaml
selectors:
  link: "a.result"             # CSS selector
field_attributes:
  link: ["href"]               # Extract href attribute instead of text
```

Multiple attributes can be listed as fallbacks. The first non-empty value wins:

```yaml
field_attributes:
  link: ["data-player-url", "href", "onclick"]
```

### Nested Download Links

For detail pages with grouped download links (e.g., multiple hosters):

```yaml
selectors:
  download_links:
    container: "div#download-list"   # Outer wrapper
    item_group: "ul.hoster-links"    # Group within container
    items: "li"                      # Each download item
    fields:
      hoster_name: "p.hostName"
      link: "a.button"
    field_attributes:
      link: ["href"]
```

This extracts each hoster's link as a separate download option.

## Multi-Stage Execution

Stages execute sequentially. Within each stage, URLs are processed **in parallel**
(bounded by concurrency limits).

### Example: Two-Stage Pipeline

```
Stage 1: search_results (type: list)
  Input:  /search/iron+man
  Output: [/stream/123, /stream/456, /stream/789]
              |            |            |
              v            v            v
Stage 2: movie_detail (type: detail)  [parallel]
  Input:  /stream/123    /stream/456    /stream/789
  Output: SearchResult   SearchResult   SearchResult
```

### Execution Rules

1. Stage 1 (list) extracts URLs via the `link` selector
2. All extracted URLs feed into Stage 2 in parallel
3. Stage 2 (detail) extracts `SearchResult` data from each page
4. Results are deduplicated by `(title, download_link)` tuple
5. Dead links are filtered via link validation (parallel HEAD requests)

## Real-World Example

The included `filmpalast.to.yaml` plugin demonstrates a two-stage pipeline:

```yaml
name: "filmpalast"
version: "1.0.0"
base_url: "https://filmpalast.to"

scraping:
  mode: "scrapy"
  start_stage: "search_results"
  max_depth: 4
  delay_seconds: 2.0

  stages:
    - name: "search_results"
      type: "list"
      url_pattern: "/search/title/{query}"
      selectors:
        link: "a[href*='/stream/']"
        title: "h2.bgDark"
      field_attributes:
        link: ["href"]
      next_stage: "movie_detail"

    - name: "movie_detail"
      type: "detail"
      url_pattern: "/stream/{movie_id}"
      selectors:
        title: "h2.bgDark"
        release_name: "span#release_text"
        description: "span[itemprop='description']"
        download_links:
          container: "div#grap-stream-list"
          item_group: "ul.currentStreamLinks"
          items: "li"
          fields:
            hoster_name: "p.hostName, p"
            link: "a.button.iconPlay, a.button"
          field_attributes:
            link: ["data-player-url", "href", "onclick"]

auth:
  type: "none"

http:
  timeout_seconds: 15.0
  follow_redirects: true
  user_agent: "Scavengarr/1.0"
```

## Creating a New Plugin

1. Create a `.yaml` file in the plugin directory
2. Define required fields: `name`, `version`, `base_url`, `scraping`
3. Add at least one stage with appropriate selectors
4. Set `auth.type` (use `none` if no authentication needed)
5. Restart the server to discover the new plugin
6. Test: `GET /api/v1/torznab/{name}?t=search&q=test`

### Tips

- Use browser DevTools to identify CSS selectors
- Start with a simple list+detail pipeline
- Set `delay_seconds` to avoid rate limiting
- Use `field_attributes` when links are in `href` attributes (not text content)
- Check health: `GET /api/v1/torznab/{name}/health`
