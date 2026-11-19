"""Verify the signature of webhook deliveries.

Every delivery carries ``X-Webhook-Signature: t=<unix seconds>,v1=<hex>``,
where ``v1`` is the HMAC-SHA256 of ``f"{t}.{raw body}"`` keyed with the
subscription's signing secret (``wh_sec_…``, shown once when you subscribe or
rotate). Always verify the **raw** request bytes: re-serialised JSON has
different bytes.

>>> event = construct_event(request.get_data(), request.headers, secret)
>>> if event["type"] == "render.completed": ...

The ``webhookUrl`` callback of a single render request is *not* signed; this
module is for subscription deliveries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import List, Mapping, Optional, Sequence, Tuple, Union, cast

from .errors import WebhookSignatureError
from .types import WebhookEvent

SIGNATURE_HEADER = "X-Webhook-Signature"
TIMESTAMP_HEADER = "X-Webhook-Timestamp"
EVENT_HEADER = "X-Webhook-Event"
DELIVERY_HEADER = "X-Webhook-Delivery"
"""Unique per subscription and event, unchanged across retries: deduplicate on it."""

DEFAULT_TOLERANCE = 300
"""Seconds a signature stays valid either side of now (replay protection)."""

Payload = Union[bytes, bytearray, memoryview, str]
HeaderInput = Union[str, Mapping[str, str], None]
Secrets = Union[str, Sequence[str]]


# Unix seconds: ASCII digits only (``int()`` would also take "+1", "1_000" or
# other scripts' digits), and short enough to compare with ``time.time()``
# (a 400-digit value would raise OverflowError there).
_TIMESTAMP = re.compile(r"[0-9]{1,15}")
# A hex HMAC-SHA256; anything else can't match, and stays out of compare_digest.
_SIGNATURE = re.compile(r"[0-9a-fA-F]{64}")


def _raw(payload: Payload) -> bytes:
    if isinstance(payload, str):
        return payload.encode("utf-8")
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return bytes(payload)
    # A TypeError, not WebhookSignatureError, so verify_signature doesn't turn
    # the mistake into a silent False: parsed JSON can't be verified.
    raise TypeError(
        "Pass the raw request body (bytes or str, e.g. request.get_data()), not parsed JSON: "
        "re-serialised JSON has different bytes"
    )


def _header_text(value: object) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("latin-1")
    return value if isinstance(value, str) else ""


def _signature_header(header: HeaderInput) -> str:
    """Accept the header's value, or the request's headers (any mapping, any case)."""
    if header is None:
        return ""
    if isinstance(header, (str, bytes, bytearray)):
        return _header_text(header)
    wanted = SIGNATURE_HEADER.lower()
    for key, value in header.items():
        if isinstance(key, str) and key.lower() == wanted:
            return _header_text(value)
    return ""


def parse_signature_header(header: str) -> Tuple[Optional[int], List[str]]:
    """``t=…,v1=…[,v1=…]`` -> (timestamp, signatures)."""
    timestamp: Optional[int] = None
    signatures: List[str] = []
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            if not _TIMESTAMP.fullmatch(value.strip()):
                return None, []
            timestamp = int(value.strip())
        elif key == "v1" and value.strip():
            signatures.append(value.strip().lower())
    return timestamp, signatures


def compute_signature(payload: Payload, timestamp: int, secret: str) -> str:
    """The ``v1`` value for ``payload`` signed at ``timestamp`` (for tests and fixtures)."""
    signed = f"{timestamp}.".encode("utf-8") + _raw(payload)
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def sign_header(payload: Payload, secret: str, timestamp: Optional[int] = None) -> str:
    """A complete ``X-Webhook-Signature`` value, e.g. to test your own endpoint."""
    t = int(time.time()) if timestamp is None else timestamp
    return f"t={t},v1={compute_signature(payload, t, secret)}"


def _problem(
    payload: Payload, header: HeaderInput, secret: Secrets, tolerance: Optional[float], now: Optional[float]
) -> Optional[str]:
    raw = _raw(payload)
    value = _signature_header(header)
    if not value:
        return f"Missing {SIGNATURE_HEADER} header"
    given: Sequence[object] = [secret] if isinstance(secret, str) else list(secret or [])
    secrets = [s for s in given if isinstance(s, str) and s]
    if not secrets:
        return "No signing secret given"
    timestamp, parsed = parse_signature_header(value)
    signatures = [s for s in parsed if _SIGNATURE.fullmatch(s)]
    if timestamp is None or not signatures:
        return f"Malformed {SIGNATURE_HEADER} header"
    current = time.time() if now is None else now
    # `not (age <= tolerance)` so a NaN tolerance or clock refuses the delivery.
    if tolerance is not None and not abs(current - timestamp) <= tolerance:
        return f"Signature timestamp is outside the {tolerance:g} s tolerance"
    for candidate in secrets:
        expected = compute_signature(raw, timestamp, candidate).encode("ascii")
        for signature in signatures:
            if hmac.compare_digest(expected, signature.encode("ascii")):
                return None
    return "No signature matches the payload"


def verify_signature(
    payload: Payload,
    header: HeaderInput,
    secret: Secrets,
    *,
    tolerance: Optional[float] = DEFAULT_TOLERANCE,
    now: Optional[float] = None,
) -> bool:
    """``True`` when ``header`` is a valid, fresh signature of ``payload``.

    ``payload`` is the raw body (bytes or str). ``header`` is the
    ``X-Webhook-Signature`` value or the request's headers. ``secret`` may be
    a list (e.g. the old and new secret while you rotate). ``tolerance`` is
    in seconds (``None`` skips the freshness check; don't, outside tests).
    """
    return _problem(payload, header, secret, tolerance, now) is None


def construct_event(
    payload: Payload,
    header: HeaderInput,
    secret: Secrets,
    *,
    tolerance: Optional[float] = DEFAULT_TOLERANCE,
    now: Optional[float] = None,
) -> WebhookEvent:
    """Verify, then parse the delivery: ``{ id, type, createdAt, data }``.

    Raises :class:`~jamframe.errors.WebhookSignatureError` when the signature
    is missing, stale or wrong, or the body isn't an event.
    """
    problem = _problem(payload, header, secret, tolerance, now)
    if problem:
        raise WebhookSignatureError(problem)
    try:
        event = json.loads(_raw(payload).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebhookSignatureError("The payload isn't JSON") from exc
    if not isinstance(event, dict) or not isinstance(event.get("type"), str) or "data" not in event:
        raise WebhookSignatureError("The payload isn't a webhook event")
    return cast(WebhookEvent, event)


__all__ = [
    "DEFAULT_TOLERANCE",
    "DELIVERY_HEADER",
    "EVENT_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "compute_signature",
    "construct_event",
    "parse_signature_header",
    "sign_header",
    "verify_signature",
]
