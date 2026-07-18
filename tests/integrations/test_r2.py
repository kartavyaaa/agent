"""Unit tests for R2Client.

No real network calls — boto3 S3 client is injected as a mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.client import ClientError

from core.exceptions import IntegrationError
from integrations.r2 import R2Client


def _make_r2(*, s3_client: MagicMock | None = None) -> R2Client:
    r2 = R2Client(
        account_id="test-account",
        access_key_id="test-key-id",
        secret_access_key="test-secret",
        bucket="test-bucket",
        public_base_url="https://cdn.example.com",
    )
    if s3_client is not None:
        r2._s3 = s3_client
    return r2


def _client_error(code: str, message: str, http_status: int = 500) -> ClientError:
    return ClientError(
        error_response={
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": http_status},
        },
        operation_name="PutObject",
    )


# ---------------------------------------------------------------------------
# upload() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_returns_public_url() -> None:
    s3 = MagicMock()
    s3.put_object = MagicMock(return_value={})
    r2 = _make_r2(s3_client=s3)

    url = await r2.upload(b"image data", key="user1/abc.jpg")

    assert url == "https://cdn.example.com/user1/abc.jpg"


@pytest.mark.asyncio
async def test_upload_calls_put_object_with_correct_args() -> None:
    s3 = MagicMock()
    s3.put_object = MagicMock(return_value={})
    r2 = _make_r2(s3_client=s3)
    data = b"\xff\xd8\xff"  # JPEG header bytes

    await r2.upload(data, key="user1/photo.jpg", content_type="image/jpeg")

    s3.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="user1/photo.jpg",
        Body=data,
        ContentType="image/jpeg",
    )


@pytest.mark.asyncio
async def test_upload_public_url_strips_trailing_slash() -> None:
    s3 = MagicMock()
    s3.put_object = MagicMock(return_value={})
    r2 = R2Client(
        account_id="acct",
        access_key_id="key",
        secret_access_key="secret",
        bucket="bucket",
        public_base_url="https://cdn.example.com/",  # trailing slash
    )
    r2._s3 = s3

    url = await r2.upload(b"data", key="img.jpg")

    assert url == "https://cdn.example.com/img.jpg"


# ---------------------------------------------------------------------------
# upload() — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_4xx_raises_integration_error() -> None:
    s3 = MagicMock()
    s3.put_object = MagicMock(
        side_effect=_client_error("AccessDenied", "Access Denied", http_status=403)
    )
    r2 = _make_r2(s3_client=s3)

    with pytest.raises(IntegrationError, match="R2 upload error"):
        await r2.upload(b"data", key="img.jpg")


@pytest.mark.asyncio
async def test_upload_4xx_does_not_retry() -> None:
    """4xx errors must not be retried — they indicate config/auth problems."""
    s3 = MagicMock()
    s3.put_object = MagicMock(
        side_effect=_client_error("AccessDenied", "Access Denied", http_status=403)
    )
    r2 = _make_r2(s3_client=s3)

    with pytest.raises(IntegrationError):
        await r2.upload(b"data", key="img.jpg")

    # Called once — no retries for 4xx
    assert s3.put_object.call_count == 1


@pytest.mark.asyncio
async def test_upload_5xx_retries_and_reraises() -> None:
    """5xx ClientErrors are retried up to 3 times, then reraised."""
    s3 = MagicMock()
    s3.put_object = MagicMock(
        side_effect=_client_error("InternalError", "Service unavailable", http_status=503)
    )
    r2 = _make_r2(s3_client=s3)

    with pytest.raises(ClientError):
        await r2.upload(b"data", key="img.jpg")

    assert s3.put_object.call_count == 3


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_returns_true_when_configured() -> None:
    r2 = _make_r2()
    assert await r2.health_check() is True


def test_construction_with_empty_account_id_raises() -> None:
    # boto3 builds the endpoint eagerly in __init__; empty account_id produces
    # an invalid URL and raises. Wiring guards construction with r2_ready = all([...])
    # so this path never occurs in practice — this test documents the fail-fast behavior.
    with pytest.raises(ValueError):
        R2Client(
            account_id="",
            access_key_id="key",
            secret_access_key="secret",
            bucket="bucket",
            public_base_url="https://cdn.example.com",
        )


# ---------------------------------------------------------------------------
# aclose()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_is_a_no_op() -> None:
    r2 = _make_r2()
    await r2.aclose()  # must not raise
