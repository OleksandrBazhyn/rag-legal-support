"""Middleware шари безпеки для FastAPI.

1. RateLimitMiddleware  — ковзне вікно per-IP + глобальний ліміт
2. SecurityHeadersMiddleware — HTTP security headers
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.security import mask_ip

import logging
logger = logging.getLogger(__name__)


# ── Rate Limiter ──────────────────────────────────────────────────────────────

# Ендпоінти, на які поширюється rate limit (за вартістю виклику OpenAI)
_RATE_LIMITED_PATHS = {"/query", "/query/stream"}


def _get_client_ip(request: Request) -> str:
    """Повертає IP клієнта з урахуванням reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding window rate limiter.

    Параметри (всі конфігуруються через env):
    - per_ip_rpm: запитів на хвилину з одного IP (default 15)
    - global_rpm: максимум запитів на хвилину загалом (default 200)
    """

    def __init__(
        self,
        app,
        per_ip_rpm: int = 15,
        global_rpm: int = 200,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._per_ip_limit  = per_ip_rpm
        self._global_limit  = global_rpm
        self._window        = window_seconds
        self._ip_history: dict[str, deque[float]] = defaultdict(deque)
        self._global_history: deque[float]         = deque()
        self._lock = asyncio.Lock()   # захист від race condition під concurrent requests
        self._request_counter: int = 0  # лічильник для cleanup (не залежить від розміру deque)

    def _cleanup(self, dq: deque, cutoff: float) -> None:
        while dq and dq[0] < cutoff:
            dq.popleft()

    async def dispatch(self, request: Request, call_next):
        if request.url.path not in _RATE_LIMITED_PATHS:
            return await call_next(request)

        async with self._lock:
            now     = time.monotonic()
            cutoff  = now - self._window
            ip      = _get_client_ip(request)

            # ── Глобальний ліміт ──────────────────────────────────────────────
            self._cleanup(self._global_history, cutoff)
            if len(self._global_history) >= self._global_limit:
                logger.warning("Rate limit GLOBAL: %d req/min exceeded", self._global_limit)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Сервер перевантажений. Спробуйте через хвилину."},
                    headers={"Retry-After": str(self._window)},
                )
            self._global_history.append(now)

            # ── Per-IP ліміт ──────────────────────────────────────────────────
            ip_dq = self._ip_history[ip]
            self._cleanup(ip_dq, cutoff)

            if len(ip_dq) >= self._per_ip_limit:
                retry_after = max(1, int(self._window - (now - ip_dq[0])))
                logger.warning(
                    "Rate limit IP %s: %d req/min exceeded",
                    mask_ip(ip), self._per_ip_limit,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            f"Забагато запитів. Ліміт: {self._per_ip_limit} запитів/хв. "
                            f"Спробуйте через {retry_after} сек."
                        )
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            ip_dq.append(now)

            # ── Cleanup пам'яті: кожні 500 запитів видаляємо порожні IP ───────
            self._request_counter += 1
            if self._request_counter % 500 == 0:
                stale = [k for k, dq in self._ip_history.items() if not dq]
                for k in stale:
                    del self._ip_history[k]

        return await call_next(request)


# ── Security Headers ──────────────────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Додає стандартні HTTP security headers до кожної відповіді."""

    # Content Security Policy: дозволяємо тільки те, що реально потрібно
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "   # потрібен для inline JS у index.html
        "style-src 'self' 'unsafe-inline'; "    # потрібен для inline CSS
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"]        = "nosniff"
        h["X-Frame-Options"]               = "DENY"
        h["X-XSS-Protection"]             = "1; mode=block"
        h["Referrer-Policy"]               = "strict-origin-when-cross-origin"
        h["Permissions-Policy"]            = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        h["Content-Security-Policy"]       = self._CSP
        # HSTS — вмикаємо тільки якщо сервер за HTTPS (reverse proxy встановить це)
        # h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
