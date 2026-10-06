"""Storing the parent-contacts workbook and running it as a job.

The parser says what the table contains; this decides what to do about it. The rules are the
ones the other importers settled on:

A class whose rows have errors is left exactly as it was. Half-importing a class is worse than
not importing it: a run would then send reports to a class that looks complete and is not.

A student who disappears from the table is deactivated, never deleted. The address that was
used last module still explains what was sent, and a row that vanished by accident comes back.

Matching is by class and name together. The name alone is not enough: two students can share
one in different classes, and the price of confusing them is a child's report in another
family's inbox.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from google_integration.errors import GoogleIntegrationError
from google_integration.ids import extract_spreadsheet_id
from google_integration.joblog import log_google_error, log_google_step
from jobs.models import JobRun
from pipeline.models import ParentContact

from ..models import ClassGroup, SchoolYear
from .parent_contacts import parse_parent_contacts
from .text import comparison_key

JOB_TYPE = "parent_contact_import"


def _parallel_of(class_code: str) -> int:
    """The grade number a class belongs to, kept because the admin filters still use it."""

    digits = ""
    for char in class_code:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return int(digits) if digits else 0


def _known_classes(school_year: SchoolYear) -> dict[str, str]:
    return {
        comparison_key(group.class_code): group.class_code
        for group in ClassGroup.objects.filter(school_year=school_year, is_active=True)
    }


def sync_contacts(school_year: SchoolYear, parsed) -> dict:
    """Bring the stored contacts in line with the sheet, class by class."""

    known = _known_classes(school_year)
    summary = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "deactivated": 0,
        "classes_imported": 0,
        "classes_skipped": [],
        "classes_unknown": [],
    }

    by_class: dict[str, list] = {}
    for row in parsed.rows:
        by_class.setdefault(row.class_code, []).append(row)

    seen_keys: set[tuple[str, str]] = set()

    for class_code, rows in sorted(by_class.items()):
        if parsed.issues.has_critical_for_class(class_code):
            summary["classes_skipped"].append(class_code)
            continue
        if comparison_key(class_code) not in known:
            # Imported anyway: the roster may simply not be loaded yet, and refusing the
            # contacts would make the завуч choose which table to fix first.
            summary["classes_unknown"].append(class_code)

        summary["classes_imported"] += 1
        for row in rows:
            key = (comparison_key(row.class_code), comparison_key(row.student_name))
            seen_keys.add(key)
            email_1 = row.emails[0] if row.emails else ""
            email_2 = row.emails[1] if len(row.emails) > 1 else ""

            contact = _find(row)
            if contact is None:
                ParentContact.objects.create(
                    parallel=_parallel_of(row.class_code),
                    class_code=row.class_code,
                    student_name=row.student_name,
                    parent_email_1=email_1,
                    parent_email_2=email_2,
                    is_active=row.is_active,
                )
                summary["created"] += 1
                continue

            fields = {
                "parallel": _parallel_of(row.class_code),
                "class_code": row.class_code,
                "parent_email_1": email_1,
                "parent_email_2": email_2,
                "is_active": row.is_active,
            }
            if all(getattr(contact, name) == value for name, value in fields.items()):
                summary["unchanged"] += 1
                continue
            for name, value in fields.items():
                setattr(contact, name, value)
            contact.save(update_fields=[*fields, "updated_at"])
            summary["updated"] += 1

    for contact in ParentContact.objects.filter(is_active=True):
        key = (comparison_key(contact.class_code), comparison_key(contact.student_name))
        if key in seen_keys:
            continue
        if contact.class_code and contact.class_code in summary["classes_skipped"]:
            continue
        contact.is_active = False
        contact.save(update_fields=["is_active", "updated_at"])
        summary["deactivated"] += 1

    return summary


def _find(row) -> ParentContact | None:
    """The stored row for this student, by class and name.

    Rows imported before class codes were stored carry an empty one; those are matched by
    name within the parallel and get their class filled in rather than duplicated.
    """

    for contact in ParentContact.objects.filter(student_name__iexact=row.student_name):
        if comparison_key(contact.class_code) == comparison_key(row.class_code):
            return contact
    for contact in ParentContact.objects.filter(
        student_name__iexact=row.student_name, class_code=""
    ):
        if contact.parallel == _parallel_of(row.class_code):
            return contact
    return None


def _fail(job_run: JobRun, message: str) -> JobRun:
    job_run.status = JobRun.Status.FAILED
    job_run.finished_at = timezone.now()
    job_run.result_json = {"error": message, "summary": {}}
    job_run.save(update_fields=["status", "finished_at", "result_json"])
    return job_run


def import_parent_contacts(
    school_year: SchoolYear,
    *,
    sheets,
    initiated_by=None,
    dry_run: bool = False,
) -> JobRun:
    """Read the workbook and store it, recording the whole attempt as a job run."""

    job_run = JobRun.objects.create(
        job_type=JOB_TYPE,
        status=JobRun.Status.RUNNING,
        started_at=timezone.now(),
        params_json={
            "school_year_id": school_year.pk,
            "school_year": str(school_year),
            "dry_run": dry_run,
        },
        initiated_by=initiated_by,
    )

    url = (school_year.parent_contacts_sheet_url or "").strip()
    if not url:
        return _fail(job_run, "Ссылка на таблицу контактов родителей не указана в настройках года.")

    try:
        spreadsheet_id = extract_spreadsheet_id(url)
        log_google_step(job_run, "Parent-contacts import started", context={"spreadsheet_id": spreadsheet_id})
        parsed = parse_parent_contacts(sheets, spreadsheet_id)
    except GoogleIntegrationError as exc:
        log_google_error(job_run, exc, message="Parent-contacts import failed")
        return _fail(job_run, exc.user_message)

    result = parsed.as_dict()
    result["dry_run"] = dry_run

    if not parsed.is_usable:
        result["sync"] = {}
        job_run.result_json = result
        job_run.status = JobRun.Status.FAILED
        job_run.finished_at = timezone.now()
        job_run.save(update_fields=["result_json", "status", "finished_at"])
        return job_run

    if dry_run:
        result["sync"] = {}
        job_run.result_json = result
        job_run.status = JobRun.Status.SUCCESS
        job_run.finished_at = timezone.now()
        job_run.save(update_fields=["result_json", "status", "finished_at"])
        return job_run

    with transaction.atomic():
        result["sync"] = sync_contacts(school_year, parsed)

    job_run.result_json = result
    job_run.status = (
        JobRun.Status.PARTIAL
        if result["sync"]["classes_skipped"] or result["issue_counts"].get("critical")
        else JobRun.Status.SUCCESS
    )
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["result_json", "status", "finished_at"])
    log_google_step(job_run, "Parent-contacts import finished", context=result["sync"])
    return job_run
