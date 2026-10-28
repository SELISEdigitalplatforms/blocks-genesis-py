"""Fetching token signing keys and certificates from a remote source.

A key source is configured by a tenant admin, so the URL is untrusted: it must not be
able to reach this host's private network or metadata service, follow a redirect
elsewhere, hang a request, or exhaust memory. Every remote download goes through
`fetch_key_material`.

Errors and logs carry only a reason and the host, never the URL: a JWKS or certificate
URL may hold a signed query string.
"""
import asyncio
import ipaddress
import json
import logging
import math
import socket
import time
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import urlsplit

import aiohttp
import jwt
from aiohttp.abc import AbstractResolver, ResolveResult
from jwt import PyJWK, PyJWKSet, PyJWKSetError

from blocks_genesis._core.configuration import is_local_deployment

_logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 1024 * 1024
JWKS_CACHE_TTL_SECONDS = 600
JWKS_MIN_REFRESH_SECONDS = 60
# How long past the TTL the last good keys still serve while refreshes fail. Long enough
# to ride out a provider blip, short enough that a key the provider withdrew stops working.
JWKS_STALE_MAX_SECONDS = 3600
JWKS_CACHE_MAX_URLS = 1024


class KeyFetchError(Exception):
    def __init__(self, reason: str, host: str = ""):
        self.reason = reason
        self.host = host
        super().__init__(f"{reason} (host {host or '-'})")


def _refuse(reason: str, host: str) -> KeyFetchError:
    _logger.warning("Key fetch refused: %s (host %s)", reason, host)
    return KeyFetchError(reason, host)


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # is_global alone is not enough: Python reports IPv4 multicast and the deprecated
    # IPv6 site-local range as global.
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (ip.version == 6 and ip.is_site_local)
    )


async def _resolve_addresses(host: str, port: int) -> List[Tuple[int, str]]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys((family, sockaddr[0]) for family, _, _, _, sockaddr in infos))


def _ascii_host(host: str) -> str:
    """The host as aiohttp names it to its resolver: IDNA-encoded, lowercased, without
    one trailing dot."""
    ascii_host = host.encode("idna").decode("ascii").lower()
    return ascii_host[:-1] if ascii_host.endswith(".") else ascii_host


class _PinnedResolver(AbstractResolver):
    """Answers only for the checked host, and only with the addresses that were checked.

    aiohttp asks its resolver on every connection, so no second lookup can happen
    between the check and the connect, and a rebinding answer is never used. TLS still
    verifies the real hostname.
    """

    def __init__(self, host: str, addresses: List[Tuple[int, str]]):
        self._host = _ascii_host(host)
        self._addresses = list(addresses)

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC
    ) -> List[ResolveResult]:
        if _ascii_host(host) != self._host:
            raise OSError("host was not checked")
        return [
            ResolveResult(
                hostname=host,
                host=ip,
                port=port,
                family=ip_family,
                proto=0,
                flags=socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
            )
            for ip_family, ip in self._addresses
            if family in (socket.AF_UNSPEC, ip_family)
        ]

    async def close(self) -> None:
        pass


async def _checked_addresses(host: str, port: int, check_address: bool) -> List[Tuple[int, str]]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        # aiohttp never asks the resolver about an IP literal, so it is checked here.
        addresses = [(socket.AF_INET6 if literal.version == 6 else socket.AF_INET, host)]
    else:
        try:
            addresses = await _resolve_addresses(host, port)
        except (OSError, UnicodeError):
            # UnicodeError: a name IDNA cannot encode, such as an empty or over-long label.
            raise _refuse("dns", host) from None
        if not addresses:
            raise _refuse("dns", host) from None

    if check_address and not all(is_public_address(ip) for _, ip in addresses):
        raise _refuse("address_not_allowed", host) from None
    return addresses


async def _download(url: str, host: str, addresses: List[Tuple[int, str]]) -> bytes:
    try:
        # trust_env=False: through a proxy aiohttp would connect to the proxy, not the
        # checked address.
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(
                resolver=_PinnedResolver(host, addresses), use_dns_cache=False
            ),
            trust_env=False,
        ) as session:
            async with session.get(url, allow_redirects=False) as resp:
                if 300 <= resp.status < 400:
                    raise _refuse("redirect", host) from None
                if resp.status != 200:
                    raise _refuse("status", host) from None
                if resp.content_length is not None and resp.content_length > MAX_RESPONSE_BYTES:
                    raise _refuse("too_large", host) from None
                body = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise _refuse("too_large", host) from None
                return bytes(body)
    except (aiohttp.ClientError, OSError):
        raise _refuse("connect", host) from None


