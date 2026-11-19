"""A fake API behind ``httpx.MockTransport``: no network, no server."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import httpx
import pytest

from jamframe import AsyncClient, Client

BASE = "https://api.test"
KEY = "jf_live_testkey"

Responder = Union[httpx.Response, Exception, Callable[[httpx.Request], httpx.Response]]


def json_response(status: int, body: Any, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


class MockAPI:
    """Routes ``(method, path)`` to a queue of answers; the last one repeats."""

    def __init__(self) -> None:
        self.routes: Dict[Tuple[str, str], List[Responder]] = {}
        self.requests: List[httpx.Request] = []

    def add(self, method: str, path: str, *answers: Responder) -> "MockAPI":
        self.routes.setdefault((method, "/v1" + path), []).extend(answers)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        queue = self.routes.get(key)
        if not queue:
            return json_response(404, {"error": "NOT_FOUND", "message": f"no mock for {key}"})
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        if callable(answer) and not isinstance(answer, httpx.Response):
            return answer(request)
        return answer

    # Helpers for assertions.
    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self, index: int = -1) -> Any:
        content = self.requests[index].content
        return json.loads(content) if content else None

    def calls(self) -> List[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: List[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    async def asleep(self, seconds: float) -> None:
        self.sleep(seconds)

    def clock(self) -> float:
        return self.now


@pytest.fixture
def api() -> MockAPI:
    return MockAPI()


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


def make_client(api: MockAPI, fake_time: FakeTime, **kwargs: Any) -> Client:
    http = httpx.Client(transport=httpx.MockTransport(api.handler))
    client = Client(KEY, base_url=BASE, http_client=http, **kwargs)
    client._sleep = fake_time.sleep
    client._clock = fake_time.clock
    client._random = lambda: 1.0  # no jitter: delays are exactly the base backoff
    return client


def make_async_client(api: MockAPI, fake_time: FakeTime, **kwargs: Any) -> AsyncClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
    client = AsyncClient(KEY, base_url=BASE, http_client=http, **kwargs)
    client._sleep = fake_time.asleep
    client._clock = fake_time.clock
    client._random = lambda: 1.0
    return client


@pytest.fixture
def client(api: MockAPI, fake_time: FakeTime) -> Client:
    return make_client(api, fake_time)


# ---------------------------------------------------------------- sample payloads

DESIGN = {
    "id": "tpl_1",
    "name": "Coffee tips carousel",
    "orientation": "square",
    "pages": 2,
    "animated": False,
    "createdAt": "2026-11-01T09:12:44.000Z",
    "updatedAt": "2026-11-05T16:03:10.000Z",
}

RESULT = {
    "content": "https://cdn.example.com/r/1.png",
    "type": "url",
    "format": "png",
    "pages": [1],
    "credits": 1,
    "renderId": "rnd_1",
    "responseTime": 1840,
}


def job(status: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "id": "job_1",
        "status": status,
        "progress": 1 if status == "succeeded" else 0.4,
        "format": "mp4",
        "templateId": "tpl_1",
        "createdAt": "2026-11-09T12:36:24.161Z",
        "startedAt": "2026-11-09T12:36:24.165Z",
        "finishedAt": None,
        "result": None,
        "error": None,
    }
    if status == "succeeded":
        body["result"] = dict(RESULT, format="mp4", content="https://cdn.example.com/r/1.mp4", duration=13)
    if status == "failed":
        body["error"] = {"error": "VALIDATION_ERROR", "message": "Video failed to load"}
    body.update(extra)
    return body


POST = {
    "id": "post_1",
    "status": "queued",
    "caption": "Hello",
    "targets": [],
    "createdAt": "2026-11-09T12:00:00.000Z",
}

SUBSCRIPTION = {
    "id": "sub_1",
    "url": "https://example.com/hook",
    "events": ["render.completed"],
    "status": "active",
    "secretTail": "wxyz",
    "failureCount": 0,
    "lastFiredAt": None,
    "createdAt": "2026-11-09T12:00:00.000Z",
    "updatedAt": "2026-11-09T12:00:00.000Z",
}
