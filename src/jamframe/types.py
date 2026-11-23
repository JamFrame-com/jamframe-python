"""Typed shapes of the v1 API, transcribed from the OpenAPI spec
(``spec/openapi.yaml``; ``tests/test_spec.py`` checks them against it).

Every SDK method returns the JSON the API sends, as plain ``dict`` and
``list`` objects; these ``TypedDict`` classes only describe them for type
checkers and editors. Keys keep the API's camelCase spelling.

Fields that the API may omit are declared on a ``total=False`` class;
fields that are always present (possibly ``None``) are required.
"""

from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

# ---------------------------------------------------------------- enums

WebhookEventType = Literal[
    "design.created",
    "design.updated",
    "design.deleted",
    "render.completed",
    "render.failed",
    "social.post.published",
    "social.post.failed",
    "workflow.run.completed",
    "workflow.run.failed",
]
"""An event a webhook subscription can listen to (also the ``X-Webhook-Event`` header)."""

ApiErrorCode = Literal[
    "MISSING_API_KEY",
    "INVALID_API_KEY",
    "EXPIRED_API_KEY",
    "ORG_ON_HOLD",
    "ORG_SUSPENDED",
    "RATE_LIMITED",
    "VALIDATION_ERROR",
    "INVALID_REQUEST",
    "BODY_TOO_LARGE",
    "UNSUPPORTED_MEDIA_TYPE",
    "NOT_FOUND",
    "CONFLICT",
    "QUOTA_EXCEEDED",
    "WORKSPACE_QUOTA_EXCEEDED",
    "PLAN_LIMIT_EXCEEDED",
    "WEBHOOK_URL_INVALID",
    "WEBHOOK_LIMIT_REACHED",
    "VIDEO_UNAVAILABLE",
    "AI_UNAVAILABLE",
    "AI_CONTENT_REFUSED",
    "AI_TIMEOUT",
    "AI_RATE_LIMITED",
    "AI_BAD_OUTPUT",
    "AI_BAD_REQUEST",
    "AI_UPSTREAM_ERROR",
    "AI_CREDITS_EXCEEDED",
    "INTERNAL",
]
"""Stable codes of the error envelope's ``error`` field. Other strings may appear.

``INVALID_REQUEST`` (400) is a body that isn't JSON, ``BODY_TOO_LARGE`` (413)
a body over 5 MB, ``UNSUPPORTED_MEDIA_TYPE`` (415) a charset or encoding the
API can't read; the ``AI_*`` codes come from the AI calls and AI layers.
"""

OutputFormat = Literal["png", "jpeg", "jpg", "webp", "pdf", "mp4"]
"""``response.format`` values. ``jpg`` is an alias of ``jpeg``."""

ResultFormat = Literal["png", "jpeg", "webp", "pdf", "mp4"]
"""The format a render reports (``jpg`` requests report ``jpeg``)."""

DesignSource = Literal["workspace", "library"]
Orientation = Literal["landscape", "portrait", "square"]
ParameterType = Literal["text", "image", "qr", "shape", "video", "audio", "subtitles", "variable"]
RenderJobStatus = Literal["queued", "running", "succeeded", "failed"]
RenderSource = Literal["api", "editor", "signed-url", "dynamic-url", "form", "workflow", "shopify", "webflow"]
SocialPlatform = Literal[
    "instagram", "facebook", "linkedin", "linkedin_page", "x", "tiktok", "youtube", "pinterest", "bluesky", "buffer"
]
"""``linkedin`` is a profile, ``linkedin_page`` a company page, ``facebook`` a Page,
``pinterest`` posts Pins to a board, ``buffer`` is a channel of a connected Buffer
account (the post goes into its Buffer queue)."""
CmykProfile = Literal[
    "fogra39",
    "fogra51",
    "fogra52",
    "fogra47",
    "fogra29",
    "fogra40",
    "fogra45",
    "ifra26",
    "gracol2013",
    "gracol2013-uncoated",
    "swop2013",
    "japan2011",
    "gracol2006",
]
"""A printing condition for ``response["cmyk"]``. Ask your print shop which one to use."""
CompressPreset = Literal["ebook", "screen", "printer", "prepress"]
WorkflowRunStatus = Literal["queued", "running", "succeeded", "partial", "failed", "canceled"]
"""``succeeded``, ``partial``, ``failed`` and ``canceled`` are final."""
SocialMetricName = Literal["impressions", "reach", "likes", "comments", "shares", "saves", "clicks", "engagement"]
SocialPostStatus = Literal[
    "draft", "scheduled", "queued", "rendering", "publishing", "published", "partial", "failed", "canceled"
]
SocialTargetStatus = Literal["draft", "queued", "rendering", "publishing", "published", "failed", "canceled"]

