"""fetch_url tool — fetch and read the contents of a URL.

The URL is chosen by the model, which may have read it on a page somebody else wrote. So the
tool must not become a way to make this machine send requests for a stranger (audit P7-09):

* It reaches only the public internet. A URL that names a loopback, private, link-local,
  carrier-grade-NAT, multicast or reserved address is refused, however the address is spelled
  (``127.1``, ``2130706433``, ``[::ffff:7f00:1]``), and so is a name that resolves to one, or
  a redirect that leads to one. Names that are local by convention (``localhost``,
  ``*.local``, ``*.internal``, single-label names) are refused without a lookup.
* Without a proxy, the address that was checked is the one connected to: the name is resolved
  once, and the request goes to that address with the name in ``Host`` and in the TLS server
  name, so there is no second lookup for a hostile DNS server to answer differently.
* Through a proxy (``HTTP(S)_PROXY`` and friends, as before), the proxy resolves names itself,
  so a local lookup would say nothing about where the request lands (behind a poisoned DNS it
  says nonsense). Only what can be judged without a lookup is refused: literal addresses and
  the local names above. A name that a proxy resolves to a private address is not caught.
* What is read is bounded: at most ``MAX_BODY_BYTES`` of body, ``MAX_REDIRECTS`` redirects and
  ``TOTAL_TIMEOUT_S`` seconds for everything. HTML is turned into text by a scanner that is
  linear in its input (the standard library's ``HTMLParser`` is not, on malformed markup) and
  runs off the event loop.

Deliberate deviations: redirects are followed by hand (each hop is checked before it is made),
and the tool does not react to the turn's abort signal — the time limit is what stops it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import urllib.request
from dataclasses import dataclass
from html import unescape
from typing import Any

import httpx
from pydantic import BaseModel, Field

from pi_agent_core.coding_tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_head,
)
from pi_agent_core.types import AgentToolResult

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
TOTAL_TIMEOUT_S = 30.0

_USER_AGENT = "pi-python/0.1 (web-access extension)"
_STEP_TIMEOUT = httpx.Timeout(10.0, read=20.0)  # each step; TOTAL_TIMEOUT_S bounds the sum
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")

_IP = ipaddress.IPv4Address | ipaddress.IPv6Address


class FetchUrlParams(BaseModel):
    url: str = Field(description="The URL to fetch (http or https, on the public internet)")
    extract_text: bool = Field(
        default=True,
        description="Extract readable text from HTML (strip tags). Set false for raw content.",
    )
    max_length: int | None = Field(
        default=None,
        description="Maximum number of lines to return (default: 2000)",
    )


class _Refused(Exception):
    """The URL is not one this tool fetches; the message says why (it reaches the model)."""


class _Failed(Exception):
    """The fetch could not be completed; the message says why (it reaches the model)."""


# -- which addresses are public ---------------------------------------------------------------


def _literal_ip(host: str) -> _IP | None:
    """The address *host* spells, in any form a resolver would accept; None for a name."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:  # "127.1", "2130706433", "0x7f.1", "0177.0.0.1": what inet_aton reads as IPv4
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 one stands for (mapped, 6to4, NAT64), if it stands for one."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _is_public(ip: _IP) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(ip)
        if embedded is not None:
            return _is_public(embedded)  # what matters is where the IPv4 side leads
        if ip.is_site_local:
            return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved)


def _check_host(host: str) -> _IP | None:
    """Refuse a host that is not public; return the address it spells, or None for a name."""
    if "%" in host:  # a proxy may decode it into something else ("%31%32%37.0.0.1")
        raise _Refused("the host name is not valid")
    bare = host[:-1] if host.endswith(".") else host
    address = _literal_ip(bare)
    if address is not None:
        if not _is_public(address):
            raise _Refused("that address is not on the public internet")
        return address
    if "." not in bare or bare.endswith(_LOCAL_SUFFIXES):
        raise _Refused("that name is not on the public internet")
    return None


