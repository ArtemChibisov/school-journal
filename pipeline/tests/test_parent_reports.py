from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from google_integration.drive import MIME_PDF
from google_integration.fakes import FakeDriveClient
from jobs.models import JobRun
from notifications.models import NotificationEvent
from pipeline.models import ParentContact
from pipeline.parent_reports import (
    JOB_TYPE_CHECK,
    JOB_TYPE_SEND,
    _run_in_thread,
    class_codes_for,
    enqueue,
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

    def test_a_report_named_with_a_double_space_still_finds_the_family(self):
        """`1А Даниил  Надточий.pdf` is the same child as the contact `Даниил Надточий`."""

        self._class("2А", 2)
        self._pdf("2А", "Даниил  Надточий")
        self._contact("2А", "Даниил Надточий", "mum@mail.ru")

        job_run = self._check()
        students = job_run.result_json["classes"][0]["students"]

        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]["emails"], ["mum@mail.ru"])
        self.assertEqual(students[0]["problem"], "")
        self.assertEqual(job_run.result_json["summary"]["students_total"], 1)

        self._send()
        self.assertEqual([m.to[0] for m in mail.outbox], ["mum@mail.ru"])

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

    def test_a_test_address_takes_the_letters_instead_of_the_families(self):
        self._class("2А", 2)
        for student in ("Анна Белова", "Мария Иванова"):
            self._pdf("2А", student)
            self._contact("2А", student, f"{student.split()[0].lower()}@mail.ru")

        self._send(test_address="me@school.ge")

        self.assertEqual([m.to[0] for m in mail.outbox], ["me@school.ge", "me@school.ge"])

    def test_a_test_mailbox_gets_one_letter_per_child_not_one_per_parent(self):
        """Both parents get the same letter; two copies in one mailbox only fill it up."""

        self._class("2А", 2)
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Мария Иванова", "dad@mail.ru", "mum@mail.ru")

        job_run = self._send(test_address="me@school.ge")

        self.assertEqual([m.to[0] for m in mail.outbox], ["me@school.ge"])
        self.assertEqual(job_run.result_json["summary"]["sent"], 1)
        self.assertEqual(job_run.result_json["summary"]["letters"], 1)

    # -- sending to the students picked in the table --------------------------------------

    def test_only_the_picked_students_are_written_to(self):
        self._class("2А", 2)
        for student in ("Анна Белова", "Мария Иванова", "Пётр Быков"):
            self._pdf("2А", student)
            self._contact("2А", student, f"{student.split()[0].lower()}@mail.ru")

        job_run = self._send(students=["2А|Мария Иванова"])

        self.assertEqual([m.to[0] for m in mail.outbox], ["мария@mail.ru"])
        self.assertEqual(
            [s["student"] for s in job_run.result_json["classes"][0]["students"]], ["Мария Иванова"]
        )
        self.assertEqual(job_run.params_json["students"], ["2А|Мария Иванова"])

    def test_picked_students_bring_their_own_classes(self):
        """The class boxes do not narrow a pick: the students named are the whole run."""

        self._class("2А", 2)
        self._class("7А", 7)
        self._pdf("2А", "Мария Иванова")
        self._pdf("7А", "Пётр Быков", root=self.senior_pdf_root)
        self._contact("2А", "Мария Иванова", "junior@mail.ru")
        self._contact("7А", "Пётр Быков", "senior@mail.ru")

        job_run = self._send(students=["2А|Мария Иванова", "7А|Пётр Быков"], class_codes=["2А"])

        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["junior@mail.ru", "senior@mail.ru"])
        self.assertEqual(job_run.params_json["class_codes"], ["2А", "7А"])

    def test_a_picked_family_already_written_to_is_still_skipped_without_resend(self):
        self._class("2А", 2)
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Мария Иванова", "mum@mail.ru")

        self._send()
        mail.outbox.clear()
        self._send(students=["2А|Мария Иванова"])
        self.assertEqual(mail.outbox, [])

        self._send(students=["2А|Мария Иванова"], resend=True)
        self.assertEqual([m.to[0] for m in mail.outbox], ["mum@mail.ru"])

    def test_a_pick_reaches_the_background_run(self):
        self._class("2А", 2)
        self._pdf("2А", "Анна Белова")
        self._pdf("2А", "Мария Иванова")
        self._contact("2А", "Анна Белова", "anna@mail.ru")
        self._contact("2А", "Мария Иванова", "maria@mail.ru")

        with patch("pipeline.parent_reports.threading.Thread") as thread, patch(
            "pipeline.parent_reports._drive", return_value=self.drive
        ), patch("pipeline.parent_reports.close_old_connections"):
            job_run = enqueue(JOB_TYPE_SEND, module=self.module, students=["2А|Анна Белова"])
            _run_in_thread(*thread.call_args.kwargs["args"])

        job_run.refresh_from_db()
        self.assertEqual(job_run.params_json["class_codes"], ["2А"])
        self.assertEqual([m.to[0] for m in mail.outbox], ["anna@mail.ru"])

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


class ParentReportsPageTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
        )
        ClassGroup.objects.create(
            school_year=self.year, class_code="2А", grade_number=2, tutor_name="Тьютор"
        )
        self.user = User.objects.create_user(username="sender", password="p")
        for codename in ("view_jobrun", "run_full_pipeline"):
            self.user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.user)
        self.url = reverse("pipeline:parent_reports")

        self.started: list[tuple[str, dict]] = []
        patcher = patch("pipeline.parent_reports.enqueue", side_effect=self._enqueue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _enqueue(self, job_type, **kwargs):
        self.started.append((job_type, kwargs))
        return JobRun.objects.create(job_type=job_type, status=JobRun.Status.PENDING)

    def _checked_class(self):
        return JobRun.objects.create(
            job_type=JOB_TYPE_CHECK,
            status=JobRun.Status.SUCCESS,
            result_json={
                "summary": {},
                "classes": [
                    {
                        "class_code": "2А",
                        "reason": "",
                        "students": [
                            {"student": "Анна Белова", "file_id": "f1", "file_name": "2А Анна Белова.pdf",
                             "emails": ["anna@mail.ru"], "sent": ["anna@mail.ru"], "problem": ""},
                            {"student": "Мария Иванова", "file_id": "f2", "file_name": "2А Мария Иванова.pdf",
                             "emails": ["maria@mail.ru"], "sent": [], "problem": ""},
                            {"student": "Егор Белышев", "file_id": "f3", "file_name": "2А Егор Белышев.pdf",
                             "emails": [], "sent": [], "problem": "нет адреса родителей"},
                        ],
                    }
                ],
            },
        )

    def test_rows_that_can_be_sent_have_a_tick_box(self):
        job_run = self._checked_class()

        response = self.client.get(self.url, {"run_id": job_run.id})

        self.assertContains(response, 'value="2А|Мария Иванова"')
        self.assertContains(response, 'value="2А|Анна Белова"')
        self.assertNotContains(response, 'value="2А|Егор Белышев"')
        self.assertContains(response, "Отправить отмеченным")

    def test_sending_to_the_ticked_rows_starts_a_send_for_them_only(self):
        response = self.client.post(
            self.url,
            {"module": self.module.pk, "send_selected": "1", "students": ["2А|Мария Иванова"]},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.started), 1)
        job_type, kwargs = self.started[0]
        self.assertEqual(job_type, JOB_TYPE_SEND)
        self.assertEqual(kwargs["students"], ["2А|Мария Иванова"])

    def test_nothing_ticked_sends_nothing(self):
        response = self.client.post(self.url, {"module": self.module.pk, "send_selected": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.started, [])
        self.assertContains(response, "Отметьте в таблице учеников")

    def test_the_class_buttons_ignore_stray_ticks(self):
        """Ticks only count for the «send ticked» button; the class send stays a class send."""

        self.client.post(
            self.url, {"module": self.module.pk, "send": "1", "students": ["2А|Мария Иванова"]}
        )

        self.assertEqual(self.started[0][1]["students"], [])
