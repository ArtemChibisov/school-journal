from django.test import SimpleTestCase

from google_integration.api import guard
from google_integration.credentials import (
    ACCESS_MODE_OAUTH_OWNER,
    ACCESS_MODE_PUBLIC,
    build_credentials,
    get_access_mode,
)
from google_integration.drive import MIME_FOLDER, DriveClient, ensure_folder, require_folder
from google_integration.errors import (
    CATEGORY_INVALID_CONFIG,
    CATEGORY_NOT_FOUND,
    CATEGORY_PERMISSION_DENIED,
    CATEGORY_RATE_LIMITED,
    CATEGORY_UNKNOWN,
    GoogleIntegrationError,
    user_message_for,
)
from google_integration.fakes import FakeDriveClient, FakeSheetsClient
from google_integration.sheets import SheetsClient, replace_sheet_contents


class _Response:
    def __init__(self, status):
        self.status = status


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = _Response(status)


class FakesSatisfyProtocolsTests(SimpleTestCase):
    def test_fake_drive_is_a_drive_client(self):
        self.assertIsInstance(FakeDriveClient(), DriveClient)

    def test_fake_sheets_is_a_sheets_client(self):
        self.assertIsInstance(FakeSheetsClient(), SheetsClient)


class FakeDriveClientTests(SimpleTestCase):
    def setUp(self):
        self.drive = FakeDriveClient()
        self.root = self.drive.add_folder("Отчеты 4-11")

    def test_get_file_returns_metadata(self):
        item = self.drive.get_file(self.root)

        self.assertEqual(item["name"], "Отчеты 4-11")
        self.assertEqual(item["mimeType"], MIME_FOLDER)

    def test_get_file_raises_not_found_for_unknown_id(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            self.drive.get_file("nope")

        self.assertEqual(ctx.exception.category, CATEGORY_NOT_FOUND)

    def test_list_children_filters_by_mime_type(self):
        self.drive.add_folder("1 модуль", parent_id=self.root)
        self.drive.add_file("readme", parent_id=self.root, mime_type="text/plain")

        folders = self.drive.list_children(self.root, mime_type=MIME_FOLDER)

        self.assertEqual([item["name"] for item in folders], ["1 модуль"])

    def test_find_child_matches_by_name(self):
        self.drive.add_folder("2 модуль", parent_id=self.root)

        self.assertIsNotNone(self.drive.find_child(self.root, "2 модуль"))
        self.assertIsNone(self.drive.find_child(self.root, "3 модуль"))

    def test_copy_file_places_a_new_file_in_the_target_folder(self):
        template = self.drive.add_file("Шаблон")
        target = self.drive.add_folder("1 модуль", parent_id=self.root)

        copy = self.drive.copy_file(template, parent_id=target, name="4А module 1")

        self.assertEqual(copy["name"], "4А module 1")
        self.assertEqual(copy["parents"], [target])
        self.assertNotEqual(copy["id"], template)

    def test_trashed_file_is_no_longer_visible(self):
        doomed = self.drive.add_file("Старый", parent_id=self.root)

        self.drive.trash_file(doomed)

        self.assertEqual(self.drive.list_children(self.root), [])
        with self.assertRaises(GoogleIntegrationError):
            self.drive.get_file(doomed)


class EnsureFolderTests(SimpleTestCase):
    def setUp(self):
        self.drive = FakeDriveClient()
        self.root = self.drive.add_folder("Отчеты 0-3")

    def test_creates_the_folder_when_missing(self):
        created = ensure_folder(self.drive, self.root, "1 модуль")

        self.assertEqual(created["name"], "1 модуль")
        self.assertEqual(len(self.drive.list_children(self.root, mime_type=MIME_FOLDER)), 1)

    def test_reuses_the_existing_folder_instead_of_duplicating(self):
        first = ensure_folder(self.drive, self.root, "1 модуль")
        second = ensure_folder(self.drive, self.root, "1 модуль")

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.drive.list_children(self.root, mime_type=MIME_FOLDER)), 1)

    def test_require_folder_rejects_a_file(self):
        not_a_folder = self.drive.add_file("Таблица", parent_id=self.root)

        with self.assertRaises(GoogleIntegrationError) as ctx:
            require_folder(self.drive, not_a_folder)

        self.assertEqual(ctx.exception.category, CATEGORY_NOT_FOUND)
        self.assertIn("не на папку", ctx.exception.user_message)


