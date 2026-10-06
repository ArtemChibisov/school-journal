"""Applying the parsed roster to the domain models.

DEV-0401. Reading is in `roster.py`; this module decides what the database should look like
afterwards and records the run as a `JobRun`.

Deactivation policy, which is the only genuinely tricky part: a class that the sheet marks
inactive, or that has disappeared from the sheet, is deactivated. A class that is still in the
sheet but failed validation is left exactly as it was - a typo in one cell must not silently
remove a class that already has generated files behind it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from django.db import transaction
from django.utils import timezone

from google_integration.errors import GoogleIntegrationError
from google_integration.joblog import log_google_error, log_google_step
from jobs.models import JobRun

from ..models import ClassGroup
from .roster import RosterParseResult, read_roster_for_school_year
from ..models import Student
from .text import comparison_key

JOB_TYPE = "class_roster_import"


@dataclass
class SyncSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    skipped_with_errors: int = 0
    students_created: int = 0
    students_updated: int = 0
    students_deactivated: int = 0


def _existing_by_key(school_year) -> dict[str, ClassGroup]:
    return {
        comparison_key(group.class_code): group
        for group in ClassGroup.objects.filter(school_year=school_year)
    }


@transaction.atomic
def sync_class_groups(school_year, result: RosterParseResult) -> SyncSummary:
    """Bring `ClassGroup` in line with the parsed roster."""

    summary = SyncSummary()
    existing = _existing_by_key(school_year)
    imported_keys: set[str] = set()

    for item in result.classes:
        key = comparison_key(item.class_code)
        imported_keys.add(key)
        grade_band = ClassGroup.grade_band_for_number(item.grade_number)
        group = existing.get(key)

        if group is None:
            ClassGroup.objects.create(
                school_year=school_year,
                class_code=item.class_code,
                grade_number=item.grade_number,
                grade_band=grade_band,
                tutor_name=item.tutor_name,
                is_active=True,
            )
            summary.created += 1
            continue

        changed = (
            group.class_code != item.class_code
            or group.grade_number != item.grade_number
            or group.grade_band != grade_band
            or group.tutor_name != item.tutor_name
            or not group.is_active
        )
        if not changed:
            summary.unchanged += 1
            continue

        group.class_code = item.class_code
        group.grade_number = item.grade_number
        # Set explicitly: the model only derives the band when it is empty, and refuses a band
        # that contradicts the grade, which is exactly what a 3 -> 4 move would produce.
        group.grade_band = grade_band
        group.tutor_name = item.tutor_name
        group.is_active = True
        group.save()
        summary.updated += 1

    for key, group in existing.items():
        if key in imported_keys or not group.is_active:
            continue
        gone_from_sheet = key not in result.seen_class_keys
        marked_inactive = key in result.inactive_class_keys
        if not (gone_from_sheet or marked_inactive):
            # Still in the sheet, just not importable this time. Leave it alone.
            summary.skipped_with_errors += 1
            continue

        group.is_active = False
        group.save()
        summary.deactivated += 1

    _sync_students(school_year, result, summary)
    return summary


def _sync_students(school_year, result: RosterParseResult, summary: SyncSummary) -> None:
    """Сохранить учеников класса.

    Лист `students` читался и проверялся с самого начала, но никуда не записывался — система
    знала, что учеников 367, и не знала ни одного по имени. Создавать журнал класса было не
    из чего.

    Трогаются только классы, пришедшие из таблицы без ошибок: класс с ошибкой остаётся таким,
    каким был, вместе со своими учениками — половина класса хуже, чем вчерашний список.
    """

    groups = {
        comparison_key(group.class_code): group
        for group in ClassGroup.objects.filter(school_year=school_year)
    }

    for item in result.classes:
        group = groups.get(comparison_key(item.class_code))
        if group is None:
            continue

        existing = {
            comparison_key(student.student_name): student
            for student in Student.objects.filter(class_group=group)
        }
        seen: set[str] = set()

        for parsed in item.students:
            key = comparison_key(parsed.student_name)
            seen.add(key)
            student = existing.get(key)
            if student is None:
                Student.objects.create(
                    school_year=school_year,
                    class_group=group,
                    student_name=parsed.student_name,
                    student_order=parsed.student_order,
                    notes=parsed.notes,
                    is_active=True,
                )
                summary.students_created += 1
                continue

            changed = (
                student.student_name != parsed.student_name
                or student.student_order != parsed.student_order
                or student.notes != parsed.notes
                or not student.is_active
            )
            if not changed:
                continue
            student.student_name = parsed.student_name
            student.student_order = parsed.student_order
            student.notes = parsed.notes
            student.is_active = True
            student.save()
            summary.students_updated += 1

        # Ушедшие отключаются, а не удаляются: отчёт за прошлый модуль всё ещё о них.
        for key, student in existing.items():
            if key in seen or not student.is_active:
                continue
            student.is_active = False
            student.save(update_fields=["is_active", "updated_at"])
            summary.students_deactivated += 1


def _job_status(result: RosterParseResult, summary: SyncSummary) -> str:
    if not result.is_usable:
        return JobRun.Status.FAILED
    if result.classes_valid == 0:
        return JobRun.Status.FAILED
    if result.issues.critical():
        return JobRun.Status.PARTIAL
    return JobRun.Status.SUCCESS


def import_class_roster(school_year, *, sheets, initiated_by=None, dry_run: bool = False) -> JobRun:
    """Read the roster and apply it, recording the run for the administrator.

    `dry_run` parses and reports without touching the database, which is what the preflight
    screen needs before the завуч commits to anything.
    """

    job_run = JobRun.objects.create(
        job_type=JOB_TYPE,
        status=JobRun.Status.RUNNING,
        started_at=timezone.now(),
        params_json={
            "school_year_id": school_year.pk,
            "dry_run": dry_run,
            "trigger": "manual",
        },
        initiated_by=initiated_by,
    )

    log_google_step(
        job_run,
        "Class roster import started",
        context={"school_year": str(school_year), "dry_run": dry_run},
    )

    try:
        result = read_roster_for_school_year(school_year, sheets=sheets)
    except GoogleIntegrationError as exc:
        log_google_error(job_run, exc, message="Class roster import aborted")
        job_run.status = JobRun.Status.FAILED
        job_run.finished_at = timezone.now()
        job_run.result_json = {"error": exc.as_dict()}
        job_run.save(update_fields=["status", "finished_at", "result_json"])
        return job_run

    summary = SyncSummary()
    if not dry_run and result.classes:
        summary = sync_class_groups(school_year, result)

    job_run.status = _job_status(result, summary)
    job_run.finished_at = timezone.now()
    job_run.result_json = {**result.as_dict(), "sync": asdict(summary), "dry_run": dry_run}
    job_run.save(update_fields=["status", "finished_at", "result_json"])

    log_google_step(
        job_run,
        "Class roster import finished",
        context={
            "classes_valid": result.classes_valid,
            "critical_issues": len(result.issues.critical()),
            **asdict(summary),
        },
    )
    return job_run