def _host(url: httpx.URL) -> str:
    """The host as it goes on the wire (an international name in punycode)."""
    return url.raw_host.decode("ascii")


def _check_url(url: httpx.URL) -> None:
    if url.scheme not in _DEFAULT_PORTS:
        raise _Refused("only http and https URLs are fetched")
    if not _host(url):
        raise _Refused("the URL has no host")
    if url.userinfo:
        raise _Refused("URLs with credentials in them are not fetched")


def _parse(text: str, base: httpx.URL | None = None) -> httpx.URL:
    try:
        url = httpx.URL(text) if base is None else base.join(text)
    except httpx.InvalidURL:
        raise _Refused("not a valid URL") from None
    _check_url(url)
    return url


async def _resolve(host: str, port: int) -> list[str]:
    """The distinct addresses *host* has, as the system resolver returns them."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for *_, sockaddr in infos:
        if sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    return addresses


async def _public_addresses(host: str, port: int) -> list[str]:
    """The addresses to try for *host*, IPv4 first; all of them must be public."""
    addresses = await _resolve(host, port)
    if not addresses:
        raise _Refused("the name has no address")
    if not all(_is_public(ipaddress.ip_address(address)) for address in addresses):
        # One private address among public ones is enough: a client that is offered the
        # list will use whichever it likes. What the name resolved to stays unsaid.
        raise _Refused("the name resolves to an address that is not on the public internet")
    return sorted(addresses, key=lambda address: ":" in address)


# -- reaching the host -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Route:
    """How one request gets to its host."""

    proxy: str | None = None  # send it through this proxy
    address: str | None = None  # connect here instead of to the URL's host
    named: bool = False  # the address was looked up for the URL's name: present the name


def _proxy_for(url: httpx.URL) -> str | None:
    proxies = urllib.request.getproxies()
    proxy = proxies.get(url.scheme) or proxies.get("all")
    if not proxy or urllib.request.proxy_bypass(_host(url)):
        return None
    return proxy


async def _routes(url: httpx.URL) -> list[_Route]:
    literal = _check_host(_host(url))
    proxy = _proxy_for(url)
    if proxy is not None:
        return [_Route(proxy=proxy)]
    if literal is not None:
        return [_Route(address=str(literal))]
    port = url.port or _DEFAULT_PORTS[url.scheme]
    return [_Route(address=a, named=True) for a in await _public_addresses(_host(url), port)]


def _make_client(proxy: str | None) -> httpx.AsyncClient:
    # An explicit transport switches off httpx's own proxy detection: the proxy is chosen
    # above, where the addresses are. The transport still honours SSL_CERT_FILE / SSL_CERT_DIR.
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(proxy=proxy),
        timeout=_STEP_TIMEOUT,
        follow_redirects=False,
    )


@dataclass(frozen=True)
class _Response:
    status: int
    content_type: str
    charset: str | None
    body: bytes
    cut: bool  # the body was longer than MAX_BODY_BYTES
    location: str | None  # where a redirect leads


async def _read_capped(response: httpx.Response) -> tuple[bytes, bool]:
    """Read the body, stopping at MAX_BODY_BYTES rather than reading it to the end."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        room = MAX_BODY_BYTES - total
        if len(chunk) > room:
            chunks.append(chunk[:room])
            return b"".join(chunks), True
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), False


async def _request(url: httpx.URL, route: _Route) -> _Response:
    target = url
    headers = {"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"}
    extensions: dict[str, Any] = {}
    if route.address is not None:
        target = url.copy_with(host=route.address)
        if route.named:
            headers["Host"] = url.netloc.decode("ascii")
            if url.scheme == "https":
                extensions["sni_hostname"] = _host(url)  # the certificate is checked for this
    async with (
        _make_client(route.proxy) as client,
        client.stream("GET", target, headers=headers, extensions=extensions) as resp,
    ):
        content_type = resp.headers.get("content-type", "")
        if resp.has_redirect_location:
            return _Response(
                resp.status_code, content_type, None, b"", False, resp.headers["location"]
            )
        body, cut = await _read_capped(resp)
        return _Response(resp.status_code, content_type, resp.charset_encoding, body, cut, None)


