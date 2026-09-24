from __future__ import annotations
import json
from pathlib import Path
import re
from enum import Enum
from typing import Any, ClassVar, Dict, Literal, Optional, Union, cast
import requests
from google.auth.credentials import Credentials
from fileroute.item import ServiceItem

from fileroute.auth.google import GoogleAuth
from fileroute.clients.base import BaseClient
from fileroute.exceptions import GoogleApiError, GoogleDriveError
from fileroute.models import ServiceType
import pydantic
from pydantic import BeforeValidator, Field
from typing import Annotated


GDriveKind = Annotated[
    Literal["drive", "file"], BeforeValidator(lambda v: v.replace("drive#", ""))
]
GDriveParents = Annotated[list[str], Field(default_factory=list)]


class GDriveApiFile(pydantic.BaseModel, validate_assignment=True):
    model_config = pydantic.ConfigDict()

    kind: Annotated[GDriveKind, Literal["file"]] = "file"
    id: Optional[str] = None
    name: Optional[str] = None
    mimeType: Optional[str] = None
    parents: GDriveParents
    webViewLink: Optional[str] = None
    driveId: Optional[str] = None


class GDriveApiDrive(pydantic.BaseModel, validate_assignment=True):
    kind: Annotated[GDriveKind, Literal["drive"]] = "drive"
    id: Optional[str] = None
    name: Optional[str] = None


class GoogleBaseClient(BaseClient):
    """Shared Google client base: auth lifecycle and HTTP transport helpers.

    Provides the Bearer-token header, a unified ``_request()`` method with
    structured error handling, and stream-to-disk / stream-to-bytes helpers.
    Concrete subclasses supply ``api_error_cls`` to choose which exception is
    raised on HTTP failures.

    Design note: this class intentionally separates low-level HTTP transport
    from drive-specific business logic so that non-Drive Google APIs (Sheets,
    etc.) could extend it without carrying Drive-specific state.

    """

    api_error_cls = GoogleApiError

    # Satisfy BaseClient abstract requirements at the intermediate level so
    # that direct subclasses only need to override if they want custom behaviour.
    auth_methods: ClassVar[list[str]] = ["adc", "service_account", "user_oauth"]

    def __init__(
        self,
        auth: GoogleAuth | None = None,
        *,
        credentials: Credentials | None = None,
        session: requests.Session | None = None,
        timeout: int = 120,
    ) -> None:

        self.session = session or requests.Session()
        self.timeout = timeout

        if auth is not None and credentials is not None:
            raise ValueError("Provide either auth or credentials, not both.")

        if credentials is not None:
            self._auth = GoogleAuth(credentials)
        elif auth is not None:
            self._auth = auth
        else:
            raise ValueError("GoogleBaseClient requires either auth or credentials.")

    @property
    def _hdrs(self) -> dict[str, str]:
        if not self._auth._creds.valid:
            self.refresh()
        return {"Authorization": f"Bearer {self._auth.credentials.token}"}

    def refresh(self) -> None:
        self._auth.refresh()

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        merged_headers = {**self._hdrs, **headers}

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=merged_headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise self.api_error_cls(f"HTTP request failed: {exc}") from exc

        if response.ok:
            return response

        try:
            payload = response.json()
        except ValueError:
            payload = None

        message = f"Google API request failed with status {response.status_code}"
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                message = f"{message}: {error['message']}"

        raise self.api_error_cls(
            message,
            status_code=response.status_code,
            response_text=response.text,
            response_json=payload,
        )

    @staticmethod
    def _write_stream_to_path(resp: requests.Response, output_path: str | Path) -> str:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        return str(destination)

    @staticmethod
    def _read_stream_to_bytes(resp: requests.Response) -> bytes:
        return b"".join(
            chunk for chunk in resp.iter_content(chunk_size=1024 * 1024) if chunk
        )

    @classmethod
    def build_default(cls) -> "GoogleBaseClient":
        """Construct from environment variables / settings.

        Reads ``GOOGLE_AUTH_MODE`` (and associated credentials) from the
        environment or a ``.env`` file.  Override in concrete subclasses to
        return the exact subclass type.
        """
        return cls(auth=GoogleAuth.from_settings())

    @classmethod
    def check_auth(cls) -> None:
        """Validate that Google credentials are available.

        Calls :meth:`~fileroute.auth.google.GoogleAuth.from_settings` which
        raises :class:`~fileroute.exceptions.GoogleAuthError` if the
        credentials are missing or invalid.
        """
        GoogleAuth.from_settings()


