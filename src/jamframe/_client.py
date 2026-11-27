"""The synchronous client.

``_async_client.py`` mirrors this file method for method; change both together.
"""

from __future__ import annotations

import datetime
import time
from types import TracebackType
from typing import Any, Callable, Dict, Iterator, List, Literal, Mapping, Optional, Sequence, Type, Union, cast

import httpx

from . import webhooks as _webhooks
from ._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    DEFAULT_WAIT_TIMEOUT,
    FINAL_RUN_STATUSES,
    BaseClient,
    FileInput,
    Params,
    Result,
    Upload,
    analytics_params,
    api_error_from_response,
    backoff_delay,
    design_generate_body,
    media_source,
    next_poll_interval,
    page_cursor,
    parse_success,
    path_segment,
    render_body,
    retry_delay,
    run_body,
    signed_url_body,
    social_post_body,
    unwrap,
    workflow_body,
)
from .errors import APIConnectionError, APITimeoutError, DesignJobFailedError, RenderJobFailedError, WaitTimeoutError
from .types import (
    AiStatus,
    AuthResult,
    BackgroundRemoval,
    BrandValues,
    Design,
    DesignDetail,
    DesignGenerateAccepted,
    DesignGenerateJob,
    DesignPage,
    DesignSource,
    Modifications,
    RenderAiOptions,
    RenderItem,
    RenderJob,
    RenderJobAccepted,
    RenderOptions,
    RenderPage,
    RenderResponse,
    SignedUrlKeyRotation,
    SignedUrlOptions,
    SignedUrlResponse,
    SocialAccount,
    SocialAnalyticsDaily,
    SocialAnalyticsInsights,
    SocialAnalyticsOverview,
    SocialPost,
    SocialPostCreated,
    SocialPostInput,
    SocialPostPage,
    Transcription,
    WebhookEventType,
    WebhookSubscription,
    WebhookSubscriptionPage,
    WebhookSubscriptionWithSecret,
    WebhookTestResult,
    Workflow,
    WorkflowCreateInput,
    WorkflowRun,
    WorkflowRunPage,
    WorkflowRunWithRows,
    WorkflowUpdateInput,
)

Day = Union[str, datetime.date]
"""A day: ``"2026-11-19"`` or a ``datetime.date``."""
Many = Union[str, Sequence[str]]
"""One value, or several (sent comma-separated)."""


