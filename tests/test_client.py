from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest

import jamframe
from jamframe import Client, NotFoundError, RenderJobFailedError, SDKError, WaitTimeoutError, file_urls
from jamframe._base import normalize_base_url

from .conftest import (
    BASE,
    DESIGN,
    KEY,
    POST,
    RESULT,
    SUBSCRIPTION,
    FakeTime,
    MockAPI,
    job,
    json_response,
    make_client,
)

PAGE_END = {"nextCursor": None, "hasMore": False}

# ---------------------------------------------------------------- construction


def test_version_matches_pyproject() -> None:
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', text, re.M)
    assert match and match.group(1) == jamframe.__version__


def test_brand_aliases() -> None:
    assert jamframe.JamFrame is jamframe.Client
    assert jamframe.AsyncJamFrame is jamframe.AsyncClient
    assert jamframe.PRODUCT_NAME == "JamFrame"


@pytest.mark.parametrize(
    "given, expected",
    [
        ("https://api.example.com", "https://api.example.com/v1"),
        ("https://api.example.com/", "https://api.example.com/v1"),
        ("https://api.example.com/v1", "https://api.example.com/v1"),
        ("https://api.example.com/v1/", "https://api.example.com/v1"),
        ("http://localhost:3100", "http://localhost:3100/v1"),
        # The older address keeps working as given.
        ("https://app.example.com/api/v1", "https://app.example.com/api/v1"),
        ("https://app.example.com/api/v1/", "https://app.example.com/api/v1"),
    ],
)
def test_base_url_normalisation(given: str, expected: str) -> None:
    assert normalize_base_url(given) == expected


def test_bad_base_url() -> None:
    with pytest.raises(SDKError):
        Client("k", base_url="app.example.com")


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAMFRAME_API_KEY", "jf_live_env")
    monkeypatch.setenv("JAMFRAME_BASE_URL", "http://localhost:3100")
    with Client() as client:
        assert client.api_key == "jf_live_env"
        assert client.base_url == "http://localhost:3100/v1"
        assert "jf_live_env" not in repr(client)


def test_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAMFRAME_BASE_URL", raising=False)
    with Client("k") as client:
        assert client.base_url == "https://api.jamframe.com/v1"


def test_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAMFRAME_API_KEY", raising=False)
    with pytest.raises(SDKError, match="JAMFRAME_API_KEY"):
        Client()


def test_headers(api: MockAPI, client: Client) -> None:
    api.add("POST", "/auth", json_response(200, {"workspaceId": "ws_1", "username": "Jane"}))
    assert client.whoami() == {"workspaceId": "ws_1", "username": "Jane"}
    request = api.last
    assert str(request.url) == f"{BASE}/v1/auth"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["accept"] == "application/json"
    assert request.headers["user-agent"] == f"jamframe-python/{jamframe.__version__}"


def test_custom_user_agent(api: MockAPI, fake_time: FakeTime) -> None:
    client = make_client(api, fake_time, user_agent="my-app/2.0")
    api.add("POST", "/auth", json_response(200, {"username": "Jane"}))
    client.whoami()
    assert api.last.headers["user-agent"] == f"my-app/2.0 jamframe-python/{jamframe.__version__}"


def test_own_http_client_not_closed(api: MockAPI, fake_time: FakeTime) -> None:
    http = httpx.Client(transport=httpx.MockTransport(api.handler))
    with Client("k", base_url=BASE, http_client=http):
        pass
    assert not http.is_closed


# ---------------------------------------------------------------- render


def test_render_sync(api: MockAPI, client: Client) -> None:
    api.add("POST", "/render", json_response(200, {"data": RESULT, "warnings": ["Unknown key: titel"]}))
    out = client.render("tpl_1", {"title": "Hi", "page2@title.color": "#fff"}, response={"format": "png", "scale": 2})
    assert out == {"data": RESULT, "warnings": ["Unknown key: titel"]}
    assert api.body() == {
        "templateId": "tpl_1",
        "modifications": {"title": "Hi", "page2@title.color": "#fff"},
        "response": {"format": "png", "scale": 2},
    }
    assert api.last.headers["content-type"] == "application/json"


def test_render_without_warnings_gives_empty_list(api: MockAPI, client: Client) -> None:
    api.add("POST", "/render", json_response(200, {"data": RESULT}))
    assert client.render("tpl_1")["warnings"] == []
    assert api.body() == {"templateId": "tpl_1"}


