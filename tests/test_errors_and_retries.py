from __future__ import annotations

import email.utils
import time
from typing import Type

import httpx
import pytest

from jamframe import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    Client,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PaymentRequiredError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from jamframe._base import parse_retry_after

from .conftest import DESIGN, RESULT, FakeTime, MockAPI, json_response, make_client

PAGE = {"designs": [DESIGN], "pagination": {"nextCursor": None, "hasMore": False}}


@pytest.mark.parametrize(
    "status, code, cls",
    [
        (400, "VALIDATION_ERROR", BadRequestError),
        (401, "INVALID_API_KEY", AuthenticationError),
        (402, "QUOTA_EXCEEDED", PaymentRequiredError),
        (403, "ORG_ON_HOLD", PermissionDeniedError),
        (404, "NOT_FOUND", NotFoundError),
        (409, "CONFLICT", ConflictError),
        (422, "VALIDATION_ERROR", UnprocessableEntityError),
        (500, "INTERNAL", InternalServerError),
        (418, "TEAPOT", APIError),
    ],
)
def test_error_mapping(api: MockAPI, client: Client, status: int, code: str, cls: Type[APIError]) -> None:
    detail = {"remaining": 1}
    api.add("POST", "/render", json_response(status, {"error": code, "message": "Nope", "detail": detail}))
    with pytest.raises(cls) as info:
        client.render("tpl_1")
    err = info.value
    assert type(err) is cls
    assert (err.status, err.code, err.error, err.message, err.detail) == (status, code, code, "Nope", detail)
    assert err.body == {"error": code, "message": "Nope", "detail": detail}
    assert str(err) == f"{status} {code}: Nope"
    assert len(api.requests) == 1  # POST /render is never retried on errors


def test_non_json_error_body(api: MockAPI, client: Client) -> None:
    api.add("GET", "/designs", httpx.Response(502, text="<html>Bad gateway</html>"))
    client.max_retries = 0
    with pytest.raises(InternalServerError) as info:
        client.designs.list()
    assert info.value.code == "HTTP_502"
    assert info.value.message == "<html>Bad gateway</html>"
    assert info.value.body == "<html>Bad gateway</html>"


def test_empty_error_body(api: MockAPI, client: Client) -> None:
    api.add("DELETE", "/webhooks/subscriptions/x", httpx.Response(404))
    with pytest.raises(NotFoundError) as info:
        client.webhooks.subscriptions.delete("x")
    assert info.value.code == "HTTP_404"
    assert info.value.message == "Not Found"


def test_non_json_success_body(api: MockAPI, client: Client) -> None:
    api.add("GET", "/designs", httpx.Response(200, text="oops"))
    with pytest.raises(APIError) as info:
        client.designs.list()
    assert info.value.code == "INVALID_RESPONSE"


# ---------------------------------------------------------------- retries


