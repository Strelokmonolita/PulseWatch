"""SSRF protection for user-supplied URLs.

Blocks addresses that reach the host's own infrastructure: loopback, link-local
(cloud metadata), RFC 1918, CGNAT, unique-local IPv6 and friends.

Note: Python's ``ipaddress.is_private`` is deliberately NOT used. It folds in the
benchmarking range ``198.18.0.0/15``, which Docker Desktop and several VPN/proxy
setups use for DNS interception -- blocking it would make ordinary public sites
unreachable. The ranges we care about are listed explicitly instead.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import get_settings

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
}

# Ranges that can reach the host, its LAN, or a cloud metadata service.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),  # "this network"
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("255.255.255.255/32"),  # broadcast
    ipaddress.ip_network("::/128"),  # unspecified
    ipaddress.ip_network("::1/128"),  # loopback
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("ff00::/8"),  # multicast
]


class UnsafeURLError(ValueError):
    """Raised when a URL points into a private or reserved network."""


def _parse_extra_networks(raw: str | None) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse a comma-separated list of extra networks to block."""
    if not raw:
        return []
    networks = []
    for chunk in raw.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return networks


def blocked_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Blocked ranges, including any added through configuration."""
    return [*_BLOCKED_NETWORKS, *_parse_extra_networks(get_settings().ssrf_blocked_networks)]


def _is_forbidden_ip(raw: str) -> bool:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return True
    if address.is_multicast or address.is_unspecified or address.is_reserved:
        return True
    return any(address in network for network in blocked_networks())


async def _resolve(hostname: str, port: int) -> set[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeURLError("hostname could not be resolved") from exc
    return {info[4][0] for info in infos}


async def ensure_safe_url(url: str) -> None:
    """Reject URLs targeting local or private infrastructure."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("only http and https URLs are allowed")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL must contain a hostname")

    normalized = hostname.rstrip(".").lower()
    if normalized in _BLOCKED_HOSTNAMES or normalized.endswith(".local"):
        raise UnsafeURLError("hostname is not allowed")

    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_forbidden_ip(str(literal)):
            raise UnsafeURLError("the target address is not allowed")
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = await _resolve(normalized, port)
    if not addresses:
        raise UnsafeURLError("hostname could not be resolved")
    for address in addresses:
        if _is_forbidden_ip(address):
            raise UnsafeURLError("the target address is not allowed")
