from __future__ import annotations
from sharedrive.models import ServiceType

import json
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import quote, unquote, urlparse


import requests

from sharedrive.clients.base import AdapterCapabilities, BaseClient
from sharedrive.exceptions import GraphApiDriveError, GraphApiSiteError
from sharedrive.item import ServiceItem

if TYPE_CHECKING:
    from sharedrive.auth.microsoft import MicrosoftAuth


class SharepointClient(BaseClient):
    """SharePoint / OneDrive client backed by the Microsoft Graph API.

    Supports both app-only (client credentials) and delegated auth via
    :class:`~sharedrive.auth.microsoft.MicrosoftAuth`.

    Instantiate via a :class:`~sharedrive.auth.microsoft.MicrosoftAuth` object::

        auth = MicrosoftAuth.from_app_only(tenant_id, client_id, client_secret)
        client = SharepointClient(auth, host_url="contoso.sharepoint.com")

        # Or from environment variables / .env file:
        client = SharepointClient.build_default()

    The *access_token* keyword argument is a low-level escape hatch kept for
    tests only; prefer :class:`~sharedrive.auth.microsoft.MicrosoftAuth` in all
    production code.

    The class is registered as the ``"sharepoint"`` provider via the
    Selected by ServiceType through sharedrive.clients.get_provider.

    Microsoft Graph API reference:
        https://learn.microsoft.com/en-us/graph/api/resources/onedrive?view=graph-rest-1.0
    """

    auth_methods: ClassVar[list[str]] = ["app_only", "delegated"]
    capabilities: ClassVar[AdapterCapabilities] = AdapterCapabilities(
        supports_fetch=True,
        supports_download=True,
        supports_auth_check=True,
        supports_write=True,
        supports_upload=True,
    )

    def __init__(
        self,
        auth: "MicrosoftAuth | None" = None,
        host_url: str = "norc.sharepoint.com",
        *,
        access_token: str | None = None,
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

    @classmethod
    def build_default(cls) -> "SharepointClient":
        """Construct from environment variables / settings.

        Reads ``SHAREPOINT_AUTH_MODE``, ``AZURE_TENANT_ID``, ``AZURE_CLIENT_ID``,
        ``AZURE_CLIENT_SECRET``, and ``SHAREPOINT_HOST_URL`` from the environment
        or a ``.env`` file via
        :class:`~sharedrive.auth.settings.MicrosoftAuthConfig`.
        """
        from sharedrive.auth.settings import MicrosoftAuthConfig

        config = MicrosoftAuthConfig()
        auth = config.to_auth()
        return cls(auth=auth, host_url=config.host_url)

    @classmethod
    def check_auth(cls) -> None:
        """Validate that Microsoft Graph credentials are available.

        Raises :class:`~sharedrive.exceptions.GraphAuthError` if the
        credentials configured in the environment are missing or invalid.
        """
        from sharedrive.auth.settings import MicrosoftAuthConfig

        config = MicrosoftAuthConfig()
        config.to_auth()

    def _request_json(
        self, endpoint: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = requests.get(endpoint, headers=self.auth_header, params=params)
            response.raise_for_status()
        except requests.exceptions.HTTPError as http_err:
            raise GraphApiDriveError(
                f"HTTP error while calling Microsoft Graph\n"
                f"URL: {response.url}\n"
                f"Status: {response.status_code} - {response.reason}\n"
                f"Details: {response.text}",
                status_code=response.status_code,
                response_text=response.text,
            ) from http_err
        except requests.exceptions.RequestException as req_err:
            raise GraphApiDriveError(
                f"Request error when calling {endpoint}: {req_err}"
            ) from req_err

        response_json = response.json()

        # Handle pagination for collection endpoints (e.g., drives, children)
        if "@odata.nextLink" in response_json:
            current_json = response_json
            while "@odata.nextLink" in current_json:
                next_link = current_json["@odata.nextLink"]
                try:
                    next_resp = requests.get(next_link, headers=self.auth_header)
                    next_resp.raise_for_status()
                except requests.exceptions.HTTPError as http_err:
                    raise GraphApiDriveError(
                        f"HTTP error while calling Microsoft Graph (pagination)\n"
                        f"URL: {next_resp.url}\n"
                        f"Status: {next_resp.status_code} - {next_resp.reason}\n"
                        f"Details: {next_resp.text}",
                        status_code=next_resp.status_code,
                        response_text=next_resp.text,
                    ) from http_err
                except requests.exceptions.RequestException as req_err:
                    raise GraphApiDriveError(
                        f"Request error when calling {next_link}: {req_err}"
                    ) from req_err

                current_json = next_resp.json()
                # Aggregate items if "value" array exists
                if "value" in response_json and "value" in current_json:
                    response_json["value"].extend(current_json.get("value", []))

            # Remove the nextLink from final aggregated response
            response_json.pop("@odata.nextLink", None)

        return response_json

    def get_site_id(self, site_name):
        endpoint = (
            f"https://graph.microsoft.com/v1.0/sites/{self.host_url}:/sites/{site_name}"
        )
        try:
            response = requests.get(endpoint, headers=self.auth_header)
            response.raise_for_status()
            site_data = response.json()
        except requests.exceptions.HTTPError as http_err:
            raise GraphApiSiteError(
                f"HTTP error fetching site ID for '{site_name}'\n"
                f"URL: {endpoint}\n"
                f"Status: {response.status_code} - {response.reason}\n"
                f"Details: {response.text}"
            ) from http_err
        except requests.exceptions.RequestException as req_err:
            raise GraphApiDriveError(
                f"Request error when calling {endpoint}: {req_err}"
            ) from req_err

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

        try:
            response = requests.get(endpoint, headers=self.auth_header)
            response.raise_for_status()
            drive_data = response.json()
        except requests.exceptions.HTTPError as http_err:
            raise GraphApiDriveError(
                f"HTTP error while retrieving drive for site '{site_id}'\n"
                f"URL: {endpoint}\n"
                f"Status: {response.status_code} - {response.reason}\n"
                f"Details: {response.text}"
            ) from http_err
        except requests.exceptions.RequestException as req_err:
            raise GraphApiDriveError(
                f"Request error calling {endpoint}: {req_err}"
            ) from req_err

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
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid SharePoint URL: {url}")

        self.host_url = parsed.hostname or self.host_url
        path_parts = [
            part for part in Path(unquote(parsed.path)).parts if part not in {"/", ""}
        ]

        site_name = None
        for index, part in enumerate(path_parts):
            if part.lower() == "sites" and index + 1 < len(path_parts):
                site_name = path_parts[index + 1]
                drive_name = (
                    path_parts[index + 2]
                    if index + 2 < len(path_parts)
                    else "Shared Documents"
                )
                item_path_parts = path_parts[index + 3 :]
                break
        else:
            raise ValueError(
                f"Could not extract site and library from SharePoint URL: {url}"
            )

        site_id = self.get_site_id(site_name)
        drive_id = self.get_drive_id(site_id, drive_name=drive_name)
        item_path = "/" + "/".join(item_path_parts) if item_path_parts else "/"
        return {
            "site_name": site_name,
            "site_id": site_id,
            "drive_name": drive_name,
            "drive_id": drive_id,
            "item_path": item_path,
        }

    def download_content(self, drive_id=None, item_id=None, download_url=None):
        """takes in the components needed to download content --

        drive id and item id -- uses Oauth to download
        download_url -- uses a presigned url (note: if on VPN, need to use this option - I think)

        """

        if download_url:
            url = download_url
            response = requests.get(url)
        else:
            if drive_id and item_id:
                url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"
                response = requests.get(url, headers=self.auth_header)
            else:
                raise GraphApiDriveError(
                    "Need drive_id and item_id if not using download_url"
                )

        if response.status_code != 200:
            raise GraphApiDriveError(
                f"Failed to download file. Status code: {response.status_code} - {response.reason}",
                status_code=response.status_code,
                response_text=response.text,
            )
        return response.content

    def _put_file(self, url: str, local_file_path: str | Path) -> dict[str, Any]:
        local_path = Path(local_file_path)
        content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        headers = {**self.auth_header, "Content-Type": content_type}
        try:
            with local_path.open("rb") as file_stream:
                response = requests.put(url, headers=headers, data=file_stream)
        except requests.exceptions.RequestException as exc:
            raise GraphApiDriveError(
                f"Request error while uploading {local_path}: {exc}"
            ) from exc

        if response.status_code not in {200, 201}:
            raise GraphApiDriveError(
                f"Failed to upload file. Status code: "
                f"{response.status_code} - {response.reason}",
                status_code=response.status_code,
                response_text=response.text,
            )
        return response.json()

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
        drive_id = destination["drive_id"]
        folder_path = destination["item_path"].strip("/")
        folder = self.get_item_metadata(drive_id, item_path=folder_path or "/")
        if "folder" not in folder:
            raise ValueError(f"Publication destination is not a folder: {folder_url}")

        parent_id = folder["id"]
        for part in relative.parts[:-1]:
            folder_path = f"{folder_path}/{part}".strip("/")
            try:
                child = self.get_item_metadata(drive_id, item_path=folder_path)
            except GraphApiDriveError as exc:
                if exc.status_code != 404:
                    raise
                try:
                    response = requests.post(
                        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{parent_id}/children",
                        headers={
                            **self.auth_header,
                            "Content-Type": "application/json",
                        },
                        json={
                            "name": part,
                            "folder": {},
                            "@microsoft.graph.conflictBehavior": "fail",
                        },
                    )
                except requests.exceptions.RequestException as error:
                    raise GraphApiDriveError(
                        f"Failed to create folder {folder_path}: {error}"
                    ) from error
                if response.status_code == 409:
                    child = self.get_item_metadata(drive_id, item_path=folder_path)
                elif response.status_code == 201:
                    child = response.json()
                else:
                    raise GraphApiDriveError(
                        f"Failed to create folder {folder_path}: {response.status_code} {response.text}",
                        status_code=response.status_code,
                        response_text=response.text,
                    )
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
