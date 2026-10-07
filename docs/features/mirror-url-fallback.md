[← Back to Index](./README.md)

# Mirror URL Fallback

> Plugins list their mirror domains in `_domains`; `_verify_domain()` picks the first reachable one as `base_url` and keeps it until a restart. None reachable raises `PluginUnreachableError`.

---

## Overview

Many indexer and forum sites operate multiple mirror domains (`example.sx`, `example.am`, `example.im`, ...). Both plugin base classes (`HttpxPluginBase`, `PlaywrightPluginBase`) implement domain fallback in `_verify_domain()`, driven by the `_domains` class attribute:

```text
plugin.search()
     |
     v
_verify_domain()  (no-op if already verified or only one domain)
     |
     v
+---------------------------+
|  Probe _domains in order  |
+-------------+-------------+
              |
       +------+------+
       v             v
  First success   All failed
       |             |
       v             v
  base_url =      PluginUnreachableError
  that domain     (log warning; the next search checks again)
```

Fallback only happens when the plugin calls `await self._verify_domain()` (usually at the start of `search()`). Most plugins have a single domain, where the call is a no-op. Some plugins implement their own loop instead — e.g. `plugins/boerse.py` tries each domain during login.

**Site moves:** when a request of `HttpxPluginBase._fetch_text()` or `_safe_fetch()` to the base host ends on another host after only permanent redirects (301/308) and with a status below 400, that host becomes `base_url` (`{name}_site_moved`), without a plugin change. hdfilme answered every search with a 301 from `hdfilme.cafe` to `hdfilme.ceo`, one more round trip per request. Temporary redirects (302/307), redirects that stay on the host and moves that end on an error page change nothing. The move lasts until a restart or until a request to the new host gets no answer (timeout, connect or DNS error): then the base URL goes back to the one the site moved from (`{name}_site_move_undone`), whose redirect leads to the site's newest host. hdfilme moves on every few days (`.legal`, `.press`, `.party`, `.bid`, `.cafe`, `.ceo`), and a dead new host kept every search on it until a restart.

---

## Plugin Domain Configuration

```python
# plugins/example_site.py
class ExampleSitePlugin(HttpxPluginBase):
    name = "example-site"
    _domains = ["example.sx", "example.am", "example.im", "example.kz"]
```

- Domains are bare host names; the base classes build `https://{domain}`
- Order is the fallback priority — list the most reliable mirror first
- Before verification, `base_url` is `https://{_domains[0]}`
- The verified domain is kept (`_domain_verified`) for the life of the process (`cleanup()` resets it, but the app does not call it); the base classes do not re-probe after later request errors

---

## HttpxPluginBase Domain Fallback

```python
# src/scavengarr/infrastructure/plugins/httpx_base.py (simplified)
async def _verify_domain(self) -> None:
    if self._domain_verified or len(self._domains) <= 1:
        self._domain_verified = True
        return

    client = await self._ensure_client()
    answering = None
    for domain in self._domains:
        try:
            resp = await client.head(f"https://{domain}/", timeout=5.0)
        except Exception:
            continue
        if resp.status_code < 400:
            self._use_domain(domain, resp)  # final URL after redirects
            return
        if answering is None and _site_answers(resp):  # below 500, or a challenge
            answering = (domain, resp)

    if answering is not None:  # an error page or a challenge: the site is up
        self._use_domain(*answering)
        return
    # No domain answers: the next search checks again
    self._log.warning(f"{self.name}_no_domain_reachable")
    raise PluginUnreachableError(self.name)
```

