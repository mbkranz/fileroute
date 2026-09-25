from __future__ import annotations
from fileroute.models import Location, ServiceType
from fileroute.resolution import ResolvedLocation, relative_path

import json
import mimetypes
import time
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import quote, unquote, urlparse


import requests

from fileroute.clients.base import ClientCapabilities, BaseClient
from fileroute.clients._http import is_transient_status, retry_delay
from fileroute.exceptions import GraphApiDriveError, GraphApiSiteError, GraphApiError
from fileroute.item import ServiceItem

if TYPE_CHECKING:
    from fileroute.auth.microsoft import MicrosoftAuth


class SharepointClient(BaseClient):
    """SharePoint / OneDrive client backed by the Microsoft Graph API.

    Supports both app-only (client credentials) and delegated auth via
    :class:`~fileroute.auth.microsoft.MicrosoftAuth`.

    Instantiate via a :class:`~fileroute.auth.microsoft.MicrosoftAuth` object::

        auth = MicrosoftAuth.from_app_only(tenant_id, client_id, client_secret)
        client = SharepointClient(auth, host_url="contoso.sharepoint.com")

        # Or from environment variables / .env file:
        client = SharepointClient.build_default()

    The *access_token* keyword argument is a low-level escape hatch kept for
    tests only; prefer :class:`~fileroute.auth.microsoft.MicrosoftAuth` in all
    production code.

    The class is registered as the ``"sharepoint"`` provider via the
    Selected by ServiceType through fileroute.clients.get_provider.

    Microsoft Graph API reference:
        https://learn.microsoft.com/en-us/graph/api/resources/onedrive?view=graph-rest-1.0
    """

    auth_methods: ClassVar[list[str]] = ["app_only", "delegated"]
    capabilities: ClassVar[ClientCapabilities] = ClientCapabilities(
        supports_download=True, supports_upload=True
    )

    def __init__(
        self,
        auth: "MicrosoftAuth | None" = None,
        host_url: str = "norc.sharepoint.com",
        *,
        access_token: str | None = None,
        session: requests.Session | None = None,
        timeout: int = 120,
    ):
        self.host_url = host_url or "norc.sharepoint.com"

        if auth is not None and access_token is not None:
            raise ValueError("Provide either auth or access_token, not both.")

        if access_token is not None:
            self.access_token = access_token
        elif auth is not None:
            self.access_token = auth.access_token
        else:
            raise ValueError("SharepointClient requires either auth or access_token.")

        self.auth_header = {"Authorization": f"Bearer {self.access_token}"}
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def build_default(cls) -> "SharepointClient":
        """Construct from environment variables / settings.

        Reads ``SHAREPOINT_AUTH_MODE``, ``AZURE_TENANT_ID``, ``AZURE_CLIENT_ID``,
        ``AZURE_CLIENT_SECRET``, and ``SHAREPOINT_HOST_URL`` from the environment
        or a ``.env`` file via
        :class:`~fileroute.auth.settings.MicrosoftAuthConfig`.
        """
        from fileroute.auth.settings import MicrosoftAuthConfig

        config = MicrosoftAuthConfig()
        auth = config.to_auth()
        return cls(auth=auth, host_url=config.host_url)

    @classmethod
    def check_auth(cls) -> None:
        """Validate that Microsoft Graph credentials are available.

        Raises :class:`~fileroute.exceptions.GraphAuthError` if the
        credentials configured in the environment are missing or invalid.
        """
        from fileroute.auth.settings import MicrosoftAuthConfig

        config = MicrosoftAuthConfig()
        config.to_auth()

    def _request(
        self, method: str, url: str, *,
        error_cls: type[GraphApiError] = GraphApiDriveError,
        authenticated: bool = True, retry: bool = False, **kwargs: Any,
    ) -> requests.Response:
        """Translate transport errors; replay only explicitly safe operations.

        Presigned download URLs must not receive the Graph authorization header.
        Folder-creation POSTs are never replayed on ambiguous failures.
        """
        headers = kwargs.pop("headers", {})
        if authenticated:
            headers = {**self.auth_header, **headers}
        for attempt in range(4):
            try:
                response = self.session.request(
                    method, url, headers=headers, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:
                error = error_cls(f"Request error when calling {url}: {exc}")
                if retry and attempt < 3:
                    time.sleep(retry_delay(attempt + 1))
                    continue
                raise error from exc
            if 200 <= response.status_code < 300:
                return response
            try:
                payload = response.json()
            except ValueError:
                payload = None
            error = error_cls(
                f"Microsoft Graph request failed: {url}: {response.status_code} "
                f"{response.reason}: {response.text}",
                status_code=response.status_code, response_text=response.text,
                response_json=payload if isinstance(payload, dict) else None,
                response_headers=dict(response.headers),
            )
            if retry and attempt < 3 and is_transient_status(error.status_code):
                time.sleep(retry_delay(attempt + 1, headers=error.response_headers))
                continue
            raise error
        raise AssertionError("Unreachable retry loop")

    def _request_json(
        self, endpoint: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response_json = self._request("GET", endpoint, params=params, retry=True).json()
        current_json = response_json
        while "@odata.nextLink" in current_json:
            current_json = self._request(
                "GET", current_json["@odata.nextLink"], retry=True
            ).json()
            if "value" in response_json and "value" in current_json:
                response_json["value"].extend(current_json["value"])
        response_json.pop("@odata.nextLink", None)
        return response_json

    def get_site_id(self, site_name):
        endpoint = (
            f"https://graph.microsoft.com/v1.0/sites/{self.host_url}:/sites/{site_name}"
        )
        site_data = self._request(
            "GET", endpoint, error_cls=GraphApiSiteError, retry=True
        ).json()
        if "id" not in site_data:
            raise GraphApiDriveError(
                f"Site found but no 'id' returned.\n"
                f"Site Name: {site_name}\n"
                f"Response JSON: {json.dumps(site_data, indent=2)}"
            )
        return site_data["id"]

    def list_site_drives(self, site_id: str) -> list[dict[str, Any]]:
        data = self._request_json(
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
        )
        value = data.get("value", [])
        if not isinstance(value, list):
            raise GraphApiDriveError(f"Unexpected drives response for site '{site_id}'")
        return value

    def get_drive_id(self, site_id, drive_name: str | None = None):
        """
        Retrieves the default document drive associated with a SharePoint site.
        """
        if drive_name is not None:
            normalized_drive_name = drive_name.strip().strip("/")
            for drive in self.list_site_drives(site_id):
                drive_id = drive.get("id")
                if not isinstance(drive_id, str) or not drive_id.strip():
                    continue

                candidate_names = {
                    str(drive.get("name", "")).strip(),
                    str(drive.get("driveType", "")).strip(),
                }
                web_url = str(drive.get("webUrl", "")).strip()
                if web_url:
                    # Unquote the path to convert %20 back to spaces
                    decoded_path = unquote(urlparse(web_url).path)
                    candidate_names.add(Path(decoded_path).name)

                if normalized_drive_name in {
                    value for value in candidate_names if value
                }:
                    return drive_id

            raise GraphApiDriveError(
                f"Drive '{normalized_drive_name}' was not found for site '{site_id}'."
            )

        endpoint = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive"

        drive_data = self._request("GET", endpoint, retry=True).json()

        if "id" not in drive_data:
            raise GraphApiDriveError(
                f"Drive request succeeded but no 'id' field was returned.\n"
                f"Site ID: {site_id}\n"
                f"Response JSON:\n{json.dumps(drive_data, indent=2)}"
            )

        return drive_data["id"]

    def get_item_metadata(
        self,
        drive: str,
        *,
        item_path: str | None = None,
        item_id: str | None = None,
        fields: list[str] | None = None,
    ):
        """
        get item metadata based on relative file path or item id within the drive
        """
        if fields is None:
            fields = [
                "id",
                "name",
                "folder",
                "file",
                "parentReference",
                "webUrl",
                "lastModifiedDateTime",
            ]

        select_query = ",".join(fields)

        if item_id:
            endpoint = f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item_id}?$select={select_query}"
        else:
            normalized_itempath = str(item_path).strip() if item_path else "/"
            if not normalized_itempath:
                normalized_itempath = "/"
            if normalized_itempath == "/":
                endpoint = f"https://graph.microsoft.com/v1.0/drives/{drive}/root?$select={select_query}"
            else:
                if not normalized_itempath.startswith("/"):
                    normalized_itempath = f"/{normalized_itempath}"
                endpoint = f"https://graph.microsoft.com/v1.0/drives/{drive}/root:{quote(normalized_itempath, safe='/')}?$select={select_query}"

        return self._request_json(endpoint)

    def list_children(
        self, drive_id: str, item_id: str, *, fields: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if fields is None:
            fields = [
                "id",
                "name",
                "folder",
                "file",
                "parentReference",
                "webUrl",
                "lastModifiedDateTime",
            ]
        select_query = ",".join(fields)
        data = self._request_json(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/"
            f"{item_id}/children",
            params={"$select": select_query},
        )
        value = data.get("value", [])
        if not isinstance(value, list):
            raise GraphApiDriveError(
                f"Unexpected children response for item '{item_id}'"
            )
        return value

    def scan_descendants(self, *, drive_id: str) -> list[dict[str, Any]]:
        data = self._request_json(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta"
        )
        value = data.get("value", [])
        if not isinstance(value, list):
            raise GraphApiDriveError(
                f"Unexpected delta response for drive '{drive_id}'"
            )
        deduplicated: dict[str, dict[str, Any]] = {}
        for item in value:
            item_id = item.get("id")
            if item_id and "deleted" not in item:
                deduplicated[str(item_id)] = item
        return list(deduplicated.values())

    def resolve_descendant(
        self, *, drive_id: str, parent_path: str, name: str
    ) -> dict[str, Any]:
        item_path = f"/{parent_path.strip('/')}/{name}".replace("//", "/")
        return self.get_item_metadata(drive_id, item_path=item_path)

    def get_from_weburl(self, url: str) -> "SharepointItem":
        resolved = self._resolve_weburl(url)
        metadata = self.get_item_metadata(
            resolved["drive_id"], item_path=resolved["item_path"]
        )
        path = "" if resolved["item_path"] == "/" else resolved["item_path"].strip("/")
        return SharepointItem._from_api_response(metadata, self, path=path)

    @classmethod
    def recognizes_url(cls, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and (
            host == "sharepoint.com" or host.endswith(".sharepoint.com")
        )

    @classmethod
    def parse_location(cls, location: Location) -> ResolvedLocation:
        parsed = urlparse(location.path)
        context: dict[str, str] = {}
        remote_path: str | None = None
        if cls.recognizes_url(location.path):
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            if len(parts) < 2 or parts[0].lower() != "sites":
                raise ValueError(f"Cannot parse SharePoint site from URL: {location.path}")
            context["site"] = parts[1]
            if len(parts) > 2:
                context["drive"] = parts[2]
                remote_path = relative_path("/".join(parts[3:]))
        elif not parsed.scheme:
            remote_path = relative_path(location.path)
        elif parsed.scheme not in {"http", "https"}:
            raise ValueError(f"SharePoint requires an HTTP URL or scoped path: {location.path}")
        # Explicitly provided context is also accepted for a relative path.
        context.update({key: value for key in ("site", "site_id", "drive", "drive_id")
                        if (value := getattr(location, key)) is not None and key not in context})
        if not parsed.scheme and not (context.get("site") or context.get("site_id")):
            raise ValueError(f"SharePoint path requires site or siteId: {location.path}")
        if not parsed.scheme and not (context.get("drive") or context.get("drive_id")):
            raise ValueError(f"SharePoint path requires drive or driveId: {location.path}")
        return ResolvedLocation(ServiceType.SHAREPOINT, remote_path, context=context)

    def _location_ids(self, location: Location, *, need_site_id: bool = False) -> tuple[str | None, str]:
        parsed = urlparse(location.path)
        if self.recognizes_url(location.path):
            self.host_url = parsed.hostname or self.host_url
        site_id = location.site_id
        if not site_id and location.site and (need_site_id or not location.drive_id):
            site_id = self.get_site_id(location.site)
        drive_id = location.drive_id
        if not drive_id:
            if not site_id:
                raise ValueError(f"SharePoint location needs site/siteId: {location.path}")
            if not location.drive:
                if not self.recognizes_url(location.path):
                    raise ValueError(f"SharePoint location needs drive/driveId: {location.path}")
                drive_id = self.get_drive_id(site_id)
            else:
                drive_id = self.get_drive_id(site_id, drive_name=location.drive)
        return site_id, drive_id

    def get_from_id(self, item_id: str, *, drive_id: str, path: str = "") -> "SharepointItem":
        metadata = self.get_item_metadata(drive_id, item_id=item_id)
        return SharepointItem._from_api_response(metadata, self, path=path)

    def get_from_location(self, location: Location) -> "SharepointItem":
        if location.service_id:
            _, drive_id = self._location_ids(location)
            return self.get_from_id(location.service_id, drive_id=drive_id,
                                    path=location.remote_path or "")
        if location.drive_id or location.site or location.site_id:
            _, drive_id = self._location_ids(location)
            metadata = self.get_item_metadata(drive_id, item_path=location.remote_path or "/")
            return SharepointItem._from_api_response(metadata, self,
                                                     path=location.remote_path or "")
        return self.get_from_weburl(location.path)

    def resolve_location(self, location: Location) -> ResolvedLocation:
        site_id, drive_id = self._location_ids(location, need_site_id=True)
        item = self.get_from_location(location.model_copy(update={"drive_id": drive_id}))
        context = {key: value for key in ("site", "drive")
                   if (value := getattr(location, key)) is not None}
        if site_id:
            context["site_id"] = site_id
        context["drive_id"] = drive_id
        return ResolvedLocation(ServiceType.SHAREPOINT, location.remote_path,
                                item.id, "Directory" if item.is_directory else "File", context)

    def get_from_path(
        self, *, site_name: str, item_path: str = "/", library_name: str | None = None
    ) -> "SharepointItem":
        normalized_library = library_name.strip(" /") if library_name else None
        normalized_library = normalized_library or None
        normalized_path = item_path.strip()
        parts = [part for part in normalized_path.strip("/").split("/") if part]
        if normalized_library is None and parts:
            normalized_library = parts.pop(0)
        relative_path = f"/{'/'.join(parts)}" if parts else "/"

        site_id = self.get_site_id(site_name)
        drive_id = self.get_drive_id(site_id, drive_name=normalized_library)
        metadata = self.get_item_metadata(drive_id, item_path=relative_path)
        path = "" if relative_path == "/" else relative_path.strip("/")
        return SharepointItem._from_api_response(metadata, self, path=path)

    def _resolve_weburl(self, url: str) -> dict[str, str]:
        if not self.recognizes_url(url):
            raise ValueError(f"Invalid SharePoint URL: {url}")
        location = Location(path=url)
        self.parse_location(location).apply(location)
        # Historical URL-only call sites use the default library for a site URL.
        location.drive = location.drive or "Shared Documents"
        site_id, drive_id = self._location_ids(location, need_site_id=True)
        assert site_id is not None and location.site is not None
        return {
            "site_name": location.site,
            "site_id": site_id,
            "drive_name": location.drive,
            "drive_id": drive_id,
            "item_path": f"/{location.remote_path}" if location.remote_path else "/",
        }

    def download_content(self, drive_id=None, item_id=None, download_url=None):
        """takes in the components needed to download content --

        drive id and item id -- uses Oauth to download
        download_url -- uses a presigned url (note: if on VPN, need to use this option - I think)

        """

        if download_url:
            response = self._request("GET", download_url, authenticated=False, retry=True)
        elif drive_id and item_id:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"
            response = self._request("GET", url, retry=True)
        else:
            raise GraphApiDriveError("Need drive_id and item_id if not using download_url")
        return response.content

    def _put_file(self, url: str, local_file_path: str | Path) -> dict[str, Any]:
        local_path = Path(local_file_path)
        content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        # Reopen the file for each attempt so a failed upload cannot replay an
        # exhausted stream. PUT content at a fixed path/item is replay-safe.
        for attempt in range(4):
            try:
                with local_path.open("rb") as stream:
                    return self._request(
                        "PUT", url, headers={"Content-Type": content_type},
                        data=stream,
                    ).json()
            except GraphApiDriveError as exc:
                if attempt == 3 or not (
                    exc.status_code is None or is_transient_status(exc.status_code)
                ):
                    raise
                time.sleep(retry_delay(attempt + 1, headers=exc.response_headers))
        raise AssertionError("Unreachable retry loop")

    def create_file(
        self, *, site_name: str, folder_path: str, local_file_path: str | Path
    ) -> "SharepointItem":
        """Create or replace a file at a drive-relative folder path."""
        site_id = self.get_site_id(site_name)
        drive_id = self.get_drive_id(site_id)
        local_path = Path(local_file_path)
        remote_path = f"{folder_path.rstrip('/')}/{local_path.name}"
        payload = self._put_file(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}"
            f"/root:/{remote_path.lstrip('/')}:/content",
            local_path,
        )
        return SharepointItem._from_api_response(
            payload, self, path=remote_path.strip("/")
        )

    def update_file(
        self, *, site_name: str, folder_path: str, local_file_path: str | Path
    ) -> "SharepointItem":
        """Replace the content of an existing file."""
        site_id = self.get_site_id(site_name)
        drive_id = self.get_drive_id(site_id)
        local_path = Path(local_file_path)
        remote_path = f"{folder_path.rstrip('/')}/{local_path.name}"
        metadata = self.get_item_metadata(drive_id, item_path=remote_path)
        item_id = metadata.get("id")
        if not item_id:
            raise FileNotFoundError(
                f"File {remote_path!r} was not found in site {site_name!r}"
            )
        payload = self._put_file(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}"
            f"/items/{item_id}/content",
            local_path,
        )
        return SharepointItem._from_api_response(
            payload, self, path=remote_path.strip("/")
        )

    def upload_file(
        self,
        *,
        site_name: str,
        folder_path: str,
        local_file_path: str | Path,
        create_if_missing: bool = True,
    ) -> "SharepointItem":
        """Update a file, optionally creating it when it does not exist."""
        try:
            return self.update_file(
                site_name=site_name,
                folder_path=folder_path,
                local_file_path=local_file_path,
            )
        except GraphApiDriveError as exc:
            if exc.status_code != 404:
                raise
        except FileNotFoundError:
            pass

        if not create_if_missing:
            remote_path = f"{folder_path.rstrip('/')}/{Path(local_file_path).name}"
            raise FileNotFoundError(
                f"File {remote_path!r} was not found in site {site_name!r}"
            )
        return self.create_file(
            site_name=site_name,
            folder_path=folder_path,
            local_file_path=local_file_path,
        )

    def upload_to_folder(
        self, folder_url: str, relative_path: Path, local_file_path: str | Path
    ) -> "SharepointItem":
        """Create or replace a file below an existing SharePoint folder URL.

        Missing child folders are created. The destination folder itself must
        already exist, preventing a mistaken URL from silently creating a new
        publication location. Graph's single-request PUT limit is 250 MB.
        """
        local = Path(local_file_path)
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ValueError(f"Unsafe relative upload path: {relative}")
        if local.stat().st_size > 250_000_000:
            raise ValueError(
                f"File exceeds Microsoft Graph's 250 MB PUT limit: {local}"
            )

        destination = self._resolve_weburl(folder_url)
        return self._upload_to_drive_folder(
            destination["drive_id"], destination["item_path"].strip("/"),
            relative, local,
        )

    def upload_to_location(
        self, location: Location, relative_path: Path, local_file_path: str | Path
    ) -> "SharepointItem":
        """Upload beneath a verified folder ID or a scoped provider path."""
        _, drive_id = self._location_ids(location)
        return self._upload_to_drive_folder(
            drive_id, location.remote_path or "", relative_path, local_file_path,
            folder_id=location.service_id,
        )

    def _upload_to_drive_folder(
        self, drive_id: str, folder_path: str, relative: Path,
        local: str | Path, *, folder_id: str | None = None,
    ) -> "SharepointItem":
        relative = Path(relative)
        local = Path(local)
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ValueError(f"Unsafe relative upload path: {relative}")
        if local.stat().st_size > 250_000_000:
            raise ValueError(f"File exceeds Microsoft Graph's 250 MB PUT limit: {local}")
        folder = (self.get_item_metadata(drive_id, item_id=folder_id)
                  if folder_id else self.get_item_metadata(drive_id, item_path=folder_path or "/"))
        if "folder" not in folder:
            raise ValueError(f"Publication destination is not a folder: {folder_path}")

        parent_id = folder["id"]
        for part in relative.parts[:-1]:
            folder_path = f"{folder_path}/{part}".strip("/")
            try:
                child = self.get_item_metadata(drive_id, item_path=folder_path)
            except GraphApiDriveError as exc:
                if exc.status_code != 404:
                    raise
                try:
                    response = self._request(
                        "POST",
                        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{parent_id}/children",
                        headers={"Content-Type": "application/json"},
                        json={
                            "name": part,
                            "folder": {},
                            "@microsoft.graph.conflictBehavior": "fail",
                        },
                    )
                except GraphApiDriveError as error:
                    if error.status_code != HTTPStatus.CONFLICT:
                        raise
                    child = self.get_item_metadata(drive_id, item_path=folder_path)
                else:
                    child = response.json()
            if "folder" not in child:
                raise ValueError(f"Upload path is occupied by a file: {folder_path}")
            parent_id = child["id"]

        remote_path = f"{folder_path}/{relative.name}".strip("/")
        payload = self._put_file(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}"
            f"/root:/{quote(remote_path, safe='/')}:/content",
            local,
        )
        return SharepointItem._from_api_response(payload, self, path=remote_path)


