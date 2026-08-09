from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import Any

import httpx

from spiderhub.challenges.detect import ChallengeDetectedError, detect_challenge
from spiderhub.core.settings import Settings
from spiderhub.downloaders.base import FetchedResponse

logger = logging.getLogger(__name__)


class ExternalSolverFetcher:
    """L4 adapter: FlareSolverr / Solverr compatible `/v1` API (default off)."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client = client
        self._owns_client = client is None
        self._cookies: list[dict[str, Any]] = []

    async def __aenter__(self) -> ExternalSolverFetcher:
        if self._client is None:
            timeout = httpx.Timeout(
                self._settings.external_solver_timeout_ms / 1000.0 + 30.0
            )
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=timeout,
            )
            self._owns_client = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def set_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Store cookies for AutoFetcher symmetry (solver API ignores them)."""
        self._cookies = list(cookies)

    async def export_cookies(self) -> list[dict[str, Any]]:
        return list(self._cookies)

    async def fetch(self, url: str) -> FetchedResponse:
        if self._client is None:
            raise RuntimeError(
                "ExternalSolverFetcher must be used as async context manager"
            )
        delay = self._settings.request_delay_seconds
        if delay > 0:
            await asyncio.sleep(delay)

        endpoint = self._settings.external_solver_url.strip()
        payload: dict[str, Any] = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": int(self._settings.external_solver_timeout_ms),
        }
        session = self._settings.external_solver_session.strip()
        if session:
            payload["session"] = session

        logger.info("L4 external solver request url=%s endpoint=%s", url, endpoint)
        response = await self._client.post(endpoint, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("external solver returned non-object JSON")

        status = str(data.get("status", "")).lower()
        if status != "ok":
            message = data.get("message") or data.get("error") or status or "unknown"
            raise RuntimeError(f"external solver failed: {message}")

        solution = data.get("solution")
        if not isinstance(solution, dict):
            raise RuntimeError("external solver missing solution object")

        final_url = str(solution.get("url") or url)
        http_status = int(solution.get("status") or 200)
        text = str(solution.get("response") or "")
        cookies_raw = solution.get("cookies") or []
        cookies: list[dict[str, Any]] = []
        if isinstance(cookies_raw, list):
            for item in cookies_raw:
                if isinstance(item, dict):
                    cookies.append(dict(item))
        self._cookies = cookies

        headers: dict[str, str] = {"content-type": "text/html"}
        ua = solution.get("userAgent")
        if isinstance(ua, str) and ua:
            headers["user-agent"] = ua

        reason = detect_challenge(
            url=final_url,
            status_code=http_status,
            text=text,
            headers=headers,
        )
        if reason:
            raise ChallengeDetectedError(final_url, http_status, reason)
        if not (200 <= http_status < 300):
            raise RuntimeError(f"external solver status {http_status} for {final_url}")

        return FetchedResponse(
            url=final_url,
            status_code=http_status,
            text=text,
            headers=headers,
        )
