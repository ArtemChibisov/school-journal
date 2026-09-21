from django.test import SimpleTestCase

from school_years.imports.class_code_match import (
    STATUS_AMBIGUOUS,
    STATUS_MATCHED,
    STATUS_NOT_FOUND,
    fold,
    match_class_code,
)

# The real roster of 2025/26, exactly as the class codes are written in Student_Classes.
CLASS_CODES = [
    "0M", "0T",
    "1А", "1И", "1Н",
    "2А", "2Г", "2Р",
    "3Д", "3Р", "3С",
    "4А", "4К1", "4К2",
    "5А", "5Б", "5К1", "5К2",
    "6А", "6К1", "6К2",
    "7А", "7К1", "7К2",
    "8А", "8К",
    "9А", "9К",
    "10А", "10К",
    "11А", "11К",
]

# The real file names from both module folders, as Drive reports them.
PRIMARY_FILES = {
    "0М-Модуль 1-Class register template": "0M",
    "0Т-Модуль 1-Class register template": "0T",
    "1А-Модуль 1-Class register template": "1А",
    "1И-Модуль 1-Class register template": "1И",
    "1Н-Модуль 1-Class register template": "1Н",
    "2А-Модуль 1-Class register template": "2А",
    "2Г-Модуль 1-Class register template": "2Г",
    "2Р-Модуль 1-Class register template": "2Р",
    "3Д-Модуль 1-Class register template": "3Д",
    "3Р-Модуль 1-Class register template": "3Р",
    "3С-Модуль 1-Class register template": "3С",
}

SECONDARY_FILES = {
    "M1. 4А Class register": "4А",
    "M1. 4К1/4С1 Class register": "4К1",
    "M1. 4К2/4С2 Class register": "4К2",
    "M1. 5А Class register": "5А",
    "M1. 5Б Class register": "5Б",
    "M1. 5K1/5С1 Class register": "5К1",
    "M1. 5K2/5С2 Class register": "5К2",
    "M1. 6А Class register": "6А",
    "M1. 6K1/6С1 Class register": "6К1",
    "M1. 6K2/6С2 Class register": "6К2",
    "M1. 7А Class register": "7А",
    "M1. 7K1/7С1 Class register": "7К1",
    "M1. 7K2/7С2 Class register": "7К2",
    "M1. 8А Class register": "8А",
    "M1. 8K/8С Class register": "8К",
    "M1. 9А Class register": "9А",
    "M1. 9K/9С Class register": "9К",
    "M1. 10А Class register": "10А",
    "M1. 10K/10С Class register": "10К",
    "M1. 11А Class register": "11А",
    "M1. 11К/11C Class register": "11К",
}


class FoldTests(SimpleTestCase):
    def test_unifies_cyrillic_and_latin_lookalikes(self):
        self.assertEqual(fold("0М"), fold("0M"))
        self.assertEqual(fold("11К"), fold("11K"))
        self.assertEqual(fold("4С1"), fold("4C1"))

    def test_keeps_letters_that_have_no_counterpart(self):
        self.assertNotEqual(fold("2Г"), fold("2G"))
        self.assertNotEqual(fold("3Д"), fold("3D"))

    def test_is_case_insensitive(self):
        self.assertEqual(fold("4К1"), fold("4к1"))

    def test_collapses_whitespace(self):
        self.assertEqual(fold("  M1.   4А  "), "m1. 4a")


class RealFileNameTests(SimpleTestCase):
    def test_every_primary_file_finds_its_class(self):
        for file_name, expected in PRIMARY_FILES.items():
            with self.subTest(file_name=file_name):
                result = match_class_code(file_name, CLASS_CODES)
                self.assertEqual(result.status, STATUS_MATCHED, result)
                self.assertEqual(result.class_code, expected)

    def test_every_secondary_file_finds_its_class(self):
        for file_name, expected in SECONDARY_FILES.items():
            with self.subTest(file_name=file_name):
                result = match_class_code(file_name, CLASS_CODES)
                self.assertEqual(result.status, STATUS_MATCHED, result)
                self.assertEqual(result.class_code, expected)

    def test_all_thirty_two_classes_are_covered_exactly_once(self):
        matched = [
            match_class_code(name, CLASS_CODES).class_code
            for name in (*PRIMARY_FILES, *SECONDARY_FILES)
        ]

        self.assertEqual(len(matched), 32)
        self.assertEqual(sorted(matched), sorted(CLASS_CODES))

    def test_extension_does_not_affect_the_match(self):
        self.assertEqual(
            match_class_code("M1. 11К/11C Class register.xlsx", CLASS_CODES).class_code,
            "11К",
        )


class BoundaryTests(SimpleTestCase):
    def test_short_code_does_not_match_inside_a_longer_one(self):
        self.assertEqual(match_class_code("M1. 11А Class register", CLASS_CODES).class_code, "11А")

    def test_numbered_group_does_not_match_its_prefix(self):
        self.assertEqual(match_class_code("M1. 4К1 Class register", CLASS_CODES).class_code, "4К1")

    def test_module_number_is_not_taken_for_a_class(self):
        self.assertEqual(
            match_class_code("1А-Модуль 1-Class register template", CLASS_CODES).class_code,
            "1А",
        )

    def test_reports_not_found_for_an_unrelated_name(self):
        result = match_class_code("Итоги года.pdf", CLASS_CODES)

        self.assertEqual(result.status, STATUS_NOT_FOUND)

    def test_reports_not_found_for_an_empty_name(self):
        self.assertEqual(match_class_code("", CLASS_CODES).status, STATUS_NOT_FOUND)

    def test_reports_ambiguity_instead_of_guessing(self):
        result = match_class_code("M1. 4А и 5Б Class register", CLASS_CODES)

        self.assertEqual(result.status, STATUS_AMBIGUOUS)
        self.assertEqual(result.candidates, ("4А", "5Б"))
