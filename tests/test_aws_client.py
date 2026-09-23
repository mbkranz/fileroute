from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from sharedrive.clients.aws import S3Client, S3Item


def client_error(code: str = "404") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "not found"}}, "HeadObject")


class FakeS3Client:
    def __init__(
        self,
        *,
        head_objects: dict[tuple[str, str], dict[str, Any]] | None = None,
        head_errors: dict[tuple[str, str], ClientError] | None = None,
        lists: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.head_objects = head_objects or {}
        self.head_errors = head_errors or {}
        self.lists = lists or {}
        self.head_calls: list[dict[str, str]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.download_calls: list[tuple[str, str, str]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.head_calls.append({"Bucket": Bucket, "Key": Key})
        lookup = (Bucket, Key)
        if lookup in self.head_errors:
            raise self.head_errors[lookup]
        if lookup in self.head_objects:
            return self.head_objects[lookup]
        raise client_error()

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        lookup = (kwargs["Bucket"], kwargs.get("Prefix", ""))
        return self.lists.get(lookup, {"KeyCount": 0})

    def download_file(self, bucket: str, key: str, target: str) -> None:
        self.download_calls.append((bucket, key, target))


def test_get_from_weburl_resolves_s3_object() -> None:
    fake_s3 = FakeS3Client(
        head_objects={("example-bucket", "path/file.csv"): {"ContentLength": 3}}
    )
    client = S3Client(client=fake_s3)

    item = client.get_from_weburl("s3://example-bucket/path/file.csv")

    assert item.bucket == "example-bucket"
    assert item.key == "path/file.csv"
    assert item.name == "file.csv"
    assert item.path == "path/file.csv"
    assert item.source_url == "s3://example-bucket/path/file.csv"
    assert not item.is_directory


def test_s3_item_move_is_explicitly_unsupported() -> None:
    client = S3Client(client=FakeS3Client())
    item = S3Item(
        client=client, bucket="example-bucket", key="file.csv", is_directory=False
    )

    with pytest.raises(NotImplementedError, match="Moving S3 items"):
        item.move("new-parent")


def test_get_from_weburl_resolves_bucket_root_as_directory() -> None:
    fake_s3 = FakeS3Client()
    client = S3Client(client=fake_s3)

    item = client.get_from_weburl("s3://example-bucket")

    assert item.bucket == "example-bucket"
    assert item.key == ""
    assert item.name == "example-bucket"
    assert item.source_url == "s3://example-bucket"
    assert item.is_directory
    assert fake_s3.head_calls == []
    assert fake_s3.list_calls == []


def test_get_from_weburl_resolves_trailing_slash_prefix_as_directory() -> None:
    fake_s3 = FakeS3Client()
    client = S3Client(client=fake_s3)

    item = client.get_from_weburl("s3://example-bucket/archive/")

    assert item.bucket == "example-bucket"
    assert item.key == "archive/"
    assert item.name == "archive"
    assert item.source_url == "s3://example-bucket/archive/"
    assert item.is_directory
    assert fake_s3.head_calls == []
    assert fake_s3.list_calls == []


def test_get_from_weburl_resolves_prefix_after_missing_object() -> None:
    missing = client_error()
    fake_s3 = FakeS3Client(
        head_errors={("example-bucket", "archive"): missing},
        lists={("example-bucket", "archive/"): {"KeyCount": 1}},
    )
    client = S3Client(client=fake_s3)

    item = client.get_from_weburl("s3://example-bucket/archive")

    assert item.bucket == "example-bucket"
    assert item.key == "archive/"
    assert item.is_directory
    assert fake_s3.list_calls == [
        {"Bucket": "example-bucket", "Prefix": "archive/", "MaxKeys": 1}
    ]


def test_client_get_from_path_resolves_object_key() -> None:
    fake_s3 = FakeS3Client(
        head_objects={("example-bucket", "path/file.csv"): {"ContentLength": 3}}
    )
    client = S3Client(client=fake_s3)

    item = client.get_from_path(bucket="example-bucket", key="path/file.csv")

    assert item.bucket == "example-bucket"
    assert item.path == "path/file.csv"
    assert not item.is_directory


def test_client_get_from_path_resolves_bucket_root() -> None:
    fake_s3 = FakeS3Client()
    client = S3Client(client=fake_s3)

    item = client.get_from_path(bucket="example-bucket")

    assert item.bucket == "example-bucket"
    assert item.path == ""
    assert item.is_directory


def test_get_from_weburl_propagates_missing_object_and_prefix() -> None:
    missing = client_error()
    fake_s3 = FakeS3Client(
        head_errors={("example-bucket", "missing.csv"): missing},
        lists={("example-bucket", "missing.csv/"): {"KeyCount": 0}},
    )
    client = S3Client(client=fake_s3)

    with pytest.raises(ClientError) as exc_info:
        client.get_from_weburl("s3://example-bucket/missing.csv")

    assert exc_info.value is missing
    assert fake_s3.list_calls == [
        {"Bucket": "example-bucket", "Prefix": "missing.csv/", "MaxKeys": 1}
    ]


def test_s3_item_children_are_loaded_from_prefix_listing() -> None:
    fake_s3 = FakeS3Client(
        lists={
            ("example-bucket", "archive/"): {
                "CommonPrefixes": [{"Prefix": "archive/nested/"}],
                "Contents": [
                    {"Key": "archive/"},
                    {"Key": "archive/file.txt"},
                    {"Key": "archive/marker/"},
                ],
            }
        }
    )
    client = S3Client(client=fake_s3)
    item = S3Item(
        client=client, bucket="example-bucket", key="archive/", is_directory=True
    )

    children = item.children

    assert [(child.path, child.is_directory) for child in children] == [
        ("archive/file.txt", False),
        ("archive/nested/", True),
    ]
    assert all(child.parent_id == item.id for child in children)
    assert all(child.parent is item for child in children)
    assert fake_s3.list_calls == [
        {"Bucket": "example-bucket", "Prefix": "archive/", "Delimiter": "/"}
    ]


def test_s3_item_download_uses_boto3_download_file(tmp_path: Path) -> None:
    fake_s3 = FakeS3Client()
    client = S3Client(client=fake_s3)
    item = S3Item(
        client=client,
        bucket="example-bucket",
        key="archive/file.txt",
        is_directory=False,
    )

    item.download(tmp_path)

    assert fake_s3.download_calls == [
        ("example-bucket", "archive/file.txt", str(tmp_path / "file.txt"))
    ]


def test_s3_recursive_traversal_pages_and_synthesizes_directories() -> None:
    class PagingS3(FakeS3Client):
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            self.list_calls.append(kwargs)
            if "ContinuationToken" not in kwargs:
                return {
                    "IsTruncated": True,
                    "NextContinuationToken": "page-2",
                    "Contents": [{"Key": "archive/nested/a.txt"}],
                }
            return {
                "IsTruncated": False,
                "Contents": [
                    {"Key": "archive/nested/deeper/b.txt"},
                    {"Key": "archive/marker/"},
                ],
            }

    fake_s3 = PagingS3()
    item = S3Item(
        client=S3Client(client=fake_s3),
        bucket="example-bucket",
        key="archive/",
        is_directory=True,
    )

    descendants = list(item.iter_items())
    assert [(child.path, child.is_directory) for child in descendants] == [
        ("archive/nested/", True),
        ("archive/nested/a.txt", False),
        ("archive/nested/deeper/", True),
        ("archive/nested/deeper/b.txt", False),
    ]
    by_path = {child.path: child for child in descendants}
    assert by_path["archive/nested/"].parent is item
    assert by_path["archive/nested/a.txt"].parent is by_path["archive/nested/"]
    assert by_path["archive/nested/deeper/"].parent is by_path["archive/nested/"]
    assert (
        by_path["archive/nested/deeper/b.txt"].parent
        is by_path["archive/nested/deeper/"]
    )
    assert fake_s3.list_calls == [
        {"Bucket": "example-bucket", "Prefix": "archive/"},
        {
            "Bucket": "example-bucket",
            "Prefix": "archive/",
            "ContinuationToken": "page-2",
        },
    ]
