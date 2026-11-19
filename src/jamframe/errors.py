"""Exceptions raised by the SDK.

Every non-2xx answer becomes an :class:`APIError` subclass chosen by its HTTP
status, carrying the API's envelope (``{ "error", "message", "detail" }``):
``status``, ``code`` (the envelope's ``error``, e.g. ``QUOTA_EXCEEDED``),
``message`` (safe to show to end users) and ``detail``.
"""

from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Type

if TYPE_CHECKING:
    from .types import DesignGenerateJob, RenderJob


class SDKError(Exception):
    """Base class of every exception the SDK raises."""


class APIError(SDKError):
    """The API answered with an error status."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        detail: Optional[Dict[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail: Dict[str, Any] = dict(detail or {})
        self.headers: Dict[str, str] = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body

    @property
    def error(self) -> str:
        """The envelope's ``error`` field (same as :attr:`code`)."""
        return self.code

    def __str__(self) -> str:
        return f"{self.status} {self.code}: {self.message}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status!r}, code={self.code!r}, message={self.message!r})"


class BadRequestError(APIError):
    """400: an invalid request (``VALIDATION_ERROR``, ``INVALID_REQUEST``, ``WEBHOOK_URL_INVALID``)."""


class AuthenticationError(APIError):
    """401: a missing, invalid, expired or revoked API key."""


class PaymentRequiredError(APIError):
    """402: not enough credits (``QUOTA_EXCEEDED``, ``WORKSPACE_QUOTA_EXCEEDED``, ``AI_CREDITS_EXCEEDED``) or a plan limit."""


class PermissionDeniedError(APIError):
    """403: the organization is on hold (``ORG_ON_HOLD``) or suspended (``ORG_SUSPENDED``)."""


class NotFoundError(APIError):
    """404: no such template, render job, post or subscription in this workspace."""


class ConflictError(APIError):
    """409: e.g. canceling a post that already went out."""


class UnprocessableEntityError(APIError):
    """422: an image, video, audio or subtitle URL could not be loaded, or the AI refused or couldn't
    process the input (``AI_CONTENT_REFUSED``, ``AI_BAD_REQUEST``). No credits were charged."""


class RateLimitError(APIError):
    """429: too many requests. ``retry_after`` is the wait the API asked for, in seconds."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        detail: Optional[Dict[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        body: Any = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(status, code, message, detail=detail, headers=headers, body=body)
        self.retry_after = retry_after


class InternalServerError(APIError):
    """5xx: the server or the renderer failed, or a feature is off (``503 AI_UNAVAILABLE``, ``VIDEO_UNAVAILABLE``)."""


class APIConnectionError(SDKError):
    """The request never got an answer (DNS, refused connection, reset…)."""


class APITimeoutError(APIConnectionError):
    """The request took longer than the client's ``timeout``."""


class RenderJobFailedError(SDKError):
    """A render job ended ``failed``. Failed jobs cost no credits."""

    def __init__(self, job: "RenderJob") -> None:
        error: Mapping[str, Any] = job.get("error") or {}
        self.job = job
        self.job_id: str = job.get("id", "")
        self.code: str = str(error.get("error") or "RENDER_FAILED")
        self.message: str = str(error.get("message") or "The render job failed.")
        super().__init__(f"Render job {self.job_id} failed: {self.code}: {self.message}")


class DesignJobFailedError(SDKError):
    """A design generation ended ``failed``. Its AI credits were refunded."""

    def __init__(self, job: "DesignGenerateJob") -> None:
        error: Mapping[str, Any] = job.get("error") or {}
        self.job = job
        self.job_id: str = job.get("jobId", "")
        self.code: str = str(error.get("error") or "INTERNAL")
        self.message: str = str(error.get("message") or "The design generation failed.")
        super().__init__(f"Design generation {self.job_id} failed: {self.code}: {self.message}")


class WaitTimeoutError(SDKError):
    """Waiting for a job (a render job, a design generation or a workflow run)
    took longer than the timeout. The job keeps running: wait again or poll it."""

    def __init__(
        self, job_id: str, last_job: Optional[Mapping[str, Any]], timeout: float, what: str = "Render job"
    ) -> None:
        self.job_id = job_id
        self.last_job = last_job
        self.timeout = timeout
        status = last_job.get("status") if last_job else "unknown"
        super().__init__(f"{what} {job_id} was still {status} after {timeout:g} s; it keeps running.")


class WebhookSignatureError(SDKError):
    """A webhook delivery's signature is missing, stale or doesn't match."""


_BY_STATUS: Dict[int, Type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    402: PaymentRequiredError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableEntityError,
    429: RateLimitError,
}


def error_class_for(status: int) -> Type[APIError]:
    if status >= 500:
        return InternalServerError
    return _BY_STATUS.get(status, APIError)


__all__ = [
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "DesignJobFailedError",
    "InternalServerError",
    "NotFoundError",
    "PaymentRequiredError",
    "PermissionDeniedError",
    "RateLimitError",
    "RenderJobFailedError",
    "SDKError",
    "UnprocessableEntityError",
    "WaitTimeoutError",
    "WebhookSignatureError",
]
