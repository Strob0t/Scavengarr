[← Back to Index](./README.md)

# Mirror URL Fallback

Scavengarr supports mirror URL fallback for indexer sites that operate across multiple
domains. When a primary domain becomes unreachable, the system automatically tries
configured mirror URLs to maintain scraping availability. This feature works for both
YAML plugins (via `mirror_urls` configuration) and Python plugins (via custom domain
iteration logic).

---

## Overview

Many indexer and forum sites operate multiple mirror domains for redundancy. A site
might be available at `example.sx`, `example.am`, `example.im`, and others. Scavengarr
handles domain failures transparently:

```
Primary Domain Request
     │
     ▼
┌─────────────────────────┐
│  Fetch with retry       │
│  (exponential backoff,  │
│   max_retries attempts) │
└──────────┬──────────────┘
           │
           ▼ (all retries failed with network error)
┌─────────────────────────┐
│  Try Mirror URLs        │
│  (iterate configured    │
│   mirrors in order)     │
└──────────┬──────────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
  Success     All Failed
     │           │
     ▼           ▼
  Switch      Return None
  Domain      (log error)
```

---

## YAML Plugin Configuration

Mirror URLs are declared in the plugin's YAML definition:

```yaml
# plugins/example-site.yaml
name: "example-site"
version: "1.0.0"
base_url: "https://example.sx"
mirror_urls:
  - "https://example.am"
  - "https://example.im"
  - "https://example.ai"
  - "https://example.kz"

scraping:
  mode: "scrapy"
  stages:
    - name: "search_results"
      type: "list"
      # ...
```

The `mirror_urls` field accepts a list of alternative base URLs. The order determines
the fallback priority -- mirrors are tried from first to last.

### Schema

```python
# src/scavengarr/domain/plugins/plugin_schema.py
@dataclass(frozen=True)
class YamlPluginDefinition:
    name: str
    version: str
    base_url: str
    scraping: ScrapingConfig
    mirror_urls: list[str] | None = None  # Alternative base URLs
    # ...
```

---

## ScrapyAdapter Mirror Logic

The `ScrapyAdapter` implements mirror fallback at the HTTP fetching layer. When all
retries for a request fail with a network error, the adapter attempts each mirror URL
before giving up.

### Initialization

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py
class ScrapyAdapter:
    def __init__(self, plugin: YamlPluginDefinition, ...):
        self.base_url = str(plugin.base_url)
        self._mirror_urls: list[str] = (
            [str(u) for u in plugin.mirror_urls] if plugin.mirror_urls else []
        )
```

### Trigger Condition

Mirror fallback is triggered only when:
1. All `max_retries` attempts have been exhausted
2. The failure is a **network error** (`httpx.RequestError`)
3. At least one mirror URL is configured

Mirror fallback is **not** triggered for HTTP status errors (4xx/5xx), as these
indicate the server is reachable but the content is unavailable.

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py (_fetch_page)
except httpx.RequestError as e:
    if attempt < self.max_retries - 1:
        backoff = self.retry_backoff_base ** attempt
        await asyncio.sleep(backoff)
    else:
        # All retries failed -- try mirrors
        mirror_result = await self._try_mirrors(url)
        if mirror_result is not None:
            return mirror_result
        return None
```

### `_try_mirrors()`

The mirror iteration method replaces the domain in the failed URL and tries each mirror:

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py
async def _try_mirrors(self, url: str) -> BeautifulSoup | None:
    if not self._mirror_urls:
        return None

    for mirror in self._mirror_urls:
        if mirror == self.base_url:
            continue  # Skip current domain

        new_url = self._replace_domain(url, mirror)

        try:
            await asyncio.sleep(self.delay)
            resp = await self.client.get(new_url)
            resp.raise_for_status()
            self._switch_domain(mirror)  # Persist the switch
            return BeautifulSoup(resp.content, "html.parser")
        except (httpx.RequestError, httpx.HTTPStatusError):
            continue  # Try next mirror

    return None  # All mirrors failed
```

Key behaviors:
- Mirrors matching the current `base_url` are skipped
- Rate limiting (`delay`) is applied before each mirror request
- On success, the adapter **switches its domain permanently** for the rest of the session
- On failure, the next mirror is tried
- If all mirrors fail, `None` is returned and the page fetch is skipped

### `_switch_domain()`

When a mirror succeeds, the adapter updates `base_url` on itself and all stage scrapers:

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py
def _switch_domain(self, new_base_url: str) -> None:
    self.base_url = new_base_url
    for stage in self.stages.values():
        stage.base_url = new_base_url
```

This ensures all subsequent requests (including next-stage URL construction via
`urljoin`) use the working mirror domain.

### `_replace_domain()`

The URL rewriting preserves the original path, query, and fragment while swapping
only the scheme and network location:

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py
@staticmethod
def _replace_domain(url: str, new_base_url: str) -> str:
    old = urlsplit(url)
    new = urlsplit(new_base_url)
    return urlunsplit((new.scheme, new.netloc, old.path, old.query, old.fragment))
