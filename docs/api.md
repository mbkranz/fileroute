# Python API

Auto-generated from source signatures and docstrings.

## `sharedrive.upload`

### Functions

- `def plan_upload(descriptor: Path, *, root: Path | None = None) -> tuple[UploadFile, ...]`
  - Publish path to targets, never sources; validate everything before auth.
- `def upload(files: tuple[UploadFile, ...]) -> None`
  - Transfer a prepared plan; remote files outside it are never deleted.

### Classes

#### `UploadFile`
- Fields:
  - `local: Path`
  - `remote: str`
  - `relative: Path`
  - `service: ServiceType`
  - `direct_file: bool`
- Methods:
  - `def destination(self) -> str`


## `sharedrive.download`

### Functions

- `def plan_download(descriptor: Path, *, root: Path | None = None) -> tuple[Download, ...]`
  - Plan remote sources to local paths, offline.
- `def download(entries: tuple[Download, ...]) -> None`
  - Resolve remote items, check their paths, then download planned files.

### Classes

#### `Download`
- Fields:
  - `local: Path`
  - `remote: str`
  - `service: ServiceType`
  - `directory: bool`


## `sharedrive.migration`

### Functions

- `def migrate_descriptor(path: Path, *, direction: Literal['pull', 'push'] = 'pull') -> Catalog`
  - Read a legacy descriptor and return canonical models, inlining references.


## `sharedrive.helpers`

### Constants

- `DESCRIPTOR_DEFAULTS_FILE = Path('.sharedrive/sharedrive_set.json')`

### Functions

- `def descriptor_scope_key(descriptor: Path | str) -> str`
  - Return the stable key used for descriptor-scoped defaults.
- `def get_saved_params_for_descriptor(descriptor: Path | str | None = None) -> dict[str, Any]`
  - Return merged global and descriptor-scoped saved params.
- `def has_saved_global_descriptor() -> bool`
  - Return whether the global defaults include a descriptor path.
- `def load_descriptor_defaults_store() -> dict[str, Any]`
  - Load persisted descriptor defaults for global and descriptor scopes.
- `def resolve_default_descriptor() -> Path`
  - Return the first existing default descriptor path.
- `def resolve_descriptor_path(descriptor: Path | str | None = None) -> Path`
  - Resolve descriptor path from explicit input, saved defaults, or standard locations.
- `def save_params_for_scope(parsed: dict[str, Any], descriptor: Path | str | None, *, global_scope: bool) -> str`
  - Save reusable CLI params to the global or descriptor-specific scope.
- `def save_descriptor_defaults_store(data: dict[str, Any]) -> None`
  - Persist descriptor defaults store to disk.
- `def set_active_descriptor(descriptor_path: Path) -> Path`
  - Persist the active descriptor.


## `sharedrive.models`

### Constants

- `CATALOG_PROFILE = 'sharedrive-catalog'`
- `EntityTypeValue = Annotated[str, BeforeValidator(normalize_entity_type)]`

### Functions

- `def walk_entities(root: Any, prefix: str = '', pointer: str = '') -> Iterator[EntityPath]`
  - One structural walker for models and authored mappings; no I/O.
- `def read_descriptor(path: Path) -> dict[str, Any]`
  - Read JSON/YAML without discarding authored extension metadata.
- `def write_descriptor(path: Path, document: dict[str, Any]) -> None`
  - Write JSON/YAML, preserving fields but not YAML comments/formatting.
- `def local_path(path: str, root: Path, *, reject_symlinks: bool = False) -> Path`
  - Resolve a local artifact inside root; never accept URLs or escapes.
- `def normalize_entity_type(value: str | None) -> str | None`
  - Normalize OpenMetadata-style drive/storage entity names.

### Classes

#### `ServiceType`

#### `Catalog`
- Nested groups of resources and catalogs.
- Fields:
  - `profile: str`
  - `resources: list[Resource]`
  - `catalogs: list[Catalog | CatalogReference]`
  - `_origin: Path | None`
- Methods:
  - `def identify_references(cls, children: Any) -> Any`
  - `def from_path(cls, path: str | Path) -> Self`
  - `def iter_entity_paths(self, *, include_self: bool = False, traverse_references: bool = True, _seen: frozenset[Path] = frozenset()) -> Iterator[EntityPath]`
  - `def assert_valid_entity_paths(self) -> None`
  - `def get_resource(self, name: str) -> Resource`
  - `def get_catalog(self, name: str) -> Catalog`
  - `def dereference(self, _seen: frozenset[Path] = frozenset()) -> Self`

#### `Resource`
- A materialized artifact and its provenance/publication locations.
- Fields:
  - `path: str`
  - `format: str | None`

#### `Location`
- Upstream input or downstream destination, with optional provider metadata.
- Fields:
  - `path: str`
  - `service_type: ServiceTypeField | None`
  - `service_id: str | None`
  - `entity_type: EntityTypeValue | None`

