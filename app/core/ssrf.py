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


def parse_http_url(url: str) -> tuple[str, int]:
    """Validate URL syntax without DNS access and return its hostname and port."""
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url
    ):
        raise UnsafeURLError("URL must not contain whitespace or control characters")
    if "\\" in url:
        raise UnsafeURLError("URL must not contain backslashes")

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError("URL contains an invalid hostname or port") from exc

    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("only http and https URLs are allowed")
    if not hostname or not hostname.rstrip("."):
        raise UnsafeURLError("URL must contain a hostname")
    if port == 0 or parsed.netloc.endswith(":"):
        raise UnsafeURLError("URL port must be between 1 and 65535")

    default_port = 443 if parsed.scheme == "https" else 80
    return hostname.rstrip(".").lower(), port if port is not None else default_port


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
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    if mapped is not None and _is_forbidden_ip(str(mapped)):
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
    normalized, port = parse_http_url(url)
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

    addresses = await _resolve(normalized, port)
    if not addresses:
        raise UnsafeURLError("hostname could not be resolved")
    for address in addresses:
        if _is_forbidden_ip(address):
            raise UnsafeURLError("the target address is not allowed")