async def fetch_key_material(url: str, check_address: bool = True) -> bytes:
    """Download a key set or certificate.

    https only (http too in a local deployment), no redirects, a 5 s total timeout
    covering DNS, connect and read, and a 1 MiB cap. With `check_address`, every address
    the host resolves to must be public, and the connection goes only to those addresses.
    Only the primary tenant certificate, which the platform writes, turns the check off.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        raise _refuse("invalid_url", "") from None

    scheme = parts.scheme.lower()
    if not (scheme == "https" or (scheme == "http" and is_local_deployment())):
        raise _refuse("scheme_not_allowed", host) from None
    if not host or parts.username is not None or parts.password is not None:
        raise _refuse("invalid_url", host) from None
    port = port or (443 if scheme == "https" else 80)

    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            addresses = await _checked_addresses(host, port, check_address)
            return await _download(url, host, addresses)
    except TimeoutError:
        raise _refuse("timeout", host) from None


def _host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _parse_jwks(body: bytes, host: str) -> Dict[str, PyJWK]:
    try:
        data = json.loads(body)
        jwk_set = PyJWKSet.from_dict(data if isinstance(data, dict) else {})
    except (PyJWKSetError, ValueError):
        raise _refuse("invalid_jwks", host) from None
    return {
        jwk.key_id: jwk
        for jwk in jwk_set.keys
        if jwk.key_id and jwk.public_key_use in ("sig", None)
    }


class _JwksEntry:
    def __init__(self):
        self.keys: Dict[str, PyJWK] = {}
        self.fetched_at = -math.inf
        self.attempted_at = -math.inf
        self.failed = False
        self.lock = asyncio.Lock()


class JwksCache:
    """Signing keys per JWKS URL, in process.

    One fetch per URL per TTL. An unknown kid refreshes at most once a minute, and so
    does a failed fetch, so an unreachable or slow URL costs one fetch per minute.
    Concurrent callers share one fetch.
    """

    def __init__(self, time_func: Callable[[], float] = time.monotonic):
        self._time = time_func
        self._entries: Dict[str, _JwksEntry] = {}

    async def get_signing_key(self, url: str, token: str) -> Any:
        host = _host_of(url)
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError:
            raise _refuse("token_header", host) from None
        if not isinstance(kid, str) or not kid:
            raise _refuse("no_kid", host) from None

        entry = self._entry(url)
        # Held for the whole decision, so callers arriving during a fetch wait for it
        # instead of fetching again or missing the keys it brings.
        async with entry.lock:
            now = self._time()
            if (
                now - entry.fetched_at >= JWKS_CACHE_TTL_SECONDS
                and now - entry.attempted_at >= JWKS_MIN_REFRESH_SECONDS
            ):
                await self._refresh(url, host, entry)

            key = self._lookup(entry, kid)
            if key is None and self._time() - entry.attempted_at >= JWKS_MIN_REFRESH_SECONDS:
                await self._refresh(url, host, entry)
                key = self._lookup(entry, kid)
            if key is not None:
                return key

            if entry.failed and not entry.keys:
                raise _refuse("fetch_failed_recently", host) from None
            raise _refuse("kid_not_found", host) from None

    def _entry(self, url: str) -> _JwksEntry:
        entry = self._entries.get(url)
        if entry is None:
            if len(self._entries) >= JWKS_CACHE_MAX_URLS:
                self._entries.clear()
            entry = self._entries[url] = _JwksEntry()
        return entry

    def _lookup(self, entry: _JwksEntry, kid: str) -> Any:
        if self._time() - entry.fetched_at >= JWKS_CACHE_TTL_SECONDS + JWKS_STALE_MAX_SECONDS:
            entry.keys = {}
        jwk = entry.keys.get(kid)
        return jwk.key if jwk is not None else None

    async def _refresh(self, url: str, host: str, entry: _JwksEntry) -> None:
        """Called with the entry's lock held."""
        now = self._time()
        entry.attempted_at = now
        try:
            keys = _parse_jwks(await fetch_key_material(url), host)
        except KeyFetchError:
            entry.failed = True
            if entry.keys and now - entry.fetched_at < JWKS_CACHE_TTL_SECONDS + JWKS_STALE_MAX_SECONDS:
                _logger.warning("JWKS refresh failed; keeping the last good keys (host %s)", host)
                return
            entry.keys = {}
            raise
        entry.keys = keys
        entry.fetched_at = now
        entry.failed = False


_jwks_cache = JwksCache()


def get_jwks_cache() -> JwksCache:
    return _jwks_cache
