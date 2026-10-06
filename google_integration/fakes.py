"""In-memory Drive and Sheets clients for tests.

DEV-0204. Business logic is tested without network access and without real credentials. The
fakes reproduce the behaviour the services depend on, including the failures: a missing file
raises the same `GoogleIntegrationError` the real client would.
"""

from __future__ import annotations

from itertools import count

from .drive import (  # noqa: F401  (FILE_FIELDS documents the shape)
    FILE_FIELDS,
    MIME_FOLDER,
    PRINCIPAL_GROUP,
    ROLE_WRITER,
)
from .errors import CATEGORY_NOT_FOUND, GoogleIntegrationError


def _not_found(file_id: str) -> GoogleIntegrationError:
    return GoogleIntegrationError(
        CATEGORY_NOT_FOUND,
        f"Drive file {file_id} not found",
        context={"file_id": file_id},
    )


class FakeDriveClient:
    """Drive in a dictionary.

    Files are `{"id", "name", "mimeType", "webViewLink", "parents", "trashed"}`, the same
    shape `ApiDriveClient` returns.
    """

    def __init__(self):
        self.files: dict[str, dict] = {}
        self.permissions: dict[str, list[dict]] = {}
        self._ids = count(1)
        self.calls: list[tuple[str, tuple]] = []

    # -- helpers for tests ---------------------------------------------------------------

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids)}"

    def add_folder(self, name: str, *, parent_id: str | None = None, file_id: str | None = None) -> str:
        return self._add(name, MIME_FOLDER, parent_id=parent_id, file_id=file_id, prefix="folder")

    def add_file(
        self,
        name: str,
        *,
        parent_id: str | None = None,
        mime_type: str = "application/vnd.google-apps.spreadsheet",
        file_id: str | None = None,
    ) -> str:
        return self._add(name, mime_type, parent_id=parent_id, file_id=file_id, prefix="file")

    def _add(self, name, mime_type, *, parent_id, file_id, prefix) -> str:
        new_id = file_id or self._new_id(prefix)
        self.files[new_id] = {
            "id": new_id,
            "name": name,
            "mimeType": mime_type,
            "webViewLink": f"https://drive.google.com/file/d/{new_id}/view",
            "parents": [parent_id] if parent_id else [],
            "trashed": False,
        }
        return new_id

    # -- DriveClient ---------------------------------------------------------------------

    def get_file(self, file_id: str) -> dict:
        self.calls.append(("get_file", (file_id,)))
        item = self.files.get(file_id)
        if item is None or item["trashed"]:
            raise _not_found(file_id)
        return dict(item)

    def list_children(self, parent_id: str, *, mime_type: str | None = None) -> list[dict]:
        self.calls.append(("list_children", (parent_id, mime_type)))
        return [
            dict(item)
            for item in self.files.values()
            if not item["trashed"]
            and parent_id in item["parents"]
            and (mime_type is None or item["mimeType"] == mime_type)
        ]

    def find_child(self, parent_id: str, name: str, *, mime_type: str | None = None) -> dict | None:
        self.calls.append(("find_child", (parent_id, name, mime_type)))
        for item in self.list_children(parent_id, mime_type=mime_type):
            if item["name"] == name:
                return item
        return None

    def create_folder(self, parent_id: str, name: str) -> dict:
        self.calls.append(("create_folder", (parent_id, name)))
        new_id = self.add_folder(name, parent_id=parent_id)
        return dict(self.files[new_id])

    def copy_file(
        self, file_id: str, *, parent_id: str, name: str, mime_type: str | None = None
    ) -> dict:
        self.calls.append(("copy_file", (file_id, parent_id, name, mime_type)))
        source = self.files.get(file_id)
        if source is None or source["trashed"]:
            raise _not_found(file_id)
        new_id = self.add_file(name, parent_id=parent_id, mime_type=mime_type or source["mimeType"])
        return dict(self.files[new_id])

    def move_file(self, file_id: str, *, new_parent_id: str, old_parent_id: str) -> dict:
        self.calls.append(("move_file", (file_id, new_parent_id, old_parent_id)))
        item = self.files.get(file_id)
        if item is None or item["trashed"]:
            raise _not_found(file_id)
        item["parents"] = [p for p in item["parents"] if p != old_parent_id] + [new_parent_id]
        return dict(item)

    def trash_file(self, file_id: str) -> None:
        self.calls.append(("trash_file", (file_id,)))
        item = self.files.get(file_id)
        if item is None:
            raise _not_found(file_id)
        item["trashed"] = True

    def list_permissions(self, file_id: str) -> list[dict]:
        self.calls.append(("list_permissions", (file_id,)))
        if file_id not in self.files:
            raise _not_found(file_id)
        return [dict(item) for item in self.permissions.get(file_id, [])]

    def share(
        self, file_id: str, *, email: str, role: str = ROLE_WRITER, principal: str = PRINCIPAL_GROUP
    ) -> dict:
        self.calls.append(("share", (file_id, email, role, principal)))
        if file_id not in self.files:
            raise _not_found(file_id)
        permission = {
            "id": self._new_id("permission"),
            "type": principal,
            "role": role,
            "emailAddress": email,
        }
        self.permissions.setdefault(file_id, []).append(permission)
        return dict(permission)


