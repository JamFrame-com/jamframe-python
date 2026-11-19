"""What the sync and async clients share: configuration, request building,
error mapping and the retry policy. Neither does any I/O here."""

from __future__ import annotations

import datetime
import email.utils
import os
import random
import time
from typing import IO, Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import quote

import httpx

from . import _brand
from ._version import __version__
from .errors import APIError, RateLimitError, SDKError, error_class_for
from .types import (
    BrandValues,
    Modifications,
    RenderAiOptions,
    RenderOptions,
    SignedUrlOptions,
    SocialPostInput,
    WorkflowCreateInput,
    WorkflowUpdateInput,
)

API_PREFIX = "/v1"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 2
# A 429 asking to wait longer than this is raised instead of slept through.
MAX_RETRY_AFTER = 60.0
# Render job polling: start at `poll_interval`, x1.5 each poll, at most this.
MAX_POLL_INTERVAL = 5.0
DEFAULT_WAIT_TIMEOUT = 600.0
# A workflow run has ended in one of these.
FINAL_RUN_STATUSES = frozenset({"succeeded", "partial", "failed", "canceled"})

Params = Mapping[str, Union[str, int, float, bool, None]]

FileInput = Union[bytes, bytearray, memoryview, "os.PathLike[str]", IO[bytes]]
"""A file to upload: its bytes, a path (``pathlib.Path``) or a binary file object."""

Upload = Tuple[str, bytes]
"""A file to send as multipart: its name and its bytes."""


def normalize_base_url(base_url: str) -> str:
    """``https://host`` or ``https://host/v1`` -> ``https://host/v1``.

    A URL ending in the older ``/api/v1`` is kept as given.
    """
    url = base_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise SDKError(f"base_url must start with http:// or https:// (got {base_url!r})")
    if url.endswith(API_PREFIX):
        return url
    return url + API_PREFIX


def path_segment(value: str) -> str:
    """An id, escaped for use as one URL path segment."""
    if not isinstance(value, str) or not value:
        raise SDKError("An id must be a non-empty string")
    return quote(value, safe="")


def clean_params(params: Optional[Params]) -> Dict[str, Union[str, int, float, bool]]:
    return {k: v for k, v in (params or {}).items() if v is not None}


