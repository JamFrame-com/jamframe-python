"""Checks the SDK against the API's OpenAPI spec (``spec/openapi.yaml``, or the
file named by ``JAMFRAME_SPEC_FILE``), so the SDK can't quietly fall behind:

- every endpoint is wrapped by a method, or skipped on purpose below;
- every enum value the API sends or accepts is in the SDK's types;
- the main types have exactly the spec's fields;
- the sync and async clients have the same methods.

``python scripts/update_spec.py`` downloads the live spec.
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Set, Union, get_args, get_origin, get_type_hints

import pytest
import yaml

from jamframe import AsyncClient, Client, types as T

from .conftest import FakeTime, MockAPI, make_client

class _Yaml12Loader(yaml.SafeLoader):
    """PyYAML follows YAML 1.1, where ``on``/``off``/``yes``/``no`` are booleans.
    The spec is YAML 1.2 (``status: [live, mock, off]``): only true/false are."""


_Yaml12Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)

SPEC_FILE = Path(os.environ.get("JAMFRAME_SPEC_FILE") or Path(__file__).resolve().parents[1] / "spec" / "openapi.yaml")
SPEC: Dict[str, Any] = yaml.load(SPEC_FILE.read_text(encoding="utf-8"), Loader=_Yaml12Loader)


def deref(schema: Dict[str, Any]) -> Dict[str, Any]:
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    if not ref:
        return schema
    return deref(SPEC["components"]["schemas"][ref.rsplit("/", 1)[-1]])


def parts(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The schema's parts: itself, its allOf/oneOf/anyOf options and, for an array, its items."""
    s = deref(schema)
    nested = [*s.get("allOf", []), *s.get("oneOf", []), *s.get("anyOf", [])]
    if isinstance(s.get("items"), dict):
        nested.append(s["items"])
    return [s, *(p for n in nested for p in parts(n))]


def at(pointer: str) -> Dict[str, Any]:
    """``RenderOptions.cmyk.profile`` -> that schema, through $ref, allOf, oneOf and array items."""
    name, *path = pointer.split(".")
    schema = SPEC["components"]["schemas"][name]
    for key in path:
        found = next((p["properties"][key] for p in parts(schema) if key in p.get("properties", {})), None)
        assert found is not None, f"no field {key} in {pointer}"
        schema = found
    return deref(schema)


def spec_fields(pointer: str) -> List[str]:
    return sorted({key for p in parts(at(pointer)) for key in p.get("properties", {})})


def spec_enum(pointer: str) -> List[str]:
    values = {str(v) for p in parts(at(pointer)) for v in p.get("enum", [])}
    assert values, f"no enum at {pointer}"
    return sorted(values)


def keys(typed_dict: Any) -> List[str]:
    return sorted(typed_dict.__required_keys__ | typed_dict.__optional_keys__)


def literals(tp: Any) -> List[str]:
    """Every value of a Literal, also inside Optional/Union/List."""
    if get_origin(tp) is Literal:
        return sorted(str(v) for v in get_args(tp))
    found: Set[str] = set()
    for arg in get_args(tp):
        found.update(literals(arg))
    return sorted(found)


def field(typed_dict: Any, name: str) -> Any:
    return get_type_hints(typed_dict)[name]


# ---------------------------------------------------------------- fields

