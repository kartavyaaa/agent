from __future__ import annotations

import asyncio
from dataclasses import dataclass

import boto3
import httpx
from botocore.client import ClientError
from botocore.config import Config
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.exceptions import IntegrationError

_PRESIGN_EXPIRES = 300  # seconds — not used for direct put_object, kept for reference


@dataclass
class R2UploadResult:
    key: str
    public_url: str


class R2Client:
    """Async Cloudflare R2 upload client.

    Uses boto3 put_object via asyncio.to_thread — the reference SigV4 implementation,
    handles all signing details for S3-compatible stores. No presigned-URL complexity.
    """

    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        public_base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,  # unused; kept for API compat
        timeout: float = 30.0,
    ) -> None:
        self._account_id = account_id
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")
        self._timeout = timeout
        self._s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def _put_object_sync(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    @retry(
        retry=retry_if_exception_type(ClientError),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def upload(self, data: bytes, key: str, content_type: str = "image/jpeg") -> str:
        """Upload bytes to R2 at the given key. Returns the public URL."""
        try:
            await asyncio.to_thread(self._put_object_sync, key, data, content_type)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            msg = exc.response.get("Error", {}).get("Message", str(exc))
            # 4xx errors from R2 are non-retryable config/auth problems
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if 400 <= status < 500:
                raise IntegrationError(f"R2 upload error ({code}): {msg}") from exc
            raise  # 5xx — tenacity will retry
        return f"{self._public_base_url}/{key}"

    async def health_check(self) -> bool:
        # A validly-constructed client always has account_id + bucket (boto3 raises otherwise),
        # so this always returns True — matches serper's no-network pattern.
        return bool(self._account_id and self._bucket)

    async def aclose(self) -> None:
        pass  # boto3 client is synchronous; no async resources to close