class FakeSheetsClientTests(SimpleTestCase):
    def setUp(self):
        self.sheets = FakeSheetsClient()
        self.book = self.sheets.add_spreadsheet(
            "book-1",
            {
                "classes": [["class_code", "grade_number"], ["4А", "4"]],
                "students": [["class_code", "student_name"], ["4А", "Оливия Арутюнян"]],
            },
        )

    def test_lists_sheet_titles(self):
        self.assertEqual(self.sheets.get_sheet_titles(self.book), ["classes", "students"])

    def test_reads_values_by_plain_title(self):
        self.assertEqual(self.sheets.read_values(self.book, "classes")[1], ["4А", "4"])

    def test_reads_values_by_quoted_range(self):
        self.assertEqual(self.sheets.read_values(self.book, "'students'!A1:D10")[1][1], "Оливия Арутюнян")

    def test_write_replaces_the_whole_sheet(self):
        self.sheets.write_values(self.book, "classes!A1", [["only"]])

        self.assertEqual(self.sheets.read_values(self.book, "classes"), [["only"]])

    def test_append_adds_rows(self):
        self.sheets.append_values(self.book, "classes", [["5Б", "5"]])

        self.assertEqual(self.sheets.read_values(self.book, "classes")[-1], ["5Б", "5"])

    def test_clear_empties_the_sheet(self):
        self.sheets.clear_values(self.book, "classes")

        self.assertEqual(self.sheets.read_values(self.book, "classes"), [])

    def test_unknown_sheet_raises_not_found_with_a_readable_message(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            self.sheets.read_values(self.book, "missing")

        self.assertEqual(ctx.exception.category, CATEGORY_NOT_FOUND)
        self.assertIn("не найден лист", ctx.exception.user_message)

    def test_unknown_spreadsheet_raises_not_found(self):
        with self.assertRaises(GoogleIntegrationError):
            self.sheets.get_sheet_titles("missing-book")


class ReplaceSheetContentsTests(SimpleTestCase):
    def setUp(self):
        self.sheets = FakeSheetsClient()
        self.book = self.sheets.add_spreadsheet("book-1", {"Summary": [["stale"], ["rows"]]})

    def test_overwrites_existing_rows(self):
        replace_sheet_contents(self.sheets, self.book, "Summary", [["fresh"]])

        self.assertEqual(self.sheets.read_values(self.book, "Summary"), [["fresh"]])

    def test_creates_the_sheet_when_missing(self):
        replace_sheet_contents(self.sheets, self.book, "Problems", [["a", "b"]])

        self.assertIn("Problems", self.sheets.get_sheet_titles(self.book))
        self.assertEqual(self.sheets.read_values(self.book, "Problems"), [["a", "b"]])

    def test_empty_payload_leaves_an_empty_sheet(self):
        replace_sheet_contents(self.sheets, self.book, "Summary", [])

        self.assertEqual(self.sheets.read_values(self.book, "Summary"), [])


class GuardTests(SimpleTestCase):
    def test_translates_403_to_permission_denied(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            with guard("drive.files.get", file_id="x"):
                raise _HttpError(403)

        self.assertEqual(ctx.exception.category, CATEGORY_PERMISSION_DENIED)
        self.assertEqual(ctx.exception.context["file_id"], "x")

    def test_translates_404_to_not_found(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            with guard("drive.files.get"):
                raise _HttpError(404)

        self.assertEqual(ctx.exception.category, CATEGORY_NOT_FOUND)

    def test_translates_429_to_rate_limited(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            with guard("sheets.values.get"):
                raise _HttpError(429)

        self.assertEqual(ctx.exception.category, CATEGORY_RATE_LIMITED)

    def test_unknown_exception_keeps_the_operation_name(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            with guard("drive.files.copy"):
                raise ValueError("boom")

        self.assertEqual(ctx.exception.category, CATEGORY_UNKNOWN)
        self.assertIn("drive.files.copy", ctx.exception.technical_message)

    def test_our_own_error_passes_through_unchanged(self):
        original = GoogleIntegrationError(CATEGORY_NOT_FOUND, "already classified")

        with self.assertRaises(GoogleIntegrationError) as ctx:
            with guard("drive.files.get"):
                raise original

        self.assertIs(ctx.exception, original)

    def test_user_message_never_leaks_http_detail(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            with guard("drive.files.get", file_id="secret-id"):
                raise _HttpError(403)

        self.assertNotIn("403", ctx.exception.user_message)
        self.assertNotIn("secret-id", ctx.exception.user_message)
        self.assertEqual(ctx.exception.user_message, user_message_for(CATEGORY_PERMISSION_DENIED))


class AccessModeTests(SimpleTestCase):
    def test_defaults_to_oauth_owner(self):
        with self.settings():
            import os

            previous = os.environ.pop("GOOGLE_ACCESS_MODE", None)
            try:
                self.assertEqual(get_access_mode(), ACCESS_MODE_OAUTH_OWNER)
            finally:
                if previous is not None:
                    os.environ["GOOGLE_ACCESS_MODE"] = previous

    def test_rejects_an_unknown_mode(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"GOOGLE_ACCESS_MODE": "nonsense"}):
            with self.assertRaises(GoogleIntegrationError) as ctx:
                get_access_mode()

        self.assertEqual(ctx.exception.category, CATEGORY_INVALID_CONFIG)

    def test_public_link_cannot_build_credentials(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"GOOGLE_ACCESS_MODE": ACCESS_MODE_PUBLIC}):
            with self.assertRaises(GoogleIntegrationError) as ctx:
                build_credentials()

        self.assertEqual(ctx.exception.category, CATEGORY_INVALID_CONFIG)
        self.assertIn("public_link", ctx.exception.technical_message)
