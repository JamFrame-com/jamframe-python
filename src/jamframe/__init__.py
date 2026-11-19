"""Python client for the JamFrame API (brand strings: see ``_brand.py``).

>>> from jamframe import Client
>>> client = Client()  # JAMFRAME_API_KEY from the environment
>>> result = client.render("tpl_id", {"title": "Hello"})
>>> result["data"]["content"]
"""

from . import types, webhooks
from ._async_client import AsyncClient
from ._base import sized_urls as file_urls
from ._brand import NAME as PRODUCT_NAME
from ._client import Client
from ._version import __version__
from .errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    DesignJobFailedError,
    InternalServerError,
    NotFoundError,
    PaymentRequiredError,
    PermissionDeniedError,
    RateLimitError,
    RenderJobFailedError,
    SDKError,
    UnprocessableEntityError,
    WaitTimeoutError,
    WebhookSignatureError,
)
from .webhooks import construct_event, verify_signature

# Brand-named aliases. A rename touches only these two lines here.
JamFrame = Client
AsyncJamFrame = AsyncClient

__all__ = [
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AsyncClient",
    "AsyncJamFrame",
    "AuthenticationError",
    "BadRequestError",
    "Client",
    "ConflictError",
    "DesignJobFailedError",
    "InternalServerError",
    "NotFoundError",
    "PRODUCT_NAME",
    "PaymentRequiredError",
    "PermissionDeniedError",
    "JamFrame",
    "RateLimitError",
    "RenderJobFailedError",
    "SDKError",
    "UnprocessableEntityError",
    "WaitTimeoutError",
    "WebhookSignatureError",
    "__version__",
    "construct_event",
    "file_urls",
    "types",
    "verify_signature",
    "webhooks",
]
