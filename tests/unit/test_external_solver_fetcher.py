from __future__ import annotations

import json

import httpx
import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.external_solver_fetcher import ExternalSolverFetcher


def _solver_ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode())
    assert body["cmd"] == "request.get"
    assert body["session"] == "spiderhub"
    return httpx.Response(
        200,
        json={
            "status": "ok",
            "solution": {
                "url": body["url"],
                "status": 200,
                "response": "<html>solver-ok</html>",
                "cookies": [
                    {
                        "name": "cf_clearance",
                        "value": "tok",
                        "domain": ".example.com",
                        "path": "/",
                    }
                ],
                "userAgent": "Mozilla/5.0",
            },
        },
        request=request,
    )


@pytest.mark.asyncio
async def test_external_solver_success() -> None:
    settings = Settings(
        request_delay_seconds=0.0,
        allow_external_solver=True,
        external_solver_url="http://solver.test/v1",
    )
    async with ExternalSolverFetcher(
        settings,
        transport=httpx.MockTransport(_solver_ok),
    ) as fetcher:
        resp = await fetcher.fetch("https://example.com/a")
    assert "solver-ok" in resp.text
    assert resp.headers.get("user-agent") == "Mozilla/5.0"
    cookies = await fetcher.export_cookies()
    assert cookies[0]["name"] == "cf_clearance"


@pytest.mark.asyncio
async def test_external_solver_still_challenge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "solution": {
                    "url": "https://example.com/a",
                    "status": 403,
                    "response": "<title>Just a moment...</title>challenge-platform",
                    "cookies": [],
                },
            },
            request=request,
        )

    settings = Settings(request_delay_seconds=0.0)
    async with ExternalSolverFetcher(
        settings,
        transport=httpx.MockTransport(handler),
    ) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://example.com/a")


@pytest.mark.asyncio
async def test_external_solver_non_ok_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "error", "message": "timeout"},
            request=request,
        )

    settings = Settings(request_delay_seconds=0.0)
    async with ExternalSolverFetcher(
        settings,
        transport=httpx.MockTransport(handler),
    ) as fetcher:
        with pytest.raises(RuntimeError, match="timeout"):
            await fetcher.fetch("https://example.com/a")
