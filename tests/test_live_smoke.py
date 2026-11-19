"""Live smoke test against a running API. Skipped unless JAMFRAME_SMOKE_API_KEY is set.

    JAMFRAME_SMOKE_API_KEY=jf_live_... \\
    JAMFRAME_SMOKE_BASE_URL=http://localhost:3100 \\
    pytest tests/test_live_smoke.py -s

Spends 2 credits (one sync PNG, one async PNG of a library template). Posts
to a social account only when the workspace has an active *sandbox* account
(simulated posting); otherwise that test is skipped. Creates one webhook
subscription and deletes it.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Iterator

import pytest

from jamframe import AsyncClient, Client, file_urls
from jamframe._brand import SMOKE_API_KEY_ENV, SMOKE_BASE_URL_ENV
from jamframe.types import DesignDetail

API_KEY = os.environ.get(SMOKE_API_KEY_ENV, "")
BASE_URL = os.environ.get(SMOKE_BASE_URL_ENV, "http://localhost:3100")

pytestmark = pytest.mark.skipif(not API_KEY, reason=f"set {SMOKE_API_KEY_ENV} to run the live smoke test")

STATIC_TYPES = {"text", "image", "qr", "shape", "variable"}


@pytest.fixture(scope="module")
def client() -> Iterator[Client]:
    with Client(API_KEY, base_url=BASE_URL, max_retries=1) as c:
        yield c


@pytest.fixture(scope="module")
def template(client: Client) -> DesignDetail:
    """A one-page, still library template (no video, audio or subtitles)."""
    checked = 0
    for design in client.designs.iter(source="library", limit=100):
        if design["animated"] or design["pages"] != 1:
            continue
        detail = client.designs.get(design["id"])
        checked += 1
        if all(p["type"] in STATIC_TYPES for p in detail["parameters"]):
            print(f"\nlibrary template: {detail['name']} ({detail['id']}), {len(detail['parameters'])} parameters")
            return detail
        if checked >= 15:
            break
    pytest.skip("no one-page still template in the library")


@pytest.fixture(scope="module")
def rendered(client: Client, template: DesignDetail) -> Dict[str, Any]:
    text = next((p["key"] for p in template["parameters"] if p["type"] in ("text", "variable")), None)
    modifications = {text: "Python SDK smoke test"} if text else {}
    result = client.render(template["id"], modifications, response={"format": "png"})
    return dict(result)


def test_whoami_and_designs(client: Client) -> None:
    me = client.whoami()
    assert me["username"]
    page = client.designs.list(limit=2)
    assert len(page["designs"]) <= 2
    assert set(page["pagination"]) == {"nextCursor", "hasMore"}
    library = list(client.designs.iter(source="library", limit=50))
    assert library, "the template library is empty"
    assert len({d["id"] for d in library}) == len(library)
    print(f"\nworkspace {me.get('workspaceId')}: {len(library)} library templates")


def test_render_png(rendered: Dict[str, Any]) -> None:
    data = rendered["data"]
    assert data["format"] == "png" and data["credits"] == 1
    urls = file_urls(data["content"])
    assert len(urls) == 1 and urls[0].startswith("http")
    print(f"\nsync render {data['renderId']}: {urls[0]}")


def test_render_and_wait(client: Client, template: DesignDetail) -> None:
    started = time.monotonic()
    job = client.render_and_wait(template["id"], response={"format": "png"}, timeout=120)
    assert job["status"] == "succeeded" and job["result"] is not None
    assert file_urls(job["result"]["content"])[0].startswith("http")
    print(f"\nasync render job {job['id']}: succeeded in {time.monotonic() - started:.1f} s")


def test_renders_list_finds_the_render(client: Client, rendered: Dict[str, Any]) -> None:
    render_id = rendered["data"]["renderId"]
    page = client.renders.list(limit=10)
    assert "totals" in page
    assert render_id in [r["id"] for r in page["renders"]]
    one = client.renders.get(render_id)
    assert one["id"] == render_id and one["status"] == "succeeded"


def test_designs_name_search(client: Client, template: DesignDetail) -> None:
    found = client.designs.list(source="library", q=template["name"].upper(), limit=100)
    assert template["id"] in [d["id"] for d in found["designs"]]


def test_signed_url(client: Client, template: DesignDetail) -> None:
    signed = client.signed_urls.create(template["id"], response={"format": "png"}, expires_in=600)
    assert "/r/s/" in signed["data"]["url"]


def test_social_post_to_sandbox(client: Client, rendered: Dict[str, Any]) -> None:
    accounts = [a for a in client.social.accounts.list() if a["sandbox"] and a["status"] == "active"]
    if not accounts:
        pytest.skip("no active sandbox social account in this workspace")
    created = client.social.posts.create(
        render_id=rendered["data"]["renderId"],
        accounts=[accounts[0]["id"]],
        caption="Python SDK smoke test",
    )
    post = created["post"]
    deadline = time.monotonic() + 60
    while post["status"] not in ("published", "partial", "failed", "canceled") and time.monotonic() < deadline:
        time.sleep(2)
        post = client.social.posts.get(post["id"])
    print(f"\nsocial post {post['id']}: {post['status']}")
    assert post["status"] == "published"


def test_ai_status_workflows_and_analytics(client: Client) -> None:
    """Free reads."""
    ai = client.ai.status()
    assert ai["status"] in ("live", "mock", "off")
    assert isinstance(ai["credits"]["used"], int)
    workflows = client.workflows.list()
    if workflows:
        client.workflows.runs.list(workflows[0]["id"], limit=1)
    overview = client.social.analytics.overview()
    assert overview["range"]["days"] > 0
    daily = client.social.analytics.daily(platform=["instagram", "linkedin"])
    assert len(daily["days"]) == daily["range"]["days"]
    client.social.analytics.insights()


def test_webhook_subscription_round_trip(client: Client) -> None:
    created = client.webhooks.subscriptions.create("https://example.com/jamframe-sdk-smoke", ["design.deleted"])
    sub_id = created["subscription"]["id"]
    try:
        assert created["signingSecret"]
        assert client.webhooks.subscriptions.get(sub_id)["events"] == ["design.deleted"]
    finally:
        client.webhooks.subscriptions.delete(sub_id)


def test_async_client() -> None:
    async def go() -> None:
        async with AsyncClient(API_KEY, base_url=BASE_URL) as client:
            me = await client.whoami()
            page = await client.designs.list(source="library", limit=3)
            assert me["username"] and len(page["designs"]) <= 3

    asyncio.run(go())
