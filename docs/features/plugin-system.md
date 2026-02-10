[← Back to Index](./README.md)

# YAML Plugin System

> Declarative plugins define scraping rules using CSS selectors and URL templates, processed by the Scrapy engine through a multi-stage pipeline.

---

## Overview

YAML plugins are the primary way to add new indexer sources to Scavengarr. They describe **what** to scrape (selectors, URLs, stages) without writing any code. The scraping engine interprets the plugin definition and executes the multi-stage pipeline automatically.

Key characteristics:
- **Declarative:** CSS selectors and URL patterns, no imperative logic
- **Multi-stage:** Pipeline from search results to detail pages to download links
- **Validated:** Pydantic schema validation on load with clear error messages
- **Lazy-loaded:** Parsed only when first accessed, cached in memory afterward
- **Extensible:** Nested selectors, pagination, auth, HTTP overrides

---

## Plugin Discovery & Loading

### Discovery Flow

```
Server startup
  |
  v
PluginRegistry.discover()
  |-- Scans plugin_dir for .yaml, .yml, .py files
  |-- Indexes files by path (NO parsing yet)
  |-- Logs: "plugins_discovered count=N"
  |
  v
First request for plugin "filmpalast"
  |
  v
PluginRegistry.get("filmpalast")
  |-- Reads YAML file
  |-- Validates via Pydantic schema
  |-- Converts to domain model (YamlPluginDefinition)
  |-- Caches in memory
  |-- Returns domain model
```

### Key Behaviors

1. **File scan only at startup:** `discover()` reads no file contents, just indexes paths by extension
2. **Lazy loading:** YAML is parsed and validated only on first `get()` call
3. **In-memory caching:** Once loaded, the plugin stays cached for the process lifetime
4. **Name peeking:** `list_names()` does a lightweight YAML parse (reads only the `name` field)
5. **Duplicate detection:** `load_all()` raises `DuplicatePluginError` if two plugins share a name

### Plugin Directory

The default plugin directory is `./plugins`. Override via configuration:

```bash
# Environment variable
export SCAVENGARR_PLUGIN_DIR=/path/to/plugins

# CLI argument
start --plugin-dir /path/to/plugins
```

Supported file extensions: `.yaml`, `.yml` (YAML plugins), `.py` (Python plugins).

---

## YAML Plugin Schema

### Minimal Example

The smallest valid YAML plugin requires a name, version, base URL, scraping mode, and at least one stage:

```yaml
# plugins/example-site.yaml
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
      next_stage: "detail_page"

    - name: "detail_page"
      type: "detail"
      url_pattern: "/detail/{id}"
      selectors:
        title: "h1.title"
        download_link: "a.download-btn"

auth:
  type: "none"
```

### Full Schema Reference

```yaml
# ============================================================
# Plugin Metadata (required)
# ============================================================
name: "plugin-name"              # Unique ID, lowercase + hyphens [a-z0-9-]+
version: "1.0.0"                 # Semantic version (X.Y.Z)
base_url: "https://example.com"  # Primary URL (string or list for mirrors)

# ============================================================
# Scraping Configuration (required)
# ============================================================
scraping:
  mode: "scrapy"                 # "scrapy" (static HTML) or "playwright" (JS-heavy)
  start_stage: "search_results"  # Entry point stage name
  max_depth: 5                   # Maximum cascading depth (default: 5)
  delay_seconds: 1.5             # Delay between requests in seconds (default: 1.5)

  stages:                        # At least one stage required for scrapy mode
    - name: "stage_name"         # Identifier [a-z0-9_]+
      type: "list"               # "list" (intermediate) or "detail" (terminal)
      url_pattern: "/path/{var}" # URL template with {variable} substitution
      # OR
      url: "/static/path"        # Fixed URL (no substitution)

      selectors:                 # At least one selector required
        # --- Simple Selectors (CSS) ---
        link: "a.selector"           # Link to next stage (required for list type)
        title: "h2.title"            # Title text
        description: "p.desc"        # Description text
        release_name: "span.release" # Release/file name
        download_link: "a.download"  # Direct download URL
        seeders: "span.seeds"        # Seeder count
        leechers: "span.leeches"     # Leecher count
        size: "span.size"            # File size (human-readable)
        published_date: "span.date"  # Publication date

        # --- Nested Selectors ---
        download_links:
          container: "div.downloads"      # Outer wrapper
          item_group: "ul.group"          # Optional grouping within container
          items: "li"                     # Individual items
          fields:                         # At least one field required
            hoster_name: "p.host"         # Named field -> CSS selector
            link: "a.button"              # Link fields must have field_attributes
          field_attributes:
            link: ["href", "data-url"]    # Ordered attribute fallback list
          multi_value_fields: ["link"]    # Fields that collect multiple values

        # --- Custom Fields ---
        custom:
          my_field: "span.custom"         # Arbitrary named selectors

      # --- Field Attributes (for simple selectors) ---
      field_attributes:
        link: ["href"]                    # Extract attribute instead of text

      # --- Pagination ---
      pagination:
        enabled: true
        selector: "a.next-page"           # Next page link selector
        max_pages: 5                      # Maximum pages to follow

      # --- Stage Navigation ---
      next_stage: "next_stage_name"       # Next stage (list type only)

      # --- Conditions (optional) ---
      conditions:
        min_results: 1                    # Custom processing conditions

# ============================================================
# Authentication (optional, defaults to "none")
# ============================================================
auth:
  type: "none"                   # "none" | "basic" | "form" | "cookie"
  username: "user"               # Direct credentials
  password: "pass"
  username_env: "MY_USER_ENV"    # OR read from environment variable
  password_env: "MY_PASS_ENV"
  login_url: "https://example.com/login"  # Required for form auth
  username_field: "input#user"            # CSS selector for username input
  password_field: "input#pass"            # CSS selector for password input
  submit_selector: "button#login"         # CSS selector for submit button

# ============================================================
# HTTP Overrides (optional)
# ============================================================
http:
  timeout_seconds: 15.0          # Request timeout (must be > 0)
  follow_redirects: true         # Follow HTTP redirects
  user_agent: "Scavengarr/1.0"  # Custom User-Agent header
```

