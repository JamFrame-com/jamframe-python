from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from jamframe import WebhookSignatureError, construct_event, verify_signature
from jamframe.webhooks import (
    SIGNATURE_HEADER,
    compute_signature,
    parse_signature_header,
    sign_header,
)

SECRET = "wh_sec_AbCdEfGh01234567890abcdefghijKLMN"
NOW = 1_762_700_000
BODY = json.dumps(
    {"id": "whe_1", "type": "render.completed", "createdAt": "2026-11-09T12:38:50.201Z", "data": {"id": "job_1"}},
    separators=(",", ":"),
).encode()


def header(body: bytes = BODY, secret: str = SECRET, t: int = NOW) -> str:
    # The server's recipe (server/src/lib/webhookDispatcher.ts), written out independently.
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def test_valid_signature() -> None:
    assert verify_signature(BODY, header(), SECRET, now=NOW)
    assert verify_signature(BODY.decode(), header(), SECRET, now=NOW + 299)
    assert verify_signature(bytearray(BODY), header(), SECRET, now=NOW - 300)


def test_tampered_body() -> None:
    assert not verify_signature(BODY.replace(b"job_1", b"job_2"), header(), SECRET, now=NOW)


def test_wrong_secret() -> None:
    assert not verify_signature(BODY, header(), "wh_sec_other", now=NOW)


def test_stale_and_future_timestamps() -> None:
    assert not verify_signature(BODY, header(), SECRET, now=NOW + 301)
    assert not verify_signature(BODY, header(), SECRET, now=NOW - 301)
    assert verify_signature(BODY, header(), SECRET, now=NOW + 3600, tolerance=None)
    assert verify_signature(BODY, header(), SECRET, now=NOW + 50, tolerance=60)


def test_timestamp_is_part_of_the_signature() -> None:
    forged = header().replace(f"t={NOW}", f"t={NOW + 10}")
    assert not verify_signature(BODY, forged, SECRET, now=NOW)


def test_multiple_secrets_and_signatures() -> None:
    assert verify_signature(BODY, header(), ["wh_sec_old", SECRET], now=NOW)
    both = f"{header(secret='wh_sec_new')},v1={header().split('v1=')[1]}"
    assert verify_signature(BODY, both, SECRET, now=NOW)
    assert verify_signature(BODY, both, "wh_sec_new", now=NOW)


def test_malformed_headers() -> None:
    for bad in ["", "garbage", f"t={NOW}", "v1=abc", f"t=abc,v1={'0' * 64}", f"t={NOW},v1=zzé"]:
        assert not verify_signature(BODY, bad, SECRET, now=NOW), bad
    assert not verify_signature(BODY, None, SECRET, now=NOW)
    assert not verify_signature(BODY, header(), "", now=NOW)
    assert not verify_signature(BODY, header(), [], now=NOW)


def test_uppercase_hex_accepted() -> None:
    upper = f"t={NOW},v1={header().split('v1=')[1].upper()}"
    assert verify_signature(BODY, upper, SECRET, now=NOW)


def test_headers_mapping_any_case() -> None:
    assert verify_signature(BODY, {"x-webhook-signature": header(), "content-type": "application/json"}, SECRET, now=NOW)
    assert verify_signature(BODY, {SIGNATURE_HEADER: header()}, SECRET, now=NOW)
    assert not verify_signature(BODY, {"content-type": "application/json"}, SECRET, now=NOW)


def test_construct_event() -> None:
    event = construct_event(BODY, header(), SECRET, now=NOW)
    assert event["type"] == "render.completed"
    assert event["data"] == {"id": "job_1"}


@pytest.mark.parametrize(
    "body, sig, message",
    [
        (BODY, "", "Missing"),
        (BODY, "t=1", "Malformed"),
        (BODY, header(t=NOW - 1000), "tolerance"),
        (BODY + b" ", header(), "No signature matches"),
    ],
)
def test_construct_event_errors(body: bytes, sig: str, message: str) -> None:
    with pytest.raises(WebhookSignatureError, match=message):
        construct_event(body, sig, SECRET, now=NOW)


def test_construct_event_rejects_non_events() -> None:
    for body in [b"not json", b"[1,2]", b'{"id":"x"}']:
        with pytest.raises(WebhookSignatureError):
            construct_event(body, header(body), SECRET, now=NOW)


def test_helpers_round_trip() -> None:
    assert compute_signature(BODY, NOW, SECRET) == header().split("v1=")[1]
    assert sign_header(BODY, SECRET, NOW) == header()
    assert verify_signature(BODY, sign_header(BODY, SECRET), SECRET)
    assert parse_signature_header(f"t={NOW}, v1=AB ,v0=x") == (NOW, ["ab"])


def test_hostile_timestamps_are_refused_without_raising() -> None:
    mac = header().split("v1=")[1]
    # A 400-digit t used to raise OverflowError when compared with time.time().
    assert not verify_signature(BODY, f"t={'9' * 400},v1={mac}", SECRET)
    # int() would take these; the server only ever sends ASCII digits.
    for t in ["+1762700000", "1_762_700_000", "١٧٦٢٧٠٠٠٠٠", "-1"]:
        assert not verify_signature(BODY, f"t={t},v1={mac}", SECRET, now=NOW), t
    with pytest.raises(WebhookSignatureError, match="Malformed"):
        construct_event(BODY, f"t={'9' * 400},v1={mac}", SECRET)


def test_nan_tolerance_or_clock_fails_closed() -> None:
    stale = header(t=NOW - 3600)
    assert not verify_signature(BODY, stale, SECRET, now=NOW, tolerance=float("nan"))
    assert not verify_signature(BODY, header(), SECRET, now=float("nan"))


def test_header_mapping_with_list_or_bytes_values() -> None:
    assert verify_signature(BODY, {"X-Webhook-Signature": [header()]}, SECRET, now=NOW)
    assert verify_signature(BODY, {"x-webhook-signature": header().encode()}, SECRET, now=NOW)  # type: ignore[dict-item]
    assert verify_signature(BODY, header().encode(), SECRET, now=NOW)  # type: ignore[arg-type]
    assert not verify_signature(BODY, {"X-Webhook-Signature": []}, SECRET, now=NOW)  # type: ignore[dict-item]
    assert not verify_signature(BODY, {"X-Webhook-Signature": None}, SECRET, now=NOW)  # type: ignore[dict-item]


def test_missing_secret_and_non_ascii_signature() -> None:
    assert not verify_signature(BODY, header(), None, now=NOW)  # type: ignore[arg-type]
    assert not verify_signature(BODY, header(), [None, ""], now=NOW)  # type: ignore[list-item]
    assert not verify_signature(BODY, f"t={NOW},v1={'é' * 64}", SECRET, now=NOW)


def test_parsed_json_payload_raises_type_error() -> None:
    # Re-serialised JSON has different bytes: say so instead of returning False.
    with pytest.raises(TypeError, match="raw request body"):
        verify_signature(json.loads(BODY), header(), SECRET, now=NOW)  # type: ignore[arg-type]