# ---------------------------------------------------------------- errors and paging


class _ApiErrorBodyRequired(TypedDict):
    error: str
    message: str


class ApiErrorBody(_ApiErrorBodyRequired, total=False):
    """The envelope of every non-2xx answer: ``{ error, message, detail? }``."""

    detail: Dict[str, Any]


class Pagination(TypedDict):
    nextCursor: Optional[str]
    hasMore: bool


class _AuthResultRequired(TypedDict):
    username: str


class AuthResult(_AuthResultRequired, total=False):
    """``POST /auth``: the key's workspace and a label for it."""

    workspaceId: str


# ---------------------------------------------------------------- designs


class Design(TypedDict):
    """A template summary. Also the ``data`` of ``design.*`` webhook events.

    ``pages`` is the page count (0 only for an unreadable template).
    ``animated`` marks a video template (animated layers, a video layer, a
    soundtrack, set page durations or transitions): render it as ``mp4``.
    """

    id: str
    name: str
    orientation: Orientation
    pages: int
    animated: bool
    createdAt: str
    updatedAt: str


class _TemplateParameterRequired(TypedDict):
    key: str
    type: ParameterType
    pages: List[int]
    example: str


class TemplateParameter(_TemplateParameterRequired, total=False):
    """One key a render's ``modifications`` can set.

    ``prompt``: layers filled by AI at render time. Send ``<key>.prompt`` to
    change it, or a plain value to skip the AI.
    """

    prompt: str


class TemplateSize(TypedDict):
    id: str
    name: str
    width: int
    height: int


class DesignDetail(Design):
    """``GET /designs/{id}``: the template with what a render can set."""

    parameters: List[TemplateParameter]
    sizes: List[TemplateSize]


class DesignPage(TypedDict):
    designs: List[Design]
    pagination: Pagination


class BrandValues(TypedDict, total=False):
    """Explicit brand values for an AI design: hex colours (primary first), fonts (heading first), a logo URL."""

    colors: List[str]
    fonts: List[str]
    logo: str


# ``async`` is a Python keyword, so the optional half uses the functional syntax.
_DesignGenerateRequestOptional = TypedDict(
    "_DesignGenerateRequestOptional",
    {
        "size": str,
        "width": int,
        "height": int,
        "pages": int,
        "video": bool,
        "brand": Union[bool, BrandValues],
        "referenceImageUrl": str,
        "name": str,
        "async": bool,
        "webhookUrl": str,
    },
    total=False,
)


class DesignGenerateRequest(_DesignGenerateRequestOptional):
    """The body of ``POST /designs/generate``."""

    prompt: str


class GeneratedDesign(DesignDetail, total=False):
    """A design made by ``POST /designs/generate``, with a link to the editor."""

    editorUrl: str


class _DesignGenerateJobRequired(TypedDict):
    jobId: str
    status: Literal["running", "succeeded", "failed"]
    createdAt: str


class DesignGenerateJob(_DesignGenerateJobRequired, total=False):
    """A design generation: ``running``, then ``succeeded`` (``design``) or ``failed`` (``error``).

    ``creditsCharged`` AI credits are refunded when the generation fails.
    """

    design: GeneratedDesign
    message: str
    changes: List[str]
    creditsCharged: int
    mock: bool
    error: ApiErrorBody
    finishedAt: Optional[str]


class DesignGenerateAccepted(TypedDict):
    """The answer of :meth:`Designs.generate_async`."""

    jobId: str
    status: Literal["running"]


class DesignGenerateCallback(TypedDict):
    """The body POSTed to a generation's ``webhookUrl`` (unsigned)."""

    event: Literal["design.generate.completed", "design.generate.failed"]
    job: DesignGenerateJob


# ---------------------------------------------------------------- render


class SubtitleCue(TypedDict):
    start: float
    end: float
    text: str


ModificationValue = Union[str, int, float, bool, None, List[SubtitleCue]]
Modifications = Dict[str, ModificationValue]
"""Keys: ``title``, ``page2@title``, ``title.color``, ``page2@title.color``, ``audio``, ``subtitles`` …"""


class CmykOptions(TypedDict, total=False):
    profile: CmykProfile


class CompressOptions(TypedDict, total=False):
    preset: CompressPreset
    dpi: int