__all__ = ["SharepointClient", "SharepointItem"]


class SharepointItem(ServiceItem):
    """A SharePoint file or folder item backed by Microsoft Graph."""

    def __init__(
        self,
        client: "SharepointClient",
        api_payload: dict[str, Any] | None = None,
        path: str | None = None,
        id: str | None = None,
        name: str | None = None,
        source_url: str | None = None,
        parent_id: str | None = None,
        is_folder: bool = False,
    ):
        self.client = client
        self._api_payload = api_payload or {}
        self._path = path
        self._id = id
        self._name = name
        self._source_url = source_url
        self._parent_id = parent_id
        self._is_folder = is_folder
        self._drive_id = self._api_payload.get("parentReference", {}).get("driveId")

    def move(self, new_parent_id: str) -> "SharepointItem":
        raise NotImplementedError("Moving SharePoint items is not implemented")

    @classmethod
    def _from_api_response(
        cls,
        api_payload: dict[str, Any],
        client: "SharepointClient",
        current_rel_path: str = "",
        *,
        path: str | None = None,
    ) -> "SharepointItem":
        is_folder = "folder" in api_payload
        parent_id = api_payload.get("parentReference", {}).get("id")

        if path is None:
            relative_path = str(api_payload.get("relative_path", "")).strip()
            path = relative_path or api_payload.get("name", "")
            if current_rel_path:
                path = f"{current_rel_path}/{path}".strip("/")

        return cls(
            client=client,
            api_payload=api_payload,
            id=api_payload.get("id"),
            name=api_payload.get("name"),
            path=path,
            source_url=api_payload.get("webUrl"),
            parent_id=parent_id,
            is_folder=is_folder,
        )

    @property
    def id(self) -> str:
        return self._id or ""

    @property
    def name(self) -> str:
        return self._name or ""

    @property
    def path(self) -> str:
        return self._path or ""

    @property
    def service_type(self) -> ServiceType:
        return ServiceType.SHAREPOINT

    @property
    def source_url(self) -> str:
        return self._source_url or ""

    @property
    def is_directory(self) -> bool:
        return self._is_folder

    @property
    def children(self) -> list["SharepointItem"]:
        """Direct children of this directory; empty list for files."""
        if not self.is_directory:
            return []
        indexed = self._indexed_children()
        if indexed is not None:
            return indexed
        contents = self._api_payload.get("children")
        if contents is None:
            drive_id = self._drive_id
            if not drive_id:
                raise ValueError("Missing driveId in SharePoint folder metadata")
            contents = self.client.list_children(drive_id, self.id)

        child_items = [
            SharepointItem._from_api_response(
                child_payload, client=self.client, current_rel_path=self.path
            )
            for child_payload in contents
        ]
        return self._cache_children(child_items)

    def _scan_descendants(self) -> list["ServiceItem"]:
        if "children" in self._api_payload:
            return super()._scan_descendants()
        if not self._drive_id:
            raise ValueError("Missing driveId in SharePoint folder metadata")
        metadata_by_id = {
            str(entry["id"]): entry
            for entry in self.client.scan_descendants(drive_id=self._drive_id)
            if entry.get("id")
        }
        by_parent: dict[str, list[dict[str, Any]]] = {}
        for entry in metadata_by_id.values():
            parent_id = entry.get("parentReference", {}).get("id")
            if parent_id:
                by_parent.setdefault(str(parent_id), []).append(entry)

        descendants: list[ServiceItem] = []
        pending: list[tuple[SharepointItem, dict[str, Any]]] = [
            (self, child) for child in by_parent.get(self.id, [])
        ]
        while pending:
            parent, payload = pending.pop(0)
            item = SharepointItem._from_api_response(
                payload, client=self.client, current_rel_path=parent.path
            )
            descendants.append(item)
            pending.extend((item, child) for child in by_parent.get(item.id, []))
        return descendants

    def _resolve_children(self, name: str) -> list["ServiceItem"]:
        indexed = self._indexed_children()
        if indexed is not None:
            return [child for child in indexed if child.name == name]
        if not self._drive_id:
            return super()._resolve_children(name)
        try:
            payload = self.client.resolve_descendant(
                drive_id=self._drive_id, parent_path=self.path, name=name
            )
        except GraphApiDriveError as exc:
            if exc.status_code == 404:
                return []
            raise
        item = SharepointItem._from_api_response(
            payload, client=self.client, current_rel_path=self.path
        )
        return [item]

    def refresh(self, *, include_children: bool = True) -> "SharepointItem":
        """Re-fetch the API payload (and optionally children) from Graph."""
        drive_id = self._api_payload.get("parentReference", {}).get("driveId")
        if not drive_id:
            raise ValueError(
                f"Missing driveId in SharePoint "
                f"{'folder' if self.is_directory else 'item'} metadata"
            )
        old_name = self.name
        old_path = Path(self.path)
        refreshed = self.client.get_item_metadata(drive_id, item_id=self.id)
        self._invalidate_traversal()
        self._api_payload = refreshed
        self._name = refreshed.get("name")
        if self.path and old_path.name == old_name:
            self._path = str(old_path.with_name(self.name)).replace("\\", "/")
        self._id = refreshed.get("id")
        self._source_url = refreshed.get("webUrl")
        self._is_folder = "folder" in refreshed
        self._parent_id = refreshed.get("parentReference", {}).get("id")
        self._drive_id = refreshed.get("parentReference", {}).get("driveId")
        return self

    def download(self, target_dir: str | Path) -> None:
        """Download this item.

        Directories are walked recursively via :meth:`iter_files` and each
        leaf file is written relative to *target_dir*.  Files are fetched via
        their Graph API download URL or a presigned URL.
        """
        if self.is_directory:
            super().download(target_dir)
            return
        target = Path(target_dir)
        if target.is_dir():
            target = target / self.name

        target.parent.mkdir(parents=True, exist_ok=True)
        drive_id = self._api_payload.get("parentReference", {}).get("driveId")
        if not drive_id:
            raise ValueError("Missing driveId in Sharepoint item metadata")

        content = self.client.download_content(
            drive_id=drive_id,
            item_id=self.id,
            download_url=self._api_payload.get("@microsoft.graph.downloadUrl"),
        )
        target.write_bytes(content)
