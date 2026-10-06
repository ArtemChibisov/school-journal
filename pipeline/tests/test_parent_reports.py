from datetime import date

from django.core import mail
from django.test import TestCase, override_settings

from google_integration.drive import MIME_PDF
from google_integration.fakes import FakeDriveClient
from jobs.models import JobRun
from notifications.models import NotificationEvent
from pipeline.models import ParentContact
from pipeline.parent_reports import (
    JOB_TYPE_CHECK,
    JOB_TYPE_SEND,
    class_codes_for,
    run_parent_reports_check,
    run_parent_reports_send,
)
from school_years.models import ClassGroup, SchoolModule, SchoolYear


@override_settings(EMAIL_SEND_PAUSE_SECONDS=0)
class ParentReportsTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        self.drive = FakeDriveClient()
        self.pdf_root = self.drive.add_folder("PDF 0-3")
        self.senior_pdf_root = self.drive.add_folder("PDF 4-11")
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
            docx_folder_0_3_url="https://drive.google.com/drive/folders/docxjunior11",
            docx_folder_4_11_url="https://drive.google.com/drive/folders/docxsenior11",
            pdf_folder_0_3_url=f"https://drive.google.com/drive/folders/{self.pdf_root}",
            pdf_folder_4_11_url=f"https://drive.google.com/drive/folders/{self.senior_pdf_root}",
        )

    def _class(self, class_code, grade_number):
        return ClassGroup.objects.create(
            school_year=self.year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name="Тьютор",
        )

    def _pdf(self, class_code, student, root=None):
        root = root or self.pdf_root
        module_folder = self.drive.find_child(root, "1 модуль/module")
        module_id = (
            module_folder["id"]
            if module_folder
            else self.drive.add_folder("1 модуль/module", parent_id=root)
        )
        class_folder = self.drive.find_child(module_id, class_code)
        class_id = (
            class_folder["id"]
            if class_folder
            else self.drive.add_folder(class_code, parent_id=module_id)
        )
        return self.drive.add_file(
            f"{class_code} {student}.pdf", parent_id=class_id, mime_type=MIME_PDF
        )

    def _contact(self, class_code, student, *emails):
        return ParentContact.objects.create(
            parallel=int(class_code[0]),
            class_code=class_code,
            student_name=student,
            parent_email_1=emails[0] if emails else "",
            parent_email_2=emails[1] if len(emails) > 1 else "",
        )

    def _check(self, **kwargs):
        return run_parent_reports_check(module=self.module, drive=self.drive, **kwargs)

    def _send(self, **kwargs):
        return run_parent_reports_send(module=self.module, drive=self.drive, **kwargs)

    # -- choosing who the run covers ------------------------------------------------------

    def test_a_parallel_means_all_of_its_classes(self):
        for code in ("4А", "4К1", "4К2"):
            self._class(code, 4)
        self._class("2А", 2)

        self.assertEqual(class_codes_for(self.module, class_codes=None, parallels=[4]),
                         ["4А", "4К1", "4К2"])

    def test_nothing_chosen_means_every_class(self):
        self._class("2А", 2)
        self._class("4А", 4)

        self.assertEqual(class_codes_for(self.module, class_codes=None, parallels=None),
                         ["2А", "4А"])

    def test_a_class_can_be_picked_on_its_own(self):
        self._class("4А", 4)
        self._class("4К1", 4)

        self.assertEqual(class_codes_for(self.module, class_codes=["4К1"], parallels=None), ["4К1"])

    # -- the check ------------------------------------------------------------------------

    def test_the_check_reports_who_would_receive_and_sends_nothing(self):
        self._class("2А", 2)
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Мария Иванова", "dad@mail.ru", "mum@mail.ru")

        job_run = self._check()

        self.assertEqual(job_run.job_type, JOB_TYPE_CHECK)
        self.assertEqual(job_run.result_json["summary"]["ready"], 1)
        self.assertEqual(job_run.result_json["summary"]["letters"], 2)
        self.assertEqual(mail.outbox, [])

    def test_a_report_with_no_address_is_named(self):
        self._class("2А", 2)
        self._pdf("2А", "Егор Белышев")

        row = self._check().result_json["classes"][0]["students"][0]

        self.assertEqual(row["problem"], "нет адреса родителей")

    def test_a_family_waiting_for_a_report_that_was_never_built(self):
        """The class was built, this one student was not - and the family is waiting."""

        self._class("2А", 2)
        self._pdf("2А", "Анна Белова")
        self._contact("2А", "Анна Белова", "belova@mail.ru")
        self._contact("2А", "Мария Иванова", "mum@mail.ru")

        students = self._check().result_json["classes"][0]["students"]
        waiting = [s for s in students if s["student"] == "Мария Иванова"]

        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]["problem"], "нет PDF-отчёта")

    def test_a_class_without_a_pdf_folder_says_what_to_do(self):
        self._class("2А", 2)

        row = self._check().result_json["classes"][0]

        self.assertIn("сохраните отчёты в pdf", row["reason"].lower())

    # -- sending --------------------------------------------------------------------------

    def test_each_parent_gets_their_own_letter_with_the_report(self):
        self._class("2А", 2)
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Мария Иванова", "dad@mail.ru", "mum@mail.ru")

        job_run = self._send()

        self.assertEqual(job_run.job_type, JOB_TYPE_SEND)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual([m.to[0] for m in mail.outbox], ["dad@mail.ru", "mum@mail.ru"])
        self.assertEqual(mail.outbox[0].attachments[0][0], "2А Мария Иванова.pdf")
        self.assertIn("Мария Иванова", mail.outbox[0].subject)

    def test_a_student_without_an_address_is_simply_not_written_to(self):
        self._class("2А", 2)
        self._pdf("2А", "Егор Белышев")

        self._send()

        self.assertEqual(mail.outbox, [])

    def test_a_second_run_does_not_write_to_the_same_family_again(self):
        self._class("2А", 2)
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Мария Иванова", "mum@mail.ru")

        self._send()
        mail.outbox.clear()
        job_run = self._send()

        self.assertEqual(mail.outbox, [])
        self.assertEqual(job_run.result_json["summary"]["skipped_already_sent"], 1)

    def test_a_repeat_can_be_asked_for(self):
        self._class("2А", 2)
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Мария Иванова", "mum@mail.ru")

        self._send()
        mail.outbox.clear()
        self._send(resend=True)

        self.assertEqual(len(mail.outbox), 1)

    def test_a_test_address_takes_every_letter_instead_of_the_families(self):
        self._class("2А", 2)
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Мария Иванова", "dad@mail.ru", "mum@mail.ru")

        self._send(test_address="me@school.ge")

        self.assertEqual([m.to[0] for m in mail.outbox], ["me@school.ge", "me@school.ge"])

    def test_a_test_run_does_not_count_as_having_written_to_the_family(self):
        """Otherwise the real run would skip everyone it had only pretended to write to."""

        self._class("2А", 2)
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Мария Иванова", "mum@mail.ru")

        self._send(test_address="me@school.ge")
        mail.outbox.clear()
        self._send()

        self.assertEqual([m.to[0] for m in mail.outbox], ["mum@mail.ru"])

    def test_one_failed_letter_does_not_stop_the_rest(self):
        self._class("2А", 2)
        for student in ("Анна Белова", "Мария Иванова"):
            self._pdf("2А", student)
            self._contact("2А", student, f"{student.split()[0].lower()}@mail.ru")

        real_download = self.drive.download_file
        calls: list[str] = []

        def flaky(file_id):
            calls.append(file_id)
            if len(calls) == 1:
                raise RuntimeError("Drive said no")
            return real_download(file_id)

        self.drive.download_file = flaky
        job_run = self._send()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(job_run.result_json["summary"]["failed"], 1)
        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)
        self.assertTrue(
            NotificationEvent.objects.filter(status=NotificationEvent.Status.ERROR).exists()
        )

    def test_the_two_halves_of_the_school_read_their_own_folders(self):
        self._class("2А", 2)
        self._class("7А", 7)
        self._pdf("2А", "Мария Иванова")
        self._pdf("7А", "Пётр Быков", root=self.senior_pdf_root)
        self._contact("2А", "Мария Иванова", "junior@mail.ru")
        self._contact("7А", "Пётр Быков", "senior@mail.ru")

        self._send()

        self.assertEqual(
            sorted(m.to[0] for m in mail.outbox), ["junior@mail.ru", "senior@mail.ru"]
        )
