"""Guard for server addresses users supply (Ollama, self-hosted, gateways): this server will connect to them.

Always refused: anything that is not http(s), and cloud metadata / link-local addresses (the classic way to steal a host's credentials).
Private networks and localhost are allowed by default, because a self-hosted model is usually exactly there; set
LLM_ALLOW_PRIVATE_URLS=0 on a shared deployment to refuse those too."""
import ipaddress
import os
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    pass


def check(url: str) -> str:
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UnsafeURL("The address must start with http:// or https://.")
    allow_private = os.getenv("LLM_ALLOW_PRIVATE_URLS", "1").lower() not in ("0", "false", "no")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return url.strip().rstrip("/")          # unresolved now (e.g. a container name): the connection error will say so when it is used
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or str(ip) == "fd00:ec2::254":
            raise UnsafeURL("That address is not allowed (link-local and metadata addresses are blocked).")
        if not allow_private and (ip.is_private or ip.is_loopback):
            raise UnsafeURL("This server does not allow private-network model addresses.")
    return url.strip().rstrip("/")
