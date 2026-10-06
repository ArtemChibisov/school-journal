"""Saving a Drive DOCX as a PDF without downloading it or installing a converter."""

from django.test import SimpleTestCase

from google_integration.drive import MIME_DOCX, MIME_GOOGLE_DOC, MIME_PDF
from google_integration.fakes import FakeDriveClient
from google_integration.pdf import docx_to_pdf, pdf_name_for


class PdfNameTests(SimpleTestCase):
    def test_the_extension_is_swapped(self):
        self.assertEqual(pdf_name_for("4А Иванов.docx"), "4А Иванов.pdf")

    def test_an_uppercase_extension_counts(self):
        self.assertEqual(pdf_name_for("4А Иванов.DOCX"), "4А Иванов.pdf")

    def test_a_name_without_one_simply_gains_it(self):
        self.assertEqual(pdf_name_for("4А Иванов"), "4А Иванов.pdf")


class DocxToPdfTests(SimpleTestCase):
    def setUp(self):
        self.drive = FakeDriveClient()
        self.docx_folder = self.drive.add_folder("4А")
        self.pdf_folder = self.drive.add_folder("4А pdf")
        self.docx_id = self.drive.add_file(
            "4А Иванов.docx", parent_id=self.docx_folder, mime_type=MIME_DOCX
        )
        self.docx = self.drive.get_file(self.docx_id)

    def _convert(self):
        return docx_to_pdf(self.drive, docx=self.docx, pdf_folder_id=self.pdf_folder)

    def test_the_pdf_lands_in_the_folder_it_was_asked_for(self):
        result = self._convert()

        self.assertEqual(result["name"], "4А Иванов.pdf")
        self.assertEqual(result["mimeType"], MIME_PDF)
        self.assertEqual(self.drive.find_child(self.pdf_folder, "4А Иванов.pdf")["id"], result["id"])

    def test_google_does_the_rendering(self):
        """A copy as a Google Doc, exported - no download, no converter on the server."""

        self._convert()

        actions = [call[0] for call in self.drive.calls]
        self.assertIn("copy_file", actions)
        self.assertIn("export_file", actions)
        copy = next(call for call in self.drive.calls if call[0] == "copy_file")
        self.assertEqual(copy[1][3], MIME_GOOGLE_DOC)

    def test_the_working_copy_does_not_stay_in_the_folder(self):
        self._convert()

        names = [item["name"] for item in self.drive.list_children(self.pdf_folder)]
        self.assertEqual(names, ["4А Иванов.pdf"])

    def test_the_source_docx_is_left_alone(self):
        self._convert()

        self.assertEqual(
            [item["name"] for item in self.drive.list_children(self.docx_folder)],
            ["4А Иванов.docx"],
        )

    def test_running_again_replaces_the_pdf_and_keeps_its_id(self):
        """A link already sent to a parent has to open the corrected report."""

        first = self._convert()
        second = self._convert()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.drive.list_children(self.pdf_folder)), 1)

    def test_a_failed_export_does_not_leave_the_copy_behind(self):
        def refuse(file_id, mime_type):
            raise RuntimeError("Google said no")

        self.drive.export_file = refuse

        with self.assertRaises(RuntimeError):
            self._convert()

        self.assertEqual(self.drive.list_children(self.pdf_folder), [])