class RenderOptions(TypedDict, total=False):
    """``response`` of a render request. Every field is optional.

    ``cmyk`` and ``compress`` are **experimental** and PDF only: CMYK colours
    for print (``True`` uses ``fogra39``) and a smaller file (``True`` uses the
    ``ebook`` preset). Their options and output may still change.
    """

    type: Literal["url", "base64"]
    format: OutputFormat
    scale: int
    quality: int
    includePages: List[int]
    size: str
    sizes: List[str]
    subtitles: Literal["srt", "vtt"]
    cmyk: Union[bool, CmykOptions]
    compress: Union[bool, CompressOptions]


class RenderAiOptions(TypedDict, total=False):
    """How a render's AI layers and translation behave: ``fail`` (default) or ``fallback``."""

    onError: Literal["fail", "fallback"]


# ``async`` is a Python keyword, so the optional half uses the functional syntax.
_RenderRequestOptional = TypedDict(
    "_RenderRequestOptional",
    {
        "modifications": Modifications,
        "response": RenderOptions,
        "async": bool,
        "webhookUrl": str,
        "expiresIn": int,
        "translate": bool,
        "lang": str,
        "ai": RenderAiOptions,
    },
    total=False,
)


class RenderRequest(_RenderRequestOptional):
    """The body of ``POST /render``.

    ``templateId`` is a workspace template's id, a template-library
    template's id, or a library template's slug (``tool-quote-card-paper``).
    """

    templateId: str


class _SizedContentRequired(TypedDict):
    size: str
    name: str
    width: int
    height: int
    pages: List[int]


class SizedContent(_SizedContentRequired, total=False):
    """One size of a ``response.sizes`` render: ``url`` for one file, ``urls`` for a carousel."""

    url: str
    urls: List[str]


class _RenderResultRequired(TypedDict):
    content: Union[str, List[str], List[SizedContent]]
    type: Literal["url", "base64"]
    format: ResultFormat
    pages: List[int]
    credits: int
    renderId: str
    responseTime: int


class RenderResult(_RenderResultRequired, total=False):
    """The ``data`` of a render, and a succeeded render job's ``result``.

    ``url`` renders also carry ``urls`` (every file's link in one list) and
    ``expiresAt`` (when the links stop working). ``aiCredits``: AI credits
    for AI layers or ``translate`` (0 when cached).
    """

    urls: List[str]
    expiresAt: str
    duration: float
    subtitles: str
    aiCredits: int


class RenderResponse(TypedDict):
    """What :meth:`Client.render` returns. ``warnings`` is always a list (empty when none)."""

    data: RenderResult
    warnings: List[str]


class RenderJobAccepted(TypedDict):
    """The ``data`` of a ``202`` render answer."""

    jobId: str
    status: Literal["queued", "running"]


class _RenderJobRequired(TypedDict):
    id: str
    status: RenderJobStatus
    progress: float
    format: ResultFormat
    templateId: Optional[str]
    createdAt: str
    startedAt: Optional[str]
    finishedAt: Optional[str]
    result: Optional[RenderResult]
    error: Optional[ApiErrorBody]


class RenderJob(_RenderJobRequired, total=False):
    """A render job: an MP4 render or an ``async`` render. Also the ``data`` of ``render.*`` events."""

    warnings: List[str]


class RenderJobCallback(TypedDict):
    """The body POSTed to a render request's ``webhookUrl`` (unsigned)."""

    event: Literal["render.completed", "render.failed"]
    job: RenderJob


class _RenderOutputFileRequired(TypedDict):
    url: str
    page: Optional[int]


class RenderOutputFile(_RenderOutputFileRequired, total=False):
    """A stored file of the render log. ``url`` works until ``expiresAt`` (5 minutes)."""

    expiresAt: str


class RenderItem(TypedDict):
    """One row of the render log (``GET /renders``)."""

    id: str
    createdAt: str
    designId: Optional[str]
    designName: Optional[str]
    source: RenderSource
    format: ResultFormat
    pages: int
    credits: int
    status: Literal["succeeded", "failed"]
    durationMs: int
    outputs: List[RenderOutputFile]
    error: Optional[str]
    cached: bool


class RenderTotals(TypedDict):
    periodStart: str
    periodEnd: str
    creditsUsed: int
    creditsQuota: Optional[int]
    creditsRemaining: Optional[int]
    workspaceCreditsUsed: int


class RenderPage(TypedDict):
    renders: List[RenderItem]
    pagination: Pagination
    totals: RenderTotals


# ---------------------------------------------------------------- signed URLs


