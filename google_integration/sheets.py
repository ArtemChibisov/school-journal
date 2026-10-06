"""Google Sheets operations used by the reporting pipeline.

DEV-0203. Reading the input tables, filling a freshly copied class register, and rewriting
the monitoring and unsent-reports spreadsheets.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .api import guard
from .credentials import build_credentials
from .errors import CATEGORY_NOT_FOUND, GoogleIntegrationError


@runtime_checkable
class SheetsClient(Protocol):
    """What the services need from Sheets. Implemented for real and in memory."""

    def get_sheet_titles(self, spreadsheet_id: str) -> list[str]: ...

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[str]]: ...

    def write_values(self, spreadsheet_id: str, range_a1: str, values: list[list]) -> None: ...

    def append_values(self, spreadsheet_id: str, range_a1: str, values: list[list]) -> None: ...

    def clear_values(self, spreadsheet_id: str, range_a1: str) -> None: ...

    def add_sheet(self, spreadsheet_id: str, title: str) -> None: ...

    def delete_sheet(self, spreadsheet_id: str, title: str) -> None: ...

    def duplicate_sheet(self, spreadsheet_id: str, source_title: str, new_title: str) -> None: ...


class ApiSheetsClient:
    """Sheets v4 client."""

    def __init__(self, service=None, *, credentials=None):
        self._service = service
        self._credentials = credentials

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            credentials = self._credentials or build_credentials()
            with guard("sheets.build"):
                self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return self._service

    def _sheet_properties(self, spreadsheet_id: str) -> list[dict]:
        with guard("sheets.spreadsheets.get", spreadsheet_id=spreadsheet_id):
            response = (
                self.service.spreadsheets()
                .get(spreadsheetId=spreadsheet_id, fields="sheets.properties")
                .execute()
            )
        return [sheet["properties"] for sheet in response.get("sheets", [])]

    def get_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        return [props["title"] for props in self._sheet_properties(spreadsheet_id)]

    def _sheet_id(self, spreadsheet_id: str, title: str) -> int:
        for props in self._sheet_properties(spreadsheet_id):
            if props["title"] == title:
                return props["sheetId"]
        raise GoogleIntegrationError(
            CATEGORY_NOT_FOUND,
            f"Worksheet {title!r} not found in spreadsheet {spreadsheet_id}",
            user_message=f"В таблице не найден лист «{title}».",
            context={"spreadsheet_id": spreadsheet_id, "title": title},
        )

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[str]]:
        with guard("sheets.values.get", spreadsheet_id=spreadsheet_id, range=range_a1):
            response = (
                self.service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_a1)
                .execute()
            )
        return response.get("values", [])

    def write_values(self, spreadsheet_id: str, range_a1: str, values: list[list]) -> None:
        with guard("sheets.values.update", spreadsheet_id=spreadsheet_id, range=range_a1):
            self.service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()

    def append_values(self, spreadsheet_id: str, range_a1: str, values: list[list]) -> None:
        with guard("sheets.values.append", spreadsheet_id=spreadsheet_id, range=range_a1):
            self.service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            ).execute()

    def clear_values(self, spreadsheet_id: str, range_a1: str) -> None:
        with guard("sheets.values.clear", spreadsheet_id=spreadsheet_id, range=range_a1):
            self.service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id, range=range_a1, body={}
            ).execute()

    def _batch_update(self, spreadsheet_id: str, requests: list[dict], *, operation: str) -> dict:
        with guard(operation, spreadsheet_id=spreadsheet_id):
            return (
                self.service.spreadsheets()
                .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
                .execute()
            )

    def add_sheet(self, spreadsheet_id: str, title: str) -> None:
        self._batch_update(
            spreadsheet_id,
            [{"addSheet": {"properties": {"title": title}}}],
            operation="sheets.batchUpdate.addSheet",
        )

    def duplicate_sheet(self, spreadsheet_id: str, source_title: str, new_title: str) -> None:
        """Копия листа вместе с форматом и проверкой данных.

        Не `addSheet`: он создаёт пустой лист. В бланке журнала выпадающие списки — это и есть
        бланк, учитель выбирает уровень из них, а не печатает его руками.
        """

        sheet_id = self._sheet_id(spreadsheet_id, source_title)
        self._batch_update(
            spreadsheet_id,
            [
                {
                    "duplicateSheet": {
                        "sourceSheetId": sheet_id,
                        "newSheetName": new_title,
                    }
                }
            ],
            operation="sheets.batchUpdate.duplicateSheet",
        )

    def delete_sheet(self, spreadsheet_id: str, title: str) -> None:
        sheet_id = self._sheet_id(spreadsheet_id, title)
        self._batch_update(
            spreadsheet_id,
            [{"deleteSheet": {"sheetId": sheet_id}}],
            operation="sheets.batchUpdate.deleteSheet",
        )


def replace_sheet_contents(
    client: SheetsClient,
    spreadsheet_id: str,
    title: str,
    values: list[list],
) -> None:
    """Rewrite a whole worksheet: clear it, then write the new rows.

    Used by the monitoring export, which must not accumulate stale rows from a previous run.
    """

    if title not in client.get_sheet_titles(spreadsheet_id):
        client.add_sheet(spreadsheet_id, title)

    client.clear_values(spreadsheet_id, f"'{title}'")
    if values:
        client.write_values(spreadsheet_id, f"'{title}'!A1", values)
