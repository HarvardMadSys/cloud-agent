"""Control-plane S3 storage for immutable, verified sandbox deletion archives.

The destination and credentials are the deployment's sandbox archive policy
(**Admin → Settings → Sandbox → Sandbox archives**), the same one sandbox
managers use for lifecycle archives: its bucket and region, and its saved
access key when it has one. A policy without an access key uses the control
plane's standard AWS SDK chain, such as a workload role. Deletion archives
are kept under :data:`DELETION_ARCHIVE_PREFIX`, inside the policy's fixed
prefix, so one IAM grant on that prefix covers both. The host receives a
short-lived upload capability for one checksum-bound object, never an AWS
credential or a caller-selected bucket/key.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import quote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from cloud_agent_shared.sandbox_archive_settings import (
    SANDBOX_ARCHIVE_PREFIX,
    SandboxArchivePolicy,
    SandboxArchivePolicyError,
)

MAX_ARCHIVE_BYTES = 5 * 1024**3
UPLOAD_EXPIRES_SECONDS = 900
ARCHIVE_CONTENT_TYPE = "application/octet-stream"
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
#: Where deletion archives go in the policy's bucket. Lifecycle archives use
#: the ``v1/`` and ``v2/`` key spaces beside it.
DELETION_ARCHIVE_PREFIX = f"{SANDBOX_ARCHIVE_PREFIX}/deletion"


class SandboxArchiveError(RuntimeError):
    """An archive could not be prepared or verified; deletion must not proceed."""


class SandboxArchiveUnavailable(SandboxArchiveError):
    """No archive bucket is configured for this deployment."""


class SandboxArchiveVerificationError(SandboxArchiveError):
    """The stored object does not attest the expected complete archive."""


class _ArchivePolicySource(Protocol):
    async def sandbox_archive_policy(self) -> SandboxArchivePolicy: ...


class _S3Client(Protocol):
    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any],
        ExpiresIn: int,
        HttpMethod: str,
    ) -> str: ...

    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SandboxArchiveConfiguration:
    """Where deletion archives go and how the control plane authenticates there.

    ``bucket`` is empty when the deployment's archive policy is disabled, which
    leaves deletion with an archive unavailable.
    """

    bucket: str = ""
    region: str = ""
    expected_bucket_owner: str = ""
    prefix: str = DELETION_ARCHIVE_PREFIX
    aws_access_key_id: str = field(default="", repr=False)
    aws_secret_access_key: str = field(default="", repr=False)

    @classmethod
    def from_policy(cls, policy: SandboxArchivePolicy) -> SandboxArchiveConfiguration:
        """The deletion destination of an enabled policy; none of a disabled one."""
        if not policy.enabled:
            return cls()
        return cls(
            bucket=policy.bucket,
            region=policy.region,
            expected_bucket_owner=policy.expected_bucket_owner,
            aws_access_key_id=policy.aws_access_key_id,
            aws_secret_access_key=policy.aws_secret_access_key,
        )


@dataclass(frozen=True)
class ArchiveUpload:
    """One PUT capability; its required headers must be sent unchanged."""

    upload_url: str
    key: str
    headers: dict[str, str]
    expires_in_seconds: int = UPLOAD_EXPIRES_SECONDS


@dataclass(frozen=True)
class ArchiveReceipt:
    """Verified S3 identity and content digest safe to persist as an audit receipt."""

    archive_uri: str
    bucket: str
    key: str
    sha256: str
    size_bytes: int
    version_id: str | None = None


def _archive_digest(sha256: str, size_bytes: int) -> tuple[str, str]:
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise SandboxArchiveError("A complete SHA-256 digest is required for the archive")
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes <= 0
        or size_bytes > MAX_ARCHIVE_BYTES
    ):
        raise SandboxArchiveError("The archive must be between 1 byte and 5 GiB")
    return sha256.lower(), base64.b64encode(bytes.fromhex(sha256)).decode("ascii")


def _identifier(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or len(value.encode("utf-8")) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise SandboxArchiveError("A valid sandbox and archive request identity are required")
    return quote(value, safe="")


class S3SandboxArchiveStore:
    """Prepare conditional S3 PUTs and verify service-computed SHA-256 checksums."""

    def __init__(
        self,
        configuration: SandboxArchiveConfiguration,
        *,
        client: _S3Client | None = None,
    ) -> None:
        self.configuration = configuration
        self._client = client

    @classmethod
    async def for_deployment(cls, host_pool: _ArchivePolicySource) -> S3SandboxArchiveStore:
        """Snapshot the stored archive policy; SDK creation is deferred until an action.

        A stored policy that does not parse, or whose access key does not open,
        leaves deletion with an archive unavailable rather than guessing.
        """
        try:
            policy = await host_pool.sandbox_archive_policy()
        except SandboxArchivePolicyError:
            return cls(SandboxArchiveConfiguration())
        return cls(SandboxArchiveConfiguration.from_policy(policy))

    @property
    def available(self) -> bool:
        """Whether a bucket is configured, independent of remote reachability."""
        return bool(self.configuration.bucket)

    def _require_available(self) -> None:
        if not self.available:
            raise SandboxArchiveUnavailable("Enable sandbox archives before deletion")

    def _get_client(self) -> _S3Client:
        if self._client is None:
            configuration = self.configuration
            # A saved access key authenticates as it does for sandbox managers;
            # without one the SDK's standard chain applies.
            credentials = (
                {
                    "aws_access_key_id": configuration.aws_access_key_id,
                    "aws_secret_access_key": configuration.aws_secret_access_key,
                }
                if configuration.aws_access_key_id
                else {}
            )
            self._client = cast(
                "_S3Client",
                boto3.client(
                    "s3",
                    region_name=configuration.region or None,
                    config=Config(
                        signature_version="s3v4",
                        connect_timeout=5,
                        read_timeout=30,
                        retries={"max_attempts": 2, "mode": "standard"},
                    ),
                    **credentials,
                ),
            )
        return self._client

    def key_for(self, sandbox_id: str, request_id: str) -> str:
        """Name the request object before the host produces its archive bytes."""
        self._require_available()
        key = "/".join(
            filter(
                None,
                (
                    self.configuration.prefix.strip("/"),
                    _identifier(sandbox_id),
                    _identifier(request_id),
                    "archive.tar",
                ),
            )
        )
        if len(key.encode("utf-8")) > 1024:
            raise SandboxArchiveError("The configured archive object key exceeds the S3 limit")
        return key

    async def prepare_upload(
        self, sandbox_id: str, request_id: str, sha256: str, size_bytes: int
    ) -> ArchiveUpload:
        """Sign one immutable, size-bound, checksum-bound single PUT archive."""
        self._require_available()
        _, checksum = _archive_digest(sha256, size_bytes)
        key = self.key_for(sandbox_id, request_id)
        headers = {
            "Content-Type": ARCHIVE_CONTENT_TYPE,
            "Content-Length": str(size_bytes),
            "x-amz-checksum-sha256": checksum,
            "If-None-Match": "*",
        }

        def prepare() -> str:
            return self._get_client().generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.configuration.bucket,
                    "Key": key,
                    "ContentType": ARCHIVE_CONTENT_TYPE,
                    "ContentLength": size_bytes,
                    "ChecksumSHA256": checksum,
                    "IfNoneMatch": "*",
                },
                ExpiresIn=UPLOAD_EXPIRES_SECONDS,
                HttpMethod="PUT",
            )

        try:
            upload_url = await asyncio.to_thread(prepare)
        except (BotoCoreError, ClientError):
            raise SandboxArchiveError("Unable to prepare the sandbox archive upload") from None
        return ArchiveUpload(upload_url=upload_url, key=key, headers=headers)

    async def verify_archive(self, key: str, sha256: str, size_bytes: int) -> ArchiveReceipt:
        """Verify a current object key or an already trusted, persisted S3 receipt URI.

        A persisted URI keeps its original bucket and prefix when settings change.
        Callers must obtain that URI from the control-plane deletion record, never
        from a browser or host-selected location.
        """
        digest, checksum = _archive_digest(sha256, size_bytes)
        bucket = self.configuration.bucket
        persisted_uri = key.startswith("s3://")
        if persisted_uri:
            parsed = urlsplit(key)
            if (
                not _BUCKET.fullmatch(parsed.netloc)
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/")
            ):
                raise SandboxArchiveVerificationError("The persisted S3 archive URI is invalid")
            bucket = parsed.netloc
            key = parsed.path[1:]
        else:
            self._require_available()
        prefix = self.configuration.prefix.strip("/")
        relative = key if persisted_uri else (key.removeprefix(f"{prefix}/") if prefix else key)
        segments = relative.split("/")
        if (
            (not persisted_uri and prefix and not key.startswith(f"{prefix}/"))
            or (len(segments) < 3 if persisted_uri else len(segments) != 3)
            or not all(segments)
            or any(segment in {".", ".."} for segment in segments[:-1])
            or any(ord(char) < 32 or ord(char) == 127 for char in key)
            or segments[-1] != "archive.tar"
            or len(key.encode("utf-8")) > 1024
        ):
            raise SandboxArchiveVerificationError("The archive object is outside the request scope")

        request: dict[str, Any] = {"Bucket": bucket, "Key": key, "ChecksumMode": "ENABLED"}
        owner = self.configuration.expected_bucket_owner
        if owner and bucket == self.configuration.bucket:
            # The policy names who owns its bucket; a receipt in a previous
            # bucket is read without that assertion.
            request["ExpectedBucketOwner"] = owner

        def inspect() -> dict[str, Any]:
            return self._get_client().head_object(**request)

        try:
            remote = await asyncio.to_thread(inspect)
        except (BotoCoreError, ClientError):
            raise SandboxArchiveVerificationError(
                "Unable to verify the sandbox archive in S3"
            ) from None
        actual_checksum = remote.get("ChecksumSHA256")
        actual_size = remote.get("ContentLength")
        if (
            not isinstance(actual_checksum, str)
            or not hmac.compare_digest(actual_checksum, checksum)
            or not isinstance(actual_size, int)
            or isinstance(actual_size, bool)
            or actual_size != size_bytes
            or remote.get("ChecksumType") not in {None, "FULL_OBJECT"}
        ):
            raise SandboxArchiveVerificationError("The S3 archive checksum or size does not match")
        version = remote.get("VersionId")
        return ArchiveReceipt(
            archive_uri=f"s3://{bucket}/{key}",
            bucket=bucket,
            key=key,
            sha256=digest,
            size_bytes=size_bytes,
            version_id=version if isinstance(version, str) else None,
        )
