"""Создание журнала модуля из бланка.

Раньше завуч копировал шаблон тридцать два раза, переименовывал копии, в каждой размножал лист
по предметам и вписывал учителя. Одна ошибка в этой работе всплывала через месяц — в отчёте.
"""

from datetime import date

from django.test import SimpleTestCase, TestCase

from google_integration.drive import MIME_SPREADSHEET
from jobs.models import JobRun
from google_integration.fakes import FakeDriveClient, FakeSheetsClient
from school_years.models import (
    ClassGroup,
    SchoolModule,
    SchoolYear,
    SubjectTeacher,
    Teacher,
)
from school_years.register_generation import (
    PROTOTYPE_SHEET,
    TUTOR_SHEET,
    generate_for_class,
    register_name,
    split_name,
)

JUNIOR_TEMPLATE = "https://docs.google.com/spreadsheets/d/juniortemplate01/edit"
SENIOR_TEMPLATE = "https://docs.google.com/spreadsheets/d/seniortemplate01/edit"

HEADER_ROWS = [
    ["", "Класс | Grade", ""],
    ["", "Учитель | Teacher", ""],
    ["", "Учебный модуль # | Module #", ""],
    ["", "Дескриптор | Descriptor", ""],
    ["", "Критерии оценивания | Assessment criteria", "1"],
    ["ИМЯ", "ФАМИЛИЯ", ""],
]


class SplitNameTests(SimpleTestCase):
    def test_name_and_surname(self):
        self.assertEqual(split_name("Мария Иванова"), ("Мария", "Иванова"))

    def test_a_double_surname_stays_whole(self):
        self.assertEqual(
            split_name("Мария Шилина-Терентьева"), ("Мария", "Шилина-Терентьева")
        )

    def test_a_surname_of_two_words(self):
        self.assertEqual(split_name("Теа Уклеба Орлова"), ("Теа", "Уклеба Орлова"))

    def test_one_word_is_a_name(self):
        self.assertEqual(split_name("Мария"), ("Мария", ""))


