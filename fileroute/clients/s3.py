from __future__ import annotations
from fileroute.models import Location, ServiceType
from fileroute.resolution import ResolvedLocation, relative_path

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

from fileroute.clients.base import BaseClient
from fileroute.item import ServiceItem


def check_s3_credentials() -> None:
    """Validate that AWS credentials are available for S3 operations."""
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            "No AWS credentials were found in the current environment or configuration."
        )

    frozen = credentials.get_frozen_credentials()
    if not frozen.access_key or not frozen.secret_key:
        raise RuntimeError("AWS credentials are incomplete for S3 operations.")


def _parse_s3_source_url(
    source_url: str, *, allow_empty_key: bool = False
) -> tuple[str, str]:
    """Parse an S3 URL into bucket/key.

    Returns ``(bucket, key)`` where ``bucket`` is the S3 bucket name and ``key``
    is the object key or prefix (possibly empty when ``allow_empty_key`` is set).
    Set ``allow_empty_key=True`` for bucket-root directory locators.
    """
    parsed = urlparse(source_url)
    scheme = parsed.scheme.lower()

    if scheme == "s3":
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
    elif scheme in {"http", "https"}:
        host = parsed.netloc.lower()
        path = parsed.path.lstrip("/")
        if host == "s3.amazonaws.com" or host.startswith("s3."):
            parts = path.split("/", 1)
            bucket = parts[0] if parts else ""
            key = parts[1] if len(parts) > 1 else ""
        elif ".s3." in host or host.endswith(".s3.amazonaws.com"):
            bucket = host.split(".s3", 1)[0]
            key = path
        else:
            raise ValueError(f"Unsupported S3 URL host: {parsed.netloc}")
    else:
        raise ValueError(f"Unsupported URL scheme for S3 source: {parsed.scheme}")

    if not bucket:
        raise ValueError(f"Could not parse bucket from source URL: {source_url}")
    if not allow_empty_key and not key:
        raise ValueError(f"Could not parse key from source URL: {source_url}")
    return bucket, key