def rate_limited(retry_after: str | None = "2", detail_retry: int | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    detail = {"retryAfter": detail_retry} if detail_retry is not None else {}
    return json_response(429, {"error": "RATE_LIMITED", "message": "Slow down", "detail": detail}, headers)


def test_429_on_post_render_is_retried_after_retry_after(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("POST", "/render", rate_limited("3"), json_response(200, {"data": RESULT}))
    assert client.render("tpl_1")["data"] == RESULT
    assert fake_time.sleeps == [3.0]
    assert len(api.requests) == 2


def test_429_uses_detail_retry_after_without_header(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("GET", "/designs", rate_limited(None, 7), json_response(200, PAGE))
    client.designs.list()
    assert fake_time.sleeps == [7.0]


def test_429_without_hint_uses_backoff(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("GET", "/designs", rate_limited(None), rate_limited(None), json_response(200, PAGE))
    client.designs.list()
    assert fake_time.sleeps == [0.5, 1.0]


def test_429_http_date_retry_after(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    when = email.utils.formatdate(time.time() + 5, usegmt=True)
    api.add("GET", "/designs", rate_limited(when), json_response(200, PAGE))
    client.designs.list()
    assert 3.0 <= fake_time.sleeps[0] <= 5.0


def test_too_long_retry_after_raises(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("GET", "/designs", rate_limited("120"))
    with pytest.raises(RateLimitError) as info:
        client.designs.list()
    assert info.value.retry_after == 120.0
    assert fake_time.sleeps == []


def test_429_gives_up_after_max_retries(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("POST", "/render", rate_limited("1"))
    with pytest.raises(RateLimitError):
        client.render("tpl_1")
    assert len(api.requests) == 3  # 1 + max_retries (2)
    assert fake_time.sleeps == [1.0, 1.0]


def test_503_on_get_is_retried(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    unavailable = json_response(503, {"error": "INTERNAL", "message": "busy"})
    api.add("GET", "/designs", unavailable, unavailable, json_response(200, PAGE))
    assert client.designs.list() == PAGE
    assert fake_time.sleeps == [0.5, 1.0]


def test_503_on_post_render_is_not_retried(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("POST", "/render", json_response(503, {"error": "INTERNAL", "message": "busy"}), json_response(200, {"data": RESULT}))
    with pytest.raises(InternalServerError):
        client.render("tpl_1")
    assert len(api.requests) == 1
    assert fake_time.sleeps == []


@pytest.mark.parametrize(
    "path, call",
    [
        ("/social/posts", lambda c: c.social.posts.create(renderId="r", accounts=["a"])),
        ("/signed-urls/rotate-key", lambda c: c.signed_urls.rotate_key()),
        ("/webhooks/subscriptions", lambda c: c.webhooks.subscriptions.create("https://x.test", ["design.created"])),
    ],
)
def test_500_on_non_idempotent_posts_is_not_retried(api: MockAPI, client: Client, path: str, call: object) -> None:
    api.add("POST", path, json_response(500, {"error": "INTERNAL", "message": "boom"}))
    with pytest.raises(InternalServerError):
        call(client)  # type: ignore[operator]
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    "path, call",
    [
        ("/auth", lambda c: c.whoami()),
        ("/signed-urls", lambda c: c.signed_urls.create("tpl_1")),
    ],
)
def test_500_on_idempotent_posts_is_retried(api: MockAPI, client: Client, path: str, call: object) -> None:
    api.add("POST", path, json_response(500, {"error": "INTERNAL", "message": "boom"}), json_response(200, {"username": "x", "data": {}}))
    call(client)  # type: ignore[operator]
    assert len(api.requests) == 2


def test_connection_error_on_get_is_retried(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("GET", "/designs", httpx.ConnectError("refused"), json_response(200, PAGE))
    assert client.designs.list() == PAGE
    assert fake_time.sleeps == [0.5]


def test_connection_error_on_post_render_is_raised(api: MockAPI, client: Client) -> None:
    api.add("POST", "/render", httpx.ReadError("reset"))
    with pytest.raises(APIConnectionError) as info:
        client.render("tpl_1")
    assert not isinstance(info.value, APITimeoutError)
    assert len(api.requests) == 1


def test_timeout_on_get_is_retried_then_raised(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("GET", "/designs", httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        client.designs.list()
    assert len(api.requests) == 3
    assert fake_time.sleeps == [0.5, 1.0]


def test_max_retries_zero(api: MockAPI, fake_time: FakeTime) -> None:
    client = make_client(api, fake_time, max_retries=0)
    api.add("GET", "/designs", json_response(503, {"error": "INTERNAL", "message": "busy"}))
    with pytest.raises(InternalServerError):
        client.designs.list()
    assert len(api.requests) == 1


def test_jitter_range(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    client._random = lambda: 0.0
    unavailable = json_response(503, {"error": "INTERNAL", "message": "busy"})
    api.add("GET", "/designs", unavailable, json_response(200, PAGE))
    client.designs.list()
    assert fake_time.sleeps == [0.25]  # 0.5 s * (0.5 + 0/2)


def test_parse_retry_after() -> None:
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after("0.5") == 0.5
    assert parse_retry_after("-3") == 0.0
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412470.0) == pytest.approx(10.0)
