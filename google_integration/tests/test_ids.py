from django.test import SimpleTestCase

from google_integration.errors import CATEGORY_INVALID_URL, GoogleIntegrationError
from google_integration.ids import (
    KIND_DOCUMENT,
    KIND_FILE,
    KIND_FOLDER,
    KIND_SPREADSHEET,
    extract_file_id,
    extract_folder_id,
    extract_gid,
    extract_spreadsheet_id,
    folder_url,
    parse_google_url,
    spreadsheet_url,
)

SHEET_ID = "1ZF69YxwebacQEs6sadKwXXyUNvGBy_QpwxhGWEH1laQ"
FOLDER_ID = "1EWQ3fTwkvlbfgzq-vOSt_U7kLA1ddJfS"


class ParseGoogleUrlTests(SimpleTestCase):
    def test_parses_spreadsheet_link(self):
        ref = parse_google_url(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")

        self.assertEqual(ref.kind, KIND_SPREADSHEET)
        self.assertEqual(ref.resource_id, SHEET_ID)
        self.assertIsNone(ref.gid)

    def test_parses_spreadsheet_link_with_gid_in_fragment(self):
        ref = parse_google_url(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=0#gid=1388516290")

        self.assertEqual(ref.kind, KIND_SPREADSHEET)
        self.assertEqual(ref.gid, "0")

    def test_reads_gid_from_fragment_when_query_has_none(self):
        self.assertEqual(
            extract_gid(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=1388516290"),
            "1388516290",
        )

    def test_parses_folder_link(self):
        ref = parse_google_url(f"https://drive.google.com/drive/folders/{FOLDER_ID}?usp=sharing")

        self.assertEqual(ref.kind, KIND_FOLDER)
        self.assertEqual(ref.resource_id, FOLDER_ID)
        self.assertTrue(ref.is_folder)

    def test_parses_file_link(self):
        ref = parse_google_url(f"https://drive.google.com/file/d/{FOLDER_ID}/view")

        self.assertEqual(ref.kind, KIND_FILE)
        self.assertEqual(ref.resource_id, FOLDER_ID)

    def test_parses_document_link(self):
        ref = parse_google_url(f"https://docs.google.com/document/d/{SHEET_ID}/edit")

        self.assertEqual(ref.kind, KIND_DOCUMENT)

    def test_parses_open_id_link(self):
        ref = parse_google_url(f"https://drive.google.com/open?id={FOLDER_ID}")

        self.assertEqual(ref.kind, KIND_FILE)
        self.assertEqual(ref.resource_id, FOLDER_ID)

    def test_accepts_bare_id(self):
        ref = parse_google_url(SHEET_ID)

        self.assertEqual(ref.kind, KIND_FILE)
        self.assertEqual(ref.resource_id, SHEET_ID)

    def test_trims_surrounding_whitespace(self):
        ref = parse_google_url(f"  https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit  ")

        self.assertEqual(ref.resource_id, SHEET_ID)

    def test_rejects_empty_value(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            parse_google_url("")

        self.assertEqual(ctx.exception.category, CATEGORY_INVALID_URL)

    def test_rejects_unrelated_url(self):
        with self.assertRaises(GoogleIntegrationError):
            parse_google_url("https://example.com/some/page")

    def test_rejects_short_fragment(self):
        with self.assertRaises(GoogleIntegrationError):
            parse_google_url("edit")


class TypedExtractorTests(SimpleTestCase):
    def test_spreadsheet_id_from_link(self):
        self.assertEqual(
            extract_spreadsheet_id(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"),
            SHEET_ID,
        )

    def test_spreadsheet_id_rejects_folder_link(self):
        with self.assertRaises(GoogleIntegrationError) as ctx:
            extract_spreadsheet_id(f"https://drive.google.com/drive/folders/{FOLDER_ID}")

        self.assertEqual(ctx.exception.category, CATEGORY_INVALID_URL)
        self.assertIn("Google Sheets", ctx.exception.technical_message)

    def test_folder_id_from_link(self):
        self.assertEqual(
            extract_folder_id(f"https://drive.google.com/drive/folders/{FOLDER_ID}"),
            FOLDER_ID,
        )

    def test_folder_id_rejects_spreadsheet_link(self):
        with self.assertRaises(GoogleIntegrationError):
            extract_folder_id(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")

    def test_file_id_rejects_folder_link(self):
        with self.assertRaises(GoogleIntegrationError):
            extract_file_id(f"https://drive.google.com/drive/folders/{FOLDER_ID}")

    def test_bare_id_is_accepted_by_every_extractor(self):
        self.assertEqual(extract_spreadsheet_id(SHEET_ID), SHEET_ID)
        self.assertEqual(extract_folder_id(SHEET_ID), SHEET_ID)
        self.assertEqual(extract_file_id(SHEET_ID), SHEET_ID)


class UrlBuilderTests(SimpleTestCase):
    def test_round_trip_spreadsheet(self):
        self.assertEqual(extract_spreadsheet_id(spreadsheet_url(SHEET_ID)), SHEET_ID)

    def test_round_trip_folder(self):
        self.assertEqual(extract_folder_id(folder_url(FOLDER_ID)), FOLDER_ID)
