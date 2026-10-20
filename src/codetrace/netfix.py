"""Opt-in IPv4-only name resolution (``CODETRACE_FORCE_IPV4=1``).

Some networks advertise IPv6 routes that silently drop traffic; Python HTTP
clients then stall instead of falling back to IPv4. This is a host network
workaround, not part of the method, and is off by default.
"""

from __future__ import annotations

import os
import socket
from typing import Any

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host: Any, port: Any, family: int = 0, *args: Any, **kwargs: Any) -> Any:
    return _orig_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)


def apply_from_env() -> bool:
    if os.environ.get("CODETRACE_FORCE_IPV4") == "1":
        socket.getaddrinfo = _ipv4_getaddrinfo
        return True
    return False
