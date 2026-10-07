"""Sending each child's PDF to their parents.

This is the last step and the one that reaches outside the school, so it is built to be looked
at before it runs. The check walks exactly what the send would walk and reports, per student,
whether there is a report, whether there is an address, and whether it has gone out already.
Nothing is sent by the check and nothing is created by it.

Where things come from:

- the PDF, from `PDF 0-3 / 1 модуль/module / 4А` on Drive, by file name;
- the addresses, from the contacts table, matched by class and name;
- the wording, from the letter the school edits on the mail screen.

Selection is by parallel or by class. A parallel is the natural unit here - «четвёртые»
means 4А, 4К1 and 4К2 - and is how the school talks about sending.

Two things keep a repeat run from flooding anyone. A letter already recorded as sent for this
module, student and address is skipped unless the run explicitly asks to send it again. And a
pause separates letters, because several hundred of them in one burst is the shape of traffic
a provider throttles.
"""

from __future__ import annotations

import hashlib
import threading
import time

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import close_old_connections
from django.utils import timezone

from google_integration.drive import MIME_PDF
from google_integration.errors import GoogleIntegrationError
from jobs.models import JobLog, JobRun
from jobs.services import log_step
from notifications.models import NotificationEvent
from school_years.models import ClassGroup, SchoolModule
from school_years.report_folders import find_report_folder
from school_years.imports.text import comparison_key
from school_years.report_targets import docx_target_for_class

from .models import ParentContact
from .parent_letter import letter_for, stored_template

JOB_TYPE_CHECK = "parent_reports_check"
JOB_TYPE_SEND = "parent_reports_send"


def class_codes_for(
    module: SchoolModule, *, class_codes: list[str] | None, parallels: list[int] | None
) -> list[str]:
    """The classes a run covers. Nothing chosen means all of them."""

    groups = ClassGroup.objects.filter(school_year=module.school_year, is_active=True)
    if parallels:
        groups = groups.filter(grade_number__in=parallels)
    chosen = {code.casefold() for code in (class_codes or [])}
    codes = [group.class_code for group in groups.order_by("grade_number", "class_code")]
    if chosen:
        codes = [code for code in codes if code.casefold() in chosen]
    return codes


def _chosen(students: list[str] | None) -> dict[str, set[str]]:
    """`["4А|Мария Иванова"]` -> `{"4а": {"мария иванова"}}`: the students a run is narrowed to.

    Each entry is one ticked row of the table on the page. Picking students is how the families
    left out of a class run - no address yet, no PDF yet - are written to later without writing
    to the rest of the class again.
    """

    chosen: dict[str, set[str]] = {}
    for item in students or []:
        class_code, _, student = str(item).partition("|")
        if class_code.strip() and student.strip():
            chosen.setdefault(class_code.strip().casefold(), set()).add(comparison_key(student))
    return chosen


def _codes_for_run(
    module: SchoolModule,
    *,
    class_codes: list[str] | None,
    parallels: list[int] | None,
    chosen: dict[str, set[str]],
) -> list[str]:
    # Picked students bring their own classes; the class and parallel boxes do not narrow them.
    if chosen:
        return class_codes_for(module, class_codes=list(chosen), parallels=None)
    return class_codes_for(module, class_codes=class_codes, parallels=parallels)


def _only_chosen(rows: list[dict], chosen: dict[str, set[str]]) -> list[dict]:
    if not chosen:
        return rows
    return [
        {
            **row,
            "students": [
                student
                for student in row["students"]
                if comparison_key(student["student"]) in chosen.get(row["class_code"].casefold(), set())
            ],
        }
        for row in rows
    ]


def _student_of(pdf_name: str, class_code: str) -> str:
    """`4А Мария Иванова.pdf` -> `Мария Иванова`, the way the report was named."""

    stem = pdf_name[: -len(".pdf")] if pdf_name.lower().endswith(".pdf") else pdf_name
    prefix = f"{class_code} "
    return stem[len(prefix):].strip() if stem.startswith(prefix) else stem.strip()


def _recipients(contact: ParentContact | None) -> list[str]:
    if contact is None:
        return []
    return [address for address in (contact.parent_email_1, contact.parent_email_2) if address]


def _letter_key(*, module: SchoolModule, class_code: str, student: str, address: str) -> str:
    payload = f"{module.pk}|{class_code}|{student}|{address}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _already_sent(key: str) -> bool:
    return NotificationEvent.objects.filter(
        payload_hash=key, channel=NotificationEvent.Channel.EMAIL, status=NotificationEvent.Status.SENT
    ).exists()


