from __future__ import annotations

import logging
import time
from datetime import datetime
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("cbnu_agent.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid4())[:8]
        requested_at = datetime.now().isoformat(timespec="seconds")
        start = time.perf_counter()
        request.state.request_id = request_id
        request.state.requested_at = requested_at
        if request.url.path == "/api/chat":
            request.state.agent_context = {
                "request_id": request_id,
                "requested_at": requested_at,
                "method": request.method,
                "path": request.url.path,
            }
        logger.info("request_start id=%s method=%s path=%s", request_id, request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception("request_error id=%s elapsed_ms=%.2f", request_id, elapsed_ms)
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "request_end id=%s status=%s elapsed_ms=%.2f",
            request_id,
            response.status_code,
            elapsed_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
