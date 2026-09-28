Create a new Scavengarr plugin for: $ARGUMENTS

Follow the mandatory plugin creation workflow:

## Phase 1: Website Analysis (MANDATORY — do NOT skip)
Use Playwright MCP to thoroughly analyze the target website:
1. Navigate to the site's homepage and search page
2. Perform a test search and inspect results (browser_snapshot)
3. Visit a detail page and inspect its structure
4. Visit a download page and inspect link patterns
5. Check for JS dependencies (Cloudflare, dynamic loading, SPAs)
6. Identify auth mechanisms (login, cookies, tokens)
7. Document all URL patterns (search, detail, download)
8. Document all CSS selectors needed for scraping

## Phase 2: Decide Plugin Type
Based on the analysis:
- **YAML plugin (default)**: Use if the site is static HTML scrapable with Scrapy
- **Python plugin**: Use ONLY if YAML is technically impossible (Cloudflare/JS-challenge, complex auth, API calls, flat tables without proper HTML structure)
- State the decision and justification explicitly

## Phase 3: Implementation
- Create the plugin file in the `plugins/` directory
- Follow existing plugin patterns (check filmpalast.to.yaml and boerse.py for reference)
- Include proper categories mapping
- Test the plugin locally

## Phase 4: Commit
Use /project:commit to safely commit the new plugin