class GenerateTests(TestCase):
    def setUp(self):
        self.drive = FakeDriveClient()
        self.sheets = FakeSheetsClient()
        self.junior_folder = self.drive.add_folder("Журналы 0-3")
        self.senior_folder = self.drive.add_folder("Журналы 4-11")

        self.year = SchoolYear.objects.create(
            name="26/27",
            is_active=True,
            class_register_template_0_3_url=JUNIOR_TEMPLATE,
            class_register_template_4_11_url=SENIOR_TEMPLATE,
            register_root_0_3_url=f"https://drive.google.com/drive/folders/{self.junior_folder}",
            register_root_4_11_url=f"https://drive.google.com/drive/folders/{self.senior_folder}",
        )
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
        )

        # Бланк 0-3: один лист-образец. Бланк 4-11: ещё и лист тьютора.
        self.drive.add_file("Шаблон 0-3", mime_type=MIME_SPREADSHEET, file_id="juniortemplate01")
        self.drive.add_file("Шаблон 4-11", mime_type=MIME_SPREADSHEET, file_id="seniortemplate01")
        self.sheets.add_spreadsheet("juniortemplate01", {PROTOTYPE_SHEET: [list(row) for row in HEADER_ROWS]})
        self.sheets.add_spreadsheet(
            "seniortemplate01",
            {
                PROTOTYPE_SHEET: [list(row) for row in HEADER_ROWS],
                TUTOR_SHEET: [list(row) for row in HEADER_ROWS],
            },
        )

    def _class(self, class_code, grade_number, tutor="Алёна Прокушева"):
        return ClassGroup.objects.create(
            school_year=self.year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name=tutor,
        )

    def _subject(self, class_group, subject, name_ru="Юлия Галкина", name_en="Yuliya Galkina"):
        teacher, _ = Teacher.objects.get_or_create(
            school_year=self.year, name_ru=name_ru, name_en=name_en
        )
        return SubjectTeacher.objects.create(
            school_year=self.year,
            class_group=class_group,
            teacher=teacher,
            subject_name=subject,
        )

    def _student(self, class_group, full_name, order=0):
        from school_years.models import Student

        return Student.objects.create(
            school_year=self.year,
            class_group=class_group,
            student_name=full_name,
            student_order=order,
        )

    def _generate(self, class_group):
        # Копия в FakeDrive получает новый id; таблицу под него заводим так же, как Google.
        result = generate_for_class(
            self.module, class_group, drive=self.drive, sheets=self.sheets
        )
        return result

    def _prepare_copy(self, source_id):
        """FakeDrive копирует файл, FakeSheets о нём не знает — связываем их, как Google."""

        original_copy = self.drive.copy_file

        def copy(file_id, *, parent_id, name, mime_type=None):
            created = original_copy(file_id, parent_id=parent_id, name=name, mime_type=mime_type)
            self.sheets.add_spreadsheet(
                created["id"],
                {title: [list(r) for r in rows] for title, rows in self.sheets.spreadsheets[source_id].items()},
            )
            return created

        self.drive.copy_file = copy

    # -- что получается ------------------------------------------------------------------

    def test_a_sheet_per_subject_and_no_prototype_left(self):
        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._subject(group, "Чтение")
        self._prepare_copy("juniortemplate01")

        result = self._generate(group)

        self.assertEqual(result.status, "created")
        titles = self.sheets.get_sheet_titles(self._created_id())
        self.assertIn("Математика", titles)
        self.assertIn("Чтение", titles)
        self.assertNotIn(PROTOTYPE_SHEET, titles)

    def _created_id(self):
        return next(
            file_id
            for file_id, item in self.drive.files.items()
            if item["mimeType"] == MIME_SPREADSHEET and item["parents"]
        )

    def test_the_header_cells_the_system_owns_are_filled(self):
        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._prepare_copy("juniortemplate01")

        self._generate(group)

        written = [call for call in self.sheets.calls if call[0] == "write_values"]
        ranges = [call[1][1] for call in written]
        self.assertIn("'Математика'!C1", ranges)
        self.assertIn("'Математика'!C2", ranges)
        self.assertIn("'Математика'!C3", ranges)

    def test_students_start_at_the_row_the_contract_says(self):
        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._student(group, "Мария Иванова", order=1)
        self._student(group, "Пётр Быков", order=2)
        self._prepare_copy("juniortemplate01")

        result = self._generate(group)

        self.assertEqual(result.students, 2)
        ranges = [call[1][1] for call in self.sheets.calls if call[0] == "write_values"]
        self.assertIn("'Математика'!A7:B8", ranges)

    def test_the_primary_school_has_no_tutor_sheet(self):
        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._prepare_copy("juniortemplate01")

        self._generate(group)

        self.assertNotIn(TUTOR_SHEET, self.sheets.get_sheet_titles(self._created_id()))

    def test_the_senior_school_keeps_its_tutor_sheet(self):
        group = self._class("7А", 7)
        self._subject(group, "Алгебра")
        self._prepare_copy("seniortemplate01")

        self._generate(group)

        self.assertIn(TUTOR_SHEET, self.sheets.get_sheet_titles(self._created_id()))

    def test_the_file_is_named_so_the_scan_can_find_it(self):
        self.assertIn("2Б", register_name(self.module, "2Б"))

    def _recorded_writes(self):
        """Фейк пишет на уровне листа и значений не хранит — записываем их сами."""

        written = {}
        original = self.sheets.write_values

        def write(spreadsheet_id, range_a1, values):
            written[range_a1] = values
            return original(spreadsheet_id, range_a1, values)

        self.sheets.write_values = write
        return written

    def test_an_english_class_gets_the_teacher_written_in_english(self):
        group = self._class("4К1", 4)
        self._subject(group, "Maths")
        self._prepare_copy("seniortemplate01")
        written = self._recorded_writes()

        self._generate(group)

        self.assertEqual(written["'Maths'!C2"], [["Yuliya Galkina"]])

    def test_a_russian_class_gets_the_russian_spelling(self):
        group = self._class("4А", 4)
        self._subject(group, "Математика")
        self._prepare_copy("seniortemplate01")
        written = self._recorded_writes()

        self._generate(group)

        self.assertEqual(written["'Математика'!C2"], [["Юлия Галкина"]])

    def test_the_class_and_the_module_are_written_in(self):
        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._prepare_copy("juniortemplate01")
        written = self._recorded_writes()

        self._generate(group)

        self.assertEqual(written["'Математика'!C1"], [["2Б"]])
        self.assertEqual(written["'Математика'!C3"], [[1]])

    def test_two_teachers_of_one_subject_stand_together(self):
        """Соведение — обычное дело, в журнале они через запятую."""

        group = self._class("2Б", 2)
        self._subject(group, "Драма", name_ru="Анна Белова", name_en="Anna Belova")
        self._subject(group, "Драма", name_ru="Пётр Быков", name_en="Petr Bykov")
        self._prepare_copy("juniortemplate01")
        written = self._recorded_writes()

        self._generate(group)

        self.assertEqual(written["'Драма'!C2"], [["Анна Белова, Пётр Быков"]])

    # -- привязка к классу ---------------------------------------------------------------

    def test_the_journal_is_attached_so_the_rest_of_the_system_sees_it(self):
        """Создать файл мало: непривязанный журнал не видит ничто."""

        from journal_links.models import ClassSheetLink
        from school_years.models import ClassRegister

        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._prepare_copy("juniortemplate01")

        self._generate(group)

        link = ClassSheetLink.objects.get(class_code="2Б")
        self.assertTrue(link.is_active)
        self.assertTrue(link.google_sheet_url)

        register = ClassRegister.objects.get(module=self.module, class_group=group)
        self.assertEqual(register.status, ClassRegister.Status.CREATED)
        self.assertEqual(register.google_file_id, self._created_id())

    def test_a_refused_class_is_attached_to_nothing(self):
        group = self._class("2Б", 2)

        self._generate(group)

        from journal_links.models import ClassSheetLink

        self.assertFalse(ClassSheetLink.objects.filter(class_code="2Б").exists())

    # -- когда делать нечего --------------------------------------------------------------

    def test_a_class_without_subjects_is_refused_rather_than_left_half_made(self):
        group = self._class("2Б", 2)

        result = self._generate(group)

        self.assertEqual(result.status, "skipped")
        self.assertIn("Нет предметов", result.reason)

    def test_without_a_template_it_says_which_one(self):
        self.year.class_register_template_0_3_url = ""
        self.year.save()
        group = self._class("2Б", 2)
        self._subject(group, "Математика")

        result = self._generate(group)

        self.assertEqual(result.status, "skipped")
        self.assertIn("0-3", result.reason)

    # -- папка модуля ---------------------------------------------------------------------

    def test_the_module_folder_is_created_inside_the_year_folder(self):
        """Завуч указывает корневую папку один раз; папку модуля заводит система."""

        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._prepare_copy("juniortemplate01")

        self._generate(group)

        module_folder = self.drive.find_child(self.junior_folder, "1 модуль/module")
        self.assertIsNotNone(module_folder)
        created = self.drive.get_file(self._created_id())
        self.assertIn(module_folder["id"], created["parents"])

    def test_the_folder_is_written_into_the_module_so_the_scan_finds_it(self):
        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._prepare_copy("juniortemplate01")

        self._generate(group)

        self.module.refresh_from_db()
        self.assertTrue(self.module.register_folder_0_3_url)

    def test_a_folder_the_school_chose_itself_is_left_alone(self):
        chosen = self.drive.add_folder("Журналы вручную")
        self.module.register_folder_0_3_url = f"https://drive.google.com/drive/folders/{chosen}"
        self.module.save()
        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._prepare_copy("juniortemplate01")

        self._generate(group)

        self.assertIn(chosen, self.drive.get_file(self._created_id())["parents"])
        self.assertIsNone(self.drive.find_child(self.junior_folder, "1 модуль/module"))

    def test_a_second_class_goes_into_the_same_module_folder(self):
        first = self._class("2Б", 2)
        second = self._class("2А", 2)
        self._subject(first, "Математика")
        self._subject(second, "Математика")
        self._prepare_copy("juniortemplate01")

        self._generate(first)
        self._generate(second)

        self.assertEqual(len(self.drive.list_children(self.junior_folder)), 1)

    def test_an_existing_journal_is_not_touched(self):
        """В нём может быть работа учителей — перезаписать её хуже, чем не создать."""

        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        module_folder = self.drive.add_folder("1 модуль/module", parent_id=self.junior_folder)
        self.drive.add_file(
            register_name(self.module, "2Б"),
            parent_id=module_folder,
            mime_type=MIME_SPREADSHEET,
        )
        before = len(self.drive.files)

        result = self._generate(group)

        self.assertEqual(result.status, "exists")
        self.assertEqual(len(self.drive.files), before)