class FakeSheetsClient:
    """Sheets in a dictionary: spreadsheet id -> worksheet title -> rows.

    Ranges are handled at worksheet granularity, which is what the services use. A range of
    the form `'Title'!A1` or `Title!A1:D10` is reduced to its worksheet.
    """

    def __init__(self):
        self.spreadsheets: dict[str, dict[str, list[list]]] = {}
        self.calls: list[tuple[str, tuple]] = []

    # -- helpers for tests ---------------------------------------------------------------

    def add_spreadsheet(self, spreadsheet_id: str, sheets: dict[str, list[list]] | None = None) -> str:
        self.spreadsheets[spreadsheet_id] = {
            title: [list(row) for row in rows] for title, rows in (sheets or {}).items()
        }
        return spreadsheet_id

    @staticmethod
    def _title_from_range(range_a1: str) -> str:
        title = range_a1.split("!", 1)[0].strip()
        if title.startswith("'") and title.endswith("'") and len(title) >= 2:
            title = title[1:-1]
        return title

    def _require(self, spreadsheet_id: str) -> dict[str, list[list]]:
        book = self.spreadsheets.get(spreadsheet_id)
        if book is None:
            raise GoogleIntegrationError(
                CATEGORY_NOT_FOUND,
                f"Spreadsheet {spreadsheet_id} not found",
                context={"spreadsheet_id": spreadsheet_id},
            )
        return book

    def _require_sheet(self, spreadsheet_id: str, title: str) -> list[list]:
        book = self._require(spreadsheet_id)
        if title not in book:
            raise GoogleIntegrationError(
                CATEGORY_NOT_FOUND,
                f"Worksheet {title!r} not found in spreadsheet {spreadsheet_id}",
                user_message=f"В таблице не найден лист «{title}».",
                context={"spreadsheet_id": spreadsheet_id, "title": title},
            )
        return book[title]

    # -- SheetsClient --------------------------------------------------------------------

    def get_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        self.calls.append(("get_sheet_titles", (spreadsheet_id,)))
        return list(self._require(spreadsheet_id))

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[str]]:
        self.calls.append(("read_values", (spreadsheet_id, range_a1)))
        rows = self._require_sheet(spreadsheet_id, self._title_from_range(range_a1))
        return [list(row) for row in rows]

    def write_values(self, spreadsheet_id: str, range_a1: str, values: list[list]) -> None:
        self.calls.append(("write_values", (spreadsheet_id, range_a1)))
        title = self._title_from_range(range_a1)
        self._require_sheet(spreadsheet_id, title)
        self.spreadsheets[spreadsheet_id][title] = [list(row) for row in values]

    def append_values(self, spreadsheet_id: str, range_a1: str, values: list[list]) -> None:
        self.calls.append(("append_values", (spreadsheet_id, range_a1)))
        title = self._title_from_range(range_a1)
        rows = self._require_sheet(spreadsheet_id, title)
        rows.extend(list(row) for row in values)

    def clear_values(self, spreadsheet_id: str, range_a1: str) -> None:
        self.calls.append(("clear_values", (spreadsheet_id, range_a1)))
        title = self._title_from_range(range_a1)
        self._require_sheet(spreadsheet_id, title)
        self.spreadsheets[spreadsheet_id][title] = []

    def add_sheet(self, spreadsheet_id: str, title: str) -> None:
        self.calls.append(("add_sheet", (spreadsheet_id, title)))
        book = self._require(spreadsheet_id)
        book.setdefault(title, [])

    def delete_sheet(self, spreadsheet_id: str, title: str) -> None:
        self.calls.append(("delete_sheet", (spreadsheet_id, title)))
        self._require_sheet(spreadsheet_id, title)
        del self.spreadsheets[spreadsheet_id][title]
