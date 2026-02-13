[← Back to Index](./README.md)

# Mirror URL Fallback

Scavengarr supports mirror URL fallback for indexer sites that operate across multiple
domains. When a primary domain becomes unreachable, the system automatically tries
configured mirror URLs to maintain scraping availability. All plugins implement
domain fallback via the `_domains` list in their base class (`HttpxPluginBase` or
`PlaywrightPluginBase`).

---

## Overview

Many indexer and forum sites operate multiple mirror domains for redundancy. A site
might be available at `example.sx`, `example.am`, `example.im`, and others. Scavengarr
handles domain failures transparently:

```
Primary Domain Request
     |
     v
+-------------------------+
|  Try primary domain     |
|  (first in _domains)    |
+----------+--------------+
           |
           v (connection failed)
+-------------------------+
|  Try Mirror Domains     |
|  (iterate _domains      |
|   in order)             |
+----------+--------------+
           |
     +-----+-----+
     v           v
  Success     All Failed
     |           |
     v           v
  Switch      Return []
  Domain      (log error)
```

---

## Plugin Domain Configuration

All Python plugins define their mirror domains via the `_domains` class attribute:

```python
# plugins/example_site.py
class ExampleSitePlugin(HttpxPluginBase):
    name = "example-site"
    _domains = ["example.sx", "example.am", "example.im", "example.kz"]
```

The `_domains` list determines the fallback priority -- domains are tried from first
to last. The first working domain becomes `self.base_url` for the session.

---

## HttpxPluginBase Domain Fallback

`HttpxPluginBase` implements domain fallback in `_verify_domain()`. When called
(typically at the start of `search()`), it probes each domain until one responds:

```python
# src/scavengarr/infrastructure/plugins/httpx_base.py (simplified)
async def _verify_domain(self) -> None:
    if self._domain_verified:
        return

    for domain in self._domains:
        base = f"https://{domain}"
        try:
            resp = await self._client.get(base, timeout=self._timeout)
            if resp.status_code < 500:
                self.base_url = base
                self._domain_verified = True
                return
        except httpx.RequestError:
            continue

    # All domains failed -- use first as fallback
    self.base_url = f"https://{self._domains[0]}"
```

Key behaviors:
- Domains are tried sequentially until one responds with a non-5xx status
- On success, `self.base_url` is updated for all subsequent requests
- The result is cached (`_domain_verified`) to avoid repeated probing
- If all domains fail, the first domain is used as a fallback

---

## PlaywrightPluginBase Domain Fallback

`PlaywrightPluginBase` implements a similar pattern using browser navigation:

```python
# src/scavengarr/infrastructure/plugins/playwright_base.py (simplified)
async def _verify_domain(self) -> None:
    for domain in self._domains:
        base = f"https://{domain}"
        try:
            page = await self._context.new_page()
            await page.goto(base, wait_until="domcontentloaded")
            await self._wait_for_cloudflare(page)
            self.base_url = base
            return
        except Exception:
            continue
        finally:
            if not page.is_closed():
                await page.close()
```

Key differences from httpx fallback:
- **Browser-based**: Uses Playwright page navigation instead of HTTP requests
- **Cloudflare-aware**: Waits for JS challenge resolution on each domain
- **Session-based**: Uses browser context with cookies for authenticated sites

---

## Health Endpoint Mirror Probing

The health endpoint (`GET /api/v1/torznab/{plugin_name}/health`) also supports mirror
probing. When the primary domain is unreachable, it checks each configured mirror:

```json
{
  "plugin": "example-site",
  "base_url": "https://example.sx",
  "checked_url": "https://example.sx/",
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
      "error": "DNS resolution failed"
    }
  ]
}
```

When the primary `base_url` is unreachable and the plugin has multiple domains
configured, the health endpoint probes each mirror and includes the results.
Mirror probes only run when the primary is down.

---

## Example: boerse.py Domain Fallback

The `boerse.py` plugin maintains a list of 5 mirror domains and tries each during login:

```python
# plugins/boerse.py
_DOMAINS = [
    "boerse.am",
    "boerse.sx",
    "boerse.im",
    "boerse.ai",
    "boerse.kz",
]

class BoersePlugin(PlaywrightPluginBase):
    name = "boerse"
    _domains = _DOMAINS

    async def _ensure_session(self) -> None:
        for domain in self._domains:
            base = f"https://{domain}"
            try:
                # Navigate to domain
                # Attempt login via vBulletin form
                # Verify session cookie
                if has_session:
                    self.base_url = base
                    self._logged_in = True
                    return
            except Exception:
                continue

        raise RuntimeError("All boerse domains failed during login")
```

Key aspects:
- **Login-aware**: Each domain attempt includes full authentication
- **Session-based**: Uses Playwright browser context with cookies
- **Cloudflare handling**: Waits for JS challenge resolution on each domain
- **Domain-independent**: The working domain is used for all subsequent requests

---

## Best Practices

### When to Use Mirrors

- Sites that operate multiple TLDs (`.sx`, `.am`, `.im`, etc.)
- Sites with frequent domain changes or seizures
- Sites behind CDNs that may have regional outages

### Mirror Ordering

List mirrors in order of reliability/speed. The first working mirror is used for the
remainder of the session:

```python
_DOMAINS = [
    "most-reliable-mirror.com",    # Try first
    "second-choice.com",           # Try second
    "last-resort.com",             # Try last
]
```

### Monitoring

Use the health endpoint to monitor mirror availability:

```bash
curl http://localhost:7979/api/v1/torznab/example-site/health | jq
```

This provides real-time visibility into which domains are reachable.

---

## Source Code References

| Component | File |
|---|---|
| `HttpxPluginBase._verify_domain()` | `src/scavengarr/infrastructure/plugins/httpx_base.py` |
| `PlaywrightPluginBase._verify_domain()` | `src/scavengarr/infrastructure/plugins/playwright_base.py` |
| Health endpoint (mirror probing) | `src/scavengarr/interfaces/api/torznab/router.py` |
| Python plugin example (boerse.py) | `plugins/boerse.py` |