class SignedUrlOptions(TypedDict, total=False):
    format: Literal["png", "jpeg", "jpg", "webp", "pdf"]
    scale: int
    quality: int
    includePages: List[int]
    size: str


class _SignedUrlRequestRequired(TypedDict):
    templateId: str


class SignedUrlRequest(_SignedUrlRequestRequired, total=False):
    modifications: Dict[str, Union[str, int, float, bool, None]]
    response: SignedUrlOptions
    expiresIn: int
    cacheSeconds: int


class SignedUrl(TypedDict):
    url: str
    templateId: str
    format: Literal["png", "jpeg", "webp", "pdf"]
    expiresAt: str


class _SignedUrlResponseRequired(TypedDict):
    data: SignedUrl


class SignedUrlResponse(_SignedUrlResponseRequired, total=False):
    warnings: List[str]


class SignedUrlKeyRotation(TypedDict):
    rotatedAt: str


# ---------------------------------------------------------------- social


class _SocialAccountRequired(TypedDict):
    id: str
    platform: SocialPlatform
    platformName: str
    name: str
    status: Literal["active", "needs_reconnect"]
    sandbox: bool
    createdAt: str


class SocialAccount(_SocialAccountRequired, total=False):
    username: Optional[str]
    avatarUrl: Optional[str]
    statusReason: Optional[str]
    reconnectBy: Optional[str]
    lastPostedAt: Optional[str]


class _SocialPostAccountRequired(TypedDict):
    id: str


class SocialPostAccount(_SocialPostAccountRequired, total=False):
    """An account of a post that overrides the caption or options."""

    caption: str
    options: Dict[str, Any]


class InstagramPostOptions(TypedDict, total=False):
    shareToFeed: bool


class FacebookPostOptions(TypedDict, total=False):
    title: str


class LinkedInPostOptions(TypedDict, total=False):
    firstComment: str
    documentTitle: str
    visibility: Literal["PUBLIC", "CONNECTIONS"]


class XPostOptions(TypedDict, total=False):
    pass


class TikTokPostOptions(TypedDict, total=False):
    """``draft``: send to the creator's TikTok inbox instead of posting (no ``privacyLevel`` needed)."""

    draft: bool
    privacyLevel: Literal["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"]
    allowComment: bool
    allowDuet: bool
    allowStitch: bool
    commercialContent: bool
    yourBrand: bool
    brandedContent: bool
    aiGenerated: bool
    title: str
    autoAddMusic: bool
    videoCoverTimestampMs: int


class YouTubePostOptions(TypedDict, total=False):
    title: str
    privacy: Literal["public", "unlisted", "private"]
    madeForKids: bool
    tags: List[str]
    categoryId: str
    aiGenerated: bool


class PinterestPostOptions(TypedDict, total=False):
    """The caption is the Pin's description. Set ``boardId`` per account to pick a board."""

    boardId: str
    title: str
    link: str
    altText: str


class BlueskyPostOptions(TypedDict, total=False):
    altText: str


class BufferPostOptions(TypedDict, total=False):
    """``mode``: ``queue`` (default, the next free slot), ``next``, ``now`` or ``draft``."""

    mode: Literal["queue", "next", "now", "draft"]
    instagramType: Literal["post", "story", "reel"]
    facebookType: Literal["post", "story", "reel"]
    pinterestBoardId: str
    title: str
    link: str
    youtubeCategoryId: str


class SocialPostOptions(TypedDict, total=False):
    """Per-platform options of a post."""

    instagram: InstagramPostOptions
    facebook: FacebookPostOptions
    linkedin: LinkedInPostOptions
    linkedin_page: LinkedInPostOptions
    x: XPostOptions
    tiktok: TikTokPostOptions
    youtube: YouTubePostOptions
    pinterest: PinterestPostOptions
    bluesky: BlueskyPostOptions
    buffer: BufferPostOptions


class SocialPostRenderOptions(TypedDict, total=False):
    format: OutputFormat
    scale: int
    quality: int
    includePages: List[int]


class SocialPostInput(TypedDict, total=False):
    """The body of ``POST /social/posts``: one media source, the accounts, the caption and when."""

    templateId: str
    modifications: Modifications
    response: SocialPostRenderOptions
    renderId: str
    mediaUrls: List[str]
    accounts: List[Union[str, SocialPostAccount]]
    caption: str
    captions: Dict[str, str]
    options: SocialPostOptions
    scheduledAt: str
    timezone: str
    draft: bool


class _SocialMediaRequired(TypedDict):
    kind: Literal["image", "video", "pdf"]
    contentType: str
    url: str


