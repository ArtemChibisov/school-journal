"""The first page is a Word template, and a Word template goes stale without saying so.

«Учебный модуль 4», «12.01 – 13.02.2026» and «2025-2026» were typed into it by hand for one
run and stayed there. Every report built afterwards carried that header - module 4 of last
year - whatever module it was actually for, because nothing in the code could tell.

These tests fail if the header is written into the template again instead of being filled in
by the run.
"""

from __future__ import annotations

import re
import zipfile

from django.test import SimpleTestCase

from pipeline.legacy_docx import LegacyDocxGenerator

PLACEHOLDERS = (
    "{{StudentName}}",
    "{{Class}}",
    "{{ModuleNumber}}",
    "{{ModuleDates}}",
    "{{SchoolYear}}",
)


def template_text() -> str:
    path = LegacyDocxGenerator().template_path
    document = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", document, re.S))


class FirstPageTemplateTests(SimpleTestCase):
    def test_every_header_value_is_left_for_the_run_to_fill(self):
        text = template_text()

        for placeholder in PLACEHOLDERS:
            self.assertIn(placeholder, text, placeholder)

    def test_no_school_year_is_written_into_it(self):
        self.assertNotRegex(template_text(), r"20\d\d\s*[-–/]\s*20\d\d")

    def test_no_module_dates_are_written_into_it(self):
        self.assertNotRegex(template_text(), r"\d\d\.\d\d\s*[-–]\s*\d\d\.\d\d")
