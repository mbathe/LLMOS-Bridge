"""SSRF (Server-Side Request Forgery) protection for the API/HTTP module.

Validates URLs before they are passed to httpx to prevent requests to:
  - Localhost / loopback addresses (127.0.0.0/8, ::1)
  - Private/internal networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
  - Link-local addresses (169.254.0.0/16, fe80::/10)
  - Cloud metadata endpoints (169.254.169.254, fd00:ec2::254)
  - Unix sockets and non-http(s) schemes
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SSRFError(Exception):
    """Raised when a URL targets a blocked address."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"SSRF blocked: {reason} (url={url!r})")


# Schemes that are allowed for outbound HTTP requests.
_ALLOWED_SCHEMES = {"http", "https"}

# Well-known cloud metadata IPs.
_METADATA_IPS = {
    "169.254.169.254",   # AWS / GCP / Azure
    "100.100.100.200",   # Alibaba Cloud
    "fd00:ec2::254",     # AWS IMDSv2 IPv6
}

# Well-known metadata hostnames.
_METADATA_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
}


def validate_url(url: str, *, allow_private: bool = False) -> str:
    """Validate *url* and return the normalised URL string.

    Raises ``SSRFError`` if the URL targets a private, loopback, link-local,
    or metadata address.

    Parameters
    ----------
    url:
        The URL to validate.
    allow_private:
        When ``True`` skip the private-network check.  This is exposed for
        testing but should **never** be ``True`` in production.
    """
    parsed = urlparse(url)

    # 1. Scheme check
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFError(url, f"scheme {parsed.scheme!r} is not allowed (only http/https)")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError(url, "no hostname in URL")

    # 2. Metadata hostname check
    if hostname.lower() in _METADATA_HOSTNAMES:
        raise SSRFError(url, f"hostname {hostname!r} is a known cloud metadata endpoint")

    # 3. Resolve hostname to IP(s) and check each one
    try:
        addrinfos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # DNS resolution failed — let httpx handle the actual error.
        return url

    for family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip_str = sockaddr[0]

        # Metadata IP check (before parsing — string comparison)
        if ip_str in _METADATA_IPS:
            raise SSRFError(url, f"resolved to cloud metadata IP {ip_str}")

        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        if addr.is_loopback:
            raise SSRFError(url, f"resolved to loopback address {ip_str}")

        if addr.is_link_local:
            raise SSRFError(url, f"resolved to link-local address {ip_str}")

        if not allow_private and addr.is_private:
            raise SSRFError(url, f"resolved to private address {ip_str}")

    return url
