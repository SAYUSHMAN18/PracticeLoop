from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings

# Everything except script-src, which is built per request so it can carry
# that request's nonce (see SecurityHeadersMiddleware.dispatch).
#
# style-src keeps 'unsafe-inline' and is not going to stop: there are ~260
# inline style="..." attributes across these templates, and a nonce cannot
# cover a style attribute -- only a <style> element. Removing it means
# moving every one of those into a class, which is a real refactor with no
# security payoff worth the churn while script-src is the actual XSS vector.
_CSP_TAIL = "; ".join(
    [
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline response headers that cost nothing and close off a few
    classes of attack (clickjacking, MIME sniffing, referrer leakage,
    and -- via CSP -- loading a script/style/font/image from anywhere this
    app doesn't itself serve).

    script-src is nonce-based rather than 'unsafe-inline': every inline
    <script> in these templates carries the per-request nonce, so injected
    markup that doesn't know it simply never executes. That's the whole
    point of a CSP for an app that renders user-supplied text (mentor
    conversations, question banks, pasted job descriptions) back into HTML."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generated before the route runs, so templates can read it off
        # request.state and stamp it on their inline <script> blocks -- the
        # same value then goes into this response's own header below. A
        # fresh 128 bits per request: a nonce an attacker can predict (or
        # one reused across responses) is worth exactly as much to them as
        # 'unsafe-inline' was.
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; {_CSP_TAIL}"
        )
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


class StaticCacheHeadersMiddleware(BaseHTTPMiddleware):
    """Marks everything under /static as long-lived and immutable.

    Safe specifically because every static asset is referenced with a
    content-hash query string (see core/templates.py's asset_version) --
    the URL itself changes whenever the file's content does, so a browser
    that caches this response forever will still fetch a new one the
    moment a deploy actually changes the file. Without this, a browser's
    own heuristic caching was serving a stale style.css against fresh
    HTML after a deploy (the sidebar shipped with no CSS for it, for
    exactly this reason) -- this both fixes that and, for anyone who
    doesn't hit that edge case, saves a repeat visitor the request
    entirely instead of just skipping the smaller 304 round-trip."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Rejects requests whose declared Content-Length exceeds a per-path (or
    default) cap, before FastAPI buffers the body into memory.

    This trusts the Content-Length header, so a client sending a chunked
    body with no declared length slips past it -- an acceptable gap for this
    app's threat model (form/file uploads from ordinary clients always send
    Content-Length); the resume upload has its own hard byte-counted cap
    in app/profile/router.py as the real backstop for the largest attack
    surface.
    """

    def __init__(self, app, default_max_bytes: int, path_overrides: dict[str, int] | None = None):
        super().__init__(app)
        self._default_max_bytes = default_max_bytes
        self._path_overrides = path_overrides or {}

    async def dispatch(self, request: Request, call_next) -> Response:
        max_bytes = self._path_overrides.get(request.url.path, self._default_max_bytes)
        content_length = request.headers.get("content-length")
        if content_length is not None and content_length.isdigit() and int(content_length) > max_bytes:
            return Response("Request body too large.", status_code=413)
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-IP rate limit on a fixed set of paths (login/signup),
    to blunt credential-stuffing and signup spam.

    In-memory and per-process: on a multi-worker or multi-replica deployment
    each process enforces its own window, so the effective limit scales with
    worker count. That's an acceptable tradeoff for this app's scale; a
    shared store (e.g. Redis) would be needed to make the limit exact across
    processes.
    """

    def __init__(self, app, limits: dict[str, tuple[int, float]]):
        super().__init__(app)
        self._limits = limits  # path -> (max_requests, window_seconds)
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next) -> Response:
        limit = self._limits.get(request.url.path)
        if limit is not None and request.method == "POST":
            max_requests, window_seconds = limit
            client_ip = request.client.host if request.client else "unknown"
            key = (request.url.path, client_ip)
            now = time.monotonic()
            hits = self._hits[key]

            while hits and now - hits[0] > window_seconds:
                hits.popleft()

            if len(hits) >= max_requests:
                return Response("Too many requests. Try again shortly.", status_code=429)

            hits.append(now)

        return await call_next(request)