def backoff_delay(attempt: int, rand: Callable[[], float]) -> float:
    """Exponential backoff with jitter: 0.5 s, 1 s, 2 s … at most 8 s, times 0.5–1."""
    base = min(8.0, 0.5 * (2**attempt))
    return float(base * (0.5 + rand() / 2))


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """``Retry-After`` as seconds (a number of seconds or an HTTP date)."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:  # pragma: no cover - older Pythons returned None
        return None
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


def api_error_from_response(response: httpx.Response) -> APIError:
    status = response.status_code
    headers = dict(response.headers.items())
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        code = str(body["error"])
        message = str(body.get("message") or code)
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else None
    else:
        code = f"HTTP_{status}"
        text = response.text.strip() if response.content else ""
        message = text[:300] if text else (response.reason_phrase or f"HTTP {status}")
        detail = None
        body = body if body is not None else (response.text if response.content else None)
    cls = error_class_for(status)
    if cls is RateLimitError:
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        if retry_after is None and detail and isinstance(detail.get("retryAfter"), (int, float)):
            retry_after = float(detail["retryAfter"])
        return RateLimitError(status, code, message, detail=detail, headers=headers, body=body, retry_after=retry_after)
    return cls(status, code, message, detail=detail, headers=headers, body=body)


def retry_delay(
    error: APIError,
    attempt: int,
    *,
    idempotent: bool,
    max_retries: int,
    rand: Callable[[], float],
) -> Optional[float]:
    """Seconds to wait before retrying ``error``, or ``None`` to raise it.

    A 429 is retried for every call: the rate limiter refuses a request
    before it reaches the handler, so nothing happened. A 5xx is retried only
    for idempotent calls: a render may have run (and been charged) before a
    proxy answered 502.
    """
    if attempt >= max_retries:
        return None
    if isinstance(error, RateLimitError):
        if error.retry_after is not None:
            return None if error.retry_after > MAX_RETRY_AFTER else error.retry_after
        return backoff_delay(attempt, rand)
    if error.status >= 500 and idempotent:
        return backoff_delay(attempt, rand)
    return None


class Result:
    """A 2xx answer: its status, parsed JSON (``None`` when empty) and headers."""

    __slots__ = ("status_code", "data", "headers")

    def __init__(self, status_code: int, data: Any, headers: httpx.Headers) -> None:
        self.status_code = status_code
        self.data = data
        self.headers = headers


def parse_success(response: httpx.Response) -> Result:
    data: Any = None
    if response.content:
        try:
            data = response.json()
        except ValueError as exc:
            raise APIError(
                response.status_code,
                "INVALID_RESPONSE",
                "The API answered with something that isn't JSON.",
                headers=dict(response.headers.items()),
                body=response.text[:1000],
            ) from exc
    return Result(response.status_code, data, response.headers)


class BaseClient:
    """Configuration shared by :class:`Client` and :class:`AsyncClient`."""

    def __init__(
        self,
        api_key: Optional[str],
        base_url: Optional[str],
        timeout: float,
        max_retries: int,
        user_agent: Optional[str],
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(_brand.API_KEY_ENV)
        if not key:
            raise SDKError(f"No API key: pass api_key=... or set {_brand.API_KEY_ENV}.")
        self.api_key = key
        self.base_url = normalize_base_url(
            base_url or os.environ.get(_brand.BASE_URL_ENV) or _brand.DEFAULT_BASE_URL
        )
        self.timeout = timeout
        if max_retries < 0:
            raise SDKError("max_retries must be 0 or more")
        self.max_retries = max_retries
        agent = f"{_brand.USER_AGENT_PREFIX}/{__version__}"
        self.user_agent = f"{user_agent} {agent}" if user_agent else agent
        # Testing hook: a function returning [0, 1), for retry jitter.
        self._random: Callable[[], float] = random.random

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r})"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

    def _request_args(
        self,
        method: str,
        path: str,
        params: Optional[Params],
        json_body: Any,
        upload: Optional[Upload] = None,
        form: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {
            "method": method,
            "url": self.base_url + path,
            "headers": self._headers(),
            "timeout": self.timeout,
        }
        query = clean_params(params)
        if query:
            args["params"] = query
        if upload is not None:
            # Multipart: the file, and the other fields as strings.
            args["files"] = {"file": upload}
            args["data"] = form_fields(form or {})
        elif json_body is not None:
            args["json"] = json_body
        return args


# ---------------------------------------------------------------- request bodies


def render_body(
    template_id: str,
    modifications: Optional[Modifications],
    response: Optional[RenderOptions],
    *,
    run_async: bool = False,
    webhook_url: Optional[str] = None,
    expires_in: Optional[int] = None,
    translate: Optional[bool] = None,
    lang: Optional[str] = None,
    ai: Optional[RenderAiOptions] = None,
) -> Dict[str, Any]:
    if not isinstance(template_id, str) or not template_id:
        raise SDKError("template_id must be a non-empty string")
    body: Dict[str, Any] = {"templateId": template_id}
    if modifications:
        body["modifications"] = dict(modifications)
    if response:
        body["response"] = dict(response)
    if run_async:
        body["async"] = True
    if webhook_url:
        body["webhookUrl"] = webhook_url
    if expires_in is not None:
        body["expiresIn"] = expires_in
    if translate is not None:
        body["translate"] = translate
    if lang:
        body["lang"] = lang
    if ai:
        body["ai"] = dict(ai)
    return body


def design_generate_body(
    prompt: str,
    *,
    size: Optional[str],
    width: Optional[int],
    height: Optional[int],
    pages: Optional[int],
    video: Optional[bool],
    brand: Union[bool, BrandValues, None],
    reference_image_url: Optional[str],
    name: Optional[str],
    run_async: bool = False,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise SDKError("prompt must be a non-empty string")
    body: Dict[str, Any] = {"prompt": prompt}
    fields = {
        "size": size,
        "width": width,
        "height": height,
        "pages": pages,
        "video": video,
        "brand": dict(brand) if isinstance(brand, dict) else brand,
        "referenceImageUrl": reference_image_url,
        "name": name,
        "webhookUrl": webhook_url,
    }
    body.update({k: v for k, v in fields.items() if v is not None})
    if run_async:
        body["async"] = True
    return body


def workflow_body(body: Union[WorkflowCreateInput, WorkflowUpdateInput, None], kwargs: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(body or {})
    merged.update(kwargs)
    return merged


def run_body(payload: Any, max_rows: Optional[int]) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    if payload is not None:
        body["payload"] = payload
    if max_rows is not None:
        body["maxRows"] = max_rows
    return body


def analytics_params(
    from_: Union[str, datetime.date, None],
    to: Union[str, datetime.date, None],
    platform: Union[str, Sequence[str], None],
    account_id: Union[str, Sequence[str], None],
) -> Dict[str, Optional[str]]:
    """Query of the social analytics reports: dates as ``YYYY-MM-DD``, lists comma-separated."""

    def day(value: Union[str, datetime.date, None]) -> Optional[str]:
        return value.isoformat() if isinstance(value, datetime.date) else value

    def joined(value: Union[str, Sequence[str], None]) -> Optional[str]:
        return value if value is None or isinstance(value, str) else ",".join(value)

    return {"from": day(from_), "to": day(to), "platform": joined(platform), "accountId": joined(account_id)}


def read_upload(file: FileInput, filename: Optional[str]) -> Upload:
    """The file's name and bytes, read once so a retried request can send them again."""
    if isinstance(file, (bytes, bytearray, memoryview)):
        return (filename or "upload", bytes(file))
    if isinstance(file, os.PathLike):
        path = os.fspath(file)
        with open(path, "rb") as handle:
            return (filename or os.path.basename(path), handle.read())
    if hasattr(file, "read"):
        data = file.read()
        if not isinstance(data, bytes):
            raise SDKError("Open the file in binary mode ('rb')")
        own = getattr(file, "name", None)
        return (filename or (os.path.basename(own) if isinstance(own, str) else "upload"), data)
    raise SDKError("file must be bytes, a path (pathlib.Path) or a binary file object")


