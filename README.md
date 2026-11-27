# jamframe (Python)

The Python client for the [JamFrame](https://jamframe.com) API: render your
templates to images, carousels, PDFs and MP4 videos, design new templates
with AI, run workflows, post to social accounts and read their analytics,
and verify webhooks.

- Python 3.9 or later. One dependency: [httpx](https://www.python-httpx.org/).
- Sync (`Client`) and async (`AsyncClient`) clients with the same methods.
- Fully typed (`py.typed`, `TypedDict` for every API shape, `mypy --strict` clean).
- Retries 429s (honouring `Retry-After`) and, for idempotent calls only,
  5xx answers, connection errors and timeouts.

API reference: [app.jamframe.com/docs/api](https://app.jamframe.com/docs/api)
(the OpenAPI spec is [app.jamframe.com/openapi.yaml](https://app.jamframe.com/openapi.yaml)).
The [Node.js SDK](https://github.com/JamFrame-com/jamframe-node) has the same surface.

## Install

```sh
pip install jamframe
```

## Quick start

Create an API key in the app under **Developers → API Keys** (it starts with
`jf_live_`), then:

```python
from jamframe import Client

client = Client(api_key="jf_live_...")  # or set JAMFRAME_API_KEY

result = client.render(
    "clx8m2k3p0001abcd1234efgh",            # a template id (yours or a library one)
    {"title": "Summer sale ends Sunday", "title.color": "#FF4D4F"},
    response={"format": "png", "scale": 2},
)
print(result["data"]["content"])        # the image URL
print(result["data"]["credits"])        # 1
print(result["warnings"])               # e.g. modification keys that matched nothing
```

`Client()` with no arguments reads `JAMFRAME_API_KEY` and, if set,
`JAMFRAME_BASE_URL`. `base_url` is the API's origin
(`https://api.jamframe.com`) or its root (`…/v1`); both work, and so does
the older `https://app.jamframe.com/api/v1`.

Use it as a context manager (or call `close()`) to release connections:

```python
with Client() as client:
    print(client.whoami())  # {'workspaceId': '…', 'username': 'Jane Doe'}
```

## Templates

```python
# Your workspace's templates, newest first (one page).
page = client.designs.list(limit=25)
page["designs"], page["pagination"]["nextCursor"]

# Every template, page after page.
for design in client.designs.iter():
    print(design["id"], design["name"], design["orientation"])

# The template library (ready-made templates any workspace can render).
for design in client.designs.iter(source="library"):
    ...

# A picker's search box: names containing the text, ignoring case (either source).
# Each design has its page count and `animated` (a video template: render it as mp4).
for design in client.designs.iter(source="library", q="reel"):
    print(design["name"], design["pages"], "video" if design["animated"] else "still")

# What a template takes: parameters (keys for `modifications`), page count, saved sizes.
detail = client.designs.get("clx8m2k3p0002abcd1234efgh")
for p in detail["parameters"]:
    print(p["key"], p["type"], p["pages"], p["example"])
```

Modification keys: `title` sets every layer (and `{{title}}` placeholder)
with that key, `page2@title` only on page 2, `title.color` overrides a style
property, `page2@title.color` both. See the API reference for the full list
(`fontSize`, `hidden`, `fill`, `audio`, `audio.volume`, `subtitles`, …).

### Design a template with AI

```python
# Waits for the design (usually 10–40 s); 15 to 30 AI credits, refunded if it fails.
job = client.designs.generate(
    "Announce our summer sale, 30% off everything this weekend. Bold and playful.",
    size="instagram-portrait",   # or "linkedin-carousel" with pages=5, or "1200x630"
    # brand=False, or {"colors": ["#0b1f4d", "#ff6b5b"], "fonts": ["Poppins"]}; default: your brand library
)
job["design"]["id"]          # render it like any other template
job["design"]["parameters"]  # [{'key': 'title', …}, {'key': 'photo', …}]
job["design"]["editorUrl"]   # open it in the editor

# Or start it and come back later (webhook_url= is called when it ends).
started = client.designs.generate_async("A teaser video for our app launch", video=True)
done = client.design_jobs.wait(started["jobId"])
```

A failed generation raises `DesignJobFailedError` (`.code`, `.message`, `.job`).

## Rendering

Credits: 1 per image or PDF page, 1 per second of video (rounded up),
charged only when a render succeeds.

```python
# A carousel: one URL per page.
r = client.render("tpl_id", {"title": "5 ways to brew", "page2@title": "1. Grind fresh"},
                  response={"format": "jpeg", "quality": 90})
r["data"]["content"]           # ['https://…-p1.jpg', 'https://…-p2.jpg', …]

# A library template by its slug, as a PDF of pages 1 and 2, with links that work for an hour.
client.render("tool-quote-card-paper", {"quote": "Stay curious"},
              response={"format": "pdf", "includePages": [1, 2]}, expires_in=3600)

# Smart Resize: one size …
client.render("tpl_id", {"title": "Hi"}, response={"format": "png", "size": "story"})
# … or several in one call (content is one entry per size).
r = client.render("tpl_id", {"title": "Hi"}, response={"sizes": ["original", "story", "1200x630"]})
for entry in r["data"]["content"]:
    print(entry["size"], entry["width"], entry["height"], entry.get("url") or entry.get("urls"))
r["data"]["urls"]              # every file's link in one list

# Translate every text layer with AI (AI credits; cached per text and language).
client.render("tpl_id", translate=True, lang="es", ai={"onError": "fallback"})
```

`jamframe.file_urls(content)` flattens any `content` (a URL, a list of URLs
or the per-size entries) into a list of URLs. File links work for
`expires_in` seconds (5 minutes by default, at most 7 days;
`data["expiresAt"]` says until when).

**Experimental, PDF only:** `response={"format": "pdf", "cmyk": True}` (or
`{"profile": "gracol2013"}`) converts the colours to CMYK for print, and
`"compress": True` (or `{"preset": "printer"}`) makes the file smaller.
Their options and output may still change.

### Videos and render jobs

MP4 renders, and any render sent as `async`, run as **render jobs**.

```python
# Simplest: render() waits. The API answers an MP4 within 90 s, or hands
# back a job, which render() then polls (up to wait_timeout seconds).
video = client.render("tpl_id", {"title": "New menu"}, response={"format": "mp4"})
video["data"]["content"], video["data"]["duration"]

# Queue it and get the job id at once …
job = client.render_async("tpl_id", {"title": "New menu"}, response={"format": "mp4"},
                          webhook_url="https://example.com/hooks/render-done")
job["jobId"]  # poll it, or wait for the webhook_url callback

# … poll it yourself …
client.render_jobs.get(job["jobId"])["progress"]  # 0 to 1

# … or wait for it (polls every 1 s, then 1.5x longer each time, at most 5 s).
done = client.render_jobs.wait(job["jobId"], timeout=600)
done["result"]["content"]

# Queue and wait in one call.
done = client.render_and_wait("tpl_id", {"title": "New menu"}, response={"format": "mp4"})
```

A failed job raises `RenderJobFailedError` (`.code`, `.message`, `.job`;
failed renders cost nothing). Running past the timeout raises
`WaitTimeoutError` (`.job_id`, `.last_job`); the job keeps running, so you
can wait again.

The `webhook_url` callback is not signed. Treat it as a hint and fetch the
job with `render_jobs.get()` before trusting it, or subscribe to the signed
`render.completed` / `render.failed` webhook events instead.

## Render log

```python
page = client.renders.list(status="failed", limit=10)
page["totals"]["creditsRemaining"]          # this billing period
for render in client.renders.iter(design_id="tpl_id"):
    print(render["id"], render["status"], [o["url"] for o in render["outputs"]])

# One render by id (the `renderId` of a render, or of a job's `result`).
render = client.renders.get(result["data"]["renderId"])
render["status"], render["credits"], render["outputs"]
render["filesExpireAt"]   # when the files are deleted (Data retention, 90 days by default)

# Delete the files now. The render stays in the log, with filesDeleted True.
client.renders.delete_files(render["id"])
```

## Signed URLs

A tamper-proof link that renders when opened, for `<img src>`, emails and
`og:image`. Creating it is free; opening it renders (and caches) the image.

```python
signed = client.signed_urls.create("tpl_id", {"title": "How we cut render times"},
                                   response={"format": "png"}, expires_in=30 * 24 * 3600)
signed["data"]["url"]

client.signed_urls.rotate_key()  # revoke every signed URL of the workspace
```

## Workflows

Workflows render a template for each row of a Google Sheet, a CSV or XLSX
file, an Airtable table or a webhook, and deliver the files. Build them in
the app (or with `workflows.create()`), then run them from code.

```python
workflows = client.workflows.list()

# Run one and wait for it to end; each row costs render credits.
run = client.workflows.run_and_wait(workflows[0]["id"], max_rows=2)   # a test run
run["status"]                             # 'succeeded', 'partial', 'failed' or 'canceled' (check it)
[row["urls"] for row in run["rows"]]      # each row's files

# A webhook-payload source: send the rows yourself.
client.workflows.run("wf_id", payload=[{"Quote": "Stay curious", "Author": "Ada"}])

for r in client.workflows.runs.iter("wf_id"):
    print(r["id"], r["status"])
client.workflows.runs.cancel("wf_id", "run_id")
client.workflows.update("wf_id", status="active")   # turns on its schedule or trigger
```

## Social posts

Accounts are connected in the app, which shows the networks you can
connect. Posting costs no credits; a template render costs the usual credits.

```python
accounts = client.social.accounts.list()
ids = [a["id"] for a in accounts if a["status"] == "active"]

created = client.social.posts.create(
    template_id="tpl_id",
    modifications={"title": "Spring sale"},
    response={"format": "jpeg"},
    accounts=ids,
    caption="Spring sale starts today #sale",
    scheduled_at="2026-11-10T09:00",
    timezone="Europe/Paris",
    # options={"pinterest": {"boardId": "…"}, "tiktok": {"draft": True}, "buffer": {"mode": "next"}},
)
post = created["post"]          # created["checks"]: per-account warnings

client.social.posts.get(post["id"])["targets"]   # each account's status and permalink
client.social.posts.list(status="attention")      # failed or partly failed
client.social.posts.cancel(post["id"])
client.social.posts.retry(post["id"])             # or target_id="…" for one account
```

`create()` takes the API body as a dict, as keyword arguments, or both;
`template_id`, `render_id`, `media_urls` and `scheduled_at` are accepted for
`templateId`, `renderId`, `mediaUrls` and `scheduledAt`.

### Analytics

```python
import datetime

overview = client.social.analytics.overview(from_=datetime.date(2026, 10, 1), to="2026-10-31",
                                            platform=["instagram", "linkedin"])
overview["totals"]["impressions"], overview["change"]["engagement"]   # change in % from the period before
days = client.social.analytics.daily(from_="2026-10-01")["days"]
insights = client.social.analytics.insights()
insights["bestTimes"]["bestWeekday"], insights["bestTimes"]["bestHour"]
```

A metric is `None` when the platforms in scope don't report it. (`from_` has
an underscore because `from` is a Python keyword.)

## AI tools

```python
from pathlib import Path

# Cut the subject out of a picture (a URL or a file); a transparent PNG in your brand library. 1 AI credit.
cutout = client.ai.remove_background(file=Path("product.jpg"))
client.render("tpl_id", {"photo": cutout["url"]})

# Subtitles from speech (a URL or a file, up to 100 MB). 1 AI credit per minute of audio.
subs = client.ai.transcribe(url="https://example.com/voiceover.mp3", style="short")
client.render("tpl_id", {"subtitles": subs["cues"]}, response={"format": "mp4"})

# Whether AI is available, the prices and your AI credits this period. Free.
client.ai.status()["credits"]["remaining"]
```

`file=` takes bytes, a `pathlib.Path` or a file opened in binary mode
(`open("product.jpg", "rb")`).

## Webhooks

Subscribe a URL to events (`design.created`, `design.updated`,
`design.deleted`, `render.completed`, `render.failed`,
`social.post.published`, `social.post.failed`, `workflow.run.completed`,
`workflow.run.failed`):

```python
created = client.webhooks.subscriptions.create(
    "https://example.com/hooks/jamframe", ["render.completed", "render.failed"]
)
secret = created["signingSecret"]   # shown once: store it
client.webhooks.subscriptions.test(created["subscription"]["id"], "render.completed")
client.webhooks.subscriptions.delete(created["subscription"]["id"])
```

Verify every delivery against the **raw** body:

```python
# Flask
from flask import Flask, request, abort
from jamframe import WebhookSignatureError, construct_event

app = Flask(__name__)

@app.post("/hooks/jamframe")
def jamframe_hook():
    try:
        event = construct_event(request.get_data(), request.headers, SECRET)
    except WebhookSignatureError:
        abort(400)
    # Deliveries are at-least-once: deduplicate on request.headers["X-Webhook-Delivery"].
    if event["type"] == "render.completed":
        print(event["data"]["result"]["content"])
    return "", 204
```

```python
# FastAPI
from fastapi import FastAPI, HTTPException, Request
from jamframe import verify_signature

app = FastAPI()

@app.post("/hooks/jamframe")
async def jamframe_hook(request: Request):
    body = await request.body()
    if not verify_signature(body, request.headers.get("x-webhook-signature", ""), SECRET):
        raise HTTPException(400)
    ...
```

`verify_signature(payload, header, secret, tolerance=300)` returns a bool;
`construct_event` raises `WebhookSignatureError` and returns the parsed event.
`header` is the `X-Webhook-Signature` value or the request's headers;
`secret` may be a list (old and new while you rotate). Signatures older or
newer than `tolerance` seconds are refused (replay protection).

## Errors

Every error answer raises a subclass of `jamframe.APIError` with `status`,
`code` (the API's `error`, e.g. `QUOTA_EXCEEDED`), `message` (safe to show
to users) and `detail`:

| Status | Exception | Typical `code` |
| --- | --- | --- |
| 400 | `BadRequestError` | `VALIDATION_ERROR`, `INVALID_REQUEST`, `WEBHOOK_URL_INVALID` |
| 401 | `AuthenticationError` | `MISSING_API_KEY`, `INVALID_API_KEY`, `EXPIRED_API_KEY` |
| 402 | `PaymentRequiredError` | `QUOTA_EXCEEDED`, `WORKSPACE_QUOTA_EXCEEDED`, `AI_CREDITS_EXCEEDED`, `PLAN_LIMIT_EXCEEDED`, `WEBHOOK_LIMIT_REACHED` |
| 403 | `PermissionDeniedError` | `ORG_ON_HOLD`, `ORG_SUSPENDED` |
| 404 | `NotFoundError` | `NOT_FOUND` |
| 409 | `ConflictError` | `CONFLICT` |
| 422 | `UnprocessableEntityError` | `VALIDATION_ERROR` (a media URL that won't load), `AI_CONTENT_REFUSED`, `AI_BAD_REQUEST` |
| 429 | `RateLimitError` (`.retry_after`) | `RATE_LIMITED`, `AI_RATE_LIMITED` |
| 5xx | `InternalServerError` | `INTERNAL`, `AI_BAD_OUTPUT`, `AI_UPSTREAM_ERROR`, `AI_UNAVAILABLE`, `VIDEO_UNAVAILABLE`, `AI_TIMEOUT` |

A body that isn't the API's JSON envelope (a proxy's HTML page) still
raises the right class, with `code` `HTTP_<status>`. Network failures raise
`APIConnectionError`, timeouts `APITimeoutError`. A failed render job raises
`RenderJobFailedError`, a failed AI design `DesignJobFailedError`, a wait
past its timeout `WaitTimeoutError`. Everything derives from
`jamframe.SDKError`.

```python
from jamframe import PaymentRequiredError, BadRequestError

try:
    client.render("tpl_id", {"titel": "typo is a warning, not an error"})
except PaymentRequiredError as e:
    print("Out of credits:", e.detail.get("remaining"))
except BadRequestError as e:
    print(e.code, e.message, e.detail.get("issues"))
```

## Retries and timeouts

- **429** is retried for every call (`max_retries`, default 2): the rate
  limiter refuses a request before anything happens. The client waits for
  `Retry-After` (or `detail.retryAfter`); if that's over 60 s it raises
  `RateLimitError` instead.
- **5xx, connection errors and timeouts** are retried only for idempotent
  calls: reads, deletes, `whoami()`, `signed_urls.create()` and
  `workflows.update()`. A render, an AI call, a social post, a workflow run
  or a subscription isn't retried: it may have happened before the error.
- Backoff is 0.5 s, 1 s, 2 s … (at most 8 s), with jitter.
- `timeout` (default 120 s) is per HTTP request. It must cover a synchronous
  MP4, which the API holds for up to 90 s. Waiting for a job has its own
  `timeout` / `wait_timeout` (default 600 s).

```python
client = Client(timeout=30, max_retries=4, user_agent="my-app/1.2")
```

`user_agent` is prepended to `jamframe-python/<version>`. Pass
`http_client=httpx.Client(...)` (or `httpx.AsyncClient` for the async
client) for proxies, custom TLS or transports; the SDK won't close a client
you pass in.

## Async

```python
import asyncio
from jamframe import AsyncClient

async def main():
    async with AsyncClient() as client:
        job = await client.render_and_wait("tpl_id", {"title": "Hi"}, response={"format": "mp4"})
        async for design in client.designs.iter(source="library"):
            print(design["name"])

asyncio.run(main())
```

## Why httpx

One dependency buys a lot here: the same request code drives a sync and an
async client, timeouts are real (connect, read, write and pool), connections
are pooled and kept alive across calls, and `httpx.MockTransport` lets the
tests run the real client code against a fake API with no server. The
standard library's `urllib` has no async client, no keep-alive pooling and
coarse timeouts, so it would mean either a sync-only SDK or a second HTTP
stack for async.

## Development

```sh
uv sync --extra dev    # or: python -m venv .venv && pip install -e ".[dev]"
uv run pytest          # unit tests (no network: httpx.MockTransport)
uv run mypy            # strict type check of src/jamframe
```

`tests/test_spec.py` checks the SDK against the API spec in
[`spec/openapi.yaml`](spec/openapi.yaml): every endpoint has a method (or is
skipped on purpose), the types have exactly the spec's fields and enum
values, and the async client has the same methods as the sync one.
`python scripts/update_spec.py` downloads the live spec; CI does that every
Monday, so a new API feature shows up as a failing check.

The live smoke test runs only when `JAMFRAME_SMOKE_API_KEY` is set. It
renders 2 PNGs of a library template (2 credits), lists designs and
renders, signs a URL, reads AI status, workflows and analytics, creates and
deletes a webhook subscription, and posts to an active *sandbox* social
account if the workspace has one:

```sh
JAMFRAME_SMOKE_API_KEY=jf_live_... JAMFRAME_SMOKE_BASE_URL=https://api.jamframe.com \
  uv run pytest tests/test_live_smoke.py -s
```

Use a throwaway key and revoke it afterwards.

## Releasing

1. Update `version` in `pyproject.toml` and `src/jamframe/_version.py` (a
   test checks they match) and add the release to [CHANGELOG.md](CHANGELOG.md).
2. Commit, then tag the commit with the bare version (`0.1.1`) and push the tag.
3. [`publish.yml`](.github/workflows/publish.yml) runs the checks, builds,
   publishes to TestPyPI, then (after approval in the `pypi` environment)
   to PyPI, through trusted publishing: no token is stored anywhere.

## License

[MIT](LICENSE)
