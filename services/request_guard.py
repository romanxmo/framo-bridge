"""Who may talk to the local bridge server.

The server listens on localhost, but "localhost" is not a trust boundary: every
website the user has open can send requests to it from their browser. CORS
headers only decide whether a page may READ a response, not whether the request
runs, so a cross-site form POST reaches the handler regardless. That is why the
decision here is made on the server, before any route does its work.

Two checks, both required:

- ``Host`` must name this server (``localhost:8080`` / ``127.0.0.1:8080``).
  A DNS-rebinding page reaches us under its own hostname, so its requests
  carry that hostname in ``Host`` and are refused.
- ``Origin`` must be Framo (production, or a local dev server). Browsers send
  ``Origin`` on every cross-origin request and on every POST, so a missing
  Origin means "not a browser page" and is refused too, with two exceptions:
  ``GET /ping`` (a health check that reveals nothing) and the add-on's own
  ``POST /upload-model`` from Blender's Python, which sends no Origin.

Pure Python, no ``bpy``: unit-tested with plain ``python3``. It imports nothing
from the add-on, so the tests can load it by path.
"""

import re

PORT = 8080

ALLOWED_HOSTS = frozenset({f"localhost:{PORT}", f"127.0.0.1:{PORT}"})

_ALLOWED_ORIGIN_EXACT = frozenset({"https://framo.app", "https://www.framo.app"})

# A local Framo dev server on any port. Only plain http on the loopback names:
# an https://localhost origin is not something the dev setup produces.
_LOCAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1)(:[0-9]{1,5})?")

ALLOWED_METHODS = "GET, POST, OPTIONS"
ALLOWED_HEADERS = "Content-Type, X-Model-Metadata, X-User-Info"

# Routes answered without an allowed Origin, by method. Nothing else is.
_NO_ORIGIN_ROUTES = frozenset({
    ("GET", "/ping"),
    # export_service POSTs the exported GLB to this server from Blender's own
    # Python (urllib), which sends no Origin. A browser always sends Origin on
    # a POST, so "no Origin" cannot be a web page here.
    ("POST", "/upload-model"),
})


def is_allowed_origin(origin):
    """True for Framo's own origins; False for anything else, including None."""
    if not origin:
        return False
    if origin in _ALLOWED_ORIGIN_EXACT:
        return True
    match = _LOCAL_ORIGIN.fullmatch(origin)
    if not match:
        return False
    port = match.group(2)
    return port is None or 0 < int(port[1:]) <= 65535


def is_allowed_host(host):
    return bool(host) and host.strip().lower() in ALLOWED_HOSTS


def route_path(raw_path):
    """The path a route matches on: query string and fragment removed."""
    return raw_path.split("?", 1)[0].split("#", 1)[0]


class Decision:
    """The verdict for one request.

    ``allowed`` -- whether the route may run at all.
    ``cors_origin`` -- the exact origin to echo in Access-Control-Allow-Origin,
    or None to send no CORS headers (the response then stays unreadable to
    any page).
    ``reason`` -- why it was refused, for the log; never sent to the caller.
    """

    __slots__ = ("allowed", "cors_origin", "reason")

    def __init__(self, allowed, cors_origin=None, reason=""):
        self.allowed = allowed
        self.cors_origin = cors_origin
        self.reason = reason

    def __repr__(self):
        return f"Decision(allowed={self.allowed}, cors_origin={self.cors_origin!r}, reason={self.reason!r})"


def decide(method, raw_path, origin, host):
    """Decide whether a request may be served, and with which CORS origin."""
    if not is_allowed_host(host):
        return Decision(False, None, f"host {host!r} is not this server")

    origin_ok = is_allowed_origin(origin)
    cors_origin = origin if origin_ok else None
    path = route_path(raw_path)

    if method == "OPTIONS":
        # A preflight from a foreign origin gets nothing, so the browser never
        # sends the real request.
        if origin_ok:
            return Decision(True, cors_origin)
        return Decision(False, None, f"preflight from origin {origin!r}")

    if origin_ok:
        return Decision(True, cors_origin)

    if origin is None and (method, path) in _NO_ORIGIN_ROUTES:
        return Decision(True, None)

    if (method, path) == ("GET", "/ping"):
        # Answered for anyone so a health check never depends on the caller,
        # but without CORS headers a foreign page cannot read the answer.
        return Decision(True, None)

    if origin is None:
        return Decision(False, None, "no Origin header")
    return Decision(False, None, f"origin {origin!r} is not allowed")