FIELDS = [
    # requests
    ("RenderRequest", T.RenderRequest),
    ("RenderRequest.ai", T.RenderAiOptions),
    ("RenderOptions", T.RenderOptions),
    ("RenderOptions.cmyk", T.CmykOptions),
    ("RenderOptions.compress", T.CompressOptions),
    ("SignedUrlRequest", T.SignedUrlRequest),
    ("SignedUrlRequest.response", T.SignedUrlOptions),
    ("SocialPostInput", T.SocialPostInput),
    ("SocialPostInput.response", T.SocialPostRenderOptions),
    ("SocialPostInput.accounts", T.SocialPostAccount),
    ("SocialPostInput.options", T.SocialPostOptions),
    ("SocialPostInput.options.instagram", T.InstagramPostOptions),
    ("SocialPostInput.options.facebook", T.FacebookPostOptions),
    ("SocialPostInput.options.x", T.XPostOptions),
    ("SocialPostInput.options.tiktok", T.TikTokPostOptions),
    ("SocialPostInput.options.youtube", T.YouTubePostOptions),
    ("SocialPostInput.options.pinterest", T.PinterestPostOptions),
    ("SocialPostInput.options.bluesky", T.BlueskyPostOptions),
    ("SocialPostInput.options.buffer", T.BufferPostOptions),
    ("LinkedInPostOptions", T.LinkedInPostOptions),
    ("DesignGenerateRequest", T.DesignGenerateRequest),
    ("DesignGenerateRequest.brand", T.BrandValues),
    ("WorkflowCreate", T.WorkflowCreateInput),
    ("WorkflowUpdate", T.WorkflowUpdateInput),
    ("WorkflowRunRequest", T.WorkflowRunRequest),
    ("WorkflowDefinition", T.WorkflowDefinition),
    ("WorkflowDefinition.trigger", T.WorkflowTrigger),
    ("WorkflowDefinition.action", T.WorkflowAction),
    # responses
    ("ApiError", T.ApiErrorBody),
    ("AuthResult", T.AuthResult),
    ("Pagination", T.Pagination),
    ("Design", T.Design),
    ("DesignDetail", T.DesignDetail),
    ("TemplateParameter", T.TemplateParameter),
    ("TemplateSize", T.TemplateSize),
    ("DesignGenerateJob", T.DesignGenerateJob),
    ("DesignGenerateJob.design", T.GeneratedDesign),
    ("DesignGenerateCallback", T.DesignGenerateCallback),
    ("RenderResult", T.RenderResult),
    ("SizedContent", T.SizedContent),
    ("RenderJob", T.RenderJob),
    ("RenderJobCallback", T.RenderJobCallback),
    ("RenderItem", T.RenderItem),
    ("RenderOutputFile", T.RenderOutputFile),
    ("RenderTotals", T.RenderTotals),
    ("SignedUrlResponse", T.SignedUrlResponse),
    ("SignedUrlResponse.data", T.SignedUrl),
    ("SocialAccount", T.SocialAccount),
    ("SocialMedia", T.SocialMedia),
    ("SocialPostTarget", T.SocialPostTarget),
    ("SocialPostTarget.error", T.SocialTargetError),
    ("SocialPost", T.SocialPost),
    ("SocialTargetIssues", T.SocialTargetIssues),
    ("SocialIssue", T.SocialIssue),
    ("SocialPostEvent", T.SocialPostEvent),
    ("SocialMetricValues", T.SocialMetricValues),
    ("SocialAnalyticsRange", T.SocialAnalyticsRange),
    ("SocialAnalyticsOverview", T.SocialAnalyticsOverview),
    ("SocialAnalyticsOverview.previousRange", T.DayRange),
    ("SocialAnalyticsDaily", T.SocialAnalyticsDaily),
    ("SocialAnalyticsDaily.days", T.SocialAnalyticsDay),
    ("SocialPostMetrics", T.SocialPostMetrics),
    ("SocialAnalyticsInsights", T.SocialAnalyticsInsights),
    ("SocialAnalyticsInsights.bestTimes", T.BestTimes),
    ("SocialAnalyticsInsights.bestTimes.weekdays", T.BestTimeWeekday),
    ("SocialAnalyticsInsights.bestTimes.hours", T.BestTimeHour),
    ("SocialAnalyticsInsights.bestTimes.bestWeekday", T.Weekday),
    ("SocialAnalyticsInsights.platforms", T.PlatformSummary),
    ("AiStatus", T.AiStatus),
    ("AiStatus.prices", T.AiPrice),
    ("AiStatus.credits", T.AiCredits),
    ("BackgroundRemoval", T.BackgroundRemoval),
    ("BrandAsset", T.BrandAsset),
    ("Transcription", T.Transcription),
    ("Transcription.words", T.TranscriptWord),
    ("SubtitleCue", T.SubtitleCue),
    ("WebhookSubscription", T.WebhookSubscription),
    ("WebhookEvent", T.WebhookEvent),
    ("Workflow", T.Workflow),
    ("Workflow.problems", T.WorkflowProblem),
    ("Workflow.trigger", T.WorkflowTriggerSummary),
    ("WorkflowSecret", T.WorkflowSecret),
    ("WorkflowRun", T.WorkflowRun),
    ("WorkflowRun.error", T.WorkflowRunError),
    ("WorkflowRunEvent", T.WorkflowRunEvent),
    ("WorkflowRunRow", T.WorkflowRunRow),
    ("WorkflowRunRow.deliveries", T.WorkflowRowDelivery),
]