class SocialMedia(_SocialMediaRequired, total=False):
    """A rendered file's ``url`` is a fresh link on every read that works for an hour (``expiresAt``)."""

    expiresAt: str
    bytes: int
    width: int
    height: int
    durationSec: float
    pages: int


class SocialTargetError(TypedDict):
    kind: Literal["auth", "transient", "permanent", "validation"]
    code: str
    message: str


class _SocialPostTargetRequired(TypedDict):
    id: str
    platform: SocialPlatform
    accountName: str
    status: SocialTargetStatus
    caption: str
    warnings: List[str]
    attempts: int


class SocialPostTarget(_SocialPostTargetRequired, total=False):
    """One account of a post."""

    accountId: Optional[str]
    permalink: Optional[str]
    externalId: Optional[str]
    error: Optional[SocialTargetError]
    nextAttemptAt: Optional[str]
    publishedAt: Optional[str]


class _SocialPostRequired(TypedDict):
    id: str
    status: SocialPostStatus
    caption: str
    targets: List[SocialPostTarget]
    createdAt: str


class SocialPost(_SocialPostRequired, total=False):
    captions: Dict[str, str]
    options: Dict[str, Any]
    media: Optional[List[SocialMedia]]
    mediaSource: Dict[str, Any]
    renderJobId: Optional[str]
    renderId: Optional[str]
    scheduledAt: Optional[str]
    timezone: Optional[str]
    source: Literal["app", "api", "mcp"]
    error: Optional[ApiErrorBody]
    updatedAt: str
    finishedAt: Optional[str]


class SocialIssue(TypedDict):
    level: Literal["error", "warning"]
    code: str
    message: str


class SocialTargetIssues(TypedDict):
    accountId: str
    platform: SocialPlatform
    accountName: str
    errors: List[SocialIssue]
    warnings: List[SocialIssue]


class SocialPostCreated(TypedDict):
    """``POST /social/posts`` (201): the post and per-account warnings."""

    post: SocialPost
    checks: List[SocialTargetIssues]


class SocialPostPage(TypedDict):
    posts: List[SocialPost]
    pagination: Pagination


class SocialPostEvent(TypedDict):
    """The ``data`` of ``social.post.*`` events: the post and the account that finished."""

    post: SocialPost
    target: SocialPostTarget


# ---------------------------------------------------------------- social analytics


class SocialMetricValues(TypedDict):
    """Metrics; ``None`` when the platform doesn't report one.

    ``impressions`` holds impressions or views; ``engagement`` is likes +
    comments + shares + saves.
    """

    impressions: Optional[float]
    reach: Optional[float]
    likes: Optional[float]
    comments: Optional[float]
    shares: Optional[float]
    saves: Optional[float]
    clicks: Optional[float]
    engagement: Optional[float]


# ``from`` is a Python keyword, so these use the functional syntax.
SocialAnalyticsRange = TypedDict(
    "SocialAnalyticsRange",
    {"from": str, "to": str, "days": int, "timezone": str, "historyDays": int, "limited": bool},
)
"""The days asked for, in the organization's analytics time zone. ``limited``:
the range started before the plan's history (``historyDays``) and ``from`` was moved up."""

DayRange = TypedDict("DayRange", {"from": str, "to": str})


class SocialAnalyticsOverview(TypedDict):
    range: SocialAnalyticsRange
    previousRange: DayRange
    available: List[SocialMetricName]
    totals: SocialMetricValues
    previous: SocialMetricValues
    change: SocialMetricValues
    """Percent change from ``previous`` to ``totals``."""
    engagementRate: Optional[float]
    """Engagement ÷ impressions, in percent."""
    postsPublished: int
    previousPostsPublished: int


class SocialAnalyticsDay(SocialMetricValues):
    day: str
    postsPublished: int


class SocialAnalyticsDaily(TypedDict):
    range: SocialAnalyticsRange
    available: List[SocialMetricName]
    days: List[SocialAnalyticsDay]


class _SocialPostMetricsRequired(TypedDict):
    id: str
    accountId: str
    accountName: str
    platform: SocialPlatform
    externalId: str
    origin: Literal["app", "imported"]
    caption: str
    publishedAt: str
    metrics: SocialMetricValues
    status: Literal["active", "done", "unavailable", "gone"]