#### `CatalogReference`
- Lazy reference to another local descriptor; resolved beside its document.
- Fields:
  - `name: str | None`
  - `path: str`
  - `_basepath: Path`
- Methods:
  - `def load(self) -> Catalog`

#### `EntityPath`
- Fields:
  - `name_path: str`
  - `model: Entity | CatalogReference`
  - `json_pointer: str`
  - `entity_type: str`


## `sharedrive.item`

### Classes

#### `ServiceItem`
- Base interface for files and directories in a remote service.
- Methods:
  - `def id(self) -> str`
  - `def name(self) -> str`
  - `def path(self) -> str`
  - `def service_type(self) -> ServiceType`
  - `def source_url(self) -> str`
  - `def is_directory(self) -> bool`
  - `def refresh(self, *, include_children: bool = True) -> 'ServiceItem'`
    - Refresh this runtime item from its backing service.
  - `def children(self) -> list['ServiceItem']`
    - Direct child items for directories; always empty for files.
  - `def parent_id(self) -> str | None`
    - Best-known parent identifier for this item, when available.
  - `def parent(self) -> 'ServiceItem' | None`
    - Best-known parent item from the active traversal snapshot.
  - `def get_path(self, relative_path: str | Path) -> 'ServiceItem'`
    - Resolve a descendant item by traversing child names in *relative_path*.
  - `def iter_items(self, *, recursive: bool = True) -> Iterator['ServiceItem']`
    - Yield child files and directories in deterministic path order.
  - `def iter_files(self, *, recursive: bool = True) -> Iterator['ServiceItem']`
    - Yield file descendants, excluding directories and the starting item.
  - `def download(self, target: Path | str) -> None`
    - Download this item to *target*.
  - `def to_catalog(self) -> Catalog | Resource`
    - Export local artifact paths and remote provenance as owned models.


## `sharedrive.clients.s3`

### Functions

- `def check_s3_credentials() -> None`
  - Validate that AWS credentials are available for S3 operations.

### Classes

#### `S3Client`
- Methods:
  - `def build_default(cls) -> 'S3Client'`
  - `def check_auth(cls) -> None`
  - `def get_from_weburl(self, url: str) -> 'S3Item'`
  - `def get_from_path(self, *, bucket: str, key: str = '') -> 'S3Item'`
  - `def resolve_descendant(self, *, bucket: str, parent_key: str, name: str) -> 'S3Item'`
  - `def list_children(self, *, bucket: str, prefix: str) -> tuple[list[dict[str, Any]], list[str]]`
  - `def scan_descendants(self, *, bucket: str, prefix: str) -> list[dict[str, Any]]`

#### `S3Item`
- Methods:
  - `def move(self, new_parent_id: str) -> 'S3Item'`
  - `def id(self) -> str`
  - `def name(self) -> str`
  - `def path(self) -> str`
  - `def service_type(self) -> ServiceType`
  - `def source_url(self) -> str`
  - `def is_directory(self) -> bool`
  - `def children(self) -> list['S3Item']`
  - `def refresh(self, *, include_children: bool = True) -> 'S3Item'`
  - `def download(self, target_dir: str | Path) -> None`


## `sharedrive.clients.googledrive`

### Classes

#### `GoogleBaseClient`
- Shared Google client base: auth lifecycle and HTTP transport helpers.
- Fields:
  - `auth_methods: ClassVar[list[str]]`
  - `capabilities: ClassVar[AdapterCapabilities]`
- Methods:
  - `def refresh(self) -> None`
  - `def build_default(cls) -> 'GoogleBaseClient'`
    - Construct from environment variables / settings.
  - `def check_auth(cls) -> None`
    - Validate that Google credentials are available.