```

Example:
```
Original:  https://example.sx/search/movies?q=iron+man
Mirror:    https://example.am
Result:    https://example.am/search/movies?q=iron+man
```

---

## Health Endpoint Mirror Probing

The health endpoint (`GET /api/v1/torznab/{plugin_name}/health`) also supports mirror
probing. When the primary domain is unreachable, it checks each configured mirror:

```python
# src/scavengarr/interfaces/api/torznab/router.py
mirror_urls: list[str] = list(getattr(plugin, "mirror_urls", None) or [])
if mirror_urls:
    mirror_results: list[dict[str, object]] = []
    if not reachable:
        for m_url in mirror_urls:
            m_ok, m_sc, m_err, m_checked = await _lightweight_http_probe(
                state.http_client, base_url=m_url, timeout_seconds=5.0
            )
            mirror_results.append({
                "url": m_url,
                "reachable": m_ok,
                "status_code": m_sc if m_ok else None,
                "error": m_err if not m_ok else None,
            })
    content["mirrors"] = mirror_results
```

### Health Response with Mirrors

When the primary is down and mirrors are probed:

```json
{
  "plugin": "example-site",
  "base_url": "https://example.sx",
  "checked_url": "https://example.sx",
  "reachable": false,
  "status_code": null,
  "error": "Connection refused",
  "mirrors": [
    {
      "url": "https://example.am",
      "reachable": true,
      "status_code": 200
    },
    {
      "url": "https://example.im",
      "reachable": false,
      "error": "Connection timeout"
    }
  ]
}
```

When the primary is reachable, the `mirrors` array is present but empty (mirrors are
only probed when needed to avoid unnecessary requests).

---

## Python Plugin Mirror Support

Python plugins can implement their own domain fallback logic for more complex scenarios
like authentication across mirrors.

### Example: boerse.py

The `boerse.py` plugin maintains a list of 5 mirror domains and tries each during login:

```python
# plugins/boerse.py
_DOMAINS = [
    "https://boerse.am",
    "https://boerse.sx",
    "https://boerse.im",
    "https://boerse.ai",
    "https://boerse.kz",
]

class BoersePlugin:
    def __init__(self) -> None:
        self._domains = list(_DOMAINS)
        self.base_url = self._domains[0]

    async def _ensure_session(self) -> None:
        for domain in self._domains:
            try:
                # Navigate to domain
                # Attempt login via vBulletin form
                # Verify session cookie
                if has_session:
                    self.base_url = domain  # Switch to working domain
                    self._logged_in = True
                    return
            except Exception:
                continue  # Try next domain

        raise RuntimeError("All boerse domains failed during login")
```

Key differences from YAML plugin mirror support:
- **Login-aware**: Each domain attempt includes full authentication
- **Session-based**: Uses Playwright browser context with cookies
- **Cloudflare handling**: Waits for JS challenge resolution on each domain
- **Domain-independent**: The working domain is used for all subsequent requests

---

## Comparison: YAML vs Python Mirror Support

| Aspect | YAML Plugins | Python Plugins |
|---|---|---|
| Configuration | `mirror_urls` field in YAML | Custom `_DOMAINS` list in code |
| Trigger | Network error after all retries | Login failure or connection error |
| Scope | Per-request fallback | Per-session fallback |
| Authentication | Not supported (mirrors assumed identical) | Full auth per domain attempt |
| Domain switch | Automatic (`_switch_domain`) | Manual (`self.base_url = domain`) |
| Persistence | Session-wide (adapter lifetime) | Session-wide (plugin lifetime) |

---

## Best Practices

### When to Use Mirrors

- Sites that operate multiple TLDs (`.sx`, `.am`, `.im`, etc.)
- Sites with frequent domain changes or seizures
- Sites behind CDNs that may have regional outages

### Mirror Ordering

List mirrors in order of reliability/speed. The first working mirror is used for the
remainder of the session:

```yaml
mirror_urls:
  - "https://most-reliable-mirror.com"    # Try first
  - "https://second-choice.com"           # Try second
  - "https://last-resort.com"             # Try last
```

### Monitoring

Use the health endpoint to monitor mirror availability:

```bash
curl http://localhost:9876/api/v1/torznab/example-site/health | jq
```

This provides real-time visibility into which domains are reachable.

---

## Source Code References

| Component | File |
|---|---|
| `ScrapyAdapter._try_mirrors()` | `src/scavengarr/infrastructure/scraping/scrapy_adapter.py` |
| `ScrapyAdapter._switch_domain()` | `src/scavengarr/infrastructure/scraping/scrapy_adapter.py` |
| `ScrapyAdapter._replace_domain()` | `src/scavengarr/infrastructure/scraping/scrapy_adapter.py` |
| `YamlPluginDefinition.mirror_urls` | `src/scavengarr/domain/plugins/plugin_schema.py` |
| Health endpoint (mirror probing) | `src/scavengarr/interfaces/api/torznab/router.py` |
| Python plugin example (boerse.py) | `plugins/boerse.py` |