class SocialPostMetrics(_SocialPostMetricsRequired, total=False):
    """One post on one account and its lifetime numbers so far.

    ``status``: ``done`` past 90 days, ``unavailable`` when the platform gives
    no numbers, ``gone`` when deleted there.
    """

    platformName: str
    postId: Optional[str]
    permalink: Optional[str]
    mediaType: Optional[str]
    thumbnailUrl: Optional[str]
    engagementRate: Optional[float]
    statusReason: Optional[str]
    lastFetchedAt: Optional[str]


class BestTimeWeekday(TypedDict):
    weekday: int
    name: str
    posts: int
    average: Optional[float]


class BestTimeHour(TypedDict):
    hour: int
    posts: int
    average: Optional[float]


class Weekday(TypedDict):
    weekday: int
    name: str


class BestTimes(TypedDict):
    """``weekdays`` run Sunday (0) to Saturday (6), ``hours`` 0 to 23."""

    basedOnPosts: int
    enoughData: bool
    metric: Literal["engagement", "impressions"]
    timezone: str
    weekdays: List[BestTimeWeekday]
    hours: List[BestTimeHour]
    bestWeekday: Optional[Weekday]
    bestHour: Optional[int]


class PlatformSummary(TypedDict):
    platform: SocialPlatform
    platformName: str
    posts: int
    totals: SocialMetricValues
    averages: SocialMetricValues
    engagementRate: Optional[float]


class SocialAnalyticsInsights(TypedDict):
    range: SocialAnalyticsRange
    topPosts: List[SocialPostMetrics]
    bestTimes: BestTimes
    platforms: List[PlatformSummary]


# ---------------------------------------------------------------- AI


class AiPrice(TypedDict):
    """An operation costs ``credits`` per started block of ``per`` units."""

    operation: Literal[
        "text",
        "design",
        "vision",
        "image",
        "image_hd",
        "background_local",
        "background_model",
        "tts",
        "stt",
        "video",
        "embedding",
    ]
    credits: int
    per: int
    unit: Literal["word", "call", "image", "character", "second", "input"]
    label: str


class AiCredits(TypedDict):
    """The organization's AI credits this billing period (``None``: unlimited).

    ``oneTime`` is true on the Free plan: its credits are given once and don't
    renew at ``periodEnd``.
    """

    planCode: str
    used: int
    reserved: int
    limit: Optional[int]
    remaining: Optional[int]
    periodStart: str
    periodEnd: str
    active: bool
    oneTime: bool


class AiStatus(TypedDict):
    """``status``: ``live`` (AI runs), ``mock`` (placeholder output) or ``off`` (``503 AI_UNAVAILABLE``)."""

    status: Literal["live", "mock", "off"]
    models: Dict[str, str]
    prices: List[AiPrice]
    credits: AiCredits


class _BrandAssetRequired(TypedDict):
    id: str
    kind: Literal["image", "video", "audio", "font", "color"]
    name: str
    url: str
    tags: List[str]


class BrandAsset(_BrandAssetRequired, total=False):
    """A file in the workspace's brand library."""

    mimeType: Optional[str]
    size: Optional[int]
    createdAt: str
    meta: Optional[Dict[str, Any]]


class _BackgroundRemovalRequired(TypedDict):
    url: str
    method: Literal["local", "model"]
    model: str
    scaled: bool
    creditsCharged: int
    asset: BrandAsset


class BackgroundRemoval(_BackgroundRemovalRequired, total=False):
    """The cut-out, a transparent PNG in the brand library; ``url`` is ready for ``modifications``."""

    width: Optional[int]
    height: Optional[int]


class TranscriptWord(TypedDict):
    word: str
    start: float
    end: float


class _TranscriptionRequired(TypedDict):
    duration: float
    text: str
    cues: List[SubtitleCue]
    srt: str
    vtt: str
    creditsCharged: int


class Transcription(_TranscriptionRequired, total=False):
    """Subtitle cues (pass ``cues`` as ``modifications["subtitles"]``), SRT and WebVTT."""

    language: Optional[str]
    words: List[TranscriptWord]
    model: str
    mock: bool


# ---------------------------------------------------------------- workflows


class _WorkflowTriggerRequired(TypedDict):
    kind: Literal["manual", "schedule", "webhook", "new_rows"]


class WorkflowTrigger(_WorkflowTriggerRequired, total=False):
    frequency: Literal["hourly", "daily", "weekly", "monthly", "custom"]
    minute: int
    time: str
    dayOfWeek: int
    dayOfMonth: int
    cron: str
    timezone: str
    intervalMinutes: int


class _WorkflowActionRequired(TypedDict):
    kind: Literal["render"]
    templateId: str