Key behaviors:
- `HEAD` request per domain with a 5 s timeout (`DEFAULT_DOMAIN_CHECK_TIMEOUT`)
- The first status `< 400` wins; errors and timeouts move on to the next domain. Without one, the first domain that answers at all is used (`{name}_domain_answers`): a status below 500 (an error page is still the site) or a Cloudflare challenge (403/503 with `cf-mitigated`: kinoger), the health check's rule; the plugin's own requests decide then, and the browser fallback solves a challenge
- `base_url` uses the final URL after redirects, so a bare domain that redirects to `www.` produces a correct base
- If all domains fail, `{name}_no_domain_reachable` is logged and `PluginUnreachableError` (`domain/plugins/base.py`) raised: `base_url` and `_domain_verified` stay as they were, so the next search checks again. A Stremio search marks the plugin unreachable in the health monitor at once, until its recheck finds the site answering ([Stremio Addon](./stremio-addon.md#request-flow)); a Torznab search logs the error and answers without the plugin

---

## PlaywrightPluginBase Domain Fallback

```python
# src/scavengarr/infrastructure/plugins/playwright_base.py (simplified)
async def _verify_domain(self) -> None:
    if self._domain_verified or len(self._domains) <= 1:
        self._domain_verified = True
        return

    page = await self._ensure_page()
    for domain in self._domains:
        try:
            resp = await page.goto(
                f"https://{domain}/", timeout=5_000, wait_until="domcontentloaded"
            )
            if resp and await self._passes_cloudflare(page, resp):
                self.base_url = f"https://{domain}"
                self._domain_verified = True
                return
        except Exception:
            continue

    self._log.warning(f"{self.name}_no_domain_reachable")
    raise PluginUnreachableError(self.name)
```

Differences from the httpx fallback:
- **Browser-based:** navigates the plugin's persistent page instead of sending HTTP requests
- **Cloudflare-aware:** a domain counts when it answers below 400, or with a Cloudflare challenge page that is solved within `_cf_timeout_ms` (`_passes_cloudflare()`); an error status without a challenge fails
- **No redirect tracking:** `base_url` is set to `https://{domain}`, not the final URL

Plugins can override `_verify_domain()`. An override of `_wait_for_cloudflare()` must call the base implementation (Turnstile solver, clearance memo) and add its own condition after it (e.g. `page.wait_for_function()` for a cookie the site's app sets).

---

## Example: boerse.py Login Fallback

`plugins/boerse.py` does not call `_verify_domain()`. Its login loop is the fallback: each of its six domains is tried with a full login in a temporary browser context.

```python
# plugins/boerse.py (simplified)
_DOMAINS = [
    "boerse.am",
    "boerse.tw",
    "boerse.sx",
    "boerse.im",
    "boerse.ai",
    "boerse.kz",
]

async def _ensure_session(self) -> None:
    for domain in self._domains:
        domain_url = f"https://{domain}"
        login_ctx = await browser.new_context(...)
        try:
            # Navigate, wait for Cloudflare, submit vBulletin login form
            cookies = await login_ctx.cookies()
            if any(c["name"] == "bbsessionhash" for c in cookies):
                self.base_url = domain_url
                self._session_cookies = self._cookie_params(cookies)
                self._logged_in = True
                return
        except Exception:
            continue
        finally:
            await login_ctx.close()

    raise RuntimeError("All boerse domains failed during login")
```

Key aspects:
- **Login-aware:** each domain attempt includes full authentication
- **Cookie hand-off:** session cookies are injected into per-request contexts via `_prepare_context()`
- **Fails loudly:** it raises `RuntimeError` when no domain works (the base classes raise `PluginUnreachableError`)

See [Python Plugins](./python-plugins.md#reference-implementation-boersepy) for the full walkthrough.

---

## Health Endpoint

`GET /api/v1/torznab/{plugin_name}/health` probes the plugin's current `base_url` (`HEAD`, falling back to a ranged `GET` on 405/501, 5 s timeout) and reports `reachable`, `status_code` and `error`. Any HTTP response counts as reachable; only network errors report `false`.

```bash
curl http://localhost:7979/api/v1/torznab/example-site/health | jq
```

---

## Source Code References

| Component | Path |
|---|---|
| `HttpxPluginBase._verify_domain()` | `src/scavengarr/infrastructure/plugins/httpx_base.py` |
| `PlaywrightPluginBase._verify_domain()` | `src/scavengarr/infrastructure/plugins/playwright_base.py` |
| `DEFAULT_DOMAIN_CHECK_TIMEOUT` | `src/scavengarr/infrastructure/plugins/constants.py` |
| Health endpoint | `src/scavengarr/interfaces/api/torznab/router.py` |
| Login-based fallback example | `plugins/boerse.py` |