---

## Plugin Metadata

### name

Unique plugin identifier. Must match the pattern `^[a-z0-9-]+$` (lowercase letters, digits, hyphens only).

```yaml
name: "filmpalast"     # valid
name: "my-site-v2"     # valid
name: "My Site"        # INVALID (uppercase, spaces)
```

The name is used in:
- Torznab endpoint URL: `/api/v1/torznab/filmpalast?t=search&q=...`
- Plugin registry lookups: `registry.get("filmpalast")`
- Log context: `plugin=filmpalast`

### version

Semantic version string matching `^\d+\.\d+\.\d+$`.

```yaml
version: "1.0.0"       # valid
version: "2.1.3"       # valid
version: "1.0"         # INVALID (missing patch)
```

### base_url

Primary site URL. Accepts a single string or a list for mirror fallback:

```yaml
# Single URL
base_url: "https://filmpalast.to"

# Multiple mirrors (first is primary, rest are fallbacks)
base_url:
  - "https://boerse.am"
  - "https://boerse.sx"
  - "https://boerse.im"
```

When a list is provided, the first URL becomes `base_url` and the rest populate `mirror_urls` in the domain model.

---

## Scraping Configuration

### mode

Selects the scraping engine:

| Mode | Engine | Use Case |
|---|---|---|
| `"scrapy"` | Scrapy (httpx) | Static HTML sites, server-rendered pages |
| `"playwright"` | Playwright (Chromium) | JS-heavy sites, SPAs, Cloudflare-protected |

Currently, only `"scrapy"` mode is fully implemented for YAML plugins. Playwright mode for YAML plugins is planned.

### start_stage

Names the entry point stage. If omitted, the first stage in the `stages` list is used.

### max_depth

Maximum number of stage cascades allowed. Prevents runaway recursion if stages form a cycle or chain too deeply. Default: `5`.

### delay_seconds

Minimum delay between HTTP requests in seconds. Prevents rate-limiting by target sites. Default: `1.5`. Must be >= 0.

---

## Stage Configuration

### Stage Types

| Type | Purpose | Required Selector | Output |
|---|---|---|---|
| `"list"` | Intermediate -- extracts URLs for the next stage | `link` | List of URLs fed to `next_stage` |
| `"detail"` | Terminal -- extracts data for `SearchResult` objects | `download_link` or `download_links` | `SearchResult` entities |

### Stage Flow

Stages chain via the `next_stage` field:

```
search_results (list) --next_stage--> movie_detail (detail)
```

A stage without `next_stage` is terminal. List stages **must** have `next_stage` and a `link` selector. Detail stages produce final `SearchResult` objects.

### url vs url_pattern

- `url`: Fixed path, used as-is (e.g., `url: "/latest"`)
- `url_pattern`: Template with `{variable}` placeholders (e.g., `url_pattern: "/search/{query}"`)

The `{query}` placeholder is substituted with the search term. Other placeholders (like `{movie_id}`) are filled from data extracted by previous stages.

