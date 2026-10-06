"""fetch_url is handed URLs by the model, which may have read them on a page someone else wrote
(audit P7-09).

* It reaches only the public internet: a URL naming loopback, a private network, link-local or
  metadata address is refused — however the name is spelled, whatever it resolves to and
  wherever a redirect leads. A resolved address is the one connected to (no second lookup for
  an attacker's DNS to answer differently).
* What it reads is bounded: in bytes, in time, and the HTML-to-text step is linear in the input
  and runs off the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import socket
import sys
import time
import types
import urllib.request
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pi_web_access import fetch_url
from pi_web_access.fetch_url import FetchUrlParams, _simple_html_to_text, fetch_url_execute

HTML = {"content-type": "text/html; charset=utf-8"}
TEXT = {"content-type": "text/plain; charset=utf-8"}


class Net:
    """The network as ``fetch_url`` sees it: a DNS table, and a handler for every server.

    The handler gets each ``httpx.Request`` as it reaches the transport, so a test sees the
    address connected to, the ``Host`` header and the TLS server name.
    """

    def __init__(self) -> None:
        self.dns: dict[str, list[str]] = {}
        self.requests: list[httpx.Request] = []
        self.resolved: list[tuple[str, int]] = []
        self.proxies: list[str | None] = []
        self.handler: Callable[[httpx.Request], Any] = lambda request: httpx.Response(
            200, headers=HTML, content=b"<p>hello</p>"
        )

    async def resolve(self, host: str, port: int) -> list[str]:
        self.resolved.append((host, port))
        if host not in self.dns:
            raise OSError(f"cannot resolve {host}")
        return list(self.dns[host])

    def make_client(self, proxy: str | None) -> httpx.AsyncClient:
        self.proxies.append(proxy)

        async def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            result = self.handler(request)
            if asyncio.iscoroutine(result):
                result = await result
            return result

        return httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False)


@pytest.fixture(autouse=True)
def _without_readability_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """The result must not depend on whether trafilatura happens to be installed."""
    monkeypatch.setitem(sys.modules, "trafilatura", None)


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Net:
    network = Net()
    monkeypatch.setattr(fetch_url, "_resolve", network.resolve)
    monkeypatch.setattr(fetch_url, "_make_client", network.make_client)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    return network


async def fetch(url: str, **kwargs: Any) -> Any:
    return await fetch_url_execute("tc-1", FetchUrlParams(url=url, **kwargs))


def text_of(result: Any) -> str:
    return result.content[0]["text"]


def redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


# -- what is refused ---------------------------------------------------------------------

NOT_PUBLIC = [
    "127.0.0.1",
    "127.1",
    "0.0.0.0",
    "10.0.0.5",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",  # cloud metadata
    "100.64.0.1",  # carrier-grade NAT
    "224.0.0.1",  # multicast
    "240.0.0.1",  # reserved
    "2130706433",  # 127.0.0.1 as one number
    "0x7f.1",
    "0177.0.0.1",
    "[::1]",
    "[::]",
    "[fe80::1]",
    "[fc00::1]",
    "[fd00:ec2::254]",  # AWS metadata, IPv6
    "[ff02::1]",  # multicast
    "[::ffff:127.0.0.1]",  # IPv4-mapped
    "[::ffff:7f00:1]",  # ... spelled in hex
    "[::ffff:10.0.0.1]",
    "[::7f00:1]",  # IPv4-compatible
    "[2002:7f00:1::]",  # 6to4
    "[64:ff9b::7f00:1]",  # NAT64 of 127.0.0.1
    "[fec0::1]",  # site-local
    "127.0.0.1.",  # an absolute name
    "\uff11\uff12\uff17.\uff10.\uff10.\uff11",  # full-width digits
]

LOCAL_NAMES = [
    "localhost",
    "LOCALHOST.",
    "app.localhost",
    "printer.local",
    "metadata.google.internal",
    "box.localdomain",
    "router.home.arpa",
    "intranet",  # one label: not a name on the public internet
    "jira",
]


class TestAddressesThatAreNotPublic:
    @pytest.mark.parametrize("host", NOT_PUBLIC)
    async def test_a_literal_address_is_refused_without_a_request(self, net: Net, host: str):
        result = await fetch(f"http://{host}/admin")

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []
        assert net.resolved == []

    @pytest.mark.parametrize("host", LOCAL_NAMES)
    async def test_a_name_that_is_local_by_convention_is_refused(self, net: Net, host: str):
        net.dns[host.lower().rstrip(".")] = ["93.184.216.34"]  # even if a resolver says otherwise

        result = await fetch(f"http://{host}/")

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []

    @pytest.mark.parametrize(
        "host", ["notlocalhost.example", "localhost.example.com", "internal.com", "local.dev"]
    )
    async def test_a_name_that_only_looks_similar_is_fetched(self, net: Net, host: str):
        net.dns[host] = ["93.184.216.34"]

        result = await fetch(f"http://{host}/")

        assert "hello" in text_of(result)

    async def test_a_host_with_an_escape_in_it_is_refused(self, net: Net):
        net.dns["%31%32%37.0.0.1"] = ["93.184.216.34"]

        result = await fetch("http://%31%32%37.0.0.1/")  # "127.0.0.1", to a proxy that decodes

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []

    @pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1"])
    async def test_a_name_that_resolves_to_such_an_address_is_refused(self, net: Net, address: str):
        net.dns["evil.example"] = [address]

        result = await fetch("https://evil.example/")

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []

    async def test_one_private_address_among_public_ones_is_enough_to_refuse(self, net: Net):
        net.dns["rebind.example"] = ["93.184.216.34", "10.0.0.7"]

        result = await fetch("https://rebind.example/")

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []

    async def test_the_refusal_does_not_tell_what_the_name_resolved_to(self, net: Net):
        net.dns["intranet.example"] = ["10.9.8.7"]

        result = await fetch("https://intranet.example/")

        assert "10.9.8.7" not in text_of(result)

    @pytest.mark.parametrize("address", ["93.184.216.34", "2606:4700::1111"])
    async def test_a_public_literal_is_fetched_as_it_stands(self, net: Net, address: str):
        host = f"[{address}]" if ":" in address else address

        result = await fetch(f"https://{host}/x")

        assert "hello" in text_of(result)
        assert net.resolved == []  # nothing to look up
        assert net.requests[0].url.host == address

    @pytest.mark.parametrize(
        "address", ["::ffff:8.8.8.8", "64:ff9b::808:808", "2002:808:808::"]
    )  # mapped / NAT64 / 6to4 of a public IPv4
    async def test_an_ipv6_form_of_a_public_ipv4_address_is_fetched(self, net: Net, address: str):
        net.dns["v6only.example"] = [address]

        result = await fetch("https://v6only.example/")

        assert "hello" in text_of(result)


class TestEmbeddedIpv4:
    """The IPv4 address an IPv6 one stands for, whatever the standard library makes of it.

    Recent Pythons already call the mapped form public or private by its IPv4 side; older ones
    call every mapped address private, which would refuse a public one.
    """

    @pytest.mark.parametrize(
        "address",
        ["::ffff:8.8.8.8", "::ffff:808:808", "2002:808:808::", "64:ff9b::808:808"],
    )  # mapped (both spellings), 6to4, NAT64
    def test_the_address_inside_is_found(self, address: str):
        found = fetch_url._embedded_ipv4(ipaddress.IPv6Address(address))

        assert found == ipaddress.IPv4Address("8.8.8.8")

    @pytest.mark.parametrize("address", ["2606:4700::1111", "::1", "64:ff9b:1::1", "fe80::1"])
    def test_an_address_that_stands_for_no_ipv4_has_none(self, address: str):
        assert fetch_url._embedded_ipv4(ipaddress.IPv6Address(address)) is None


class TestOnlyWebUrls:
    @pytest.mark.parametrize(
        ("url", "reason"),
        [
            ("file:///etc/passwd", "only http and https"),
            ("ftp://example.com/x", "only http and https"),
            ("gopher://example.com/", "only http and https"),
            ("example.com/no-scheme", "only http and https"),
            ("http:///no-host", "no host"),
        ],
    )
    async def test_anything_else_is_refused(self, net: Net, url: str, reason: str):
        result = await fetch(url)

        assert "Refusing to fetch" in text_of(result)
        assert reason in text_of(result)
        assert net.requests == []

    async def test_credentials_in_the_url_are_refused(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        result = await fetch("https://user:secret@example.com/")

        assert "Refusing to fetch" in text_of(result)
        assert "credentials" in text_of(result)
        assert net.requests == []


class TestResolve:
    """The system resolver, as ``_resolve`` asks it."""

    async def test_it_returns_each_address_once_in_the_order_given(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        asked: list[tuple[Any, ...]] = []

        def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            asked.append((host, port, type))
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1111", port, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

        addresses = await fetch_url._resolve("example.com", 443)

        assert addresses == ["2606:4700::1111", "93.184.216.34"]
        assert asked == [("example.com", 443, socket.SOCK_STREAM)]

    async def test_a_name_that_does_not_resolve_raises(self, monkeypatch: pytest.MonkeyPatch):
        def getaddrinfo(*args, **kwargs):
            raise socket.gaierror(-2, "Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

        with pytest.raises(OSError, match="Name or service not known"):
            await fetch_url._resolve("nowhere.example", 443)


# -- the address that was checked is the one connected to ---------------------------------


class TestPinning:
    async def test_the_request_goes_to_the_checked_address_named_by_host_and_sni(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        await fetch("https://example.com/a/b?q=1")

        (request,) = net.requests
        assert request.url.host == "93.184.216.34"
        assert request.url.path == "/a/b"
        assert request.url.query == b"q=1"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"

    async def test_a_plain_http_request_has_no_sni(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        await fetch("http://example.com/")

        (request,) = net.requests
        assert request.headers["host"] == "example.com"
        assert "sni_hostname" not in request.extensions

    async def test_a_port_is_kept_and_looked_up(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        await fetch("http://example.com:8080/x")

        assert net.resolved == [("example.com", 8080)]
        (request,) = net.requests
        assert request.url.port == 8080
        assert request.headers["host"] == "example.com:8080"

    async def test_the_default_port_is_looked_up_for_the_scheme(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        await fetch("https://example.com/")
        await fetch("http://example.com/")

        assert net.resolved == [("example.com", 443), ("example.com", 80)]

    async def test_an_ipv6_address_is_pinned(self, net: Net):
        net.dns["example.com"] = ["2606:4700::1111"]

        await fetch("https://example.com/")

        (request,) = net.requests
        assert request.url.host == "2606:4700::1111"
        assert request.headers["host"] == "example.com"

    async def test_an_international_name_is_looked_up_and_sent_as_punycode(self, net: Net):
        net.dns["xn--mnchen-3ya.de"] = ["93.184.216.34"]

        await fetch("https://münchen.de/")

        assert net.resolved == [("xn--mnchen-3ya.de", 443)]
        assert net.requests[0].headers["host"] == "xn--mnchen-3ya.de"
        assert net.requests[0].extensions["sni_hostname"] == "xn--mnchen-3ya.de"

    async def test_the_next_address_is_tried_when_one_will_not_connect(self, net: Net):
        net.dns["example.com"] = ["2606:4700::1111", "93.184.216.34"]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "93.184.216.34":
                raise httpx.ConnectError("refused")
            return httpx.Response(200, headers=HTML, content=b"<p>via v6</p>")

        net.handler = handler

        result = await fetch("https://example.com/")

        assert "via v6" in text_of(result)
        assert [r.url.host for r in net.requests] == ["93.184.216.34", "2606:4700::1111"]

    async def test_the_next_address_is_tried_after_a_connect_timeout(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34", "93.184.216.35"]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "93.184.216.34":
                raise httpx.ConnectTimeout("no answer")
            return httpx.Response(200, headers=HTML, content=b"<p>the second</p>")

        net.handler = handler

        result = await fetch("https://example.com/")

        assert "the second" in text_of(result)

    async def test_a_failure_after_connecting_is_not_retried_elsewhere(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34", "93.184.216.35"]

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("connection reset")

        net.handler = handler

        result = await fetch("https://example.com/")

        assert "Failed to fetch" in text_of(result)
        assert len(net.requests) == 1

    async def test_it_gives_up_when_no_address_connects(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34", "93.184.216.35"]

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"no route to {request.url.host}")

        net.handler = handler

        result = await fetch("https://example.com/")

        assert "Failed to fetch" in text_of(result)
        assert len(net.requests) == 2

    async def test_a_name_that_does_not_resolve_is_a_failure_not_a_crash(self, net: Net):
        result = await fetch("https://nowhere.example/")

        assert "Failed to fetch" in text_of(result)
        assert net.requests == []

    async def test_a_name_with_no_addresses_is_refused(self, net: Net):
        net.dns["empty.example"] = []

        result = await fetch("https://empty.example/")

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []


# -- redirects are followed by hand, and each hop is checked --------------------------------


class TestRedirects:
    async def test_a_redirect_is_followed_and_the_final_address_is_reported(self, net: Net):
        net.dns.update({"a.example": ["93.184.216.34"], "b.example": ["93.184.216.35"]})

        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers["host"] == "a.example":
                return redirect("https://b.example/final")
            return httpx.Response(200, headers=HTML, content=b"<p>arrived</p>")

        net.handler = handler

        result = await fetch("https://a.example/start")

        assert "arrived" in text_of(result)
        assert result.details["finalUrl"] == "https://b.example/final"
        assert [r.url.host for r in net.requests] == ["93.184.216.34", "93.184.216.35"]

    async def test_a_relative_location_is_resolved_against_the_name_not_the_pinned_address(
        self, net: Net
    ):
        net.dns["example.com"] = ["93.184.216.34"]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return redirect("/next?x=1", 301)
            return httpx.Response(200, headers=HTML, content=b"<p>next</p>")

        net.handler = handler

        result = await fetch("https://example.com/start")

        assert "next" in text_of(result)
        second = net.requests[1]
        assert second.headers["host"] == "example.com"
        assert second.url.path == "/next"
        assert second.url.query == b"x=1"
        assert net.resolved == [("example.com", 443), ("example.com", 443)]

    async def test_a_redirect_to_a_private_literal_is_refused_after_the_first_request(
        self, net: Net
    ):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: redirect("http://169.254.169.254/latest/meta-data/")

        result = await fetch("https://example.com/")

        assert "Refusing to fetch" in text_of(result)
        assert len(net.requests) == 1

    async def test_a_redirect_to_a_name_that_resolves_privately_is_refused(self, net: Net):
        net.dns.update({"example.com": ["93.184.216.34"], "internal.example": ["10.0.0.9"]})
        net.handler = lambda request: redirect("http://internal.example/admin")

        result = await fetch("https://example.com/")

        assert "Refusing to fetch" in text_of(result)
        assert len(net.requests) == 1

    @pytest.mark.parametrize("location", ["file:///etc/passwd", "ftp://example.com/x"])
    async def test_a_redirect_to_another_scheme_is_refused(self, net: Net, location: str):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: redirect(location)

        result = await fetch("https://example.com/")

        assert "Refusing to fetch" in text_of(result)
        assert len(net.requests) == 1

    async def test_a_redirect_to_an_invalid_url_is_a_failure(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: redirect("http://[::1/")

        result = await fetch("https://example.com/")

        assert "Failed to fetch" in text_of(result)  # httpx reports the broken header
        assert len(net.requests) == 1

    async def test_a_loop_of_redirects_ends(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: redirect("/again")

        result = await fetch("https://example.com/")

        assert "redirects" in text_of(result)
        assert len(net.requests) == fetch_url.MAX_REDIRECTS + 1

    async def test_a_redirect_without_a_location_is_the_final_answer(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(302)

        result = await fetch("https://example.com/")

        assert "HTTP 302" in text_of(result)
        assert len(net.requests) == 1


# -- proxies ------------------------------------------------------------------------------


class TestThroughAProxy:
    """A proxy resolves names itself, so what a local lookup says about them is beside the
    point (behind a poisoned DNS it is nonsense): the request keeps its name, and only what
    can be judged without a lookup is refused."""

    PROXY = "http://127.0.0.1:8899"

    @pytest.fixture(autouse=True)
    def _proxy(self, net: Net, monkeypatch: pytest.MonkeyPatch) -> None:
        # After ``net``, which clears the environment's proxies.
        monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": self.PROXY})

    async def test_the_name_is_not_looked_up_or_pinned(self, net: Net):
        result = await fetch("https://example.com/x")

        assert "hello" in text_of(result)
        assert net.resolved == []
        assert net.proxies == [self.PROXY]
        assert net.requests[0].url.host == "example.com"

    async def test_a_scheme_the_proxy_does_not_serve_goes_direct(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        await fetch("http://example.com/x")

        assert net.proxies == [None]
        assert net.resolved == [("example.com", 80)]

    async def test_a_proxy_for_all_schemes_serves_both(self, net: Net, monkeypatch):
        monkeypatch.setattr(urllib.request, "getproxies", lambda: {"all": self.PROXY})

        await fetch("http://example.com/x")

        assert net.proxies == [self.PROXY]

    async def test_a_host_the_environment_bypasses_goes_direct(self, net: Net, monkeypatch):
        net.dns["example.com"] = ["93.184.216.34"]
        monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: host == "example.com")

        await fetch("https://example.com/x")

        assert net.proxies == [None]
        assert net.resolved == [("example.com", 443)]

    @pytest.mark.parametrize("host", ["10.0.0.1", "127.0.0.1", "2130706433", "0x7f.1", "[::1]"])
    async def test_a_literal_address_is_still_refused(self, net: Net, host: str):
        result = await fetch(f"https://{host}/")

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []

    @pytest.mark.parametrize("host", LOCAL_NAMES)
    async def test_a_name_that_is_local_by_convention_is_still_refused(self, net: Net, host: str):
        result = await fetch(f"https://{host}/")

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []


@contextlib.asynccontextmanager
async def serve(response: bytes | None = None):
    """A server on loopback that records each request head, and answers with *response*
    (or, given None, reads the request and then stays silent for a second)."""
    heads: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        heads.append((await reader.readuntil(b"\r\n\r\n")).decode("latin-1"))
        if response is None:
            await asyncio.sleep(1)
        else:
            writer.write(response)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], heads
    finally:
        server.close()
        await server.wait_closed()


OK_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nhi"
REDIRECT_RESPONSE = (
    b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:9/never\r\n"
    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
)


class TestTheRealClient:
    """``_make_client`` is what the mocks in the other tests stand in for. Here it talks to
    sockets on loopback (fetch_url itself cannot be pointed at one: loopback is refused)."""

    async def test_it_does_not_follow_a_redirect(self):
        async with (
            serve(REDIRECT_RESPONSE) as (port, heads),
            fetch_url._make_client(None) as client,
        ):
            response = await client.get(f"http://127.0.0.1:{port}/start")

        assert response.status_code == 302
        assert len(heads) == 1

    async def test_a_proxy_is_sent_the_request_for_the_url(self):
        async with (
            serve(OK_RESPONSE) as (port, heads),
            fetch_url._make_client(f"http://127.0.0.1:{port}") as client,
        ):
            response = await client.get("http://example.com/x?y=1")

        assert response.status_code == 200
        assert heads[0].startswith("GET http://example.com/x?y=1 HTTP/1.1\r\n")

    async def test_without_a_proxy_the_host_is_connected_to(self):
        async with (
            serve(OK_RESPONSE) as (port, heads),
            fetch_url._make_client(None) as client,
        ):
            await client.get(f"http://127.0.0.1:{port}/x")

        assert heads[0].startswith("GET /x HTTP/1.1\r\n")

    async def test_the_environment_is_not_asked_about_proxies(self, monkeypatch):
        # fetch_url picks the proxy itself, next to the address checks. A client that also
        # read HTTP_PROXY would send the request somewhere those checks did not look.
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, "http://127.0.0.1:9")  # nothing listens there
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)

        async with (
            serve(OK_RESPONSE) as (port, _heads),
            fetch_url._make_client(None) as client,
        ):
            response = await client.get(f"http://127.0.0.1:{port}/x")

        assert response.status_code == 200

    async def test_a_server_that_does_not_answer_times_out(self, monkeypatch):
        monkeypatch.setattr(fetch_url, "_STEP_TIMEOUT", httpx.Timeout(0.2))

        async with (
            serve(None) as (port, _heads),
            fetch_url._make_client(None) as client,
        ):
            with pytest.raises(httpx.ReadTimeout):
                await client.get(f"http://127.0.0.1:{port}/x")


# -- limits -------------------------------------------------------------------------------


class TestLimits:
    async def test_a_body_over_the_limit_is_cut_off_and_not_read_to_the_end(
        self, net: Net, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(fetch_url, "MAX_BODY_BYTES", 1000)
        net.dns["example.com"] = ["93.184.216.34"]
        pulled = 0

        async def body():
            nonlocal pulled
            for _ in range(100_000):
                pulled += 1
                yield b"x" * 100

        net.handler = lambda request: httpx.Response(200, headers=TEXT, content=body())

        result = await fetch("https://example.com/big")

        assert pulled <= 12  # 1000 bytes is ten chunks: it stopped there
        assert result.details["truncated"] is True
        assert "cut off" in text_of(result)
        assert len(text_of(result).split("\n")[0]) == 1000

    async def test_a_body_of_exactly_the_limit_is_not_cut(
        self, net: Net, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(fetch_url, "MAX_BODY_BYTES", 1000)
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(200, headers=TEXT, content=b"y" * 1000)

        result = await fetch("https://example.com/")

        assert result.details["truncated"] is False
        assert "cut off" not in text_of(result)

    async def test_a_slow_server_ends_at_the_total_time_limit(
        self, net: Net, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(fetch_url, "TOTAL_TIMEOUT_S", 0.2)
        net.dns["example.com"] = ["93.184.216.34"]

        async def never(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        net.handler = never

        started = time.monotonic()
        result = await fetch("https://example.com/")

        assert time.monotonic() - started < 3
        assert "Timed out" in text_of(result)

    async def test_a_timeout_from_the_client_reads_as_one(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("")

        net.handler = handler

        result = await fetch("https://example.com/")

        assert "Timed out" in text_of(result)

    async def test_the_time_limit_covers_the_whole_redirect_chain(
        self, net: Net, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(fetch_url, "TOTAL_TIMEOUT_S", 0.3)
        net.dns["example.com"] = ["93.184.216.34"]

        async def slow_hop(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.1)  # each hop is quick, the chain is not
            return redirect("/again")

        net.handler = slow_hop

        result = await fetch("https://example.com/")

        assert "Timed out" in text_of(result)

    async def test_the_response_is_asked_for_uncompressed(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        await fetch("https://example.com/")

        assert net.requests[0].headers["accept-encoding"] == "identity"

    async def test_conversion_to_text_does_not_block_the_event_loop(
        self, net: Net, monkeypatch: pytest.MonkeyPatch
    ):
        net.dns["example.com"] = ["93.184.216.34"]
        beats = 0

        def slow_extract(html: str) -> str:
            time.sleep(0.3)
            return "extracted"

        monkeypatch.setattr(fetch_url, "_extract_text", slow_extract)

        async def heartbeat() -> None:
            nonlocal beats
            while True:
                await asyncio.sleep(0.01)
                beats += 1

        ticker = asyncio.create_task(heartbeat())
        try:
            result = await fetch("https://example.com/")
        finally:
            ticker.cancel()

        assert text_of(result) == "extracted"
        assert beats >= 10  # the loop kept running while the conversion did


# -- what comes back ------------------------------------------------------------------


class TestWhatIsReturned:
    async def test_html_is_reduced_to_its_text(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200,
            headers=HTML,
            content=b"<html><body><script>track()</script><p>Hello World</p></body></html>",
        )

        result = await fetch("https://example.com/")

        assert text_of(result) == "Hello World"
        assert result.details["statusCode"] == 200
        assert result.details["contentType"] == HTML["content-type"]
        assert result.details["url"] == "https://example.com/"
        assert result.details["finalUrl"] == "https://example.com/"
        assert result.details["truncated"] is False

    async def test_raw_content_is_returned_when_asked(self, net: Net):
        net.dns["api.example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200, headers={"content-type": "application/json"}, content=b'{"key": "value"}'
        )

        result = await fetch("https://api.example.com/data", extract_text=False)

        assert text_of(result) == '{"key": "value"}'

    async def test_html_is_returned_as_it_is_when_text_is_not_wanted(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        result = await fetch("https://example.com/", extract_text=False)

        assert text_of(result) == "<p>hello</p>"

    async def test_the_declared_charset_is_used(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=gbk"},
            content="中文内容".encode("gbk"),
        )

        result = await fetch("https://example.com/")

        assert text_of(result) == "中文内容"

    async def test_an_unknown_charset_falls_back_to_utf8(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=no-such-charset"},
            content="héllo".encode(),
        )

        result = await fetch("https://example.com/")

        assert text_of(result) == "héllo"

    async def test_output_is_cut_to_the_requested_number_of_lines(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200, headers=TEXT, content="\n".join(f"line {i}" for i in range(50)).encode()
        )

        result = await fetch("https://example.com/", max_length=5)

        assert "line 4" in text_of(result)
        assert "line 5" not in text_of(result)
        assert "Showing 5 of 50 lines" in text_of(result)
        assert result.details["truncated"] is True

    async def test_a_long_page_is_cut_at_50kb_and_says_so(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        page = "\n".join(f"line {i:04d} " + "x" * 90 for i in range(2000))  # 2000 lines, ~200KB
        net.handler = lambda request: httpx.Response(200, headers=TEXT, content=page.encode())

        result = await fetch("https://example.com/")

        shown, _, note = text_of(result).rpartition("\n")
        assert shown.startswith("line 0000 ")
        assert len(shown.encode()) <= 50 * 1024
        assert re.fullmatch(r"\[Showing \d+ of 2000 lines\. Content truncated at 50\.0KB\.\]", note)
        assert result.details["truncated"] is True

    async def test_one_very_long_line_still_shows_its_beginning(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        page = b"a" * 100_000 + b"\n" + b"b" * 100_000
        net.handler = lambda request: httpx.Response(200, headers=TEXT, content=page)

        result = await fetch("https://example.com/")

        shown, _, note = text_of(result).rpartition("\n")
        assert shown == "a" * (50 * 1024)
        assert note == "[The first line is 97.7KB; showing its first 50.0KB.]"
        assert result.details["truncated"] is True

    async def test_the_cut_never_lands_inside_a_character(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        page = "\u4e2d" * 40_000  # three bytes each: 120,000 bytes on one line
        net.handler = lambda request: httpx.Response(200, headers=TEXT, content=page.encode())

        result = await fetch("https://example.com/")

        shown = text_of(result).rpartition("\n")[0]
        assert shown == "\u4e2d" * (50 * 1024 // 3)  # 17066 characters, 51198 bytes

    async def test_without_a_line_limit_of_its_own_a_page_is_cut_at_2000_lines(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        page = "\n".join(f"l{i}" for i in range(2500))  # short lines: well under 50KB
        net.handler = lambda request: httpx.Response(200, headers=TEXT, content=page.encode())

        result = await fetch("https://example.com/")

        shown, _, note = text_of(result).rpartition("\n")
        assert shown.endswith("l1999")
        assert note == "[Showing 2000 of 2500 lines. Content truncated at 2000 lines.]"

    async def test_a_body_cut_inside_a_character_is_still_text(
        self, net: Net, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(fetch_url, "MAX_BODY_BYTES", 1000)  # 3 x 333 = 999: one byte into #334
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200, headers=TEXT, content=("\u4e2d" * 400).encode()
        )

        result = await fetch("https://example.com/")

        assert text_of(result).startswith("\u4e2d" * 333)
        assert result.details["truncated"] is True

    async def test_a_body_without_a_declared_charset_is_read_as_utf8(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200, headers={"content-type": "text/plain"}, content="h\u00e9llo".encode()
        )

        result = await fetch("https://example.com/")

        assert text_of(result) == "h\u00e9llo"

    @pytest.mark.parametrize("status", [200, 201, 204, 299])
    async def test_a_success_status_is_content(self, net: Net, status: int):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(status, headers=TEXT, content=b"")

        result = await fetch("https://example.com/")

        assert result.details["statusCode"] == status

    @pytest.mark.parametrize("status", [300, 304, 400, 404, 500, 599])
    async def test_any_other_status_is_reported_as_an_error(self, net: Net, status: int):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(status, headers=TEXT, content=b"body")

        result = await fetch("https://example.com/")

        assert text_of(result) == f"HTTP {status} fetching https://example.com/"

    @pytest.mark.parametrize(
        "content_type", ["text/html", "TEXT/HTML; charset=utf-8", "application/xhtml+xml"]
    )
    async def test_html_is_reduced_to_text_whatever_the_case_of_its_type(
        self, net: Net, content_type: str
    ):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200, headers={"content-type": content_type}, content=b"<p>hello</p>"
        )

        result = await fetch("https://example.com/")

        assert text_of(result) == "hello"

    @pytest.mark.parametrize("content_type", ["text/plain", "application/json", ""])
    async def test_what_is_not_html_is_left_as_it_is(self, net: Net, content_type: str):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(
            200,
            headers={"content-type": content_type} if content_type else {},
            content=b"<p>hello</p>",
        )

        result = await fetch("https://example.com/")

        assert text_of(result) == "<p>hello</p>"

    async def test_an_error_status_is_reported_with_its_code(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]
        net.handler = lambda request: httpx.Response(404, content=b"gone")

        result = await fetch("https://example.com/missing")

        assert text_of(result) == "HTTP 404 fetching https://example.com/missing"

    async def test_a_failure_is_reported_not_raised(self, net: Net):
        net.dns["example.com"] = ["93.184.216.34"]

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        net.handler = handler

        result = await fetch("https://example.com/")

        assert "Failed to fetch https://example.com/" in text_of(result)
        assert "connection refused" in text_of(result)

    async def test_an_invalid_url_is_refused(self, net: Net):
        result = await fetch("http://[::1/")

        assert "Refusing to fetch" in text_of(result)
        assert net.requests == []


class TestExtractText:
    def test_the_readability_library_is_used_when_installed(self, monkeypatch):
        fake = types.SimpleNamespace(extract=lambda html, **kw: "from the library")
        monkeypatch.setitem(sys.modules, "trafilatura", fake)

        assert fetch_url._extract_text("<p>x</p>") == "from the library"

    @pytest.mark.parametrize("outcome", ["nothing", "error"])
    def test_the_plain_converter_takes_over_when_it_gives_nothing_or_fails(
        self, monkeypatch, outcome: str
    ):
        def extract(html: str, **kw: Any) -> Any:
            if outcome == "error":
                raise ValueError("lxml choked")
            return None

        monkeypatch.setitem(sys.modules, "trafilatura", types.SimpleNamespace(extract=extract))

        assert fetch_url._extract_text("<p>plain</p>") == "plain"

    def test_the_plain_converter_is_used_when_the_library_is_absent(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "trafilatura", None)  # import raises ImportError

        assert fetch_url._extract_text("<p>plain</p>") == "plain"


# -- HTML to text --------------------------------------------------------------------------

# Inputs that make a careless converter quadratic (or make it raise). The old regex-based
# one took 2.5 s on 8000 "<script>" tags and 10 s on 16000; each of these takes a fraction
# of a second, so the time budget below is far from the edge.
HOSTILE_HTML = {
    "unclosed tags": "<a " * 100_000,
    "unclosed attributes": '<a b="' * 100_000,
    "unclosed comments": "<!--" * 100_000,
    "unclosed scripts": "<script>" * 100_000,
    "closing tags without a bracket": "</script" * 100_000,
    "many scripts": "<script>var a=1;</script>" * 20_000,
    "many tags": "<p><b>x</b></p>" * 20_000,
    "brackets": "<" * 1_000_000,
    "spaces": " " * 1_000_000,
    "newlines": "\n" * 1_000_000,
    "spaces and newlines": " \n" * 500_000,
    "ampersands": "&" * 1_000_000,
    "numeric references": "&#" * 500_000,
    "unfinished references": "&#x" * 300_000,
    "long entity names": ("&" + "a" * 40) * 20_000,
    "a huge number in a reference": "&#" + "9" * 1_000_000,
    "a huge hex number in a reference": "&#x" + "f" * 1_000_000,
    "many long numbers in references": "&#99999999;" * 100_000,
}


class TestHtmlToText:
    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ("<p>Hello <b>world</b></p>", "Hello world"),
            ("a<!-- hidden -->b", "ab"),
            ("<!DOCTYPE html><p>x</p>", "x"),
            ("<?xml version='1.0'?><p>x</p>", "x"),
            ("<![CDATA[ not shown ]]>after", "after"),
            ("<p>one</p><p>two</p>", "one\n\ntwo"),
            ("a<br>b", "a\nb"),
            ("a<br/>b", "a\nb"),
            ("1 < 2 and 3 > 2", "1 < 2 and 3 > 2"),
            ("<p>a   \t b</p>", "a b"),
            ("a &amp; b&nbsp;c &#65;&#x42;", "a & b c AB"),
            ("x&#99999999999999999999y", "x\ufffdy"),  # not a character; and int() would raise
            ("x&#xfffffffffy", "x\ufffdy"),
            ("x&#1114112;y&#0000065;", "x\ufffdyA"),  # one past U+10FFFF; a padded number
            ("<SCRIPT>x</SCRIPT>y", "y"),
            ("<script type='x'>if (a<b) { s = '</p>' }</script>after", "after"),
            ("<style>p { color: red }</style>after", "after"),
            ("<script>a</script>b<script>c</script>d", "bd"),
            ("<div>a</div>\n\n\n\n<div>b</div>", "a\n\nb"),
            ("a\n\n\nb", "a\n\nb"),  # two blank lines at most
            ("a \n b", "a\nb"),  # no spaces left at the edge of a line
            ("a\r\nb", "a\nb"),
            ("a<!-- x > y -->b", "ab"),  # a comment ends at "-->", not at the first ">"
            ("<h1>T</h1>x", "T\nx"),  # a tag name may have digits
            ("a</script>b", "ab"),  # a closing tag on its own opens nothing
            ("<div>a</div><div>b</div>", "a\n\nb"),
            ("a<div>b</div>c", "a\nb\nc"),
            ("&#128512;&#x1F600;&#x10FFFD;", "\U0001f600\U0001f600\U0010fffd"),  # real characters
            ("<title>T</title><p>x</p>", "T\n\nx"),
            ("<a href='x'>link</a>", "link"),
            ('<a title="a>b">tail</a>', 'b">tail'),  # cosmetic: a quoted ">" ends the tag
        ],
    )
    def test_conversions(self, html: str, expected: str):
        assert _simple_html_to_text(html) == expected

    @pytest.mark.parametrize(
        "tag",
        [
            *("address", "article", "aside", "blockquote", "body", "br", "caption", "center"),
            *("dd", "details", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure"),
            *("footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup"),
            *("hr", "html", "legend", "li", "main", "menu", "nav", "ol", "p", "pre", "section"),
            *("summary", "table", "tbody", "td", "tfoot", "th", "thead", "title", "tr", "ul"),
        ],
    )
    def test_a_block_level_tag_starts_a_new_line(self, tag: str):
        assert _simple_html_to_text(f"a<{tag}>b") == "a\nb"
        assert _simple_html_to_text(f"a</{tag}>b") == "a\nb"

    @pytest.mark.parametrize(
        "tag",
        [
            *("a", "abbr", "b", "code", "em", "font", "i", "label"),
            *("s", "small", "span", "strong", "sub", "sup", "u"),
        ],
    )
    def test_an_inline_tag_does_not(self, tag: str):
        assert _simple_html_to_text(f"a<{tag}>b</{tag}>c") == "abc"

    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ("before<script>alert(1)", "before"),
            ("before<style>p{}", "before"),
            ("a<script>b<p>c</p>", "a"),  # a later ">" does not close it
            ("a<style>b<p>c</p>", "a"),
            ("before<!-- never closed", "before"),
            ("before<a href='x'", "before"),
            ("before<![CDATA[ never closed", "before"),
            ("before</script", "before"),
        ],
    )
    def test_something_left_open_ends_the_text_there(self, html: str, expected: str):
        assert _simple_html_to_text(html) == expected

    @pytest.mark.parametrize("name", HOSTILE_HTML)
    def test_hostile_input_is_converted_in_linear_time(self, name: str):
        html = HOSTILE_HTML[name]

        started = time.perf_counter()
        _simple_html_to_text(html)

        assert time.perf_counter() - started < 2.0
