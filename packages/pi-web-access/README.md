# pi-web-access-py

Web search and URL fetching extension for [pi-python](https://github.com/zy1233/pi-python).

Python port of [pi-web-access](https://www.npmjs.com/package/pi-web-access).

## Install

```bash
pip install pi-web-access-py
```

## Configuration

Set one of these environment variables:

- `BRAVE_API_KEY` — Brave Search API key
- `TAVILY_API_KEY` — Tavily Search API key
- `SEARXNG_URL` — SearXNG instance URL

## Tools

- **web_search** — Search the web for real-time information
- **fetch_url** — Fetch and extract text from a URL

### What `fetch_url` will and will not fetch

The URL comes from the model, which may have read it on a page somebody else wrote, so the tool only reaches the public internet:

- Only `http` and `https` URLs, without credentials in them.
- Loopback, private, link-local (including cloud metadata), carrier-grade-NAT, multicast and reserved addresses are refused however they are spelled (`127.1`, `2130706433`, `[::ffff:7f00:1]`), and so are `localhost`, `*.local`, `*.internal` and names without a dot. A name that resolves to such an address is refused too.
- Redirects are followed by hand (at most 5), and every hop is checked the same way.
- Without a proxy, the name is resolved once and the request goes to the address that was checked. **Through a proxy** (`HTTP(S)_PROXY`, `ALL_PROXY`, the Windows system proxy) the proxy resolves names, so only literal addresses and the local names above can be refused; a public name that the proxy resolves to a private address is not caught.
- At most 5 MiB of body is read, and the whole call takes at most 30 s. Output is cut at 2000 lines / 50 KB, and the result says so.
- There is no setting to allow private addresses.

Details: [Phase 7 spec, section 5.3](../../docs/specs/2026-09-22-phase7-extension-api-design.md).