DRIVE_URL = "https://www.googleapis.com/drive/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

FOLDER_MIME = "application/vnd.google-apps.folder"


class GoogleMimeTypes(Enum):
    """Google-native MIME types."""

    DOC = "application/vnd.google-apps.document"
    SHEET = "application/vnd.google-apps.spreadsheet"
    SLIDE = "application/vnd.google-apps.presentation"
    DRAW = "application/vnd.google-apps.drawing"
    FORM = "application/vnd.google-apps.form"
    JAM = "application/vnd.google-apps.jam"
    APP = "application/vnd.google-apps.script"
    SHORT = "application/vnd.google-apps.shortcut"


DEFAULT_EXPORTS = {
    GoogleMimeTypes.DOC.value: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    GoogleMimeTypes.SHEET.value: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    GoogleMimeTypes.SLIDE.value: "application/pdf",
    GoogleMimeTypes.DRAW.value: "application/pdf",
    GoogleMimeTypes.FORM.value: "application/pdf",
    GoogleMimeTypes.JAM.value: "application/pdf",
    GoogleMimeTypes.APP.value: "application/zip",
}

ALT_EXPORTS = {
    "docx": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "pdf": "application/pdf",
}


class GoogleDriveClient(GoogleBaseClient):
    """Google Drive client (ID-first) with read/write and full export coverage.

    Supports My Drive and Shared Drives via the Drive REST API v3.

    Instantiate via a :class:`~fileroute.auth.google.GoogleAuth` object::

        auth = GoogleAuth.from_adc()
        client = GoogleDriveClient(auth)

        # Or from environment variables / .env file:
        client = GoogleDriveClient.build_default()

    The *credentials* keyword argument is a low-level escape hatch kept for
    tests only; prefer :class:`~fileroute.auth.google.GoogleAuth` in all
    production code.

    The class is registered as the ``"googledrive"`` provider via the
    Selected by ServiceType through fileroute.clients.get_provider.


    NOTE: may need to bring over resourceKey for handling google drive items that you are not explicitly a member of.
    """

    api_error_cls = GoogleDriveError
    file_fields = list(GDriveApiFile.model_fields.keys())

    def __init__(
        self,
        auth: GoogleAuth | None = None,
        *,
        credentials: Credentials | None = None,
        session: requests.Session | None = None,
        timeout: int = 120,
    ):
        super().__init__(
            auth=auth, credentials=credentials, session=session, timeout=timeout
        )

    @staticmethod
    def _is_google_workspace_file(file_mime_type: str) -> bool:
        return file_mime_type in {mime.value for mime in GoogleMimeTypes}

    def list_files(
        self,
        folder_file_id: str | None = None,
        queries: list[str] | None = None,
        params: Dict[str, Any] | None = None,
        page_size: int = 100,
    ) -> list[GDriveApiFile]:
        """

        List all files the authenticated user has access to.

        References
        ---------
        [list api reference](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list)
        [search files guide](https://developers.google.com/workspace/drive/api/guides/search-files)
        [query terms reference](https://developers.google.com/workspace/drive/api/guides/ref-search-terms)
        """
        files = []

        if params is None:
            params = {}

        if queries is None:
            queries = []

        fields = GDriveApiFile.model_fields.keys()
        if "fields" not in params:
            params["fields"] = f"nextPageToken, files({', '.join(fields)})"

        if "pageSize" not in params:
            params["pageSize"] = page_size
        params.setdefault("supportsAllDrives", "true")
        params.setdefault("includeItemsFromAllDrives", "true")

        if folder_file_id:
            queries.append(f"'{folder_file_id}' in parents")

        if queries:
            params["q"] = " and ".join(queries)
        if params:
            params.update(params)

        page_token = None
        while True:
            if page_token:
                params["pageToken"] = page_token

            resp = self._request("GET", f"{DRIVE_URL}/files", params=params).json()
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return [GDriveApiFile(**f) for f in files]

    def list_children(
        self, parent_id: str, *, drive_id: str | None = None, name: str | None = None
    ) -> list[GDriveApiFile]:
        queries = [f"'{parent_id}' in parents", "trashed = false"]
        if name is not None:
            escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
            queries.append(f"name = '{escaped_name}'")
        params: dict[str, Any] = {}
        if drive_id:
            params.update({"corpora": "drive", "driveId": drive_id})
        return self.list_files(queries=queries, params=params)

    def scan_descendants(self, *, drive_id: str | None = None) -> list[GDriveApiFile]:
        params: dict[str, Any] = {}
        if drive_id:
            params.update({"corpora": "drive", "driveId": drive_id})
        return self.list_files(queries=["trashed = false"], params=params)

    def resolve_descendant(
        self, *, parent_id: str, name: str, drive_id: str | None = None
    ) -> list[GDriveApiFile]:
        return self.list_children(parent_id, drive_id=drive_id, name=name)

    def list_drives(self, *, page_size: int = 100) -> list[GDriveApiDrive]:
        """List all Shared Drives the authenticated user has access to.

        References
        ----------

        [list drives api reference](https://developers.google.com/workspace/drive/api/reference/rest/v3/drives/list)

        """

        drives = []
        page_token = None

        while True:
            params: dict[str, str] = {
                "pageSize": str(page_size),
                "fields": f"nextPageToken,drives({', '.join(GDriveApiDrive.model_fields.keys())})",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token

            resp = self._request("GET", f"{DRIVE_URL}/drives", params=params).json()
            drives.extend(resp.get("drives", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return [GDriveApiDrive(**f) for f in drives]

    def get_file(self, file_id: str, **kwargs) -> GDriveApiFile:

        endpoint = f"{DRIVE_URL}/files/{file_id}"
        response = self._request(
            "GET",
            endpoint,
            params={
                "supportsAllDrives": "true",
                "fields": ",".join(GDriveApiFile.model_fields.keys()),
                **kwargs,
            },
        )

        return GDriveApiFile(**response.json())

    def infer_export_mime_type(self, file_id: str) -> Optional[str]:
        file_metadata = self.get_file(file_id, fields="mimeType")
        source_mime = file_metadata.mimeType or ""
        return DEFAULT_EXPORTS.get(source_mime)

    def download_file(
        self,
        file_id: str,
        output_path: Optional[str] = None,
        mime_type: Optional[str] = None,
        acknowledge_abuse: bool = False,
        byte_range: Optional[str] = None,
        supports_all_drives: bool = True,
        **kwargs,
    ) -> Union[bytes, str]:
        def build_params():
            params = {
                "alt": "media",
                "supportsAllDrives": str(supports_all_drives).lower(),
            }
            if acknowledge_abuse:
                params["acknowledgeAbuse"] = "true"
            return params

        def build_headers():
            headers = dict(self._hdrs)
            if byte_range:
                headers["Range"] = byte_range
            return headers

        file_metadata = self.get_file(file_id, fields="mimeType")
        file_mime_type = file_metadata.mimeType or ""

        if self._is_google_workspace_file(file_mime_type):
            return self.export_file(
                file_id=file_id,
                mime_type=mime_type,
                output_path=output_path,
                supports_all_drives=supports_all_drives,
                **kwargs,
            )

        response = self._request(
            "GET",
            f"{DRIVE_URL}/files/{str(file_id)}",
            headers=build_headers(),
            params=build_params(),
            stream=True,
        )

        if output_path:
            return self._write_stream_to_path(response, output_path)

        return self._read_stream_to_bytes(response)

    def export_file(
        self,
        file_id: str,
        mime_type: Optional[str] = None,
        output_path: Optional[str] = None,
        supports_all_drives: bool = True,
        **kwargs,
    ) -> Union[bytes, str]:
        def build_params(target_mime: str):
            return {
                "mimeType": target_mime,
                "supportsAllDrives": str(supports_all_drives).lower(),
            }

        if mime_type is None:
            inferred_mime = self.infer_export_mime_type(file_id)
            if inferred_mime is None:
                raise ValueError(
                    f"Could not infer export MIME type for file {file_id}. "
                    "Please provide mime_type parameter explicitly."
                )
            mime_type = inferred_mime

        response = self._request(
            "GET",
            f"{DRIVE_URL}/files/{str(file_id)}/export",
            params=build_params(mime_type),
            stream=True,
        )

        if output_path:
            return self._write_stream_to_path(response, output_path)

        return self._read_stream_to_bytes(response)

    def create_file(
        self,
        name: str,
        parent_id: str,
        content: bytes,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        supports_all_drives: bool = True,
        **kwargs,
    ) -> GDriveItem:
        def build_metadata():
            file_metadata = {"name": name, "parents": [parent_id]}
            if metadata:
                file_metadata.update(metadata)
            return file_metadata

        def build_multipart_body(media, file_metadata, mime_type):
            boundary = "END_OF_PART_1234567890"
            headers = {
                **self._hdrs,
                "Content-Type": f"multipart/related; boundary={boundary}",
            }

            body = (
                (
                    f"--{boundary}\r\n"
                    "Content-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{json.dumps(file_metadata)}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode("utf-8")
                + media
                + f"\r\n--{boundary}--\r\n".encode("utf-8")
            )

            return headers, body

        def build_params():
            return {
                "uploadType": "multipart",
                "supportsAllDrives": str(supports_all_drives).lower(),
            }

        if not content:
            raise ValueError("Must provide content")

        mime_type = mime_type or "application/octet-stream"
        file_metadata = build_metadata()
        headers, body = build_multipart_body(content, file_metadata, mime_type)
        params = build_params()

        response = self._request(
            "POST", UPLOAD_URL, headers=headers, params=params, data=body
        )
        api_metadata = GDriveApiFile(**response.json())
        return GDriveItem._from_api_response(api_metadata=api_metadata, client=self)

    def update_file(
        self,
        id: str,
        params: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        file_in_bytes_or_path: Optional[Union[str, bytes]] = None,
        mime_type: Optional[str] = None,
        **kwargs,
    ) -> GDriveItem:

        def params_metadata_and_media(
            media,
            metadata,
            mime_type,
            upload_type: Literal["multipart", "resumable"] = "multipart",
        ):
            boundary = "END_OF_PART_1234567890"
            headers = {
                **self._hdrs,
                "Content-Type": f"multipart/related; boundary={boundary}",
            }

            data = (
                (
                    f"--{boundary}\r\n"
                    "Content-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{json.dumps(metadata)}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode("utf-8")
                + media
                + f"\r\n--{boundary}--\r\n".encode("utf-8")
            )

            params = {"uploadType": upload_type, "supportsAllDrives": True}
            return {"headers": headers, "data": data, "params": params}

        def params_metadata_only(metadata):
            headers = {**self._hdrs, "Content-Type": "application/json"}
            return {
                "headers": headers,
                "json": metadata,
                "params": {"supportsAllDrives": True},
            }

        def params_media_only(media, mime_type):
            headers = {**self._hdrs, "Content-Type": mime_type}
            return {
                "headers": headers,
                "data": media,
                "params": {"supportsAllDrives": True, "uploadType": "media"},
            }

        def params_default():
            headers = {**self._hdrs}
            return {"headers": headers, "params": {"supportsAllDrives": True}}

        file_in_bytes: bytes | None = None
        if isinstance(file_in_bytes_or_path, str):
            with open(file_in_bytes_or_path, "rb") as handle:
                file_in_bytes = handle.read()
        elif isinstance(file_in_bytes_or_path, bytes):
            file_in_bytes = file_in_bytes_or_path

        if file_in_bytes and metadata:
            mime_type = mime_type or "application/octet-stream"
            request_params = params_metadata_and_media(
                file_in_bytes, metadata, mime_type
            )
        elif file_in_bytes and not metadata:
            mime_type = mime_type or "application/octet-stream"
            request_params = params_media_only(file_in_bytes, mime_type)
        elif file_in_bytes is None and metadata:
            request_params = params_metadata_only(metadata)
        else:
            request_params = params_default()

        if request_params.get("params", {}).get("fields") is None:
            request_params["params"]["fields"] = ",".join(self.file_fields)

        if params:
            request_params["params"].update(params)
        response = self._request("PATCH", f"{UPLOAD_URL}/{id}", **request_params)
        api_metadata = GDriveApiFile(**response.json())
        return GDriveItem._from_api_response(api_metadata=api_metadata, client=self)

    def create_folder(self, parent_folder_id: str, name: str) -> GDriveItem:
        headers = {**self._hdrs, "Content-Type": "application/json; charset=UTF-8"}
        payload = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_folder_id]}
        response = self._request(
            "POST",
            f"{DRIVE_URL}/files",
            headers=headers,
            params={"supportsAllDrives": "true"},
            data=json.dumps(payload),
        )
        api_metadata = GDriveApiFile(**response.json())
        return GDriveItem._from_api_response(api_metadata=api_metadata, client=self)

    def get_from_weburl(self, url: str) -> GDriveItem:
        """Resolve a Google Drive file or folder URL."""
        return self.get_from_id(self._extract_id_from_url(str(url)))

    def get_from_id(self, file_id: str) -> GDriveItem:
        """Resolve a Google Drive item ID."""
        metadata = self.get_file(file_id)
        parents = metadata.parents or [None]
        if len(parents) > 1:
            raise ValueError(
                f"Item {metadata.name} has multiple parents, which is not "
                "supported by GDriveItem model"
            )
        return GDriveItem._from_api_response(api_metadata=metadata, client=self)

    def get_from_path(self, drive_name: str, path: str = "") -> GDriveItem:
        """Resolve a path relative to a My Drive or Shared Drive root."""
        normalized_drive_name = drive_name.lower().replace(" ", "").strip()

        if normalized_drive_name == "mydrive":
            metadata = self.get_file("root")
            root = GDriveItem._from_api_response(
                metadata, self, path="", container_id=metadata.id
            )
        elif normalized_drive_name == "sharedwithme":
            raise ValueError(
                "'Shared with me' has no stable hierarchy root; resolve an item by URL"
            )
        else:
            matches = [
                drive
                for drive in self.list_drives()
                if (drive.name or "").lower().replace(" ", "").strip()
                == normalized_drive_name
            ]
            if not matches:
                raise FileNotFoundError(f"Drive '{drive_name}' not found")
            if len(matches) > 1:
                raise ValueError(f"Drive name '{drive_name}' is ambiguous")
            drive = matches[0]
            root = GDriveItem(
                client=self,
                path="",
                id=drive.id,
                name=drive.name,
                mime_type=FOLDER_MIME,
                kind="drive",
                container_id=drive.id,
                drive_id=drive.id,
            )

        return root.get_path(path)

    @staticmethod
    def _extract_id_from_url(url: str) -> str:
        patterns = [
            r"/document/d/([a-zA-Z0-9_-]+)",
            r"/spreadsheets/d/([a-zA-Z0-9_-]+)",
            r"/presentation/d/([a-zA-Z0-9_-]+)",
            r"/file/d/([a-zA-Z0-9_-]+)",
            r"/folders/([a-zA-Z0-9_-]+)",
            r"(?:\?|&)id=([a-zA-Z0-9_-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        raise ValueError(f"Could not extract Google Drive ID from URL: {url}")


__all__ = ["GoogleBaseClient", "GoogleDriveClient", "GDriveItem"]


class GDriveItem(ServiceItem):
    """A Google Drive file or folder item backed by the Drive REST API.

    Whether an instance represents a file or a directory is determined at
    runtime by :attr:`is_directory` (based on ``mimeType``).


    TODO: have capability of event driven workflows: https://developers.google.com/workspace/events/guides/events-drive


    API Resource References
    -------
    https://developers.google.com/workspace/drive/api/reference/rest/v3/files#File
    """

    def __init__(
        self,
        client: GoogleDriveClient | None = None,
        path: str = "",
        id: str | None = None,
        name: str | None = None,
        source_url: str | None = None,
        parent_id: str | None = None,
        mime_type: str | None = None,
        kind: Optional[str] = None,
        container_id: str | None = None,
        drive_id: str | None = None,
    ):
        self.kind = kind
        self._client = client
        self._path = path
        self._id = id
        self._name = name
        self._source_url = source_url
        self._parent_id = parent_id
        self._mime_type = mime_type
        self._container_id = container_id
        self._drive_id = drive_id

    @classmethod
    def _from_api_response(
        cls,
        api_metadata: GDriveApiFile | GDriveApiDrive,
        client: "GoogleDriveClient",
        *,
        path: str | None = None,
        container_id: str | None = None,
        drive_id: str | None = None,
    ) -> "GDriveItem":
        api_metadata_dict = api_metadata.model_dump()
        if isinstance(api_metadata, GDriveApiDrive):
            api_metadata_dict["parent_id"] = None
            api_metadata_dict["mime_type"] = FOLDER_MIME
            api_metadata_dict["source_url"] = None
        else:
            parents = api_metadata_dict.pop("parents")
            if len(parents) > 1:
                raise ValueError(
                    "GDriveItem model only supports single parent; found multiple"
                )
            api_metadata_dict["parent_id"] = parents[0] if parents else None
            api_metadata_dict["source_url"] = api_metadata_dict.pop("webViewLink")
            api_metadata_dict["mime_type"] = api_metadata_dict.pop("mimeType")
            drive_id = drive_id or api_metadata_dict.pop("driveId", None)

        return cls(
            client=client,
            path=path if path is not None else str(api_metadata.name or ""),
            container_id=container_id,
            drive_id=drive_id,
            **api_metadata_dict,
        )

    @property
    def client(self) -> "GoogleDriveClient":
        if self._client is None:
            raise ValueError("Cannot access client: it was not provided.")
        return self._client

    @property
    def parent_id(self) -> Optional[str]:
        return self._parent_id

    @property
    def mime_type(self) -> Optional[str]:
        return self._mime_type

    @property
    def children(self) -> list["ServiceItem"]:
        """Direct children of this directory; empty list for files."""
        if not self.is_directory:
            return []
        indexed = self._indexed_children()
        if indexed is not None:
            return indexed

        children = self.client.list_children(self.id, drive_id=self._drive_id)
        child_items = [
            GDriveItem._from_api_response(
                api_metadata=child,
                client=self.client,
                path=self._child_path(child.name or "Untitled"),
                container_id=self._container_id,
                drive_id=self._drive_id,
            )
            for child in children
        ]
        return self._cache_children(child_items)

    def _child_path(self, name: str) -> str:
        return f"{self.path.rstrip('/')}/{name}".strip("/")

    def _resolve_children(self, name: str) -> list["ServiceItem"]:
        indexed = self._indexed_children()
        if indexed is not None:
            return [child for child in indexed if child.name == name]
        metadata = self.client.resolve_descendant(
            parent_id=self.id, name=name, drive_id=self._drive_id
        )
        return [
            GDriveItem._from_api_response(
                entry,
                self.client,
                path=self._child_path(entry.name or "Untitled"),
                container_id=self._container_id,
                drive_id=self._drive_id,
            )
            for entry in metadata
        ]

    def _scan_descendants(self) -> list["ServiceItem"]:
        metadata = self.client.scan_descendants(drive_id=self._drive_id)
        by_parent: dict[str, list[GDriveApiFile]] = {}
        for entry in metadata:
            if len(entry.parents) > 1:
                raise ValueError(f"Google Drive item {entry.id!r} has multiple parents")
            if entry.parents:
                by_parent.setdefault(entry.parents[0], []).append(entry)

        descendants: list[ServiceItem] = []
        pending: list[tuple[GDriveItem, GDriveApiFile]] = [
            (self, child) for child in by_parent.get(str(self.id), [])
        ]
        while pending:
            parent, entry = pending.pop(0)
            item = GDriveItem._from_api_response(
                entry,
                self.client,
                path=parent._child_path(entry.name or "Untitled"),
                container_id=self._container_id,
                drive_id=self._drive_id,
            )
            descendants.append(item)
            pending.extend((item, child) for child in by_parent.get(str(item.id), []))
        return descendants

    @property
    def id(self) -> str:
        if self._id is None:
            raise ValueError("Cannot access ID of an item that has no ID")
        return self._id

    @property
    def name(self) -> str:
        return self._name or "Untitled"

    @property
    def path(self) -> str:
        return self._path

    @property
    def source_url(self) -> str:
        return self._source_url or ""

    @property
    def is_directory(self) -> bool:
        return self._mime_type == FOLDER_MIME

    @property
    def service_type(self) -> ServiceType:
        return ServiceType.GOOGLE_DRIVE

    def refresh(self, *, include_children: bool = True) -> "GDriveItem":
        """Re-fetch raw metadata (and optionally children) from the API."""
        if self.id is None:
            raise ValueError("Cannot refresh an item with no ID")
        refreshed = self.client.get_file(
            self.id, fields="id,name,mimeType,parents,webViewLink"
        )
        old_name = self.name
        old_path = Path(self.path)
        self._invalidate_traversal()
        self._name = refreshed.name
        if self.path and old_path.name == old_name:
            self._path = str(old_path.with_name(self.name)).replace("\\", "/")
        self._mime_type = refreshed.mimeType
        self._source_url = refreshed.webViewLink
        self._parent_id = refreshed.parents[0] if refreshed.parents else None
        return self

    def export(
        self, target_mime_type: Optional[str] = None, output_path: Optional[str] = None
    ) -> Union[bytes, str]:
        """Export this item if it's a Google Workspace file, otherwise download it.

        Google Workspace files are exported to their default format or to
        *target_mime_type* if provided.  Non-Google files are downloaded as-is.
        """
        if not self.client._is_google_workspace_file(self.mime_type or ""):
            if output_path:
                self.download(target=output_path)
                return output_path
            return cast(Union[bytes, str], self.client.download_file(self.id))

        return self.client.export_file(
            file_id=self.id, mime_type=target_mime_type, output_path=output_path
        )

    def download(self, target: str | Path) -> None:
        """Download this item.

        Directories are walked recursively via :meth:`iter_files` and each
        leaf file is written relative to *target*.  Files are written
        directly; Google Workspace files are exported to their default format.
        """
        if self.is_directory:
            super().download(target)
            return
        target_path = Path(target)
        if target_path.is_dir():
            target_path = target_path / self.name

        target_path.parent.mkdir(parents=True, exist_ok=True)
        content = self.client.download_file(self.id)
        if isinstance(content, (bytes, bytearray, memoryview)):
            with open(target_path, "wb") as f:
                f.write(content)
        else:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(str(content))

    def move(self, new_parent_id: str) -> "ServiceItem":
        """Move this item to a new parent directory."""
        index = getattr(self, "_traversal_index", None)
        new_parent = (
            index.items_by_id.get(str(new_parent_id)) if index is not None else None
        )
        # Update the parent reference in the API
        params = {"addParents": new_parent_id}

        if self._parent_id:
            params["removeParents"] = self._parent_id

        self.client.update_file(id=self.id, params=params)
        self._invalidate_traversal()
        self._parent_id = new_parent_id
        if new_parent is not None:
            self._path = f"{new_parent.path.rstrip('/')}/{self.name}".strip("/")
        return self

    def add_comment(self, body: str) -> "ServiceItem":
        """Post a comment on this file via the Drive v3 comments API."""
        url = f"{DRIVE_URL}/files/{self.id}/comments"
        headers = {**self.client._hdrs, "Content-Type": "application/json"}
        self.client._request(
            "POST",
            url,
            headers=headers,
            params={"fields": "id"},
            json={"content": body},
        )
        return self
