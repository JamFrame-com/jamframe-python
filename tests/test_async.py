from __future__ import annotations

import asyncio
from typing import Any, List

import httpx
import pytest

from jamframe import AsyncClient, InternalServerError, RateLimitError, RenderJobFailedError

from .conftest import DESIGN, POST, RESULT, FakeTime, MockAPI, job, json_response, make_async_client


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_async_whoami_and_headers(api: MockAPI, fake_time: FakeTime) -> None:
    async def go() -> Any:
        async with make_async_client(api, fake_time) as client:
            return await client.whoami()

    api.add("POST", "/auth", json_response(200, {"workspaceId": "ws_1", "username": "Jane"}))
    assert run(go()) == {"workspaceId": "ws_1", "username": "Jane"}
    assert api.last.headers["authorization"].startswith("Bearer ")


def test_async_render_waits_for_202(api: MockAPI, fake_time: FakeTime) -> None:
    api.add("POST", "/render", json_response(202, {"data": {"jobId": "job_1", "status": "running"}}))
    api.add("GET", "/render-jobs/job_1", json_response(200, {"job": job("running")}), json_response(200, {"job": job("succeeded")}))

    async def go() -> Any:
        client = make_async_client(api, fake_time)
        try:
            return await client.render("tpl_1", response={"format": "mp4"})
        finally:
            await client.close()

    out = run(go())
    assert out["data"]["format"] == "mp4"
    assert fake_time.sleeps == [1.0]


def test_async_render_and_wait_failed(api: MockAPI, fake_time: FakeTime) -> None:
    api.add("POST", "/render", json_response(202, {"data": {"jobId": "job_1", "status": "queued"}}))
    api.add("GET", "/render-jobs/job_1", json_response(200, {"job": job("failed")}))
    client = make_async_client(api, fake_time)
    with pytest.raises(RenderJobFailedError):
        run(client.render_and_wait("tpl_1"))
    assert api.body(0)["async"] is True


def test_async_iter_designs(api: MockAPI, fake_time: FakeTime) -> None:
    api.add(
        "GET",
        "/designs",
        json_response(200, {"designs": [dict(DESIGN, id="a")], "pagination": {"nextCursor": "a", "hasMore": True}}),
        json_response(200, {"designs": [dict(DESIGN, id="b")], "pagination": {"nextCursor": None, "hasMore": False}}),
    )
    client = make_async_client(api, fake_time)

    async def go() -> List[str]:
        return [d["id"] async for d in client.designs.iter(source="library", q="reel")]

    assert run(go()) == ["a", "b"]
    assert [r.url.params.get("cursor") for r in api.requests] == [None, "a"]
    assert [r.url.params.get("q") for r in api.requests] == ["reel", "reel"]


def test_async_renders_get(api: MockAPI, fake_time: FakeTime) -> None:
    item = {"id": "rnd_1", "status": "succeeded", "outputs": []}
    api.add("GET", "/renders/rnd_1", json_response(200, {"render": item}))
    client = make_async_client(api, fake_time)
    assert run(client.renders.get("rnd_1")) == item
    assert api.last.url.path == "/v1/renders/rnd_1"


def test_async_retries(api: MockAPI, fake_time: FakeTime) -> None:
    api.add(
        "POST",
        "/render",
        json_response(429, {"error": "RATE_LIMITED", "message": "x"}, {"Retry-After": "2"}),
        json_response(200, {"data": RESULT}),
    )
    api.add("GET", "/social/posts/post_1", httpx.ConnectError("refused"), json_response(200, {"post": POST}))
    api.add("POST", "/social/posts", json_response(502, {"error": "INTERNAL", "message": "x"}))
    client = make_async_client(api, fake_time)
    assert run(client.render("tpl_1"))["data"] == RESULT
    assert run(client.social.posts.get("post_1")) == POST
    with pytest.raises(InternalServerError):
        run(client.social.posts.create(renderId="r", accounts=["a"]))
    assert fake_time.sleeps == [2.0, 0.5]
    assert api.calls().count("POST /v1/social/posts") == 1


def test_async_rate_limit_too_long(api: MockAPI, fake_time: FakeTime) -> None:
    api.add("GET", "/social/accounts", json_response(429, {"error": "RATE_LIMITED", "message": "x"}, {"Retry-After": "90"}))
    client = make_async_client(api, fake_time)
    with pytest.raises(RateLimitError):
        run(client.social.accounts.list())


def test_async_own_http_client_not_closed(api: MockAPI) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))

    async def go() -> None:
        async with AsyncClient("k", base_url="https://api.test", http_client=http):
            pass

    run(go())
    assert not http.is_closed
