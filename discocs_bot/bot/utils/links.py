"""Finding and vetting links people send to the bot.

The bot fetches whatever URL it is given, so the URL is the attack surface: a
link to an address inside our own network would turn the bot into a proxy for
it. Everything here runs before yt-dlp ever sees the URL.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
ALLOWED_SCHEMES = {"http", "https"}
MAX_URL_LENGTH = 2048


class UnsafeLinkError(Exception):
    """The URL must not be fetched."""

    def __init__(self, message: str, *, user_message: str) -> None:
        super().__init__(message)
        self.user_message = user_message


def find_first_url(text: str | None) -> str | None:
    if not text:
        return None
    match = URL_PATTERN.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;)»\"'")


def _is_blocked_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    )


def resolve_addresses(host: str) -> list[str]:
    """Every address the host resolves to. Empty when it does not resolve."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return [info[4][0] for info in infos]


def validate_public_url(url: str, *, resolver=resolve_addresses) -> str:
    """Return the URL when it is safe to fetch, or raise UnsafeLinkError.

    A hostname is checked after resolution, not before: `nas.local` and
    `127.0.0.1.nip.io` look like ordinary names but point inside the network.
    """
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeLinkError("URL is too long", user_message="Ссылка слишком длинная.")

    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeLinkError(
            f"Unsupported scheme: {parts.scheme}",
            user_message="Поддерживаются только http и https ссылки.",
        )

    host = parts.hostname
    if not host:
        raise UnsafeLinkError("URL has no host", user_message="В ссылке нет адреса.")

    if _is_blocked_address(host):
        raise UnsafeLinkError(
            f"Blocked literal address: {host}",
            user_message="Эта ссылка ведёт внутрь сети — не открываю.",
        )

    addresses = resolver(host)
    if not addresses:
        raise UnsafeLinkError(
            f"Host does not resolve: {host}",
            user_message="Не удалось разрешить адрес из ссылки.",
        )
    blocked = [address for address in addresses if _is_blocked_address(address)]
    if blocked:
        logger.warning("Refusing link to internal address host=%s addresses=%s", host, blocked)
        raise UnsafeLinkError(
            f"Host resolves to a blocked address: {host} -> {blocked}",
            user_message="Эта ссылка ведёт внутрь сети — не открываю.",
        )
    return url