class S3Client(BaseClient):
    auth_methods = ["aws_credentials"]

    def __init__(self, *, client: Any = None) -> None:
        self.client = client or boto3.client("s3")

    @classmethod
    def build_default(cls) -> "S3Client":
        check_s3_credentials()
        return cls()

    @classmethod
    def check_auth(cls) -> None:
        check_s3_credentials()

    def get_from_weburl(self, url: str) -> "S3Item":
        bucket, key = _parse_s3_source_url(url, allow_empty_key=True)
        return self.get_from_path(bucket=bucket, key=key)

    @classmethod
    def recognizes_url(cls, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return bool(host) and (parsed.scheme == "s3" or (
            parsed.scheme in {"http", "https"} and
            (host == "s3.amazonaws.com" or host.startswith("s3.")
             or ".s3." in host or host.endswith(".s3.amazonaws.com"))
        ))

    @classmethod
    def parse_location(cls, location: Location) -> ResolvedLocation:
        parsed = urlparse(location.path)
        if cls.recognizes_url(location.path):
            bucket, key = _parse_s3_source_url(location.path, allow_empty_key=True)
            context = {"bucket": bucket}
            remote_path = relative_path(key)
            if key.endswith("/") and remote_path:
                remote_path += "/"
        elif not parsed.scheme:
            if not location.bucket:
                raise ValueError(f"S3 path requires bucket: {location.path}")
            context = {"bucket": location.bucket}
            remote_path = relative_path(location.path)
            if location.path.endswith("/") and remote_path:
                remote_path += "/"
        else:
            raise ValueError(f"S3 requires an S3 URL or bucket-scoped path: {location.path}")
        if location.service_id:
            id_bucket, separator, id_key = location.service_id.partition(":")
            if not separator or id_bucket != context["bucket"] or id_key.strip("/") != remote_path.strip("/"):
                raise ValueError(f"serviceId {location.service_id!r} conflicts with S3 location {location.path!r}")
        return ResolvedLocation(ServiceType.S3, remote_path, context=context)

    def get_from_id(self, item_id: str) -> "S3Item":
        bucket, separator, key = item_id.partition(":")
        if not separator or not bucket:
            raise ValueError(f"Invalid S3 serviceId: {item_id!r}; expected bucket:key")
        return self.get_from_path(bucket=bucket, key=key)

    def get_from_location(self, location: Location) -> "S3Item":
        if location.service_id:
            return self.get_from_id(location.service_id)
        if location.bucket:
            return self.get_from_path(bucket=location.bucket,
                                      key=location.remote_path or "")
        return self.get_from_weburl(location.path)

    def resolve_location(self, location: Location) -> ResolvedLocation:
        item = self.get_from_location(location)
        if item.is_directory:
            if not item.key:
                self.client.head_bucket(Bucket=item.bucket)
            else:
                result = self.client.list_objects_v2(
                    Bucket=item.bucket, Prefix=item.key, MaxKeys=1
                )
                if not result.get("KeyCount") and not result.get("Contents"):
                    raise FileNotFoundError(f"S3 prefix not found: {item.source_url}")
        return ResolvedLocation(ServiceType.S3, location.remote_path, item.id,
                                "Directory" if item.is_directory else "File",
                                {"bucket": item.bucket})

    def get_from_path(self, *, bucket: str, key: str = "") -> "S3Item":
        trailing_slash = key.endswith("/")
        key = key.strip("/")
        if trailing_slash and key:
            key = f"{key}/"
        if not key or key.endswith("/"):
            return S3Item(client=self, bucket=bucket, key=key, is_directory=True)

        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return S3Item(client=self, bucket=bucket, key=key, is_directory=False)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NotFound", "NoSuchKey"}:
                prefix = key if key.endswith("/") else f"{key}/"
                response = self.client.list_objects_v2(
                    Bucket=bucket, Prefix=prefix, MaxKeys=1
                )
                if response.get("KeyCount", 0) > 0:
                    return S3Item(
                        client=self, bucket=bucket, key=prefix, is_directory=True
                    )
            raise

    def _list_objects(
        self, *, bucket: str, prefix: str = "", delimiter: str | None = None
    ) -> tuple[list[dict[str, Any]], list[str]]:
        contents: list[dict[str, Any]] = []
        prefixes: list[str] = []
        continuation_token: str | None = None
        while True:
            params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
            if delimiter is not None:
                params["Delimiter"] = delimiter
            if continuation_token:
                params["ContinuationToken"] = continuation_token
            response = self.client.list_objects_v2(**params)
            contents.extend(response.get("Contents", []))
            prefixes.extend(
                str(entry.get("Prefix", ""))
                for entry in response.get("CommonPrefixes", [])
                if entry.get("Prefix")
            )
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                break
        return contents, prefixes

    def resolve_descendant(
        self, *, bucket: str, parent_key: str, name: str
    ) -> "S3Item":
        prefix = (
            parent_key
            if parent_key.endswith("/") or not parent_key
            else f"{parent_key}/"
        )
        return self.get_from_path(bucket=bucket, key=f"{prefix}{name}")

    def list_children(
        self, *, bucket: str, prefix: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        return self._list_objects(bucket=bucket, prefix=prefix, delimiter="/")

    def scan_descendants(self, *, bucket: str, prefix: str) -> list[dict[str, Any]]:
        contents, _ = self._list_objects(bucket=bucket, prefix=prefix)
        return contents


class S3Item(ServiceItem):
    def __init__(
        self, *, client: S3Client, bucket: str, key: str, is_directory: bool
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.key = key
        self._is_directory = is_directory

    def move(self, new_parent_id: str) -> "S3Item":
        raise NotImplementedError("Moving S3 items is not implemented")

    @property
    def id(self) -> str:
        return f"{self.bucket}:{self.key}"

    @property
    def name(self) -> str:
        cleaned = self.key.rstrip("/")
        if not cleaned:
            return self.bucket
        return Path(cleaned).name

    @property
    def path(self) -> str:
        return self.key

    @property
    def service_type(self) -> ServiceType:
        return ServiceType.S3

    @property
    def source_url(self) -> str:
        return f"s3://{self.bucket}/{self.key}" if self.key else f"s3://{self.bucket}"

    @property
    def is_directory(self) -> bool:
        return self._is_directory

    @property
    def children(self) -> list["S3Item"]:
        if not self.is_directory:
            return []
        indexed = self._indexed_children()
        if indexed is not None:
            return indexed
        self.refresh(include_children=True)
        return self._indexed_children() or []

    def refresh(self, *, include_children: bool = True) -> "S3Item":
        if not self.is_directory:
            return self
        if not include_children:
            return self
        self._invalidate_traversal()

        prefix = self.key if self.key.endswith("/") or not self.key else f"{self.key}/"
        contents, prefixes = self.client.list_children(
            bucket=self.bucket, prefix=prefix
        )

        children: list[S3Item] = []
        for child_prefix in prefixes:
            if child_prefix and child_prefix != prefix:
                child_item = S3Item(
                    client=self.client,
                    bucket=self.bucket,
                    key=child_prefix,
                    is_directory=True,
                )
                children.append(child_item)

        for item in contents:
            child_key = str(item.get("Key", ""))
            if not child_key or child_key == prefix or child_key.endswith("/"):
                continue
            child_item = S3Item(
                client=self.client,
                bucket=self.bucket,
                key=child_key,
                is_directory=False,
            )
            children.append(child_item)

        self._cache_children(children)
        return self

    def _scan_descendants(self) -> list["ServiceItem"]:
        prefix = self.key if self.key.endswith("/") or not self.key else f"{self.key}/"
        contents = self.client.scan_descendants(bucket=self.bucket, prefix=prefix)
        items: dict[str, S3Item] = {}

        for entry in contents:
            key = str(entry.get("Key", ""))
            if not key or key == prefix or key.endswith("/"):
                continue
            relative = key[len(prefix) :] if prefix else key
            parts = [part for part in relative.split("/") if part]
            current = prefix
            parent: S3Item = self
            for part in parts[:-1]:
                current = f"{current}{part}/"
                directory = items.get(current)
                if directory is None:
                    directory = S3Item(
                        client=self.client,
                        bucket=self.bucket,
                        key=current,
                        is_directory=True,
                    )
                    directory._traversal_parent_id = str(parent.id)
                    items[current] = directory
                parent = directory
            file_item = S3Item(
                client=self.client, bucket=self.bucket, key=key, is_directory=False
            )
            file_item._traversal_parent_id = str(parent.id)
            items[key] = file_item
        return sorted(items.values(), key=lambda item: (item.path, item.name))

    def _resolve_children(self, name: str) -> list["ServiceItem"]:
        indexed = self._indexed_children()
        if indexed is not None:
            return [child for child in indexed if child.name == name]
        try:
            item = self.client.resolve_descendant(
                bucket=self.bucket, parent_key=self.key, name=name
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NotFound", "NoSuchKey"}:
                return []
            raise
        return [item]

    def download(self, target_dir: str | Path) -> None:
        if self.is_directory:
            super().download(target_dir)
            return
        target = Path(target_dir)
        if target.is_dir():
            target = target / self.name
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.client.download_file(self.bucket, self.key, str(target))


__all__ = ["S3Client", "S3Item", "check_s3_credentials"]