---

## Selectors

### Simple Selectors

Simple selectors use CSS selector syntax to extract text content from HTML elements:

```yaml
selectors:
  title: "h2.bgDark"                   # Extract text from <h2 class="bgDark">
  description: "span[itemprop='description']"
  release_name: "span#release_text"
  size: "span.filesize"
```

By default, selectors extract the **text content** of the matched element.

### Field Attributes

To extract an HTML attribute instead of text content, use `field_attributes`:

```yaml
selectors:
  link: "a[href*='/stream/']"
field_attributes:
  link: ["href"]                  # Extract href attribute, not text
```

Multiple attributes can be specified as an ordered fallback list. The first non-empty value wins:

```yaml
field_attributes:
  link: ["data-player-url", "href", "onclick"]
```

This is essential for extracting URLs, since link text is usually a label, not the URL itself.

### Nested Selectors (download_links)

For detail pages with grouped download links (e.g., multiple file hosters), use the nested `download_links` structure:

```yaml
selectors:
  download_links:
    container: "div#grap-stream-list"      # 1. Find outer container
    item_group: "ul.currentStreamLinks"    # 2. Optional: group within container
    items: "li"                            # 3. Individual items within group
    fields:                                # 4. Extract fields from each item
      hoster_name: "p.hostName, p"
      link: "a.button.iconPlay, a.button"
    field_attributes:
      link: ["data-player-url", "href", "onclick"]
```

**Extraction hierarchy:**

```
container (div#grap-stream-list)
  |
  +-- item_group (ul.currentStreamLinks)  [optional]
  |     |
  |     +-- items (li)
  |     |     +-- fields.hoster_name -> "RapidGator"
  |     |     +-- fields.link -> "https://keeplinks.org/..."
  |     |
  |     +-- items (li)
  |           +-- fields.hoster_name -> "DDownload"
  |           +-- fields.link -> "https://keeplinks.org/..."
  |
  +-- item_group (ul.currentStreamLinks)
        ...
```

**Validation rules:**
- `fields` must contain at least one entry
- Any field ending in `link` or `url` **must** have a corresponding entry in `field_attributes`
- `item_group` is optional (if omitted, `items` are searched directly within `container`)
- `multi_value_fields` specifies fields that collect multiple values as a list

### Custom Selectors

For site-specific data that does not fit predefined fields:

```yaml
selectors:
  custom:
    imdb_id: "a[href*='imdb.com']::attr(href)"
    quality: "span.quality::text"
```

---

## Authentication

### Auth Types

| Type | Requirements | Use Case |
|---|---|---|
| `"none"` | None | Public sites (default) |
| `"basic"` | `username`, `password` | HTTP Basic Auth |
| `"form"` | `login_url`, `username_field`, `password_field`, `submit_selector`, credentials | HTML form login |
| `"cookie"` | Varies | Pre-existing session cookies |

### Environment Variable Credentials

Credentials can be read from environment variables instead of being hardcoded in the YAML file:

```yaml
auth:
  type: "form"
  username_env: "MY_SITE_USERNAME"   # Reads os.environ["MY_SITE_USERNAME"]
  password_env: "MY_SITE_PASSWORD"   # Reads os.environ["MY_SITE_PASSWORD"]
  login_url: "https://example.com/login"
  username_field: "input#username"
  password_field: "input#password"
  submit_selector: "button[type='submit']"
```

If both `username` and `username_env` are set, the direct value takes precedence. The env var is used only when the direct field is empty.

### Form Auth Example

```yaml
auth:
  type: "form"
  username: "myuser"
  password: "mypass"
  login_url: "https://example.com/login"
  username_field: "input[name='user']"
  password_field: "input[name='pass']"
  submit_selector: "button#login-btn"
```

---

## HTTP Overrides

Per-plugin HTTP settings override global defaults:

```yaml
http:
  timeout_seconds: 15.0          # Request timeout (default: global setting)
  follow_redirects: true         # Follow redirects (default: global setting)
  user_agent: "Scavengarr/1.0"  # Custom User-Agent (default: global setting)
```

These overrides apply to all HTTP requests made for this plugin.

---

## Real-World Example: filmpalast.to

The included `filmpalast.to.yaml` demonstrates a complete two-stage pipeline:

```yaml
# plugins/filmpalast.to.yaml
name: "filmpalast"
version: "1.0.0"
base_url: "https://filmpalast.to"

scraping:
  mode: "scrapy"
  start_stage: "search_results"
  max_depth: 4
  delay_seconds: 2.0

  stages:
    # Stage 1: Search results page (list type)
    # Extracts links to individual movie detail pages
    - name: "search_results"
      type: "list"
      url_pattern: "/search/title/{query}"
      selectors:
        link: "a[href*='/stream/']"
        title: "h2.bgDark"
      field_attributes:
        link: ["href"]
      next_stage: "movie_detail"

    # Stage 2: Movie detail page (detail type)
    # Extracts title, metadata, and download links from each movie
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

**Execution flow:**

1. User searches: `GET /api/v1/torznab/filmpalast?t=search&q=iron+man`
2. Stage 1 fetches `https://filmpalast.to/search/title/iron+man`
3. Extracts all `<a href*="/stream/">` links from the results page
4. Stage 2 fetches each `/stream/{id}` page **in parallel**
5. Extracts title, release name, description, and nested download links
6. Link validation checks all extracted download URLs
7. Valid results are packaged as `SearchResult` entities
8. CrawlJobs are generated and cached
9. Torznab XML response is returned to the client

---

## Validation Rules

The Pydantic validation schema enforces these rules on YAML plugins:

| Rule | Scope | Error |
|---|---|---|
| Name matches `^[a-z0-9-]+$` | Plugin | Invalid plugin name |
| Version matches `^\d+\.\d+\.\d+$` | Plugin | Invalid version format |
| `base_url` is valid HTTP(S) URL | Plugin | Invalid base URL |
| `mode` is `"scrapy"` or `"playwright"` | Scraping | Invalid scraping mode |
| Scrapy mode has at least one stage | Scraping | Missing stages |
| `start_stage` references an existing stage | Scraping | Unknown start stage |
| Stage name matches `^[a-z0-9_]+$` | Stage | Invalid stage name |
| Stage has `url` or `url_pattern` | Stage | Missing URL |
| List stages have a `link` selector | Stage | Missing link selector |
| `next_stage` references an existing stage | Stage | Unknown next stage |
| At least one selector per stage | Selectors | No selectors defined |
| Link/URL fields in nested selectors have `field_attributes` | Nested | Missing attribute definition |
| `delay_seconds` >= 0 | Scraping | Invalid delay |
| `max_pages` >= 1 when pagination enabled | Pagination | Invalid max pages |
| Form auth has all required fields | Auth | Missing auth fields |

---

## Creating a New YAML Plugin

### Step-by-Step

1. **Inspect the target site** using browser DevTools
   - Identify the search URL pattern
   - Find CSS selectors for result links and data fields
   - Check if the site is static HTML (scrapy) or JS-heavy (playwright)

2. **Create the YAML file** in the plugin directory:
   ```bash
   touch plugins/my-site.yaml
   ```

3. **Define metadata:**
   ```yaml
   name: "my-site"
   version: "1.0.0"
   base_url: "https://my-site.com"
   ```

4. **Add scraping stages:**
   - Start with a `list` stage for search results
   - Add a `detail` stage for individual pages
   - Use `field_attributes` for all URL/link extractions

5. **Set authentication** (use `"none"` for public sites)

6. **Restart the server** to trigger plugin discovery

7. **Test the plugin:**
   ```bash
   curl "http://localhost:8080/api/v1/torznab/my-site?t=search&q=test"
   ```

### Common Mistakes

| Mistake | Fix |
|---|---|
| Forgetting `field_attributes` for links | Always specify `field_attributes` for selectors that extract URLs |
| Using text extraction for URLs | Link text is usually a label; use `field_attributes: ["href"]` |
| Too broad selectors | Be specific to avoid matching unrelated elements |
| Missing `next_stage` on list stages | List stages must chain to a next stage |
| Hardcoding credentials in YAML | Use `username_env` / `password_env` for secrets |

---

## Source Code References

| Component | Path |
|---|---|
| Domain schema (dataclasses) | `src/scavengarr/domain/plugins/plugin_schema.py` |
| Plugin protocol | `src/scavengarr/domain/plugins/base.py` |
| Plugin exceptions | `src/scavengarr/domain/plugins/exceptions.py` |
| Pydantic validation schema | `src/scavengarr/infrastructure/plugins/validation_schema.py` |
| Plugin registry | `src/scavengarr/infrastructure/plugins/registry.py` |
| Plugin loader | `src/scavengarr/infrastructure/plugins/loader.py` |
| Pydantic-to-domain adapters | `src/scavengarr/infrastructure/plugins/adapters.py` |
| Example YAML plugin | `plugins/filmpalast.to.yaml` |
| Plugin schema tests | `tests/unit/domain/test_plugin_schema.py` |