def scan_class(drive, module: SchoolModule, class_code: str) -> dict:
    """What a send would do for one class, without doing any of it."""

    row: dict = {"class_code": class_code, "students": [], "reason": ""}

    target = docx_target_for_class(module, class_code)
    if target.pdf_missing:
        row["reason"] = target.pdf_missing
        return row

    folder = find_report_folder(
        drive, root_folder_url=target.pdf_folder_url, module=module, class_code=class_code
    )
    if folder is None:
        row["reason"] = "Папки с PDF ещё нет — сохраните отчёты в PDF"
        return row

    files = {
        _student_of(item["name"], class_code): item
        for item in drive.list_children(folder.class_folder_id, mime_type=MIME_PDF)
        if item["name"].lower().endswith(".pdf")
    }
    # Names are compared the way the importers compare them. A report file named
    # `1А Даниил  Надточий.pdf`, with two spaces, is the same child as the contact
    # `Даниил Надточий`; comparing the raw text split them into a report «без адреса»
    # and a family «без PDF», and the family got nothing.
    contacts = {
        comparison_key(contact.student_name): contact
        for contact in ParentContact.objects.filter(class_code__iexact=class_code, is_active=True)
    }

    seen: set[str] = set()
    for student, item in sorted(files.items()):
        contact = contacts.get(comparison_key(student))
        seen.add(comparison_key(student))
        addresses = _recipients(contact)
        row["students"].append(
            {
                "student": student,
                "file_id": item["id"],
                "file_name": item["name"],
                "emails": addresses,
                "sent": [
                    address
                    for address in addresses
                    if _already_sent(
                        _letter_key(
                            module=module, class_code=class_code, student=student, address=address
                        )
                    )
                ],
                "problem": "" if addresses else "нет адреса родителей",
            }
        )

    # A contact with no report is the other half of the picture: the family is waiting and the
    # report was never built.
    for key, contact in sorted(contacts.items()):
        if key in seen:
            continue
        row["students"].append(
            {
                "student": contact.student_name,
                "file_id": "",
                "file_name": "",
                "emails": _recipients(contact),
                "sent": [],
                "problem": "нет PDF-отчёта",
            }
        )

    return row


def _walk(drive, module, codes, *, job_run) -> list[dict]:
    rows = []
    for class_code in codes:
        try:
            rows.append(scan_class(drive, module, class_code))
        except GoogleIntegrationError as exc:
            rows.append({"class_code": class_code, "students": [], "reason": exc.user_message})
            log_step(
                job_run=job_run,
                level=JobLog.Level.ERROR,
                message="Could not read the PDF folder",
                context={"class_code": class_code, "reason": exc.technical_message},
            )
    return rows


def _summary(rows: list[dict]) -> dict:
    students = [student for row in rows for student in row["students"]]
    ready = [s for s in students if s["file_id"] and s["emails"]]
    return {
        "classes_total": len(rows),
        "students_total": len(students),
        "ready": len(ready),
        "letters": sum(len(s["emails"]) for s in ready),
        "no_contact": sum(1 for s in students if s["file_id"] and not s["emails"]),
        "no_pdf": sum(1 for s in students if not s["file_id"]),
        "already_sent": sum(len(s["sent"]) for s in students),
    }


def _start(job_type: str, module, codes, initiated_by, extra: dict | None = None) -> JobRun:
    return JobRun.objects.create(
        job_type=job_type,
        status=JobRun.Status.RUNNING,
        started_at=timezone.now(),
        params_json={
            "module_id": module.pk,
            "module": str(module),
            "class_codes": codes,
            **(extra or {}),
        },
        initiated_by=initiated_by,
    )


def _finish(job_run: JobRun, result: dict, status: str) -> JobRun:
    job_run.result_json = result
    job_run.status = status
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["result_json", "status", "finished_at"])
    return job_run


def _drive():
    from google_integration.status import build_clients

    drive, _ = build_clients()
    return drive


def run_parent_reports_check(
    *,
    module: SchoolModule,
    class_codes: list[str] | None = None,
    parallels: list[int] | None = None,
    job_run: JobRun | None = None,
    initiated_by=None,
    drive=None,
) -> JobRun:
    codes = class_codes_for(module, class_codes=class_codes, parallels=parallels)
    job_run = job_run or _start(JOB_TYPE_CHECK, module, codes, initiated_by)
    drive = drive or _drive()

    rows = _walk(drive, module, codes, job_run=job_run)
    summary = _summary(rows)
    log_step(job_run=job_run, level=JobLog.Level.INFO, message="Parent reports check finished", context=summary)
    return _finish(
        job_run,
        {"summary": summary, "module": str(module), "classes": rows},
        JobRun.Status.SUCCESS if summary["ready"] else JobRun.Status.PARTIAL,
    )