class RunGenerationTests(GenerateTests):
    """Кнопка: один модуль, все классы, отчёт по каждому."""

    def test_every_class_of_the_module_is_done_in_one_run(self):
        from school_years.register_generation import JOB_TYPE, run_register_generation

        first = self._class("2Б", 2)
        second = self._class("2А", 2)
        self._subject(first, "Математика")
        self._subject(second, "Математика")
        self._prepare_copy("juniortemplate01")

        job_run = run_register_generation(
            self.module, drive=self.drive, sheets=self.sheets
        )

        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertEqual(job_run.result_json["summary"]["created"], 2)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)

    def test_a_class_that_cannot_be_done_does_not_cost_the_others_theirs(self):
        from school_years.register_generation import run_register_generation

        ready = self._class("2Б", 2)
        self._subject(ready, "Математика")
        self._class("2А", 2)  # без предметов
        self._prepare_copy("juniortemplate01")

        job_run = run_register_generation(self.module, drive=self.drive, sheets=self.sheets)

        summary = job_run.result_json["summary"]
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)

    def test_running_again_creates_nothing_new(self):
        from school_years.register_generation import run_register_generation

        group = self._class("2Б", 2)
        self._subject(group, "Математика")
        self._prepare_copy("juniortemplate01")

        run_register_generation(self.module, drive=self.drive, sheets=self.sheets)
        job_run = run_register_generation(self.module, drive=self.drive, sheets=self.sheets)

        self.assertEqual(job_run.result_json["summary"]["created"], 0)
        self.assertEqual(job_run.result_json["classes"][0]["status"], "exists")