class WorkflowAction(_WorkflowActionRequired, total=False):
    """The render: ``mapping`` is template parameter → value with ``{{Column}}`` placeholders."""

    mapping: Dict[str, str]
    format: ResultFormat
    scale: int
    quality: int
    includePages: List[int]


class _WorkflowDefinitionRequired(TypedDict):
    version: Literal[1]
    trigger: WorkflowTrigger
    source: Dict[str, Any]
    action: WorkflowAction


class WorkflowDefinition(_WorkflowDefinitionRequired, total=False):
    """A workflow's steps (version 1).

    ``source``, ``transforms`` and ``deliver`` steps are dicts with a ``kind``;
    their fields are in the API reference ("WorkflowDefinition").
    """

    transforms: List[Dict[str, Any]]
    deliver: List[Dict[str, Any]]


class WorkflowSecret(TypedDict, total=False):
    """A stored secret, shown masked. Send it back to keep the value, or ``""`` to clear it."""

    set: Literal[True]
    tail: str


class WorkflowCreateInput(TypedDict, total=False):
    """``POST /workflows``: a ``definition``, an Explore ``starter``, or neither (a blank draft)."""

    name: str
    description: Optional[str]
    definition: WorkflowDefinition
    starter: str


class WorkflowUpdateInput(TypedDict, total=False):
    """``PATCH /workflows/{id}``. ``status: "active"`` needs a complete definition."""

    name: str
    description: Optional[str]
    definition: WorkflowDefinition
    status: Literal["draft", "active"]


class WorkflowRunRequest(TypedDict, total=False):
    payload: Any
    maxRows: int


class WorkflowRunError(TypedDict):
    code: str
    message: str


class _WorkflowRunRequired(TypedDict):
    id: str
    workflowId: str
    trigger: Literal["manual", "schedule", "webhook", "new_rows", "api", "mcp"]
    status: WorkflowRunStatus
    rowsTotal: int
    rowsSucceeded: int
    rowsFailed: int
    rowsSkipped: int
    credits: int
    warnings: List[str]
    createdAt: str


class WorkflowRun(_WorkflowRunRequired, total=False):
    """A run of a workflow. ``credits``: render credits its renders cost."""

    error: Optional[WorkflowRunError]
    startedAt: Optional[str]
    finishedAt: Optional[str]


class _WorkflowRowDeliveryRequired(TypedDict):
    stepId: str
    kind: str
    status: Literal["succeeded", "failed", "sandbox"]


class WorkflowRowDelivery(_WorkflowRowDeliveryRequired, total=False):
    message: str
    url: str


class _WorkflowRunRowRequired(TypedDict):
    index: int
    status: Literal["pending", "running", "succeeded", "partial", "failed", "skipped"]
    values: Dict[str, str]
    urls: List[str]
    credits: int
    deliveries: List[WorkflowRowDelivery]


class WorkflowRunRow(_WorkflowRunRowRequired, total=False):
    """One row of a run. ``urls`` are fresh links on every read that work for an hour."""

    rowNumber: Optional[int]
    modifications: Optional[Dict[str, str]]
    renderId: Optional[str]
    error: Optional[WorkflowRunError]


class WorkflowRunWithRows(WorkflowRun):
    """A run with each row's result (at most 1,000 rows)."""

    rows: List[WorkflowRunRow]


class WorkflowRunPage(TypedDict):
    runs: List[WorkflowRun]
    pagination: Pagination


class WorkflowRunEvent(WorkflowRun):
    """The ``data`` of ``workflow.run.*`` webhook events."""

    workflowName: str


class WorkflowProblem(TypedDict):
    stepId: str
    message: str


class WorkflowTriggerSummary(TypedDict):
    kind: str
    summary: str


class _WorkflowRequired(TypedDict):
    id: str
    name: str
    status: Literal["draft", "active"]
    definition: WorkflowDefinition
    steps: int
    problems: List[WorkflowProblem]
    trigger: WorkflowTriggerSummary
    createdAt: str
    updatedAt: str


class Workflow(_WorkflowRequired, total=False):
    """A workflow. ``problems`` lists what stops it from running (empty when ready).

    ``webhookUrl`` (webhook trigger only) starts runs for anyone who has it.
    """

    description: Optional[str]
    nextRunAt: Optional[str]
    lastRunAt: Optional[str]
    lastRun: Optional[WorkflowRun]
    webhookUrl: Optional[str]


# ---------------------------------------------------------------- webhooks