class Client(BaseClient):
    """A synchronous client for the v1 API.

    >>> client = Client()  # reads JAMFRAME_API_KEY (and JAMFRAME_BASE_URL)
    >>> client.render("tpl_id", {"title": "Hello"})["data"]["content"]

    ``base_url`` is the API's origin (``https://api.jamframe.com``) or its
    root (``…/v1``; the older ``…/api/v1`` also works). ``timeout`` is per
    HTTP request, in seconds.
    ``max_retries`` applies to 429s (every call) and to 5xx answers,
    connection errors and timeouts of idempotent calls. Pass your own
    ``http_client`` (an ``httpx.Client``) for proxies or custom transports;
    the SDK won't close it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.Client] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        super().__init__(api_key, base_url, timeout, max_retries, user_agent)
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        # Testing hooks.
        self._sleep: Callable[[float], None] = time.sleep
        self._clock: Callable[[], float] = time.monotonic

        self.designs = Designs(self)
        self.design_jobs = DesignJobs(self)
        self.renders = Renders(self)
        self.render_jobs = RenderJobs(self)
        self.signed_urls = SignedUrls(self)
        self.social = Social(self)
        self.workflows = Workflows(self)
        self.ai = Ai(self)
        self.webhooks = Webhooks(self)

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Close the connection pool (only when the SDK created it)."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    # ------------------------------------------------------------ transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Params] = None,
        json_body: Any = None,
        idempotent: Optional[bool] = None,
        upload: Optional[Upload] = None,
        form: Optional[Mapping[str, Any]] = None,
    ) -> Result:
        if idempotent is None:
            idempotent = method in ("GET", "HEAD", "DELETE")
        args = self._request_args(method, path, params, json_body, upload, form)
        attempt = 0
        while True:
            try:
                response = self._http.request(**args)
            except httpx.TimeoutException as exc:
                if idempotent and attempt < self.max_retries:
                    self._sleep(backoff_delay(attempt, self._random))
                    attempt += 1
                    continue
                raise APITimeoutError(f"{method} {path} timed out after {self.timeout:g} s") from exc
            except httpx.TransportError as exc:
                if idempotent and attempt < self.max_retries:
                    self._sleep(backoff_delay(attempt, self._random))
                    attempt += 1
                    continue
                raise APIConnectionError(f"{method} {path} failed: {exc}") from exc
            if response.status_code < 400:
                return parse_success(response)
            error = api_error_from_response(response)
            delay = retry_delay(
                error, attempt, idempotent=idempotent, max_retries=self.max_retries, rand=self._random
            )
            if delay is None:
                raise error
            self._sleep(delay)
            attempt += 1

    # ------------------------------------------------------------ top-level calls

    def whoami(self) -> AuthResult:
        """Check the API key: ``{ workspaceId, username }`` (``POST /auth``)."""
        return cast(AuthResult, self._request("POST", "/auth", idempotent=True).data)

    def render(
        self,
        template_id: str,
        modifications: Optional[Modifications] = None,
        *,
        response: Optional[RenderOptions] = None,
        expires_in: Optional[int] = None,
        translate: Optional[bool] = None,
        lang: Optional[str] = None,
        ai: Optional[RenderAiOptions] = None,
        wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: float = 1.0,
    ) -> RenderResponse:
        """Render a template and return ``{ data, warnings }``.

        ``template_id`` is a template's id or a library template's slug.
        Images and PDFs answer at once. An MP4 the API is still rendering
        after 90 s (a ``202``) is waited for here, up to ``wait_timeout``.
        Spends credits: 1 per image or PDF page, 1 per second of video.

        ``expires_in``: seconds the file links work (300 to 604800).
        ``translate`` with ``lang`` (``"es"``, ``"pt-BR"``…) translates every
        text layer with AI. ``ai={"onError": "fallback"}`` renders AI layers'
        saved text and images instead of failing.
        """
        body = render_body(
            template_id, modifications, response, expires_in=expires_in, translate=translate, lang=lang, ai=ai
        )
        result = self._request("POST", "/render", json_body=body, idempotent=False)
        if result.status_code == 202:
            accepted = cast(RenderJobAccepted, unwrap(result, "data"))
            job = self.render_jobs.wait(accepted["jobId"], timeout=wait_timeout, poll_interval=poll_interval)
            return {"data": cast(Any, job["result"]), "warnings": list(job.get("warnings") or [])}
        payload = cast(Dict[str, Any], result.data)
        return {"data": payload["data"], "warnings": list(payload.get("warnings") or [])}

    def render_async(
        self,
        template_id: str,
        modifications: Optional[Modifications] = None,
        *,
        response: Optional[RenderOptions] = None,
        webhook_url: Optional[str] = None,
        expires_in: Optional[int] = None,
        translate: Optional[bool] = None,
        lang: Optional[str] = None,
        ai: Optional[RenderAiOptions] = None,
    ) -> RenderJobAccepted:
        """Queue a render job and return ``{ jobId, status }`` at once (``"async": true``).

        ``webhook_url`` receives ``{ event, job }`` when the job ends (unsigned:
        confirm with :meth:`RenderJobs.get`). The other options are as in :meth:`render`.
        """
        body = render_body(
            template_id,
            modifications,
            response,
            run_async=True,
            webhook_url=webhook_url,
            expires_in=expires_in,
            translate=translate,
            lang=lang,
            ai=ai,
        )
        result = self._request("POST", "/render", json_body=body, idempotent=False)
        return cast(RenderJobAccepted, unwrap(result, "data"))

    def render_and_wait(
        self,
        template_id: str,
        modifications: Optional[Modifications] = None,
        *,
        response: Optional[RenderOptions] = None,
        webhook_url: Optional[str] = None,
        expires_in: Optional[int] = None,
        translate: Optional[bool] = None,
        lang: Optional[str] = None,
        ai: Optional[RenderAiOptions] = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: float = 1.0,
    ) -> RenderJob:
        """Queue a render job and poll it until it succeeds; returns the job (files in ``result``).

        Raises :class:`RenderJobFailedError` if it fails (no credits are
        charged) and :class:`WaitTimeoutError` after ``timeout`` seconds.
        """
        accepted = self.render_async(
            template_id,
            modifications,
            response=response,
            webhook_url=webhook_url,
            expires_in=expires_in,
            translate=translate,
            lang=lang,
            ai=ai,
        )
        return self.render_jobs.wait(accepted["jobId"], timeout=timeout, poll_interval=poll_interval)

    def _poll(
        self,
        job_id: str,
        what: str,
        get: Callable[[], Mapping[str, Any]],
        done: Callable[[Mapping[str, Any]], bool],
        timeout: float,
        poll_interval: float,
    ) -> Any:
        """Call ``get`` until ``done`` says so (it may raise to end the wait).

        Polls start ``poll_interval`` apart and grow 1.5x up to 5 s. After
        ``timeout`` seconds raises :class:`WaitTimeoutError`; the job keeps running.
        """
        deadline = self._clock() + timeout
        interval = poll_interval
        while True:
            job = get()
            if done(job):
                return job
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WaitTimeoutError(job_id, job, timeout, what)
            self._sleep(min(interval, remaining))
            interval = next_poll_interval(interval, poll_interval)


class _Resource:
    def __init__(self, client: Client) -> None:
        self._client = client


class RenderJobs(_Resource):
    """MP4 renders and ``async`` renders (``GET /render-jobs/{id}``)."""

    def get(self, job_id: str) -> RenderJob:
        result = self._client._request("GET", f"/render-jobs/{path_segment(job_id)}")
        return cast(RenderJob, unwrap(result, "job"))

    def wait(
        self, job_id: str, *, timeout: float = DEFAULT_WAIT_TIMEOUT, poll_interval: float = 1.0
    ) -> RenderJob:
        """Poll until the job succeeds (returned) or fails (:class:`RenderJobFailedError`).

        Polls start ``poll_interval`` apart and grow 1.5x up to 5 s. After
        ``timeout`` seconds raises :class:`WaitTimeoutError`; the job keeps running.
        """

        def done(job: Mapping[str, Any]) -> bool:
            if job["status"] == "failed":
                raise RenderJobFailedError(cast(RenderJob, job))
            return bool(job["status"] == "succeeded")

        job = self._client._poll(job_id, "Render job", lambda: self.get(job_id), done, timeout, poll_interval)
        return cast(RenderJob, job)


class Designs(_Resource):
    """Templates: the workspace's own, or the template library (``source="library"``)."""

    def list(
        self,
        *,
        source: DesignSource = "workspace",
        q: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> DesignPage:
        """One page, newest first: ``{ designs, pagination }``.

        ``q`` keeps the designs whose name contains it, ignoring case (at most
        100 characters), for either ``source``.
        """
        params: Dict[str, Any] = {"source": source, "q": q, "cursor": cursor, "limit": limit}
        return cast(DesignPage, self._client._request("GET", "/designs", params=params).data)

    def iter(
        self, *, source: DesignSource = "workspace", q: Optional[str] = None, limit: Optional[int] = None
    ) -> Iterator[Design]:
        """Every design (matching ``q``), fetching pages of ``limit`` (default 25, at most 100) as needed."""
        cursor: Optional[str] = None
        while True:
            page = self.list(source=source, q=q, cursor=cursor, limit=limit)
            yield from page["designs"]
            cursor = page_cursor(page)
            if cursor is None:
                return

    def get(self, design_id: str) -> DesignDetail:
        """One template (yours or a library one) with its ``pages``, ``parameters`` and ``sizes``."""
        result = self._client._request("GET", f"/designs/{path_segment(design_id)}")
        return cast(DesignDetail, unwrap(result, "design"))

    def generate(
        self,
        prompt: str,
        *,
        size: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        pages: Optional[int] = None,
        video: Optional[bool] = None,
        brand: Union[bool, BrandValues, None] = None,
        reference_image_url: Optional[str] = None,
        name: Optional[str] = None,
        wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: float = 1.0,
    ) -> DesignGenerateJob:
        """Design a new template with AI from a description and save it in the workspace.

        The brand kit is applied and parameter keys set (``title``, ``photo``…);
        the job's ``design`` is ready to render. The API waits about 25 s; a
        longer design is polled here, up to ``wait_timeout``. Costs 15 to 30 AI
        credits, refunded when it fails (:class:`DesignJobFailedError`).

        ``size``: a preset id (``instagram-story``, ``linkedin-carousel``…),
        ``square``/``portrait``/``story``/``landscape`` or ``"WIDTHxHEIGHT"``.
        ``brand``: ``True`` (the brand library, default), ``False``, or
        ``{"colors": [...], "fonts": [...], "logo": url}``.
        """
        body = design_generate_body(
            prompt,
            size=size,
            width=width,
            height=height,
            pages=pages,
            video=video,
            brand=brand,
            reference_image_url=reference_image_url,
            name=name,
        )
        result = self._client._request("POST", "/designs/generate", json_body=body, idempotent=False)
        job = cast(DesignGenerateJob, unwrap(result, "data"))
        if job["status"] == "failed":
            raise DesignJobFailedError(job)
        if job["status"] == "succeeded" and job.get("design"):
            return job
        return self._client.design_jobs.wait(job["jobId"], timeout=wait_timeout, poll_interval=poll_interval)

    def generate_async(
        self,
        prompt: str,
        *,
        size: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        pages: Optional[int] = None,
        video: Optional[bool] = None,
        brand: Union[bool, BrandValues, None] = None,
        reference_image_url: Optional[str] = None,
        name: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> DesignGenerateAccepted:
        """Start an AI design and return ``{ jobId, status }`` at once.

        Wait for it with :meth:`DesignJobs.wait`, or pass ``webhook_url`` to
        receive ``{ event, job }`` when it ends (unsigned: confirm with
        :meth:`DesignJobs.get`).
        """
        body = design_generate_body(
            prompt,
            size=size,
            width=width,
            height=height,
            pages=pages,
            video=video,
            brand=brand,
            reference_image_url=reference_image_url,
            name=name,
            run_async=True,
            webhook_url=webhook_url,
        )
        result = self._client._request("POST", "/designs/generate", json_body=body, idempotent=False)
        data = unwrap(result, "data")
        return {"jobId": data["jobId"], "status": "running"}


class DesignJobs(_Resource):
    """AI design generations (``GET /designs/generate/{jobId}``)."""

    def get(self, job_id: str) -> DesignGenerateJob:
        result = self._client._request("GET", f"/designs/generate/{path_segment(job_id)}")
        return cast(DesignGenerateJob, unwrap(result, "data"))

    def wait(
        self, job_id: str, *, timeout: float = DEFAULT_WAIT_TIMEOUT, poll_interval: float = 1.0
    ) -> DesignGenerateJob:
        """Poll until the design is made (returned, with ``design``) or the generation fails
        (:class:`DesignJobFailedError`). After ``timeout`` seconds raises :class:`WaitTimeoutError`."""

        def done(job: Mapping[str, Any]) -> bool:
            if job["status"] == "failed":
                raise DesignJobFailedError(cast(DesignGenerateJob, job))
            return bool(job["status"] == "succeeded" and job.get("design"))

        job = self._client._poll(job_id, "Design generation", lambda: self.get(job_id), done, timeout, poll_interval)
        return cast(DesignGenerateJob, job)


class Renders(_Resource):
    """The render log (``GET /renders``, ``GET /renders/{id}``, ``DELETE /renders/{id}/files``)."""

    def get(self, render_id: str) -> RenderItem:
        """One render by id (a ``renderId`` from a render or a job's result), as the log lists it."""
        result = self._client._request("GET", f"/renders/{path_segment(render_id)}")
        return cast(RenderItem, unwrap(result, "render"))

    def delete_files(self, render_id: str) -> RenderItem:
        """Delete the render's stored files now. The render stays in the log, with ``filesDeleted`` true."""
        result = self._client._request("DELETE", f"/renders/{path_segment(render_id)}/files")
        return cast(RenderItem, unwrap(result, "render"))

    def list(
        self,
        *,
        status: Optional[str] = None,
        design_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> RenderPage:
        """One page, newest first: ``{ renders, pagination, totals }`` (totals: this period's credits)."""
        params = {"status": status, "designId": design_id, "cursor": cursor, "limit": limit}
        return cast(RenderPage, self._client._request("GET", "/renders", params=params).data)

    def iter(
        self,
        *,
        status: Optional[str] = None,
        design_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Iterator[RenderItem]:
        cursor: Optional[str] = None
        while True:
            page = self.list(status=status, design_id=design_id, cursor=cursor, limit=limit)
            yield from page["renders"]
            cursor = page_cursor(page)
            if cursor is None:
                return


class SignedUrls(_Resource):
    """Public, tamper-proof render URLs (``POST /signed-urls``)."""

    def create(
        self,
        template_id: str,
        modifications: Optional[Mapping[str, Any]] = None,
        *,
        response: Optional[SignedUrlOptions] = None,
        expires_in: Optional[int] = None,
        cache_seconds: Optional[int] = None,
    ) -> SignedUrlResponse:
        """Sign a render into a URL (``{ data: { url, … }, warnings? }``). Free until the URL is opened."""
        body = signed_url_body(template_id, modifications, response, expires_in, cache_seconds)
        result = self._client._request("POST", "/signed-urls", json_body=body, idempotent=True)
        return cast(SignedUrlResponse, result.data)

    def rotate_key(self) -> SignedUrlKeyRotation:
        """Revoke every signed URL of the workspace at once: ``{ rotatedAt }``."""
        result = self._client._request("POST", "/signed-urls/rotate-key", idempotent=False)
        return cast(SignedUrlKeyRotation, unwrap(result, "data"))


class SocialAccounts(_Resource):
    def list(self) -> List[SocialAccount]:
        """The workspace's connected accounts (connect them in the app)."""
        result = self._client._request("GET", "/social/accounts")
        return cast(List[SocialAccount], unwrap(result, "accounts"))


class SocialPosts(_Resource):
    def create(self, body: Optional[SocialPostInput] = None, **kwargs: Any) -> SocialPostCreated:
        """Post now, at ``scheduledAt`` or as a ``draft``: ``{ post, checks }``.

        Pass the API body as a dict, as keyword arguments, or both
        (``template_id``, ``render_id``, ``media_urls`` and ``scheduled_at``
        are accepted for their camelCase names).
        """
        payload = social_post_body(body, kwargs)
        result = self._client._request("POST", "/social/posts", json_body=payload, idempotent=False)
        return cast(SocialPostCreated, result.data)

    def list(
        self, *, status: Optional[str] = None, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> SocialPostPage:
        """``status``: ``queue``, ``drafts``, ``published``, ``attention``, ``canceled`` or a post status."""
        params = {"status": status, "cursor": cursor, "limit": limit}
        return cast(SocialPostPage, self._client._request("GET", "/social/posts", params=params).data)

    def iter(self, *, status: Optional[str] = None, limit: Optional[int] = None) -> Iterator[SocialPost]:
        cursor: Optional[str] = None
        while True:
            page = self.list(status=status, cursor=cursor, limit=limit)
            yield from page["posts"]
            cursor = page_cursor(page)
            if cursor is None:
                return

    def get(self, post_id: str) -> SocialPost:
        result = self._client._request("GET", f"/social/posts/{path_segment(post_id)}")
        return cast(SocialPost, unwrap(result, "post"))

    def cancel(self, post_id: str) -> SocialPost:
        result = self._client._request(
            "POST", f"/social/posts/{path_segment(post_id)}/cancel", idempotent=False
        )
        return cast(SocialPost, unwrap(result, "post"))

    def retry(self, post_id: str, target_id: Optional[str] = None) -> SocialPost:
        """Queue the failed accounts again (or just ``target_id``)."""
        body = {"targetId": target_id} if target_id else {}
        result = self._client._request(
            "POST", f"/social/posts/{path_segment(post_id)}/retry", json_body=body, idempotent=False
        )
        return cast(SocialPost, unwrap(result, "post"))


class SocialAnalytics(_Resource):
    """What the workspace's posts gained: impressions or views, reach, likes,
    comments, shares, saves, clicks and engagement (``None`` when the
    platforms in scope don't report one).

    Every report takes ``from_`` and ``to`` (days, in the organization's
    analytics time zone; default the last 28 days), and ``platform`` and
    ``account_id`` (one or several).
    """

    def overview(
        self,
        *,
        from_: Optional[Day] = None,
        to: Optional[Day] = None,
        platform: Optional[Many] = None,
        account_id: Optional[Many] = None,
    ) -> SocialAnalyticsOverview:
        """Totals for the range, the same for the period before, and the change in percent."""
        params = analytics_params(from_, to, platform, account_id)
        return cast(SocialAnalyticsOverview, self._client._request("GET", "/social/analytics/overview", params=params).data)

    def daily(
        self,
        *,
        from_: Optional[Day] = None,
        to: Optional[Day] = None,
        platform: Optional[Many] = None,
        account_id: Optional[Many] = None,
    ) -> SocialAnalyticsDaily:
        """One row per day, oldest first."""
        params = analytics_params(from_, to, platform, account_id)
        return cast(SocialAnalyticsDaily, self._client._request("GET", "/social/analytics/daily", params=params).data)

    def insights(
        self,
        *,
        from_: Optional[Day] = None,
        to: Optional[Day] = None,
        platform: Optional[Many] = None,
        account_id: Optional[Many] = None,
    ) -> SocialAnalyticsInsights:
        """The top posts, the best weekday and hour to post, and each platform's numbers."""
        params = analytics_params(from_, to, platform, account_id)
        return cast(SocialAnalyticsInsights, self._client._request("GET", "/social/analytics/insights", params=params).data)


class Social:
    """Social publishing: ``social.accounts``, ``social.posts`` and ``social.analytics``."""

    def __init__(self, client: Client) -> None:
        self.accounts = SocialAccounts(client)
        self.posts = SocialPosts(client)
        self.analytics = SocialAnalytics(client)


class WorkflowRuns(_Resource):
    """A workflow's runs."""

    def list(self, workflow_id: str, *, cursor: Optional[str] = None, limit: Optional[int] = None) -> WorkflowRunPage:
        """One page, newest first: ``{ runs, pagination }``."""
        params = {"cursor": cursor, "limit": limit}
        result = self._client._request("GET", f"/workflows/{path_segment(workflow_id)}/runs", params=params)
        return cast(WorkflowRunPage, result.data)

    def iter(self, workflow_id: str, *, limit: Optional[int] = None) -> Iterator[WorkflowRun]:
        cursor: Optional[str] = None
        while True:
            page = self.list(workflow_id, cursor=cursor, limit=limit)
            yield from page["runs"]
            cursor = page_cursor(page)
            if cursor is None:
                return

    def get(self, workflow_id: str, run_id: str) -> WorkflowRunWithRows:
        """The run with each row's result (at most 1,000 rows)."""
        result = self._client._request("GET", f"/workflows/{path_segment(workflow_id)}/runs/{path_segment(run_id)}")
        return cast(WorkflowRunWithRows, unwrap(result, "run"))

    def cancel(self, workflow_id: str, run_id: str) -> WorkflowRun:
        """Rows not started yet are skipped. A finished run raises :class:`ConflictError`."""
        result = self._client._request(
            "POST", f"/workflows/{path_segment(workflow_id)}/runs/{path_segment(run_id)}/cancel", idempotent=False
        )
        return cast(WorkflowRun, unwrap(result, "run"))

    def wait(
        self, workflow_id: str, run_id: str, *, timeout: float = DEFAULT_WAIT_TIMEOUT, poll_interval: float = 1.0
    ) -> WorkflowRunWithRows:
        """Poll until the run ends (``succeeded``, ``partial``, ``failed`` or ``canceled``) and return it.

        A failed run is returned, not raised: check ``status`` and each row.
        After ``timeout`` seconds raises :class:`WaitTimeoutError`.
        """
        run = self._client._poll(
            run_id,
            "Workflow run",
            lambda: self.get(workflow_id, run_id),
            lambda run: run["status"] in FINAL_RUN_STATUSES,
            timeout,
            poll_interval,
        )
        return cast(WorkflowRunWithRows, run)


class Workflows(_Resource):
    """Workflows: rows from a sheet, a file, Airtable or a webhook, each
    rendered with a template and delivered. Build them in the app or here."""

    def __init__(self, client: Client) -> None:
        super().__init__(client)
        self.runs = WorkflowRuns(client)

    def list(self) -> List[Workflow]:
        """Most recently changed first (at most 200)."""
        return cast(List[Workflow], unwrap(self._client._request("GET", "/workflows"), "workflows"))

    def get(self, workflow_id: str) -> Workflow:
        result = self._client._request("GET", f"/workflows/{path_segment(workflow_id)}")
        return cast(Workflow, unwrap(result, "workflow"))

    def create(self, body: Optional[WorkflowCreateInput] = None, **kwargs: Any) -> Workflow:
        """A draft from a ``definition``, from an Explore ``starter``, or a blank one.

        What it still needs before it can run is in ``problems``.
        """
        payload = workflow_body(body, kwargs)
        result = self._client._request("POST", "/workflows", json_body=payload, idempotent=False)
        return cast(Workflow, unwrap(result, "workflow"))

    def update(self, workflow_id: str, body: Optional[WorkflowUpdateInput] = None, **kwargs: Any) -> Workflow:
        """The fields sent replace the old ones. ``status="active"`` needs a complete definition."""
        payload = workflow_body(body, kwargs)
        result = self._client._request(
            "PATCH", f"/workflows/{path_segment(workflow_id)}", json_body=payload, idempotent=True
        )
        return cast(Workflow, unwrap(result, "workflow"))

    def delete(self, workflow_id: str) -> None:
        """Delete the workflow and its run history. Renders and posts it made stay."""
        self._client._request("DELETE", f"/workflows/{path_segment(workflow_id)}")

    def run(self, workflow_id: str, *, payload: Any = None, max_rows: Optional[int] = None) -> WorkflowRun:
        """Queue a run now (drafts too) and return it. Each row costs render credits.

        ``payload``: rows for a webhook-payload source. ``max_rows``: render
        only the first rows (a test run).
        """
        result = self._client._request(
            "POST",
            f"/workflows/{path_segment(workflow_id)}/run",
            json_body=run_body(payload, max_rows),
            idempotent=False,
        )
        return cast(WorkflowRun, unwrap(result, "run"))

    def run_and_wait(
        self,
        workflow_id: str,
        *,
        payload: Any = None,
        max_rows: Optional[int] = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: float = 1.0,
    ) -> WorkflowRunWithRows:
        """Queue a run and poll it until it ends; returns it with each row's result. Check ``status``."""
        run = self.run(workflow_id, payload=payload, max_rows=max_rows)
        return self.runs.wait(workflow_id, run["id"], timeout=timeout, poll_interval=poll_interval)

    def rotate_webhook_url(self, workflow_id: str) -> str:
        """A new incoming webhook URL. The old one stops working at once."""
        result = self._client._request(
            "POST", f"/workflows/{path_segment(workflow_id)}/webhook-token", idempotent=False
        )
        return cast(str, unwrap(result, "webhookUrl"))


class Ai(_Resource):
    """AI calls that aren't renders. They spend AI credits, refunded when a call fails."""

    def status(self) -> AiStatus:
        """Whether AI runs, the price list and the organization's AI credits. Free."""
        return cast(AiStatus, self._client._request("GET", "/ai/status").data)

    def remove_background(
        self,
        *,
        url: Optional[str] = None,
        file: Optional[FileInput] = None,
        filename: Optional[str] = None,
        name: Optional[str] = None,
    ) -> BackgroundRemoval:
        """Cut the subject out of a picture and store a transparent PNG in the brand library.

        Pass the picture's ``url``, or a ``file`` to upload (bytes, a
        ``pathlib.Path`` or a binary file object; PNG, JPEG, GIF or WebP up to
        10 MB). The answer's ``url`` is ready for an image parameter. 1 AI credit.
        """
        upload = media_source(url, file, filename)
        if upload is not None:
            result = self._client._request(
                "POST", "/images/remove-background", upload=upload, form={"name": name}, idempotent=False
            )
        else:
            body = {"url": url, **({"name": name} if name else {})}
            result = self._client._request("POST", "/images/remove-background", json_body=body, idempotent=False)
        return cast(BackgroundRemoval, result.data)

    def transcribe(
        self,
        *,
        url: Optional[str] = None,
        file: Optional[FileInput] = None,
        filename: Optional[str] = None,
        language: Optional[str] = None,
        style: Optional[Literal["standard", "short"]] = None,
        max_chars_per_line: Optional[int] = None,
        max_lines: Optional[int] = None,
        start: Optional[float] = None,
        duration: Optional[float] = None,
        offset: Optional[float] = None,
        words: Optional[bool] = None,
    ) -> Transcription:
        """The speech in an audio file or a video's sound as subtitle cues, SRT and WebVTT.

        Pass a ``url`` or a ``file`` (up to 100 MB). ``style="short"``: one
        line of up to 24 characters. ``start``/``duration`` pick a window of
        the file; ``offset`` moves the cues to where it sits on your video.
        Pass ``cues`` as ``modifications["subtitles"]`` in an MP4 render.
        1 AI credit per minute of audio.
        """
        upload = media_source(url, file, filename)
        fields = {
            "language": language,
            "style": style,
            "maxCharsPerLine": max_chars_per_line,
            "maxLines": max_lines,
            "start": start,
            "duration": duration,
            "offset": offset,
            "words": words,
        }
        if upload is not None:
            result = self._client._request("POST", "/ai/transcribe", upload=upload, form=fields, idempotent=False)
        else:
            body = {"url": url, **{k: v for k, v in fields.items() if v is not None}}
            result = self._client._request("POST", "/ai/transcribe", json_body=body, idempotent=False)
        return cast(Transcription, result.data)


class WebhookSubscriptions(_Resource):
    def list(self, *, cursor: Optional[str] = None, limit: Optional[int] = None) -> WebhookSubscriptionPage:
        params = {"cursor": cursor, "limit": limit}
        result = self._client._request("GET", "/webhooks/subscriptions", params=params)
        return cast(WebhookSubscriptionPage, result.data)

    def iter(self, *, limit: Optional[int] = None) -> Iterator[WebhookSubscription]:
        cursor: Optional[str] = None
        while True:
            page = self.list(cursor=cursor, limit=limit)
            yield from page["subscriptions"]
            cursor = page_cursor(page)
            if cursor is None:
                return

    def get(self, subscription_id: str) -> WebhookSubscription:
        result = self._client._request("GET", f"/webhooks/subscriptions/{path_segment(subscription_id)}")
        return cast(WebhookSubscription, unwrap(result, "subscription"))

    def create(self, url: str, events: Sequence[WebhookEventType]) -> WebhookSubscriptionWithSecret:
        """Subscribe ``url`` to ``events``. The ``signingSecret`` in the answer is shown once."""
        body = {"url": url, "events": list(events)}
        result = self._client._request("POST", "/webhooks/subscriptions", json_body=body, idempotent=False)
        return cast(WebhookSubscriptionWithSecret, result.data)

    def delete(self, subscription_id: str) -> None:
        self._client._request("DELETE", f"/webhooks/subscriptions/{path_segment(subscription_id)}")

    def test(self, subscription_id: str, event_type: Optional[WebhookEventType] = None) -> WebhookTestResult:
        """Queue a sample delivery (``event_type`` defaults to the subscription's first event)."""
        body = {"eventType": event_type} if event_type else {}
        result = self._client._request(
            "POST",
            f"/webhooks/subscriptions/{path_segment(subscription_id)}/test",
            json_body=body,
            idempotent=False,
        )
        return cast(WebhookTestResult, result.data)

    def rotate_secret(self, subscription_id: str) -> WebhookSubscriptionWithSecret:
        result = self._client._request(
            "POST", f"/webhooks/subscriptions/{path_segment(subscription_id)}/rotate-secret", idempotent=False
        )
        return cast(WebhookSubscriptionWithSecret, result.data)


class Webhooks:
    """``webhooks.subscriptions``, plus the signature helpers of :mod:`jamframe.webhooks`."""

    verify_signature = staticmethod(_webhooks.verify_signature)
    construct_event = staticmethod(_webhooks.construct_event)

    def __init__(self, client: Client) -> None:
        self.subscriptions = WebhookSubscriptions(client)
