"""Storing the subject-teachers workbook and running it as a job.

The parser says what the table contains; this decides what to do about it. Two rules shape it,
both learned from the roster import:

A class whose rows have errors is left exactly as it was. Half-importing a class is worse than
not importing it: generation would run on a class that looks complete and is not.

A class that disappears from the table is deactivated, never deleted. Last year's assignments
still explain last year's journals, and a row that vanished by accident can come back.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from google_integration.errors import GoogleIntegrationError
from google_integration.ids import extract_spreadsheet_id
from google_integration.joblog import log_google_error, log_google_step
from jobs.models import JobRun

from ..models import ClassGroup, SchoolYear, SubjectTeacher, Teacher
from .subject_teachers import parse_subject_teachers
from .text import comparison_key

JOB_TYPE = "subject_teacher_import"


def _teacher_key(name_ru: str, name_en: str) -> tuple[str, str]:
    return (comparison_key(name_ru), comparison_key(name_en))


def sync_teachers(school_year: SchoolYear, parsed) -> dict:
    """Bring the directory in line with the sheet, matching rows by either spelling."""

    existing = list(Teacher.objects.filter(school_year=school_year))
    by_spelling: dict[str, Teacher] = {}
    for teacher in existing:
        for spelling in (teacher.name_ru, teacher.name_en):
            if spelling:
                by_spelling[comparison_key(spelling)] = teacher

    summary = {"created": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
    seen: set[int] = set()
    resolved: dict[str, Teacher] = {}

    for row in parsed.teachers:
        match = None
        for spelling in row.spellings():
            match = by_spelling.get(comparison_key(spelling))
            if match is not None:
                break

        if match is None:
            match = Teacher(school_year=school_year)
            summary["created"] += 1
            changed = True
        else:
            changed = (
                match.name_ru != row.name_ru
                or match.name_en != row.name_en
                or match.notes != row.notes
                or match.is_active != row.is_active
            )
            summary["updated" if changed else "unchanged"] += 1

        if changed:
            match.name_ru = row.name_ru
            match.name_en = row.name_en
            match.notes = row.notes
            match.is_active = row.is_active
            match.save()

        seen.add(match.pk)
        for spelling in row.spellings():
            resolved[comparison_key(spelling)] = match

    gone = [teacher for teacher in existing if teacher.pk not in seen and teacher.is_active]
    for teacher in gone:
        teacher.is_active = False
        teacher.save()
    summary["deactivated"] = len(gone)

    return {"summary": summary, "by_spelling": resolved}


def sync_assignments(school_year: SchoolYear, parsed, by_spelling: dict[str, Teacher]) -> dict:
    """Replace the assignments of every class the table describes without error."""

    classes = {
        comparison_key(group.class_code): group
        for group in ClassGroup.objects.filter(school_year=school_year)
    }

    summary = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "deactivated": 0,
        "classes_imported": 0,
        "classes_skipped": 0,
        "classes_unknown": [],
    }

    by_class: dict[str, list] = {}
    for item in parsed.assignments:
        by_class.setdefault(comparison_key(item.class_code), []).append(item)

    for key, rows in sorted(by_class.items()):
        group = classes.get(key)
        if group is None:
            summary["classes_unknown"].append(rows[0].class_code)
            continue

        # The parser marks a problem with the class code it belongs to, so a class with a
        # critical issue is one we must not touch.
        if parsed.issues.has_critical_for_class(rows[0].class_code):
            summary["classes_skipped"] += 1
            continue

        existing = {
            (comparison_key(row.subject_name), comparison_key(row.group_name), row.teacher_id): row
            for row in SubjectTeacher.objects.filter(class_group=group)
        }
        kept: set[tuple] = set()

        for item in rows:
            teacher = by_spelling.get(comparison_key(item.teacher_name))
            if teacher is None:
                continue

            identity = (
                comparison_key(item.subject_name),
                comparison_key(item.group_name),
                teacher.pk,
            )
            kept.add(identity)
            current = existing.get(identity)

            if current is None:
                SubjectTeacher.objects.create(
                    school_year=school_year,
                    class_group=group,
                    teacher=teacher,
                    subject_name=item.subject_name,
                    group_name=item.group_name,
                    teacher_role=item.teacher_role,
                    source_sheet_name=item.source_sheet_name,
                    is_active=True,
                )
                summary["created"] += 1
                continue

            changed = (
                current.subject_name != item.subject_name
                or current.group_name != item.group_name
                or current.teacher_role != item.teacher_role
                or current.source_sheet_name != item.source_sheet_name
                or not current.is_active
            )
            if changed:
                current.subject_name = item.subject_name
                current.group_name = item.group_name
                current.teacher_role = item.teacher_role
                current.source_sheet_name = item.source_sheet_name
                current.is_active = True
                current.save()
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1

        for identity, row in existing.items():
            if identity not in kept and row.is_active:
                row.is_active = False
                row.save()
                summary["deactivated"] += 1

        summary["classes_imported"] += 1

    return summary


def import_subject_teachers(
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

    url = (school_year.subject_teacher_sheet_url or "").strip()
    if not url:
        return _fail(
            job_run,
            "Ссылка на таблицу предметов и преподавателей не указана в настройках года.",
        )

    try:
        spreadsheet_id = extract_spreadsheet_id(url)
        log_google_step(job_run, "Subject-teachers import started", context={"spreadsheet_id": spreadsheet_id})
        parsed = parse_subject_teachers(sheets, spreadsheet_id)
    except GoogleIntegrationError as exc:
        log_google_error(job_run, exc, message="Subject-teachers import failed")
        return _fail(job_run, exc.user_message)

    result = parsed.as_dict()
    result["dry_run"] = dry_run

    if not parsed.is_usable:
        result["sync"] = {}
        job_run.result_json = result
        job_run.status = JobRun.Status.FAILED
        job_run.finished_at = timezone.now()
        job_run.save()
        return job_run

    if dry_run:
        result["sync"] = {}
        job_run.result_json = result
        job_run.status = JobRun.Status.SUCCESS
        job_run.finished_at = timezone.now()
        job_run.save()
        return job_run

    with transaction.atomic():
        teachers = sync_teachers(school_year, parsed)
        assignments = sync_assignments(school_year, parsed, teachers["by_spelling"])

    result["sync"] = {"teachers": teachers["summary"], "assignments": assignments}

    critical = parsed.issues.counts().get("critical", 0)
    job_run.result_json = result
    job_run.status = JobRun.Status.PARTIAL if critical else JobRun.Status.SUCCESS
    job_run.finished_at = timezone.now()
    job_run.save()

    log_google_step(
        job_run,
        "Subject-teachers import finished",
        context={"teachers": teachers["summary"], "assignments": assignments},
    )
    return job_run


def _fail(job_run: JobRun, user_message: str) -> JobRun:
    job_run.result_json = {"error": {"user_message": user_message}}
    job_run.status = JobRun.Status.FAILED
    job_run.finished_at = timezone.now()
    job_run.save()
    return job_run