def form_fields(fields: Mapping[str, Any]) -> Dict[str, str]:
    """Multipart text fields: ``None`` dropped, booleans as ``true``/``false``."""
    out: Dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return out


def media_source(url: Optional[str], file: Optional[FileInput], filename: Optional[str]) -> Optional[Upload]:
    """Checks that exactly one of ``url`` and ``file`` is given; returns the upload for a file."""
    if (url is None) == (file is None):
        raise SDKError("Pass either url=... or file=..., not both")
    return None if file is None else read_upload(file, filename)


def signed_url_body(
    template_id: str,
    modifications: Optional[Mapping[str, Any]],
    response: Optional[SignedUrlOptions],
    expires_in: Optional[int],
    cache_seconds: Optional[int],
) -> Dict[str, Any]:
    if not isinstance(template_id, str) or not template_id:
        raise SDKError("template_id must be a non-empty string")
    body: Dict[str, Any] = {"templateId": template_id}
    if modifications:
        body["modifications"] = dict(modifications)
    if response:
        body["response"] = dict(response)
    if expires_in is not None:
        body["expiresIn"] = expires_in
    if cache_seconds is not None:
        body["cacheSeconds"] = cache_seconds
    return body


# Python-style spellings accepted by social.posts.create(**kwargs).
_SOCIAL_KWARG_NAMES = {
    "template_id": "templateId",
    "render_id": "renderId",
    "media_urls": "mediaUrls",
    "scheduled_at": "scheduledAt",
}


def social_post_body(body: Optional[SocialPostInput], kwargs: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(body or {})
    for key, value in kwargs.items():
        merged[_SOCIAL_KWARG_NAMES.get(key, key)] = value
    return merged


def page_cursor(page: Mapping[str, Any]) -> Optional[str]:
    """The next page's cursor, or ``None`` on the last page."""
    pagination = page.get("pagination") or {}
    cursor = pagination.get("nextCursor")
    if not pagination.get("hasMore") or not cursor:
        return None
    return str(cursor)


def sized_urls(content: Any) -> List[str]:
    """Every file URL of a render's ``content`` (a string, a list, or sized entries)."""
    if isinstance(content, str):
        return [content]
    urls: List[str] = []
    for item in content or []:
        if isinstance(item, str):
            urls.append(item)
        elif isinstance(item, dict):
            if isinstance(item.get("url"), str):
                urls.append(item["url"])
            urls.extend(u for u in item.get("urls") or [] if isinstance(u, str))
    return urls


def next_poll_interval(current: float, initial: float) -> float:
    return min(current * 1.5, max(MAX_POLL_INTERVAL, initial))


def unwrap(result: Result, key: str) -> Any:
    data = result.data
    if isinstance(data, dict) and key in data:
        return data[key]
    raise APIError(
        result.status_code,
        "INVALID_RESPONSE",
        f"The API answer has no {key!r} field.",
        headers=dict(result.headers.items()),
        body=data,
    )