#### `GoogleDriveClient`
- Google Drive client (ID-first) with read/write and full export coverage.
- Methods:
  - `def list_files(self, folder_file_id: str | None = None, queries: list[str] | None = None, params: Dict[str, Any] | None = None, page_size: int = 100) -> list[GDriveApiFile]`
    - List all files the authenticated user has access to.
  - `def list_children(self, parent_id: str, *, drive_id: str | None = None, name: str | None = None) -> list[GDriveApiFile]`
  - `def scan_descendants(self, *, drive_id: str | None = None) -> list[GDriveApiFile]`
  - `def resolve_descendant(self, *, parent_id: str, name: str, drive_id: str | None = None) -> list[GDriveApiFile]`
  - `def list_drives(self, *, page_size: int = 100) -> list[GDriveApiDrive]`
    - List all Shared Drives the authenticated user has access to.
  - `def get_file(self, file_id: str, **kwargs) -> GDriveApiFile`
  - `def infer_export_mime_type(self, file_id: str) -> Optional[str]`
  - `def download_file(self, file_id: str, output_path: Optional[str] = None, mime_type: Optional[str] = None, acknowledge_abuse: bool = False, byte_range: Optional[str] = None, supports_all_drives: bool = True, **kwargs) -> Union[bytes, str]`
  - `def export_file(self, file_id: str, mime_type: Optional[str] = None, output_path: Optional[str] = None, supports_all_drives: bool = True, **kwargs) -> Union[bytes, str]`
  - `def create_file(self, name: str, parent_id: str, content: bytes, mime_type: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, supports_all_drives: bool = True, **kwargs) -> GDriveItem`
  - `def update_file(self, id: str, params: Optional[Dict[str, Any]] = None, metadata: Optional[Dict[str, Any]] = None, file_in_bytes_or_path: Optional[Union[str, bytes]] = None, mime_type: Optional[str] = None, **kwargs) -> GDriveItem`
  - `def create_folder(self, parent_folder_id: str, name: str) -> GDriveItem`
  - `def get_from_weburl(self, url: str) -> GDriveItem`
    - Resolve a Google Drive file or folder URL.
  - `def get_from_id(self, file_id: str) -> GDriveItem`
    - Resolve a Google Drive item ID.
  - `def get_from_path(self, drive_name: str, path: str = '') -> GDriveItem`
    - Resolve a path relative to a My Drive or Shared Drive root.

#### `GDriveItem`
- A Google Drive file or folder item backed by the Drive REST API.
- Methods:
  - `def client(self) -> 'GoogleDriveClient'`
  - `def parent_id(self) -> Optional[str]`
  - `def mime_type(self) -> Optional[str]`
  - `def children(self) -> list['ServiceItem']`
    - Direct children of this directory; empty list for files.
  - `def id(self) -> str`
  - `def name(self) -> str`
  - `def path(self) -> str`
  - `def source_url(self) -> str`
  - `def is_directory(self) -> bool`
  - `def service_type(self) -> ServiceType`
  - `def refresh(self, *, include_children: bool = True) -> 'GDriveItem'`
    - Re-fetch raw metadata (and optionally children) from the API.
  - `def export(self, target_mime_type: Optional[str] = None, output_path: Optional[str] = None) -> Union[bytes, str]`
    - Export this item if it's a Google Workspace file, otherwise download it.
  - `def download(self, target: str | Path) -> None`
    - Download this item.
  - `def move(self, new_parent_id: str) -> 'ServiceItem'`
    - Move this item to a new parent directory.
  - `def add_comment(self, body: str) -> 'ServiceItem'`
    - Post a comment on this file via the Drive v3 comments API.


## `sharedrive.auth.google`

### Classes

#### `GoogleAuth`
- Google credential holder with named constructors for each auth mode.
- Methods:
  - `def refresh(self) -> None`
    - Refresh the access token
  - `def from_adc(cls, scopes: Sequence[str] | str | None = None) -> 'GoogleAuth'`
    - Build from Application Default Credentials (``gcloud auth application-default login``).
  - `def from_service_account(cls, credentials_path: str | Path, scopes: Sequence[str] | str | None = None) -> 'GoogleAuth'`
    - Build from a service account JSON key file.
  - `def from_user_oauth(cls, scopes: Sequence[str] | str, client_secrets_path: str | Path = None, token_path: str | Path = None, token_store: Any = None) -> 'GoogleAuth'`
    - Build via the OAuth installed-app flow, with token persistence.
  - `def from_settings(cls, config: object | None = None) -> 'GoogleAuth'`
    - Build from environment variables or a :class:`~sharedrive.auth.settings.GoogleAuthConfig`.
  - `def credentials(self) -> Credentials`
    - The underlying :class:`~google.auth.credentials.Credentials` object.
  - `def ensure_valid(self) -> None`
    - Refresh credentials when the current token is not valid.


## `sharedrive.auth.microsoft`

### Classes

#### `MicrosoftAuth`
- Microsoft access-token holder with named constructors for each auth mode.
- Methods:
  - `def from_app_only(cls, tenant_id: str, client_id: str, client_secret: str, scopes: Sequence[str] | str | None = None) -> 'MicrosoftAuth'`
    - Build using client-credential (app-only) flow via MSAL.
  - `def from_delegated(cls, tenant_id: str, client_id: str, scopes: Sequence[str] | str | None = None) -> 'MicrosoftAuth'`
    - Build using interactive delegated (user) flow via MSAL.
  - `def from_settings(cls, config: object | None = None) -> 'MicrosoftAuth'`
    - Build from environment variables or a :class:`~sharedrive.auth.settings.MicrosoftAuthConfig`.
  - `def access_token(self) -> str`
    - The raw Bearer access token string.


## `sharedrive.auth.token_store`

### Classes

