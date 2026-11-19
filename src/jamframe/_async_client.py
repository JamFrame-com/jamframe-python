"""The asynchronous client: the same surface as :class:`Client`, awaited.

This file mirrors ``_client.py`` method for method; change both together.
"""

from __future__ import annotations

import asyncio
import time
from types import TracebackType
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Type,
    Union,
    cast,
)

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
from ._client import Day, Many
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


class AsyncClient(BaseClient):
    """An asynchronous client for the v1 API (``httpx.AsyncClient``).

    >>> async with AsyncClient() as client:
    ...     result = await client.render("tpl_id", {"title": "Hello"})

    ``base_url`` is the API's origin (``https://api.jamframe.com``) or its
    root (``…/v1``; the older ``…/api/v1`` also works). ``timeout`` is per
    HTTP request, in seconds.
    ``max_retries`` applies to 429s (every call) and to 5xx answers,
    connection errors and timeouts of idempotent calls. Pass your own
    ``http_client`` (an ``httpx.AsyncClient``) for proxies or custom transports;
    the SDK won't close it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.AsyncClient] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        super().__init__(api_key, base_url, timeout, max_retries, user_agent)
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=timeout)
        # Testing hooks.
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._clock: Callable[[], float] = time.monotonic

        self.designs = AsyncDesigns(self)
        self.design_jobs = AsyncDesignJobs(self)
        self.renders = AsyncRenders(self)
        self.render_jobs = AsyncRenderJobs(self)
        self.signed_urls = AsyncSignedUrls(self)
        self.social = AsyncSocial(self)
        self.workflows = AsyncWorkflows(self)
        self.ai = AsyncAi(self)
        self.webhooks = AsyncWebhooks(self)

    # ------------------------------------------------------------ lifecycle

    async def close(self) -> None:
        """Close the connection pool (only when the SDK created it)."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()

    # ------------------------------------------------------------ transport

    async def _request(
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
                response = await self._http.request(**args)
            except httpx.TimeoutException as exc:
                if idempotent and attempt < self.max_retries:
                    await self._sleep(backoff_delay(attempt, self._random))
                    attempt += 1
                    continue
                raise APITimeoutError(f"{method} {path} timed out after {self.timeout:g} s") from exc
            except httpx.TransportError as exc:
                if idempotent and attempt < self.max_retries:
                    await self._sleep(backoff_delay(attempt, self._random))
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
            await self._sleep(delay)
            attempt += 1

    # ------------------------------------------------------------ top-level calls

    async def whoami(self) -> AuthResult:
        """Check the API key: ``{ workspaceId, username }`` (``POST /auth``)."""
        return cast(AuthResult, (await self._request("POST", "/auth", idempotent=True)).data)

    async def render(
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
        result = await self._request("POST", "/render", json_body=body, idempotent=False)
        if result.status_code == 202:
            accepted = cast(RenderJobAccepted, unwrap(result, "data"))
            job = await self.render_jobs.wait(accepted["jobId"], timeout=wait_timeout, poll_interval=poll_interval)
            return {"data": cast(Any, job["result"]), "warnings": list(job.get("warnings") or [])}
        payload = cast(Dict[str, Any], result.data)
        return {"data": payload["data"], "warnings": list(payload.get("warnings") or [])}

    async def render_async(
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
        confirm with :meth:`AsyncRenderJobs.get`). The other options are as in :meth:`render`.
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
        result = await self._request("POST", "/render", json_body=body, idempotent=False)
        return cast(RenderJobAccepted, unwrap(result, "data"))

    async def render_and_wait(
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
        accepted = await self.render_async(
            template_id,
            modifications,
            response=response,
            webhook_url=webhook_url,
            expires_in=expires_in,
            translate=translate,
            lang=lang,
            ai=ai,
        )
        return await self.render_jobs.wait(accepted["jobId"], timeout=timeout, poll_interval=poll_interval)

    async def _poll(
        self,
        job_id: str,
        what: str,
        get: Callable[[], Awaitable[Mapping[str, Any]]],
        done: Callable[[Mapping[str, Any]], bool],
        timeout: float,
        poll_interval: float,
    ) -> Any:
        """Await ``get`` until ``done`` says so (it may raise to end the wait).

        Polls start ``poll_interval`` apart and grow 1.5x up to 5 s. After
        ``timeout`` seconds raises :class:`WaitTimeoutError`; the job keeps running.
        """
        deadline = self._clock() + timeout
        interval = poll_interval
        while True:
            job = await get()
            if done(job):
                return job
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WaitTimeoutError(job_id, job, timeout, what)
            await self._sleep(min(interval, remaining))
            interval = next_poll_interval(interval, poll_interval)


class _AsyncResource:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client


class AsyncRenderJobs(_AsyncResource):
    """MP4 renders and ``async`` renders (``GET /render-jobs/{id}``)."""

    async def get(self, job_id: str) -> RenderJob:
        result = await self._client._request("GET", f"/render-jobs/{path_segment(job_id)}")
        return cast(RenderJob, unwrap(result, "job"))

    async def wait(
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

        job = await self._client._poll(job_id, "Render job", lambda: self.get(job_id), done, timeout, poll_interval)
        return cast(RenderJob, job)


class AsyncDesigns(_AsyncResource):
    """Templates: the workspace's own, or the template library (``source="library"``)."""

    async def list(
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
        return cast(DesignPage, (await self._client._request("GET", "/designs", params=params)).data)

    async def iter(
        self, *, source: DesignSource = "workspace", q: Optional[str] = None, limit: Optional[int] = None
    ) -> AsyncIterator[Design]:
        """Every design (matching ``q``), fetching pages of ``limit`` (default 25, at most 100) as needed."""
        cursor: Optional[str] = None
        while True:
            page = await self.list(source=source, q=q, cursor=cursor, limit=limit)
            for item in page["designs"]:
                yield item
            cursor = page_cursor(page)
            if cursor is None:
                return

    async def get(self, design_id: str) -> DesignDetail:
        """One template (yours or a library one) with its ``pages``, ``parameters`` and ``sizes``."""
        result = await self._client._request("GET", f"/designs/{path_segment(design_id)}")
        return cast(DesignDetail, unwrap(result, "design"))

    async def generate(
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
        result = await self._client._request("POST", "/designs/generate", json_body=body, idempotent=False)
        job = cast(DesignGenerateJob, unwrap(result, "data"))
        if job["status"] == "failed":
            raise DesignJobFailedError(job)
        if job["status"] == "succeeded" and job.get("design"):
            return job
        return await self._client.design_jobs.wait(job["jobId"], timeout=wait_timeout, poll_interval=poll_interval)

    async def generate_async(
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

        Wait for it with :meth:`AsyncDesignJobs.wait`, or pass ``webhook_url``
        to receive ``{ event, job }`` when it ends (unsigned: confirm with
        :meth:`AsyncDesignJobs.get`).
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
        result = await self._client._request("POST", "/designs/generate", json_body=body, idempotent=False)
        data = unwrap(result, "data")
        return {"jobId": data["jobId"], "status": "running"}


class AsyncDesignJobs(_AsyncResource):
    """AI design generations (``GET /designs/generate/{jobId}``)."""

    async def get(self, job_id: str) -> DesignGenerateJob:
        result = await self._client._request("GET", f"/designs/generate/{path_segment(job_id)}")
        return cast(DesignGenerateJob, unwrap(result, "data"))

    async def wait(
        self, job_id: str, *, timeout: float = DEFAULT_WAIT_TIMEOUT, poll_interval: float = 1.0
    ) -> DesignGenerateJob:
        """Poll until the design is made (returned, with ``design``) or the generation fails
        (:class:`DesignJobFailedError`). After ``timeout`` seconds raises :class:`WaitTimeoutError`."""

        def done(job: Mapping[str, Any]) -> bool:
            if job["status"] == "failed":
                raise DesignJobFailedError(cast(DesignGenerateJob, job))
            return bool(job["status"] == "succeeded" and job.get("design"))

        job = await self._client._poll(
            job_id, "Design generation", lambda: self.get(job_id), done, timeout, poll_interval
        )
        return cast(DesignGenerateJob, job)


class AsyncRenders(_AsyncResource):
    """The render log (``GET /renders``, ``GET /renders/{id}``)."""

    async def get(self, render_id: str) -> RenderItem:
        """One render by id (a ``renderId`` from a render or a job's result), as the log lists it."""
        result = await self._client._request("GET", f"/renders/{path_segment(render_id)}")
        return cast(RenderItem, unwrap(result, "render"))

    async def list(
        self,
        *,
        status: Optional[str] = None,
        design_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> RenderPage:
        """One page, newest first: ``{ renders, pagination, totals }`` (totals: this period's credits)."""
        params = {"status": status, "designId": design_id, "cursor": cursor, "limit": limit}
        return cast(RenderPage, (await self._client._request("GET", "/renders", params=params)).data)

    async def iter(
        self,
        *,
        status: Optional[str] = None,
        design_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[RenderItem]:
        cursor: Optional[str] = None
        while True:
            page = await self.list(status=status, design_id=design_id, cursor=cursor, limit=limit)
            for item in page["renders"]:
                yield item
            cursor = page_cursor(page)
            if cursor is None:
                return


class AsyncSignedUrls(_AsyncResource):
    """Public, tamper-proof render URLs (``POST /signed-urls``)."""

    async def create(
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
        result = await self._client._request("POST", "/signed-urls", json_body=body, idempotent=True)
        return cast(SignedUrlResponse, result.data)

    async def rotate_key(self) -> SignedUrlKeyRotation:
        """Revoke every signed URL of the workspace at once: ``{ rotatedAt }``."""
        result = await self._client._request("POST", "/signed-urls/rotate-key", idempotent=False)
        return cast(SignedUrlKeyRotation, unwrap(result, "data"))


class AsyncSocialAccounts(_AsyncResource):
    async def list(self) -> List[SocialAccount]:
        """The workspace's connected accounts (connect them in the app)."""
        result = await self._client._request("GET", "/social/accounts")
        return cast(List[SocialAccount], unwrap(result, "accounts"))


class AsyncSocialPosts(_AsyncResource):
    async def create(self, body: Optional[SocialPostInput] = None, **kwargs: Any) -> SocialPostCreated:
        """Post now, at ``scheduledAt`` or as a ``draft``: ``{ post, checks }``.

        Pass the API body as a dict, as keyword arguments, or both
        (``template_id``, ``render_id``, ``media_urls`` and ``scheduled_at``
        are accepted for their camelCase names).
        """
        payload = social_post_body(body, kwargs)
        result = await self._client._request("POST", "/social/posts", json_body=payload, idempotent=False)
        return cast(SocialPostCreated, result.data)

    async def list(
        self, *, status: Optional[str] = None, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> SocialPostPage:
        """``status``: ``queue``, ``drafts``, ``published``, ``attention``, ``canceled`` or a post status."""
        params = {"status": status, "cursor": cursor, "limit": limit}
        return cast(SocialPostPage, (await self._client._request("GET", "/social/posts", params=params)).data)

    async def iter(self, *, status: Optional[str] = None, limit: Optional[int] = None) -> AsyncIterator[SocialPost]:
        cursor: Optional[str] = None
        while True:
            page = await self.list(status=status, cursor=cursor, limit=limit)
            for item in page["posts"]:
                yield item
            cursor = page_cursor(page)
            if cursor is None:
                return

    async def get(self, post_id: str) -> SocialPost:
        result = await self._client._request("GET", f"/social/posts/{path_segment(post_id)}")
        return cast(SocialPost, unwrap(result, "post"))

    async def cancel(self, post_id: str) -> SocialPost:
        result = await self._client._request(
            "POST", f"/social/posts/{path_segment(post_id)}/cancel", idempotent=False
        )
        return cast(SocialPost, unwrap(result, "post"))

    async def retry(self, post_id: str, target_id: Optional[str] = None) -> SocialPost:
        """Queue the failed accounts again (or just ``target_id``)."""
        body = {"targetId": target_id} if target_id else {}
        result = await self._client._request(
            "POST", f"/social/posts/{path_segment(post_id)}/retry", json_body=body, idempotent=False
        )
        return cast(SocialPost, unwrap(result, "post"))


class AsyncSocialAnalytics(_AsyncResource):
    """What the workspace's posts gained: impressions or views, reach, likes,
    comments, shares, saves, clicks and engagement (``None`` when the
    platforms in scope don't report one).

    Every report takes ``from_`` and ``to`` (days, in the organization's
    analytics time zone; default the last 28 days), and ``platform`` and
    ``account_id`` (one or several).
    """

    async def overview(
        self,
        *,
        from_: Optional[Day] = None,
        to: Optional[Day] = None,
        platform: Optional[Many] = None,
        account_id: Optional[Many] = None,
    ) -> SocialAnalyticsOverview:
        """Totals for the range, the same for the period before, and the change in percent."""
        params = analytics_params(from_, to, platform, account_id)
        result = await self._client._request("GET", "/social/analytics/overview", params=params)
        return cast(SocialAnalyticsOverview, result.data)

    async def daily(
        self,
        *,
        from_: Optional[Day] = None,
        to: Optional[Day] = None,
        platform: Optional[Many] = None,
        account_id: Optional[Many] = None,
    ) -> SocialAnalyticsDaily:
        """One row per day, oldest first."""
        params = analytics_params(from_, to, platform, account_id)
        result = await self._client._request("GET", "/social/analytics/daily", params=params)
        return cast(SocialAnalyticsDaily, result.data)

    async def insights(
        self,
        *,
        from_: Optional[Day] = None,
        to: Optional[Day] = None,
        platform: Optional[Many] = None,
        account_id: Optional[Many] = None,
    ) -> SocialAnalyticsInsights:
        """The top posts, the best weekday and hour to post, and each platform's numbers."""
        params = analytics_params(from_, to, platform, account_id)
        result = await self._client._request("GET", "/social/analytics/insights", params=params)
        return cast(SocialAnalyticsInsights, result.data)


class AsyncSocial:
    """Social publishing: ``social.accounts``, ``social.posts`` and ``social.analytics``."""

    def __init__(self, client: AsyncClient) -> None:
        self.accounts = AsyncSocialAccounts(client)
        self.posts = AsyncSocialPosts(client)
        self.analytics = AsyncSocialAnalytics(client)


class AsyncWorkflowRuns(_AsyncResource):
    """A workflow's runs."""

    async def list(
        self, workflow_id: str, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> WorkflowRunPage:
        """One page, newest first: ``{ runs, pagination }``."""
        params = {"cursor": cursor, "limit": limit}
        result = await self._client._request("GET", f"/workflows/{path_segment(workflow_id)}/runs", params=params)
        return cast(WorkflowRunPage, result.data)

    async def iter(self, workflow_id: str, *, limit: Optional[int] = None) -> AsyncIterator[WorkflowRun]:
        cursor: Optional[str] = None
        while True:
            page = await self.list(workflow_id, cursor=cursor, limit=limit)
            for item in page["runs"]:
                yield item
            cursor = page_cursor(page)
            if cursor is None:
                return

    async def get(self, workflow_id: str, run_id: str) -> WorkflowRunWithRows:
        """The run with each row's result (at most 1,000 rows)."""
        result = await self._client._request(
            "GET", f"/workflows/{path_segment(workflow_id)}/runs/{path_segment(run_id)}"
        )
        return cast(WorkflowRunWithRows, unwrap(result, "run"))

    async def cancel(self, workflow_id: str, run_id: str) -> WorkflowRun:
        """Rows not started yet are skipped. A finished run raises :class:`ConflictError`."""
        result = await self._client._request(
            "POST", f"/workflows/{path_segment(workflow_id)}/runs/{path_segment(run_id)}/cancel", idempotent=False
        )
        return cast(WorkflowRun, unwrap(result, "run"))

    async def wait(
        self, workflow_id: str, run_id: str, *, timeout: float = DEFAULT_WAIT_TIMEOUT, poll_interval: float = 1.0
    ) -> WorkflowRunWithRows:
        """Poll until the run ends (``succeeded``, ``partial``, ``failed`` or ``canceled``) and return it.

        A failed run is returned, not raised: check ``status`` and each row.
        After ``timeout`` seconds raises :class:`WaitTimeoutError`.
        """
        run = await self._client._poll(
            run_id,
            "Workflow run",
            lambda: self.get(workflow_id, run_id),
            lambda run: run["status"] in FINAL_RUN_STATUSES,
            timeout,
            poll_interval,
        )
        return cast(WorkflowRunWithRows, run)


class AsyncWorkflows(_AsyncResource):
    """Workflows: rows from a sheet, a file, Airtable or a webhook, each
    rendered with a template and delivered. Build them in the app or here."""

    def __init__(self, client: AsyncClient) -> None:
        super().__init__(client)
        self.runs = AsyncWorkflowRuns(client)

    async def list(self) -> List[Workflow]:
        """Most recently changed first (at most 200)."""
        return cast(List[Workflow], unwrap(await self._client._request("GET", "/workflows"), "workflows"))

    async def get(self, workflow_id: str) -> Workflow:
        result = await self._client._request("GET", f"/workflows/{path_segment(workflow_id)}")
        return cast(Workflow, unwrap(result, "workflow"))

    async def create(self, body: Optional[WorkflowCreateInput] = None, **kwargs: Any) -> Workflow:
        """A draft from a ``definition``, from an Explore ``starter``, or a blank one.

        What it still needs before it can run is in ``problems``.
        """
        payload = workflow_body(body, kwargs)
        result = await self._client._request("POST", "/workflows", json_body=payload, idempotent=False)
        return cast(Workflow, unwrap(result, "workflow"))

    async def update(self, workflow_id: str, body: Optional[WorkflowUpdateInput] = None, **kwargs: Any) -> Workflow:
        """The fields sent replace the old ones. ``status="active"`` needs a complete definition."""
        payload = workflow_body(body, kwargs)
        result = await self._client._request(
            "PATCH", f"/workflows/{path_segment(workflow_id)}", json_body=payload, idempotent=True
        )
        return cast(Workflow, unwrap(result, "workflow"))

    async def delete(self, workflow_id: str) -> None:
        """Delete the workflow and its run history. Renders and posts it made stay."""
        await self._client._request("DELETE", f"/workflows/{path_segment(workflow_id)}")

    async def run(self, workflow_id: str, *, payload: Any = None, max_rows: Optional[int] = None) -> WorkflowRun:
        """Queue a run now (drafts too) and return it. Each row costs render credits.

        ``payload``: rows for a webhook-payload source. ``max_rows``: render
        only the first rows (a test run).
        """
        result = await self._client._request(
            "POST",
            f"/workflows/{path_segment(workflow_id)}/run",
            json_body=run_body(payload, max_rows),
            idempotent=False,
        )
        return cast(WorkflowRun, unwrap(result, "run"))

    async def run_and_wait(
        self,
        workflow_id: str,
        *,
        payload: Any = None,
        max_rows: Optional[int] = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: float = 1.0,
    ) -> WorkflowRunWithRows:
        """Queue a run and poll it until it ends; returns it with each row's result. Check ``status``."""
        run = await self.run(workflow_id, payload=payload, max_rows=max_rows)
        return await self.runs.wait(workflow_id, run["id"], timeout=timeout, poll_interval=poll_interval)

    async def rotate_webhook_url(self, workflow_id: str) -> str:
        """A new incoming webhook URL. The old one stops working at once."""
        result = await self._client._request(
            "POST", f"/workflows/{path_segment(workflow_id)}/webhook-token", idempotent=False
        )
        return cast(str, unwrap(result, "webhookUrl"))


class AsyncAi(_AsyncResource):
    """AI calls that aren't renders. They spend AI credits, refunded when a call fails."""

    async def status(self) -> AiStatus:
        """Whether AI runs, the price list and the organization's AI credits. Free."""
        return cast(AiStatus, (await self._client._request("GET", "/ai/status")).data)

    async def remove_background(
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
            result = await self._client._request(
                "POST", "/images/remove-background", upload=upload, form={"name": name}, idempotent=False
            )
        else:
            body = {"url": url, **({"name": name} if name else {})}
            result = await self._client._request(
                "POST", "/images/remove-background", json_body=body, idempotent=False
            )
        return cast(BackgroundRemoval, result.data)

    async def transcribe(
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
            result = await self._client._request(
                "POST", "/ai/transcribe", upload=upload, form=fields, idempotent=False
            )
        else:
            body = {"url": url, **{k: v for k, v in fields.items() if v is not None}}
            result = await self._client._request("POST", "/ai/transcribe", json_body=body, idempotent=False)
        return cast(Transcription, result.data)


class AsyncWebhookSubscriptions(_AsyncResource):
    async def list(self, *, cursor: Optional[str] = None, limit: Optional[int] = None) -> WebhookSubscriptionPage:
        params = {"cursor": cursor, "limit": limit}
        result = await self._client._request("GET", "/webhooks/subscriptions", params=params)
        return cast(WebhookSubscriptionPage, result.data)

    async def iter(self, *, limit: Optional[int] = None) -> AsyncIterator[WebhookSubscription]:
        cursor: Optional[str] = None
        while True:
            page = await self.list(cursor=cursor, limit=limit)
            for item in page["subscriptions"]:
                yield item
            cursor = page_cursor(page)
            if cursor is None:
                return

    async def get(self, subscription_id: str) -> WebhookSubscription:
        result = await self._client._request("GET", f"/webhooks/subscriptions/{path_segment(subscription_id)}")
        return cast(WebhookSubscription, unwrap(result, "subscription"))

    async def create(self, url: str, events: Sequence[WebhookEventType]) -> WebhookSubscriptionWithSecret:
        """Subscribe ``url`` to ``events``. The ``signingSecret`` in the answer is shown once."""
        body = {"url": url, "events": list(events)}
        result = await self._client._request("POST", "/webhooks/subscriptions", json_body=body, idempotent=False)
        return cast(WebhookSubscriptionWithSecret, result.data)

    async def delete(self, subscription_id: str) -> None:
        await self._client._request("DELETE", f"/webhooks/subscriptions/{path_segment(subscription_id)}")

    async def test(self, subscription_id: str, event_type: Optional[WebhookEventType] = None) -> WebhookTestResult:
        """Queue a sample delivery (``event_type`` defaults to the subscription's first event)."""
        body = {"eventType": event_type} if event_type else {}
        result = await self._client._request(
            "POST",
            f"/webhooks/subscriptions/{path_segment(subscription_id)}/test",
            json_body=body,
            idempotent=False,
        )
        return cast(WebhookTestResult, result.data)

    async def rotate_secret(self, subscription_id: str) -> WebhookSubscriptionWithSecret:
        result = await self._client._request(
            "POST", f"/webhooks/subscriptions/{path_segment(subscription_id)}/rotate-secret", idempotent=False
        )
        return cast(WebhookSubscriptionWithSecret, result.data)


class AsyncWebhooks:
    """``webhooks.subscriptions``, plus the signature helpers of :mod:`jamframe.webhooks`."""

    verify_signature = staticmethod(_webhooks.verify_signature)
    construct_event = staticmethod(_webhooks.construct_event)

    def __init__(self, client: AsyncClient) -> None:
        self.subscriptions = AsyncWebhookSubscriptions(client)