async def _get(url: httpx.URL) -> _Response:
    *others, last = await _routes(url)
    for route in others:
        try:
            return await _request(url, route)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            continue  # the name's next address may be reachable
    return await _request(url, last)


async def _fetch(text: str) -> tuple[_Response, httpx.URL]:
    """Follow redirects by hand: each hop is checked in full before it is made."""
    url = _parse(text)
    for _ in range(MAX_REDIRECTS + 1):
        response = await _get(url)
        if response.location is None:
            return response, url
        url = _parse(response.location, base=url)
    raise _Failed(f"too many redirects (more than {MAX_REDIRECTS})")


# -- HTML to text ----------------------------------------------------------------------------

# One pass over the input, always moving forward. Every search either finds what it looks for
# (and the scan continues after it) or fails (and the scan ends), so nothing is looked at more
# than a constant number of times. The regex-based cleaner this replaces took 2.5 s on 8000
# "<script>" tags and 10 s on 16000.
_MARKUP = re.compile(r"<(?:(!--)|([!?])|(/?)([A-Za-z][A-Za-z0-9:-]*))")
_RAW_TEXT_CLOSERS = {name: re.compile(f"</{name}", re.IGNORECASE) for name in ("script", "style")}
_BLOCK_TAGS = frozenset(
    {
        *("address", "article", "aside", "blockquote", "body", "br", "caption", "center", "dd"),
        *("details", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer"),
        *("form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "hr", "html"),
        *("legend", "li", "main", "menu", "nav", "ol", "p", "pre", "section", "summary"),
        *("table", "tbody", "td", "tfoot", "th", "thead", "title", "tr", "ul"),
    }
)
_LONG_NUMBER = re.compile(r"&#(?:[0-9]{8,}|[xX][0-9a-fA-F]{7,});?")
_HORIZONTAL_SPACE = re.compile(r"[ \t\r\f\v]+")
_SPACE_AROUND_NEWLINE = re.compile(r" ?\n ?")
_BLANK_LINES = re.compile(r"\n{3,}")


def _simple_html_to_text(html: str) -> str:
    """Lightweight HTML-to-text (no external deps), linear in the size of *html*.

    Tags, comments, declarations and the contents of ``<script>`` / ``<style>`` are dropped;
    block-level tags become line breaks; character references are decoded. Something left
    open (a tag, a comment, a script) swallows the rest of the document, as in a browser. A
    ``>`` inside a quoted attribute value ends the tag early; the rest of the value shows up
    as text.
    """
    pieces: list[str] = []
    pos = 0
    while True:
        match = _MARKUP.search(html, pos)
        if match is None:
            pieces.append(html[pos:])
            break
        pieces.append(html[pos : match.start()])
        comment, declaration, closing, name = match.groups()
        if comment:
            end = html.find("-->", match.end())
            pos = end + 3
        else:
            end = html.find(">", match.end())
            pos = end + 1
        if end == -1:
            break
        if declaration or comment:
            continue
        name = name.lower()
        if name in _BLOCK_TAGS:
            pieces.append("\n")
        elif name in _RAW_TEXT_CLOSERS and not closing:
            closer = _RAW_TEXT_CLOSERS[name].search(html, pos)
            end = -1 if closer is None else html.find(">", closer.end())
            if end == -1:
                break
            pos = end + 1
    text = "".join(pieces)
    # A reference with a huge number would make int() raise; it is not a character anyway.
    text = unescape(_LONG_NUMBER.sub("\ufffd", text)).replace("\xa0", " ")
    # Collapse horizontal runs first: it is what keeps the next pattern from backtracking.
    text = _HORIZONTAL_SPACE.sub(" ", text)
    text = _SPACE_AROUND_NEWLINE.sub("\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def _extract_text(html: str) -> str:
    """Try trafilatura first, fall back to simple tag stripping."""
    try:
        import trafilatura

        result = trafilatura.extract(html, include_comments=False, include_tables=True)
        if result:
            return result
    except ImportError:
        pass
    except Exception:
        logger.debug("trafilatura failed on this page; using the plain converter", exc_info=True)
    return _simple_html_to_text(html)


# -- the tool --------------------------------------------------------------------------------


def _decode(body: bytes, charset: str | None) -> str:
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:  # a charset Python does not know
        return body.decode("utf-8", errors="replace")


def _text(message: str) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": message}])


def _limit_output(text: str, max_lines: int) -> tuple[str, str | None]:
    """What to show of *text* (at most *max_lines* lines and 50KB), and a note on the rest."""
    result = truncate_head(text, max_lines=max_lines, max_bytes=DEFAULT_MAX_BYTES)
    if not result.truncated:
        return text, None
    if result.first_line_exceeds_limit:
        # One line fills the whole budget (minified JSON, say). Unlike a file there is nothing
        # to point the model at, so show the beginning of it rather than nothing.
        first_line_size = format_size(len(text.split("\n", 1)[0].encode("utf-8")))
        shown = text.encode("utf-8")[:DEFAULT_MAX_BYTES].decode("utf-8", errors="ignore")
        return shown, (
            f"[The first line is {first_line_size}; showing its first "
            f"{format_size(DEFAULT_MAX_BYTES)}.]"
        )
    if result.truncated_by == "lines":
        why = f"Content truncated at {max_lines} lines."
    else:
        why = f"Content truncated at {format_size(DEFAULT_MAX_BYTES)}."
    return result.content, f"[Showing {result.output_lines} of {result.total_lines} lines. {why}]"


async def _read(params: Any) -> AgentToolResult:
    response, final_url = await _fetch(params.url)
    if not 200 <= response.status < 300:
        return _text(f"HTTP {response.status} fetching {params.url}")

    raw = _decode(response.body, response.charset)
    if params.extract_text and "html" in response.content_type.lower():
        text = await asyncio.to_thread(_extract_text, raw)  # off the loop: it takes a while
    else:
        text = raw

    shown, note = _limit_output(text, params.max_length or DEFAULT_MAX_LINES)
    notes = [note] if note else []
    if response.cut:
        notes.append(f"[The response was cut off after {format_size(MAX_BODY_BYTES)}.]")

    return AgentToolResult(
        content=[{"type": "text", "text": "".join([shown, *(f"\n{n}" for n in notes)])}],
        details={
            "url": params.url,
            "finalUrl": str(final_url),
            "statusCode": response.status,
            "contentType": response.content_type,
            "truncated": bool(notes),
        },
    )


async def fetch_url_execute(
    tool_call_id: str,
    params: Any,
    signal: Any = None,
    on_update: Any = None,
) -> AgentToolResult:
    try:
        return await asyncio.wait_for(_read(params), TOTAL_TIMEOUT_S)
    except _Refused as e:
        return _text(f"Refusing to fetch {params.url}: {e}")
    except (TimeoutError, httpx.TimeoutException):
        return _text(f"Timed out fetching {params.url}")
    except Exception as e:
        return _text(f"Failed to fetch {params.url}: {e}")


def create_fetch_url_tool() -> Any:
    """Return a ToolDefinition for the fetch_url tool."""
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="fetch_url",
        description=(
            "Fetch and read the contents of a URL. Extracts readable text from HTML pages. "
            "Only public http(s) addresses can be fetched."
        ),
        parameters=FetchUrlParams,
        execute=fetch_url_execute,
        label="Fetch URL",
        prompt_snippet="Fetch and read the contents of a URL",
        prompt_guidelines=[
            "Use fetch_url to read the full content of a web page when you have a specific URL.",
            "Combine with web_search: search first, then fetch relevant URLs for details.",
        ],
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
