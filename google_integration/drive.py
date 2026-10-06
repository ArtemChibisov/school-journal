"""Google Drive operations used by the reporting pipeline.

DEV-0202. Folders for a module, copies of the class register template, lookups before a
conflict decision, and the links the завуч opens from the platform.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .api import guard
from .credentials import build_credentials
from .errors import CATEGORY_NOT_FOUND, GoogleIntegrationError

MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_SPREADSHEET = "application/vnd.google-apps.spreadsheet"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_GOOGLE_DOC = "application/vnd.google-apps.document"
MIME_PDF = "application/pdf"

FILE_FIELDS = "id,name,mimeType,webViewLink,parents,trashed"
PERMISSION_FIELDS = "id,type,role,emailAddress"

ROLE_WRITER = "writer"
PRINCIPAL_GROUP = "group"
PRINCIPAL_USER = "user"


@runtime_checkable
class DriveClient(Protocol):
    """What the services need from Drive. Implemented for real and in memory."""

    def get_file(self, file_id: str) -> dict: ...

    def list_children(self, parent_id: str, *, mime_type: str | None = None) -> list[dict]: ...

    def find_child(self, parent_id: str, name: str, *, mime_type: str | None = None) -> dict | None: ...

    def create_folder(self, parent_id: str, name: str) -> dict: ...

    def copy_file(
        self, file_id: str, *, parent_id: str, name: str, mime_type: str | None = None
    ) -> dict: ...

    def move_file(self, file_id: str, *, new_parent_id: str, old_parent_id: str) -> dict: ...

    def trash_file(self, file_id: str) -> None: ...

    def delete_file(self, file_id: str) -> None: ...

    def export_file(self, file_id: str, mime_type: str) -> bytes: ...

    def download_file(self, file_id: str) -> bytes: ...

    def upload_bytes(self, parent_id: str, name: str, data: bytes, mime_type: str) -> dict: ...

    def update_bytes(self, file_id: str, data: bytes, mime_type: str) -> dict: ...

    def list_permissions(self, file_id: str) -> list[dict]: ...

    def share(
        self, file_id: str, *, email: str, role: str = ROLE_WRITER, principal: str = PRINCIPAL_GROUP
    ) -> dict: ...


def _escape(value: str) -> str:
    """Escape a value for a Drive query string."""

    return value.replace("\\", "\\\\").replace("'", "\\'")


class ApiDriveClient:
    """Drive v3 client. Shared drives are enabled on every call."""

    def __init__(self, service=None, *, credentials=None):
        self._service = service
        self._credentials = credentials

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            credentials = self._credentials or build_credentials()
            with guard("drive.build"):
                self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._service

    def get_file(self, file_id: str) -> dict:
        with guard("drive.files.get", file_id=file_id):
            return (
                self.service.files()
                .get(fileId=file_id, fields=FILE_FIELDS, supportsAllDrives=True)
                .execute()
            )

    def list_children(self, parent_id: str, *, mime_type: str | None = None) -> list[dict]:
        query = f"'{_escape(parent_id)}' in parents and trashed = false"
        if mime_type:
            query += f" and mimeType = '{_escape(mime_type)}'"

        items: list[dict] = []
        page_token = None
        while True:
            with guard("drive.files.list", parent_id=parent_id):
                response = (
                    self.service.files()
                    .list(
                        q=query,
                        spaces="drive",
                        fields=f"nextPageToken,files({FILE_FIELDS})",
                        includeItemsFromAllDrives=True,
                        supportsAllDrives=True,
                        pageSize=100,
                        pageToken=page_token,
                    )
                    .execute()
                )
            items.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    def find_child(self, parent_id: str, name: str, *, mime_type: str | None = None) -> dict | None:
        query = f"name = '{_escape(name)}' and '{_escape(parent_id)}' in parents and trashed = false"
        if mime_type:
            query += f" and mimeType = '{_escape(mime_type)}'"

        with guard("drive.files.list", parent_id=parent_id, name=name):
            response = (
                self.service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields=f"files({FILE_FIELDS})",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    pageSize=1,
                )
                .execute()
            )
        files = response.get("files", [])
        return files[0] if files else None

    def create_folder(self, parent_id: str, name: str) -> dict:
        body = {"name": name, "mimeType": MIME_FOLDER, "parents": [parent_id]}
        with guard("drive.files.create", parent_id=parent_id, name=name):
            return (
                self.service.files()
                .create(body=body, fields=FILE_FIELDS, supportsAllDrives=True)
                .execute()
            )

    def copy_file(
        self, file_id: str, *, parent_id: str, name: str, mime_type: str | None = None
    ) -> dict:
        body = {"name": name, "parents": [parent_id]}
        if mime_type:
            # Drive converts on copy when the target type differs, which is how an
            # uploaded .xlsx becomes a native Google Sheet without a download round trip.
            body["mimeType"] = mime_type
        with guard("drive.files.copy", file_id=file_id, parent_id=parent_id, name=name):
            return (
                self.service.files()
                .copy(fileId=file_id, body=body, fields=FILE_FIELDS, supportsAllDrives=True)
                .execute()
            )

    def move_file(self, file_id: str, *, new_parent_id: str, old_parent_id: str) -> dict:
        with guard("drive.files.update", file_id=file_id, parent_id=new_parent_id):
            return (
                self.service.files()
                .update(
                    fileId=file_id,
                    addParents=new_parent_id,
                    removeParents=old_parent_id,
                    fields=FILE_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )

    def trash_file(self, file_id: str) -> None:
        with guard("drive.files.update", file_id=file_id):
            self.service.files().update(
                fileId=file_id, body={"trashed": True}, supportsAllDrives=True
            ).execute()

    def delete_file(self, file_id: str) -> None:
        """Permanent, so used only for a file this code made moments ago."""

        with guard("drive.files.delete", file_id=file_id):
            self.service.files().delete(fileId=file_id, supportsAllDrives=True).execute()

    def export_file(self, file_id: str, mime_type: str) -> bytes:
        """Ask Google to render one of its own documents in another format."""

        with guard("drive.files.export", file_id=file_id, mime_type=mime_type):
            return self.service.files().export(fileId=file_id, mimeType=mime_type).execute()

    def download_file(self, file_id: str) -> bytes:
        """The bytes of an uploaded file - a PDF on its way to a parent, say."""

        import io as _io

        from googleapiclient.http import MediaIoBaseDownload

        buffer = _io.BytesIO()
        with guard("drive.files.get_media", file_id=file_id):
            request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return buffer.getvalue()

    def _media(self, data: bytes, mime_type: str):
        import io as _io

        from googleapiclient.http import MediaIoBaseUpload

        return MediaIoBaseUpload(_io.BytesIO(data), mimetype=mime_type, resumable=False)

    def upload_bytes(self, parent_id: str, name: str, data: bytes, mime_type: str) -> dict:
        body = {"name": name, "parents": [parent_id], "mimeType": mime_type}
        with guard("drive.files.create", parent_id=parent_id, name=name):
            return (
                self.service.files()
                .create(
                    body=body,
                    media_body=self._media(data, mime_type),
                    fields=FILE_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )

    def update_bytes(self, file_id: str, data: bytes, mime_type: str) -> dict:
        """Replace the contents and keep the id, so a link already sent keeps working."""

        with guard("drive.files.update", file_id=file_id):
            return (
                self.service.files()
                .update(
                    fileId=file_id,
                    media_body=self._media(data, mime_type),
                    fields=FILE_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )

    def list_permissions(self, file_id: str) -> list[dict]:
        with guard("drive.permissions.list", file_id=file_id):
            response = (
                self.service.permissions()
                .list(
                    fileId=file_id,
                    fields=f"permissions({PERMISSION_FIELDS})",
                    supportsAllDrives=True,
                    pageSize=100,
                )
                .execute()
            )
        return response.get("permissions", [])

    def share(
        self, file_id: str, *, email: str, role: str = ROLE_WRITER, principal: str = PRINCIPAL_GROUP
    ) -> dict:
        """Give an address access to a file or folder.

        No notification mail: the school is told about its reports by the platform, not by
        thirty-two separate messages from Drive.
        """

        body = {"type": principal, "role": role, "emailAddress": email}
        with guard("drive.permissions.create", file_id=file_id, email=email, role=role):
            return (
                self.service.permissions()
                .create(
                    fileId=file_id,
                    body=body,
                    fields=PERMISSION_FIELDS,
                    sendNotificationEmail=False,
                    supportsAllDrives=True,
                )
                .execute()
            )


def ensure_folder(client: DriveClient, parent_id: str, name: str) -> dict:
    """Return the existing subfolder with this name, or create it.

    Drive allows several files with the same name in one folder, so a plain create would
    silently produce duplicate module folders on a repeated run.
    """

    existing = client.find_child(parent_id, name, mime_type=MIME_FOLDER)
    if existing:
        return existing
    return client.create_folder(parent_id, name)


def ensure_shared(
    client: DriveClient, file_id: str, email: str, *, role: str = ROLE_WRITER
) -> bool:
    """Make sure this address can open the file. Returns True when access was granted.

    An address that already appears in the permissions is left exactly as it is: a school that
    deliberately gave someone read-only access should not have it widened by a report run.

    Drive wants to know whether the address is a group or a person, and the settings hold one
    string either way. A group address is the usual answer, so it is tried first; `user` is the
    fallback, because the alternative is telling the завуч that their own address is invalid.
    """

    wanted = (email or "").strip()
    if not wanted:
        return False

    for permission in client.list_permissions(file_id):
        if (permission.get("emailAddress") or "").strip().lower() == wanted.lower():
            return False

    try:
        client.share(file_id, email=wanted, role=role, principal=PRINCIPAL_GROUP)
    except GoogleIntegrationError:
        client.share(file_id, email=wanted, role=role, principal=PRINCIPAL_USER)
    return True


def require_folder(client: DriveClient, folder_id: str) -> dict:
    """Fetch a folder and fail with a clear message when the id points at a file."""

    item = client.get_file(folder_id)
    if item.get("mimeType") != MIME_FOLDER:
        raise GoogleIntegrationError(
            CATEGORY_NOT_FOUND,
            f"Drive id {folder_id} is not a folder (mimeType={item.get('mimeType')!r})",
            user_message="Указанная ссылка ведёт не на папку Google Drive.",
            context={"file_id": folder_id, "mime_type": item.get("mimeType")},
        )
    return item