class WebhookSubscription(TypedDict):
    """``status``: ``suspended`` after 20 failed deliveries in a row; ``revoked``
    once its creator lost access (create a new one)."""

    id: str
    url: str
    events: List[WebhookEventType]
    status: Literal["active", "suspended", "revoked"]
    secretTail: str
    failureCount: int
    lastFiredAt: Optional[str]
    createdAt: str
    updatedAt: str


class WebhookSubscriptionPage(TypedDict):
    subscriptions: List[WebhookSubscription]
    pagination: Pagination


class WebhookSubscriptionWithSecret(TypedDict):
    """Create and rotate-secret answers. ``signingSecret`` is shown once: store it."""

    subscription: WebhookSubscription
    signingSecret: str


class WebhookEvent(TypedDict):
    """The body of every signed webhook delivery."""

    id: str
    type: WebhookEventType
    createdAt: str
    data: Union[Design, RenderJob, SocialPostEvent, WorkflowRunEvent]


class WebhookDelivery(TypedDict):
    id: str
    eventType: WebhookEventType
    createdAt: str


class WebhookTestResult(TypedDict):
    delivery: WebhookDelivery
    sample: WebhookEvent


__all__ = [
    "AiCredits",
    "AiPrice",
    "AiStatus",
    "ApiErrorBody",
    "ApiErrorCode",
    "AuthResult",
    "BackgroundRemoval",
    "BestTimeHour",
    "BestTimeWeekday",
    "BestTimes",
    "BlueskyPostOptions",
    "BrandAsset",
    "BrandValues",
    "BufferPostOptions",
    "CmykOptions",
    "CmykProfile",
    "CompressOptions",
    "CompressPreset",
    "DayRange",
    "Design",
    "DesignDetail",
    "DesignGenerateAccepted",
    "DesignGenerateCallback",
    "DesignGenerateJob",
    "DesignGenerateRequest",
    "DesignPage",
    "DesignSource",
    "FacebookPostOptions",
    "GeneratedDesign",
    "InstagramPostOptions",
    "LinkedInPostOptions",
    "ModificationValue",
    "Modifications",
    "Orientation",
    "OutputFormat",
    "Pagination",
    "ParameterType",
    "PinterestPostOptions",
    "PlatformSummary",
    "RenderAiOptions",
    "RenderItem",
    "RenderJob",
    "RenderJobAccepted",
    "RenderJobCallback",
    "RenderJobStatus",
    "RenderOptions",
    "RenderOutputFile",
    "RenderPage",
    "RenderRequest",
    "RenderResponse",
    "RenderResult",
    "RenderSource",
    "RenderTotals",
    "ResultFormat",
    "SignedUrl",
    "SignedUrlKeyRotation",
    "SignedUrlOptions",
    "SignedUrlRequest",
    "SignedUrlResponse",
    "SizedContent",
    "SocialAccount",
    "SocialAnalyticsDaily",
    "SocialAnalyticsDay",
    "SocialAnalyticsInsights",
    "SocialAnalyticsOverview",
    "SocialAnalyticsRange",
    "SocialIssue",
    "SocialMedia",
    "SocialMetricName",
    "SocialMetricValues",
    "SocialPlatform",
    "SocialPost",
    "SocialPostAccount",
    "SocialPostCreated",
    "SocialPostEvent",
    "SocialPostInput",
    "SocialPostMetrics",
    "SocialPostOptions",
    "SocialPostPage",
    "SocialPostRenderOptions",
    "SocialPostStatus",
    "SocialPostTarget",
    "SocialTargetError",
    "SocialTargetIssues",
    "SocialTargetStatus",
    "SubtitleCue",
    "TemplateParameter",
    "TemplateSize",
    "TikTokPostOptions",
    "TranscriptWord",
    "Transcription",
    "WebhookDelivery",
    "WebhookEvent",
    "WebhookEventType",
    "WebhookSubscription",
    "WebhookSubscriptionPage",
    "WebhookSubscriptionWithSecret",
    "WebhookTestResult",
    "Weekday",
    "Workflow",
    "WorkflowAction",
    "WorkflowCreateInput",
    "WorkflowDefinition",
    "WorkflowProblem",
    "WorkflowRowDelivery",
    "WorkflowRun",
    "WorkflowRunError",
    "WorkflowRunEvent",
    "WorkflowRunPage",
    "WorkflowRunRequest",
    "WorkflowRunRow",
    "WorkflowRunStatus",
    "WorkflowRunWithRows",
    "WorkflowSecret",
    "WorkflowTrigger",
    "WorkflowTriggerSummary",
    "WorkflowUpdateInput",
    "XPostOptions",
    "YouTubePostOptions",
]