@pytest.mark.parametrize("pointer,typed_dict", FIELDS, ids=[p for p, _ in FIELDS])
def test_fields_match_the_spec(pointer: str, typed_dict: Any) -> None:
    assert keys(typed_dict) == spec_fields(pointer)


def test_transcribe_keywords_match_the_spec() -> None:
    transcribe = inspect.signature(Client(api_key="k").ai.transcribe).parameters
    camel = {re.sub(r"_([a-z])", lambda m: m.group(1).upper(), name) for name in transcribe}
    assert sorted(camel - {"file", "filename"}) == spec_fields("TranscribeRequest")


# ---------------------------------------------------------------- enums

ENUMS = [
    ("ApiError.error", T.ApiErrorCode),
    ("SocialPlatform", T.SocialPlatform),
    ("WebhookEventType", T.WebhookEventType),
    ("WebhookSubscription.status", field(T.WebhookSubscription, "status")),
    ("RenderOptions.format", T.OutputFormat),
    ("RenderResult.format", T.ResultFormat),
    ("RenderOptions.cmyk.profile", T.CmykProfile),
    ("RenderOptions.compress.preset", T.CompressPreset),
    ("RenderJob.status", T.RenderJobStatus),
    ("RenderItem.source", T.RenderSource),
    ("TemplateParameter.type", T.ParameterType),
    ("DesignGenerateJob.status", field(T.DesignGenerateJob, "status")),
    ("SocialPost.status", T.SocialPostStatus),
    ("SocialPostTarget.status", T.SocialTargetStatus),
    ("SocialPostInput.options.buffer.mode", field(T.BufferPostOptions, "mode")),
    ("SocialPostInput.options.tiktok.privacyLevel", field(T.TikTokPostOptions, "privacyLevel")),
    ("SocialMetricName", T.SocialMetricName),
    ("SocialPostMetrics.status", field(T.SocialPostMetrics, "status")),
    ("AiStatus.status", field(T.AiStatus, "status")),
    ("AiStatus.prices.operation", field(T.AiPrice, "operation")),
    ("BrandAsset.kind", field(T.BrandAsset, "kind")),
    ("WorkflowRun.status", T.WorkflowRunStatus),
    ("WorkflowRun.trigger", field(T.WorkflowRun, "trigger")),
    ("WorkflowRunRow.status", field(T.WorkflowRunRow, "status")),
    ("WorkflowDefinition.trigger.kind", field(T.WorkflowTrigger, "kind")),
]


@pytest.mark.parametrize("pointer,alias", ENUMS, ids=[p for p, _ in ENUMS])
def test_enums_match_the_spec(pointer: str, alias: Any) -> None:
    assert literals(alias) == spec_enum(pointer)


# ---------------------------------------------------------------- endpoints

# Not for API-key clients: the Figma plugin's import, and the public links
# and forms that browsers open.
SKIPPED = {
    "GET /designs/import/figma",
    "POST /designs/import/figma",
    "GET /r/s/{token}",
    "GET /r/d/{file}",
    "GET /api/public/forms/{publicId}",
    "POST /api/public/forms/{publicId}/render",
    "POST /api/public/forms/{publicId}/uploads",
    "GET /api/public/forms/{publicId}/files/{handle}",
}