def test_render_waits_for_a_202(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("POST", "/render", json_response(202, {"data": {"jobId": "job_1", "status": "running"}}))
    api.add(
        "GET",
        "/render-jobs/job_1",
        json_response(200, {"job": job("running")}),
        json_response(200, {"job": job("succeeded", warnings=["w"])}),
    )
    out = client.render("tpl_1", response={"format": "mp4"})
    assert out["data"]["content"] == "https://cdn.example.com/r/1.mp4"
    assert out["data"].get("duration") == 13
    assert out["warnings"] == ["w"]
    assert "async" not in api.body(0)
    assert fake_time.sleeps == [1.0]


def test_render_async(api: MockAPI, client: Client) -> None:
    api.add("POST", "/render", json_response(202, {"data": {"jobId": "job_1", "status": "queued"}}))
    accepted = client.render_async("tpl_1", {"title": "Hi"}, response={"format": "mp4"}, webhook_url="https://x.test/cb")
    assert accepted == {"jobId": "job_1", "status": "queued"}
    assert api.body() == {
        "templateId": "tpl_1",
        "modifications": {"title": "Hi"},
        "response": {"format": "mp4"},
        "async": True,
        "webhookUrl": "https://x.test/cb",
    }


def test_render_and_wait_polls_with_backoff(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("POST", "/render", json_response(202, {"data": {"jobId": "job_1", "status": "queued"}}))
    api.add(
        "GET",
        "/render-jobs/job_1",
        *[json_response(200, {"job": job("running")}) for _ in range(6)],
        json_response(200, {"job": job("succeeded")}),
    )
    done = client.render_and_wait("tpl_1", response={"format": "png"})
    assert done["status"] == "succeeded"
    assert done["result"] is not None and done["result"]["renderId"] == "rnd_1"
    assert fake_time.sleeps == [1.0, 1.5, 2.25, 3.375, 5.0, 5.0]


def test_render_and_wait_failed(api: MockAPI, client: Client) -> None:
    api.add("POST", "/render", json_response(202, {"data": {"jobId": "job_1", "status": "queued"}}))
    api.add("GET", "/render-jobs/job_1", json_response(200, {"job": job("failed")}))
    with pytest.raises(RenderJobFailedError) as info:
        client.render_and_wait("tpl_1")
    assert info.value.code == "VALIDATION_ERROR"
    assert info.value.message == "Video failed to load"
    assert info.value.job_id == "job_1"
    assert info.value.job["status"] == "failed"


def test_wait_timeout(api: MockAPI, client: Client, fake_time: FakeTime) -> None:
    api.add("GET", "/render-jobs/job_1", json_response(200, {"job": job("running")}))
    with pytest.raises(WaitTimeoutError) as info:
        client.render_jobs.wait("job_1", timeout=10, poll_interval=2)
    assert info.value.job_id == "job_1"
    assert info.value.last_job is not None and info.value.last_job["status"] == "running"
    assert sum(fake_time.sleeps) == pytest.approx(10)
    assert "still running" in str(info.value)


def test_render_job_get_escapes_id(api: MockAPI, client: Client) -> None:
    api.add("GET", "/render-jobs/a/b", json_response(200, {"job": job("queued")}))  # url.path is decoded
    assert client.render_jobs.get("a/b")["status"] == "queued"
    assert api.last.url.raw_path == b"/v1/render-jobs/a%2Fb"


def test_empty_id_refused(client: Client) -> None:
    with pytest.raises(SDKError):
        client.designs.get("")
    with pytest.raises(SDKError):
        client.render("")


def test_file_urls() -> None:
    assert file_urls("https://a/1.png") == ["https://a/1.png"]
    assert file_urls(["https://a/1.png", "https://a/2.png"]) == ["https://a/1.png", "https://a/2.png"]
    sized = [
        {"size": "original", "name": "Original", "width": 1, "height": 1, "pages": [1], "url": "https://a/o.png"},
        {"size": "story", "name": "Story", "width": 1, "height": 2, "pages": [1, 2], "urls": ["https://a/s1.png", "https://a/s2.png"]},
    ]
    assert file_urls(sized) == ["https://a/o.png", "https://a/s1.png", "https://a/s2.png"]


# ---------------------------------------------------------------- designs and pagination


def test_designs_list_query(api: MockAPI, client: Client) -> None:
    api.add("GET", "/designs", json_response(200, {"designs": [dict(DESIGN, animated=True)], "pagination": PAGE_END}))
    page = client.designs.list(source="library", q="promo reel", limit=10, cursor="c1")
    assert page["designs"][0]["id"] == "tpl_1"
    assert page["designs"][0]["pages"] == 2
    assert page["designs"][0]["animated"] is True
    assert dict(api.last.url.params) == {"source": "library", "q": "promo reel", "limit": "10", "cursor": "c1"}
    client.designs.list()
    assert dict(api.last.url.params) == {"source": "workspace"}


def test_designs_iter_follows_cursors(api: MockAPI, client: Client) -> None:
    def page(ids: list[str], cursor: Any) -> httpx.Response:
        return json_response(
            200,
            {"designs": [dict(DESIGN, id=i) for i in ids], "pagination": {"nextCursor": cursor, "hasMore": cursor is not None}},
        )

    api.add("GET", "/designs", page(["a", "b"], "b"), page(["c", "d"], "d"), page(["e"], None))
    assert [d["id"] for d in client.designs.iter(q="coffee", limit=2)] == ["a", "b", "c", "d", "e"]
    cursors = [r.url.params.get("cursor") for r in api.requests]
    assert cursors == [None, "b", "d"]
    assert all(r.url.params["source"] == "workspace" for r in api.requests)
    assert all(r.url.params["q"] == "coffee" for r in api.requests)


def test_designs_get(api: MockAPI, client: Client) -> None:
    detail = dict(DESIGN, pages=3, parameters=[{"key": "title", "type": "text", "pages": [1], "example": "x"}], sizes=[])
    api.add("GET", "/designs/tpl_1", json_response(200, {"design": detail}))
    assert client.designs.get("tpl_1")["pages"] == 3


# ---------------------------------------------------------------- renders log


def test_renders_list_and_iter(api: MockAPI, client: Client) -> None:
    item = {"id": "rnd_1", "outputs": []}
    totals = {"creditsUsed": 1}
    api.add(
        "GET",
        "/renders",
        json_response(200, {"renders": [item], "pagination": {"nextCursor": "rnd_1", "hasMore": True}, "totals": totals}),
        json_response(200, {"renders": [dict(item, id="rnd_0")], "pagination": PAGE_END, "totals": totals}),
    )
    page = client.renders.list(status="failed", design_id="tpl_1", limit=1)
    assert page["totals"] == totals
    assert dict(api.last.url.params) == {"status": "failed", "designId": "tpl_1", "limit": "1"}
    assert [r["id"] for r in client.renders.iter(design_id="tpl_1")] == ["rnd_0"]


def test_renders_get(api: MockAPI, client: Client) -> None:
    item = {"id": "rnd/1", "status": "succeeded", "outputs": [{"url": "https://cdn/r/1.png", "page": 1}]}
    api.add("GET", "/renders/rnd/1", json_response(200, {"render": item}))  # url.path is decoded
    assert client.renders.get("rnd/1") == item
    assert api.last.url.raw_path == b"/v1/renders/rnd%2F1"
    api.add("GET", "/renders/nope", json_response(404, {"error": "NOT_FOUND", "message": "Render nope not found in this workspace"}))
    with pytest.raises(NotFoundError):
        client.renders.get("nope")
    with pytest.raises(SDKError):
        client.renders.get("")


# ---------------------------------------------------------------- signed URLs


def test_signed_urls(api: MockAPI, client: Client) -> None:
    signed = {"data": {"url": "https://app/r/s/x.png", "templateId": "tpl_1", "format": "png", "expiresAt": "2026"}}
    api.add("POST", "/signed-urls", json_response(200, signed))
    api.add("POST", "/signed-urls/rotate-key", json_response(200, {"data": {"rotatedAt": "2026-11-09T15:20:00.000Z"}}))
    assert client.signed_urls.create("tpl_1", {"title": "OG"}, response={"format": "png"}, expires_in=60, cache_seconds=0) == signed
    assert api.body() == {
        "templateId": "tpl_1",
        "modifications": {"title": "OG"},
        "response": {"format": "png"},
        "expiresIn": 60,
        "cacheSeconds": 0,
    }
    assert client.signed_urls.rotate_key() == {"rotatedAt": "2026-11-09T15:20:00.000Z"}


# ---------------------------------------------------------------- social


def test_social_accounts(api: MockAPI, client: Client) -> None:
    account = {"id": "acc_1", "platform": "linkedin", "sandbox": True}
    api.add("GET", "/social/accounts", json_response(200, {"accounts": [account]}))
    assert client.social.accounts.list() == [account]


def test_social_post_create_merges_kwargs(api: MockAPI, client: Client) -> None:
    api.add("POST", "/social/posts", json_response(201, {"post": POST, "checks": []}))
    created = client.social.posts.create(
        {"caption": "Hello", "accounts": ["acc_1"]},
        template_id="tpl_1",
        modifications={"title": "Hi"},
        scheduled_at="2026-11-10T09:00",
        timezone="Europe/Paris",
    )
    assert created == {"post": POST, "checks": []}
    assert api.body() == {
        "caption": "Hello",
        "accounts": ["acc_1"],
        "templateId": "tpl_1",
        "modifications": {"title": "Hi"},
        "scheduledAt": "2026-11-10T09:00",
        "timezone": "Europe/Paris",
    }


def test_social_posts_read_cancel_retry(api: MockAPI, client: Client) -> None:
    api.add("GET", "/social/posts", json_response(200, {"posts": [POST], "pagination": PAGE_END}))
    api.add("GET", "/social/posts/post_1", json_response(200, {"post": POST}))
    api.add("POST", "/social/posts/post_1/cancel", json_response(200, {"post": dict(POST, status="canceled")}))
    api.add("POST", "/social/posts/post_1/retry", json_response(200, {"post": dict(POST, status="queued")}))
    assert client.social.posts.list(status="queue")["posts"] == [POST]
    assert dict(api.last.url.params) == {"status": "queue"}
    assert [p["id"] for p in client.social.posts.iter()] == ["post_1"]
    assert client.social.posts.get("post_1") == POST
    assert client.social.posts.cancel("post_1")["status"] == "canceled"
    assert client.social.posts.retry("post_1", target_id="tgt_1")["status"] == "queued"
    assert api.body() == {"targetId": "tgt_1"}
    client.social.posts.retry("post_1")
    assert api.body() == {}


# ---------------------------------------------------------------- webhook subscriptions


def test_webhook_subscriptions(api: MockAPI, client: Client) -> None:
    api.add("GET", "/webhooks/subscriptions", json_response(200, {"subscriptions": [SUBSCRIPTION], "pagination": PAGE_END}))
    api.add("POST", "/webhooks/subscriptions", json_response(201, {"subscription": SUBSCRIPTION, "signingSecret": "wh_sec_x"}))
    api.add("GET", "/webhooks/subscriptions/sub_1", json_response(200, {"subscription": SUBSCRIPTION}))
    api.add("DELETE", "/webhooks/subscriptions/sub_1", httpx.Response(204))
    api.add("POST", "/webhooks/subscriptions/sub_1/test", json_response(202, {"delivery": {"id": "d"}, "sample": {}}))
    api.add("POST", "/webhooks/subscriptions/sub_1/rotate-secret", json_response(200, {"subscription": SUBSCRIPTION, "signingSecret": "wh_sec_y"}))

    subs = client.webhooks.subscriptions
    assert subs.list(limit=5)["subscriptions"] == [SUBSCRIPTION]
    assert [s["id"] for s in subs.iter()] == ["sub_1"]
    created = subs.create("https://example.com/hook", ["render.completed", "render.failed"])
    assert created["signingSecret"] == "wh_sec_x"
    assert api.body() == {"url": "https://example.com/hook", "events": ["render.completed", "render.failed"]}
    assert subs.get("sub_1") == SUBSCRIPTION
    assert subs.delete("sub_1") is None
    assert subs.test("sub_1", "render.completed") == {"delivery": {"id": "d"}, "sample": {}}
    assert api.body() == {"eventType": "render.completed"}
    assert subs.rotate_secret("sub_1")["signingSecret"] == "wh_sec_y"
    assert api.calls()[-4:] == [
        "GET /v1/webhooks/subscriptions/sub_1",
        "DELETE /v1/webhooks/subscriptions/sub_1",
        "POST /v1/webhooks/subscriptions/sub_1/test",
        "POST /v1/webhooks/subscriptions/sub_1/rotate-secret",
    ]


def test_client_exposes_signature_helpers(client: Client) -> None:
    assert client.webhooks.verify_signature is jamframe.verify_signature
    assert client.webhooks.construct_event is jamframe.construct_event