#### `JsonTokenStore`
- Persist Google authorized-user credentials as JSON.
- Methods:
  - `def load(self) -> UserCredentials | None`
  - `def save(self, creds: UserCredentials) -> None`


## `sharedrive.auth.settings`

### Classes

#### `GoogleAuthConfig`
- Fields:
  - `auth_mode: GoogleAuthMode`
  - `service_account_credentials: Path | None`
  - `oauth_client_secrets: Path | None`
  - `oauth_token_path: Path`
  - `scopes: Annotated[list[str], NoDecode]`
  - `use_local_server: bool`
- Methods:
  - `def to_scope_list(cls, value: str | list[str] | tuple[str, ...] | None) -> list[str]`
  - `def validate_for_mode(self) -> GoogleAuthConfig`
  - `def to_auth(self) -> GoogleAuth`
    - Return a :class:`~sharedrive.auth.google.GoogleAuth` for this configuration.

#### `GoogleAuthMode`

#### `MicrosoftAuthConfig`
- Fields:
  - `auth_mode: MicrosoftAuthMode`
  - `tenant_id: str | None`
  - `client_id: str | None`
  - `client_secret: SecretStr | None`
  - `host_url: str`
  - `scopes: Annotated[list[str], NoDecode]`
- Methods:
  - `def empty_string_to_none(cls, value: str | None) -> str | None`
  - `def normalize_host_url(cls, value: str | None) -> str`
  - `def to_scope_list(cls, value: str | list[str] | tuple[str, ...] | None) -> list[str]`
  - `def validate_for_mode(self) -> MicrosoftAuthConfig`
  - `def to_auth(self) -> MicrosoftAuth`
    - Return a :class:`~sharedrive.auth.microsoft.MicrosoftAuth` for this configuration.

#### `MicrosoftAuthMode`


## `sharedrive.clients.sharepoint`

### Classes

#### `SharepointClient`
- SharePoint / OneDrive client backed by the Microsoft Graph API.
- Fields:
  - `auth_methods: ClassVar[list[str]]`
  - `capabilities: ClassVar[AdapterCapabilities]`
- Methods:
  - `def build_default(cls) -> 'SharepointClient'`
    - Construct from environment variables / settings.
  - `def check_auth(cls) -> None`
    - Validate that Microsoft Graph credentials are available.
  - `def get_site_id(self, site_name)`
  - `def list_site_drives(self, site_id: str) -> list[dict[str, Any]]`
  - `def get_drive_id(self, site_id, drive_name: str | None = None)`
    - Retrieves the default document drive associated with a SharePoint site.
  - `def get_item_metadata(self, drive: str, *, item_path: str | None = None, item_id: str | None = None, fields: list[str] | None = None)`
    - get item metadata based on relative file path or item id within the drive
  - `def list_children(self, drive_id: str, item_id: str, *, fields: list[str] | None = None) -> list[dict[str, Any]]`
  - `def scan_descendants(self, *, drive_id: str) -> list[dict[str, Any]]`
  - `def resolve_descendant(self, *, drive_id: str, parent_path: str, name: str) -> dict[str, Any]`
  - `def get_from_weburl(self, url: str) -> 'SharepointItem'`
  - `def get_from_path(self, *, site_name: str, item_path: str = '/', library_name: str | None = None) -> 'SharepointItem'`
  - `def download_content(self, drive_id = None, item_id = None, download_url = None)`
    - takes in the components needed to download content --
  - `def create_file(self, *, site_name: str, folder_path: str, local_file_path: str | Path) -> 'SharepointItem'`
    - Create or replace a file at a drive-relative folder path.
  - `def update_file(self, *, site_name: str, folder_path: str, local_file_path: str | Path) -> 'SharepointItem'`
    - Replace the content of an existing file.
  - `def upload_file(self, *, site_name: str, folder_path: str, local_file_path: str | Path, create_if_missing: bool = True) -> 'SharepointItem'`
    - Update a file, optionally creating it when it does not exist.
  - `def upload_to_folder(self, folder_url: str, relative_path: Path, local_file_path: str | Path) -> 'SharepointItem'`
    - Create or replace a file below an existing SharePoint folder URL.

#### `SharepointItem`
- A SharePoint file or folder item backed by Microsoft Graph.
- Methods:
  - `def move(self, new_parent_id: str) -> 'SharepointItem'`
  - `def id(self) -> str`
  - `def name(self) -> str`
  - `def path(self) -> str`
  - `def service_type(self) -> ServiceType`
  - `def source_url(self) -> str`
  - `def is_directory(self) -> bool`
  - `def children(self) -> list['SharepointItem']`
    - Direct children of this directory; empty list for files.
  - `def refresh(self, *, include_children: bool = True) -> 'SharepointItem'`
    - Re-fetch the API payload (and optionally children) from Graph.
  - `def download(self, target_dir: str | Path) -> None`
    - Download this item.
