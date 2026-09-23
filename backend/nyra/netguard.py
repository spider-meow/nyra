"""Outbound request guard: the crawler only ever talks to the public internet.

The hosted product fetches URLs chosen by its users, from a server that
can also reach its own private network, cloud metadata endpoints
(169.254.169.254) and localhost services. Every outbound request made by
the pipeline — robots.txt, sitemaps, images, and every request the
headless browser issues — goes through `check_url` first, and is refused
if the host resolves to anything that isn't a globally routable address.

Resolution happens again at request time (per redirect hop too), not
once per site, so a redirect to an internal address is refused as well.
A DNS answer that changes between the check and the connection (DNS
rebinding) is not covered; the browser and httpx resolve on their own.

`NYRA_ALLOW_PRIVATE_HOSTS=1` lifts the guard, for crawling a local test
site from the CLI. Never set it on a server.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

_CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, Optional[str]]] = {}


class BlockedURL(httpx.HTTPError):
    """Raised when a URL points somewhere the crawler must not go."""


def private_hosts_allowed() -> bool:
    return os.environ.get("NYRA_ALLOW_PRIVATE_HOSTS") == "1"


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _verdict(host: str, addresses: list[str]) -> Optional[str]:
    if not addresses:
        return f"{host} ne se résout pas"
    for address in addresses:
        if not is_public_ip(address):
            return f"{host} pointe vers une adresse non publique ({address})"
    return None


def _cached(host: str) -> tuple[bool, Optional[str]]:
    hit = _cache.get(host)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
        return True, hit[1]
    return False, None


def _split(url: str) -> tuple[Optional[str], Optional[str]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None, f"schéma non autorisé : {parsed.scheme or '(vide)'}"
    host = (parsed.hostname or "").lower()
    if not host:
        return None, "adresse sans hôte"
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".internal") or host.endswith(".local"):
        return None, f"{host} est un hôte local"
    return host, None


def check_url(url: str) -> None:
    """Raise BlockedURL unless `url` is http(s) and resolves only to public IPs."""
    host, problem = _split(url)
    if problem:
        raise BlockedURL(problem)
    if private_hosts_allowed():
        return
    found, reason = _cached(host)
    if not found:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            addresses = sorted({info[4][0] for info in infos})
        except (socket.gaierror, UnicodeError):
            addresses = []
        reason = _verdict(host, addresses)
        _cache[host] = (time.monotonic(), reason)
    if reason:
        raise BlockedURL(reason)


async def check_url_async(url: str) -> None:
    host, problem = _split(url)
    if problem:
        raise BlockedURL(problem)
    if private_hosts_allowed():
        return
    found, reason = _cached(host)
    if not found:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            addresses = sorted({info[4][0] for info in infos})
        except (socket.gaierror, UnicodeError):
            addresses = []
        reason = _verdict(host, addresses)
        _cache[host] = (time.monotonic(), reason)
    if reason:
        raise BlockedURL(reason)


def _sync_hook(request: httpx.Request) -> None:
    check_url(str(request.url))


async def _async_hook(request: httpx.Request) -> None:
    await check_url_async(str(request.url))


def client(**kwargs) -> httpx.Client:
    """An httpx.Client whose every request (redirect hops included) is checked."""
    hooks = kwargs.pop("event_hooks", {}) or {}
    hooks.setdefault("request", []).append(_sync_hook)
    return httpx.Client(event_hooks=hooks, **kwargs)


def async_client(**kwargs) -> httpx.AsyncClient:
    hooks = kwargs.pop("event_hooks", {}) or {}
    hooks.setdefault("request", []).append(_async_hook)
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)


def read_capped(response: httpx.Response, max_bytes: int) -> Optional[bytes]:
    """Body of a streamed response, or None once it exceeds max_bytes."""
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        return None
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def aread_capped(response: httpx.Response, max_bytes: int) -> Optional[bytes]:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)