def run_parent_reports_send(
    *,
    module: SchoolModule,
    class_codes: list[str] | None = None,
    parallels: list[int] | None = None,
    resend: bool = False,
    test_address: str = "",
    students: list[str] | None = None,
    job_run: JobRun | None = None,
    initiated_by=None,
    drive=None,
) -> JobRun:
    """Send one letter per address, pausing between them.

    `test_address` redirects the letters to one mailbox. It is how a run is tried on a real
    class with real reports without anything reaching a family. Both parents of a child get the
    same letter, so the test mailbox gets it once per child rather than once per parent.

    `students` narrows the run to the ticked rows of the table, as `class|student` entries.
    """

    chosen = _chosen(students)
    codes = _codes_for_run(module, class_codes=class_codes, parallels=parallels, chosen=chosen)
    job_run = job_run or _start(
        JOB_TYPE_SEND,
        module,
        codes,
        initiated_by,
        {"resend": resend, "test_address": test_address, "students": list(students or [])},
    )
    drive = drive or _drive()
    template = stored_template()
    pause = float(getattr(settings, "EMAIL_SEND_PAUSE_SECONDS", 0) or 0)

    rows = _only_chosen(_walk(drive, module, codes, job_run=job_run), chosen)
    sent = failed = skipped = 0

    for row in rows:
        for student in row["students"]:
            if not student["file_id"] or not student["emails"]:
                continue

            attachment = None
            for address in student["emails"]:
                key = _letter_key(
                    module=module,
                    class_code=row["class_code"],
                    student=student["student"],
                    address=address,
                )
                if not resend and _already_sent(key):
                    skipped += 1
                    continue

                try:
                    if attachment is None:
                        attachment = drive.download_file(student["file_id"])
                    subject, body = letter_for(
                        student_name=student["student"],
                        module=module,
                        class_code=row["class_code"],
                        template=template,
                    )
                    message = EmailMessage(subject=subject, body=body, to=[test_address or address])
                    message.attach(student["file_name"], attachment, MIME_PDF)
                    message.send(fail_silently=False)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    NotificationEvent.objects.create(
                        job_run=job_run,
                        teacher_name=student["student"],
                        channel=NotificationEvent.Channel.EMAIL,
                        status=NotificationEvent.Status.ERROR,
                        payload_hash=key,
                        error_message=str(exc)[:500],
                    )
                    log_step(
                        job_run=job_run,
                        level=JobLog.Level.ERROR,
                        message="Parent letter failed",
                        context={
                            "class_code": row["class_code"],
                            "student": student["student"],
                            "error": str(exc)[:300],
                        },
                    )
                    continue

                sent += 1
                # Recorded against the real address even in a test run, so a test cannot make
                # the system believe a family has been written to.
                NotificationEvent.objects.create(
                    job_run=job_run,
                    teacher_name=student["student"],
                    channel=NotificationEvent.Channel.EMAIL,
                    status=(
                        NotificationEvent.Status.SENT
                        if not test_address
                        else NotificationEvent.Status.SKIPPED
                    ),
                    payload_hash=key,
                )
                if pause:
                    time.sleep(pause)
                if test_address:
                    break

    summary = {**_summary(rows), "sent": sent, "failed": failed, "skipped_already_sent": skipped}
    if test_address:
        summary["letters"] = summary["ready"]
    log_step(job_run=job_run, level=JobLog.Level.INFO, message="Parent reports send finished", context=summary)
    return _finish(
        job_run,
        {
            "summary": summary,
            "module": str(module),
            "classes": rows,
            "test_address": test_address,
        },
        JobRun.Status.SUCCESS if sent and not failed else (JobRun.Status.PARTIAL if sent else JobRun.Status.FAILED),
    )


def enqueue(job_type: str, **kwargs) -> JobRun:
    """Start a run in the background, the way the other long jobs are started."""

    module = kwargs["module"]
    students = list(kwargs.get("students") or [])
    codes = _codes_for_run(
        module,
        class_codes=kwargs.get("class_codes"),
        parallels=kwargs.get("parallels"),
        chosen=_chosen(students),
    )
    job_run = JobRun.objects.create(
        job_type=job_type,
        status=JobRun.Status.PENDING,
        started_at=timezone.now(),
        params_json={
            "module_id": module.pk,
            "module": str(module),
            "class_codes": codes,
            "resend": kwargs.get("resend", False),
            "test_address": kwargs.get("test_address", ""),
            "students": students,
        },
        initiated_by=kwargs.get("initiated_by"),
    )
    threading.Thread(target=_run_in_thread, args=(job_type, str(job_run.id)), daemon=True).start()
    return job_run


def _run_in_thread(job_type: str, job_run_id: str) -> None:
    close_old_connections()
    try:
        job_run = JobRun.objects.get(id=job_run_id)
        params = job_run.params_json or {}
        module = SchoolModule.objects.get(pk=params["module_id"])
        runner = run_parent_reports_check if job_type == JOB_TYPE_CHECK else run_parent_reports_send
        extra = (
            {}
            if job_type == JOB_TYPE_CHECK
            else {
                "resend": bool(params.get("resend")),
                "test_address": str(params.get("test_address") or ""),
                "students": list(params.get("students") or []),
            }
        )
        runner(
            module=module,
            class_codes=params.get("class_codes") or None,
            job_run=job_run,
            initiated_by=job_run.initiated_by,
            **extra,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            job_run = JobRun.objects.get(id=job_run_id)
            job_run.status = JobRun.Status.FAILED
            job_run.finished_at = timezone.now()
            job_run.result_json = {"error": {"type": exc.__class__.__name__, "message": str(exc)[:500]}}
            job_run.save(update_fields=["status", "finished_at", "result_json"])
        except JobRun.DoesNotExist:
            pass
    finally:
        close_old_connections()