# One call per endpoint. Answers don't matter: the mock answers 404.
CALLS: Dict[str, Callable[[Client], Any]] = {
    "POST /auth": lambda c: c.whoami(),
    "POST /render": lambda c: c.render("t"),
    "GET /render-jobs/{id}": lambda c: c.render_jobs.get("j"),
    "GET /renders": lambda c: c.renders.list(),
    "GET /renders/{id}": lambda c: c.renders.get("r"),
    "POST /signed-urls": lambda c: c.signed_urls.create("t"),
    "POST /signed-urls/rotate-key": lambda c: c.signed_urls.rotate_key(),
    "GET /designs": lambda c: c.designs.list(),
    "GET /designs/{id}": lambda c: c.designs.get("d"),
    "POST /designs/generate": lambda c: c.designs.generate("p"),
    "GET /designs/generate/{jobId}": lambda c: c.design_jobs.get("j"),
    "GET /webhooks/subscriptions": lambda c: c.webhooks.subscriptions.list(),
    "POST /webhooks/subscriptions": lambda c: c.webhooks.subscriptions.create("https://x", ["render.completed"]),
    "GET /webhooks/subscriptions/{id}": lambda c: c.webhooks.subscriptions.get("s"),
    "DELETE /webhooks/subscriptions/{id}": lambda c: c.webhooks.subscriptions.delete("s"),
    "POST /webhooks/subscriptions/{id}/test": lambda c: c.webhooks.subscriptions.test("s"),
    "POST /webhooks/subscriptions/{id}/rotate-secret": lambda c: c.webhooks.subscriptions.rotate_secret("s"),
    "GET /social/accounts": lambda c: c.social.accounts.list(),
    "POST /social/posts": lambda c: c.social.posts.create(caption="c"),
    "GET /social/posts": lambda c: c.social.posts.list(),
    "GET /social/posts/{id}": lambda c: c.social.posts.get("p"),
    "POST /social/posts/{id}/cancel": lambda c: c.social.posts.cancel("p"),
    "POST /social/posts/{id}/retry": lambda c: c.social.posts.retry("p"),
    "GET /social/analytics/overview": lambda c: c.social.analytics.overview(),
    "GET /social/analytics/daily": lambda c: c.social.analytics.daily(),
    "GET /social/analytics/insights": lambda c: c.social.analytics.insights(),
    "GET /ai/status": lambda c: c.ai.status(),
    "POST /images/remove-background": lambda c: c.ai.remove_background(url="https://x/a.png"),
    "POST /ai/transcribe": lambda c: c.ai.transcribe(url="https://x/a.mp3"),
    "GET /workflows": lambda c: c.workflows.list(),
    "POST /workflows": lambda c: c.workflows.create(),
    "GET /workflows/{id}": lambda c: c.workflows.get("w"),
    "PATCH /workflows/{id}": lambda c: c.workflows.update("w", name="n"),
    "DELETE /workflows/{id}": lambda c: c.workflows.delete("w"),
    "POST /workflows/{id}/run": lambda c: c.workflows.run("w"),
    "POST /workflows/{id}/webhook-token": lambda c: c.workflows.rotate_webhook_url("w"),
    "GET /workflows/{id}/runs": lambda c: c.workflows.runs.list("w"),
    "GET /workflows/{id}/runs/{runId}": lambda c: c.workflows.runs.get("w", "r"),
    "POST /workflows/{id}/runs/{runId}/cancel": lambda c: c.workflows.runs.cancel("w", "r"),
}

OPERATIONS = [
    f"{method.upper()} {path}"
    for path, item in SPEC["paths"].items()
    for method in item
    if method in ("get", "post", "put", "patch", "delete")
]


def test_every_endpoint_has_a_method() -> None:
    missing = [op for op in OPERATIONS if op not in CALLS and op not in SKIPPED]
    assert missing == [], f"endpoints without an SDK method: {missing}"
    stale = [op for op in CALLS if op not in OPERATIONS]
    assert stale == [], f"methods for endpoints the spec doesn't have: {stale}"


@pytest.mark.parametrize("operation", sorted(CALLS))
def test_each_method_sends_its_request(operation: str) -> None:
    api = MockAPI()
    client = make_client(api, FakeTime(), max_retries=0)
    try:
        CALLS[operation](client)
    except Exception:
        pass
    method, template = operation.split(" ")
    pattern = "^/v1" + re.sub(r"\{[^}]+\}", "[^/]+", template) + "$"
    assert api.requests, f"{operation}: nothing was sent"
    sent = api.requests[0]
    assert sent.method == method
    assert re.match(pattern, sent.url.path), sent.url.path


# ---------------------------------------------------------------- sync and async


def public_surface(root: Union[type, object], prefix: str = "") -> Dict[str, List[str]]:
    """Every public method of the client and its resources, with its parameter names."""
    surface: Dict[str, List[str]] = {}
    for name, value in vars(root).items() if not isinstance(root, type) else []:
        if name.startswith("_") or not hasattr(value, "__dict__"):
            continue
        surface.update(public_surface(value, f"{prefix}{name}."))
    for name, member in inspect.getmembers(type(root)):
        if name.startswith("_") or not callable(member) or isinstance(member, type):
            continue
        if name in ("verify_signature", "construct_event"):
            continue
        surface[prefix + name] = list(inspect.signature(member).parameters)
    return surface


def test_async_client_mirrors_the_sync_client() -> None:
    sync = public_surface(Client(api_key="k"))
    async_ = public_surface(AsyncClient(api_key="k"))
    assert sorted(sync) == sorted(async_)
    for name in sync:
        assert sync[name] == async_[name], name
