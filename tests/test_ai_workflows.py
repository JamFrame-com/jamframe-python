from __future__ import annotations

import asyncio
import datetime
import io
from pathlib import Path
from typing import Any, Dict

import pytest

from jamframe import Client, ConflictError, DesignJobFailedError, SDKError, WaitTimeoutError

from .conftest import RESULT, FakeTime, MockAPI, json_response, make_async_client

DETAIL = {
    "id": "tpl_9",
    "name": "Summer sale",
    "orientation": "portrait",
    "pages": 1,
    "animated": False,
    "createdAt": "2026-11-19T10:00:00.000Z",
    "updatedAt": "2026-11-19T10:00:00.000Z",
    "parameters": [],
    "sizes": [],
}


def design_job(status: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {"jobId": "dj_1", "status": status, "createdAt": "2026-11-19T10:00:00.000Z"}
    if status == "succeeded":
        body["design"] = DETAIL
    body.update(extra)
    return body


def run(status: str = "queued", **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "id": "run_1",
        "workflowId": "wf_1",
        "trigger": "api",
        "status": status,
        "rowsTotal": 0,
        "rowsSucceeded": 0,
        "rowsFailed": 0,
        "rowsSkipped": 0,
        "credits": 0,
        "warnings": [],
        "createdAt": "2026-11-19T10:00:00.000Z",
    }
    body.update(extra)
    return body


# ---------------------------------------------------------------- render options


def test_render_sends_the_new_options(client: Client, api: MockAPI) -> None:
    api.add("POST", "/render", json_response(200, {"data": RESULT}))
    client.render(
        "tool-quote-card-paper",
        response={"format": "pdf", "cmyk": {"profile": "gracol2013"}, "compress": True},
        expires_in=3600,
        translate=True,
        lang="es",
        ai={"onError": "fallback"},
    )
    assert api.body() == {
        "templateId": "tool-quote-card-paper",
        "response": {"format": "pdf", "cmyk": {"profile": "gracol2013"}, "compress": True},
        "expiresIn": 3600,
        "translate": True,
        "lang": "es",
        "ai": {"onError": "fallback"},
    }


# ---------------------------------------------------------------- AI designs


def test_generate_returns_the_design_on_201(client: Client, api: MockAPI) -> None:
    api.add("POST", "/designs/generate", json_response(201, {"data": design_job("succeeded", creditsCharged=15)}))
    job = client.designs.generate("A summer sale post", size="instagram-portrait", brand={"colors": ["#0b1f4d"]})
    assert job["design"]["id"] == "tpl_9"
    assert api.body() == {"prompt": "A summer sale post", "size": "instagram-portrait", "brand": {"colors": ["#0b1f4d"]}}


def test_generate_polls_on_202(client: Client, api: MockAPI, fake_time: FakeTime) -> None:
    api.add("POST", "/designs/generate", json_response(202, {"data": {"jobId": "dj_1", "status": "running"}}))
    api.add(
        "GET",
        "/designs/generate/dj_1",
        json_response(200, {"data": design_job("running")}),
        json_response(200, {"data": design_job("succeeded")}),
    )
    job = client.designs.generate("p")
    assert job["status"] == "succeeded"
    assert fake_time.sleeps == [1.0]


def test_generate_failure_raises(client: Client, api: MockAPI) -> None:
    failed = design_job("failed", error={"error": "AI_BAD_OUTPUT", "message": "No design"})
    api.add("POST", "/designs/generate", json_response(202, {"data": {"jobId": "dj_1", "status": "running"}}))
    api.add("GET", "/designs/generate/dj_1", json_response(200, {"data": failed}))
    with pytest.raises(DesignJobFailedError) as info:
        client.designs.generate("p")
    assert info.value.code == "AI_BAD_OUTPUT"
    assert info.value.message == "No design"


def test_generate_async_sends_async_and_webhook(client: Client, api: MockAPI) -> None:
    api.add("POST", "/designs/generate", json_response(202, {"data": {"jobId": "dj_9", "status": "running"}}))
    accepted = client.designs.generate_async("p", webhook_url="https://example.com/hook")
    assert accepted == {"jobId": "dj_9", "status": "running"}
    assert api.body() == {"prompt": "p", "webhookUrl": "https://example.com/hook", "async": True}


def test_design_wait_times_out(client: Client, api: MockAPI) -> None:
    api.add("GET", "/designs/generate/dj_1", json_response(200, {"data": design_job("running")}))
    with pytest.raises(WaitTimeoutError) as info:
        client.design_jobs.wait("dj_1", timeout=3)
    assert str(info.value).startswith("Design generation dj_1 was still running after 3 s")


def test_generate_needs_a_prompt(client: Client) -> None:
    with pytest.raises(SDKError):
        client.designs.generate("  ")


# ---------------------------------------------------------------- workflows


def test_workflow_crud(client: Client, api: MockAPI) -> None:
    wf = {"id": "wf_1", "name": "Quote cards"}
    api.add("GET", "/workflows", json_response(200, {"workflows": [wf]}))
    api.add("GET", "/workflows/wf_1", json_response(200, {"workflow": wf}))
    api.add("POST", "/workflows", json_response(201, {"workflow": wf}))
    api.add("PATCH", "/workflows/wf_1", json_response(200, {"workflow": dict(wf, status="active")}))
    api.add("DELETE", "/workflows/wf_1", json_response(204, None))

    assert client.workflows.list() == [wf]
    assert client.workflows.get("wf_1") == wf
    client.workflows.create(starter="csv-quotes-webhook")
    assert api.body() == {"starter": "csv-quotes-webhook"}
    assert client.workflows.update("wf_1", {"description": None}, status="active")["status"] == "active"
    assert api.body() == {"description": None, "status": "active"}
    client.workflows.delete("wf_1")
    assert api.last.method == "DELETE"


def test_run_and_wait_polls_until_the_run_ends(client: Client, api: MockAPI) -> None:
    api.add("POST", "/workflows/wf_1/run", json_response(202, {"run": run()}))
    api.add(
        "GET",
        "/workflows/wf_1/runs/run_1",
        json_response(200, {"run": dict(run("running"), rows=[])}),
        json_response(200, {"run": dict(run("partial", rowsFailed=1), rows=[])}),
    )
    done = client.workflows.run_and_wait("wf_1", max_rows=2, payload=[{"Quote": "Hi"}])
    assert done["status"] == "partial"
    assert api.body(0) == {"payload": [{"Quote": "Hi"}], "maxRows": 2}


def test_runs_paging_cancel_and_webhook_url(client: Client, api: MockAPI) -> None:
    api.add(
        "GET",
        "/workflows/wf_1/runs",
        json_response(200, {"runs": [run()], "pagination": {"nextCursor": "c2", "hasMore": True}}),
        json_response(200, {"runs": [run(id="run_2")], "pagination": {"nextCursor": None, "hasMore": False}}),
    )
    assert [r["id"] for r in client.workflows.runs.iter("wf_1", limit=1)] == ["run_1", "run_2"]
    assert api.last.url.params["cursor"] == "c2"

    api.add("POST", "/workflows/wf_1/runs/run_1/cancel", json_response(409, {"error": "CONFLICT", "message": "Finished"}))
    with pytest.raises(ConflictError):
        client.workflows.runs.cancel("wf_1", "run_1")

    api.add("POST", "/workflows/wf_1/webhook-token", json_response(200, {"webhookUrl": "https://api.example.com/h/new"}))
    assert client.workflows.rotate_webhook_url("wf_1") == "https://api.example.com/h/new"


# ---------------------------------------------------------------- social analytics


def test_analytics_query(client: Client, api: MockAPI) -> None:
    api.add("GET", "/social/analytics/overview", json_response(200, {"postsPublished": 3}))
    client.social.analytics.overview(
        from_=datetime.date(2026, 11, 1), to="2026-11-19", platform=["instagram", "x"], account_id="acc_1"
    )
    params = api.last.url.params
    assert params["from"] == "2026-11-01"
    assert params["to"] == "2026-11-19"
    assert params["platform"] == "instagram,x"
    assert params["accountId"] == "acc_1"

    api.add("GET", "/social/analytics/daily", json_response(200, {"days": []}))
    api.add("GET", "/social/analytics/insights", json_response(200, {"topPosts": []}))
    assert client.social.analytics.daily() == {"days": []}
    assert "from" not in api.last.url.params
    assert client.social.analytics.insights() == {"topPosts": []}


# ---------------------------------------------------------------- AI calls


def test_ai_status(client: Client, api: MockAPI) -> None:
    api.add("GET", "/ai/status", json_response(200, {"status": "live"}))
    assert client.ai.status()["status"] == "live"


def test_remove_background_from_a_url_sends_json(client: Client, api: MockAPI) -> None:
    api.add("POST", "/images/remove-background", json_response(200, {"url": "https://cdn.example.com/cut.png"}))
    client.ai.remove_background(url="https://example.com/shot.jpg", name="Product")
    assert api.body() == {"url": "https://example.com/shot.jpg", "name": "Product"}


def test_remove_background_uploads_bytes_paths_and_files(client: Client, api: MockAPI, tmp_path: Path) -> None:
    api.add("POST", "/images/remove-background", json_response(200, {"url": "https://cdn.example.com/cut.png"}))

    client.ai.remove_background(file=b"PNGDATA", filename="shot.png", name="Product")
    request = api.last
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    content = request.content
    assert b'name="file"; filename="shot.png"' in content
    assert b"PNGDATA" in content
    assert b'name="name"\r\n\r\nProduct' in content

    path = tmp_path / "photo.jpg"
    path.write_bytes(b"JPGDATA")
    client.ai.remove_background(file=path)
    assert b'filename="photo.jpg"' in api.last.content

    client.ai.remove_background(file=io.BytesIO(b"RAW"))
    assert b'filename="upload"' in api.last.content and b"RAW" in api.last.content


def test_remove_background_needs_one_source(client: Client) -> None:
    with pytest.raises(SDKError):
        client.ai.remove_background()
    with pytest.raises(SDKError):
        client.ai.remove_background(url="https://x", file=b"y")
    with pytest.raises(SDKError):
        client.ai.remove_background(file=io.StringIO("text"))  # type: ignore[arg-type]


def test_transcribe_with_a_url_or_a_file(client: Client, api: MockAPI) -> None:
    api.add("POST", "/ai/transcribe", json_response(200, {"text": "Hello"}))
    client.ai.transcribe(url="https://example.com/a.mp3", style="short", offset=3, words=True, max_lines=1)
    assert api.body() == {"url": "https://example.com/a.mp3", "style": "short", "offset": 3, "words": True, "maxLines": 1}

    client.ai.transcribe(file=b"MP3DATA", filename="a.mp3", offset=3, words=True)
    content = api.last.content
    assert b'filename="a.mp3"' in content
    assert b'name="offset"\r\n\r\n3' in content
    assert b'name="words"\r\n\r\ntrue' in content
    assert b'name="language"' not in content


# ---------------------------------------------------------------- async


def test_async_generate_and_run_and_upload(api: MockAPI, fake_time: FakeTime) -> None:
    api.add("POST", "/designs/generate", json_response(202, {"data": {"jobId": "dj_1", "status": "running"}}))
    api.add("GET", "/designs/generate/dj_1", json_response(200, {"data": design_job("succeeded")}))
    api.add("POST", "/workflows/wf_1/run", json_response(202, {"run": run()}))
    api.add("GET", "/workflows/wf_1/runs/run_1", json_response(200, {"run": dict(run("succeeded"), rows=[])}))
    api.add("POST", "/ai/transcribe", json_response(200, {"text": "Hello"}))

    async def go() -> Any:
        async with make_async_client(api, fake_time) as client:
            job = await client.designs.generate("p")
            done = await client.workflows.run_and_wait("wf_1")
            text = await client.ai.transcribe(file=b"MP3DATA", filename="a.mp3")
            return job, done, text

    job, done, text = asyncio.run(go())
    assert job["design"]["id"] == "tpl_9"
    assert done["status"] == "succeeded"
    assert text == {"text": "Hello"}
    assert b'filename="a.mp3"' in api.last.content
