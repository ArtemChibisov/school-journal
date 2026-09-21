"""Finding journals that already exist in Drive and attaching them to classes.

The first module is usually created by hand: the завуч copies the template, renames the copies
and puts them in the module folder. Regenerating those files would destroy work that exists
nowhere else, so instead the system scans the folder and remembers which file belongs to which
class.

This is read-only towards Drive. The only thing written is a `ClassRegister` row per match.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from google_integration.drive import MIME_SPREADSHEET
from google_integration.errors import GoogleIntegrationError
from google_integration.ids import extract_folder_id
from google_integration.joblog import log_google_error, log_google_step
from jobs.models import JobRun
from journal_links.models import ClassSheetLink

from ..models import ClassGroup, ClassRegister
from .class_code_match import STATUS_AMBIGUOUS, STATUS_MATCHED, match_class_code

JOB_TYPE = "class_register_scan"

BANDS = (
    (ClassGroup.GradeBand.PRIMARY_0_3, "register_folder_0_3_url", "0-3"),
    (ClassGroup.GradeBand.MIDDLE_HIGH_4_11, "register_folder_4_11_url", "4-11"),
)


def _band_classes(module, band: str) -> list[ClassGroup]:
    return list(
        ClassGroup.objects.filter(
            school_year=module.school_year,
            grade_band=band,
            is_active=True,
        ).order_by("grade_number", "class_code")
    )


def scan_module_folders(module, *, drive, job_run: JobRun | None = None) -> dict:
    """Match every spreadsheet in the module folders to a class of that band."""

    matched: dict[int, dict] = {}
    unmatched_files: list[dict] = []
    band_reports: list[dict] = []

    for band, field, label in BANDS:
        classes = _band_classes(module, band)
        url = (getattr(module, field, "") or "").strip()

        if not url:
            band_reports.append(
                {
                    "band": label,
                    "status": "not_configured",
                    "classes_total": len(classes),
                    "files_total": 0,
                    "message": f"Для параллелей {label} не указана папка модуля.",
                }
            )
            continue

        try:
            files = drive.list_children(extract_folder_id(url), mime_type=MIME_SPREADSHEET)
        except GoogleIntegrationError as exc:
            log_google_error(job_run, exc, message=f"Не удалось прочитать папку {label}")
            band_reports.append(
                {
                    "band": label,
                    "status": "failed",
                    "classes_total": len(classes),
                    "files_total": 0,
                    "message": exc.user_message,
                }
            )
            continue

        codes = [group.class_code for group in classes]
        by_code = {group.class_code: group for group in classes}

        for item in sorted(files, key=lambda file: file["name"]):
            result = match_class_code(item["name"], codes)

            if not result.is_matched:
                reason = (
                    f"название подходит сразу нескольким классам: {', '.join(result.candidates)}"
                    if result.status == STATUS_AMBIGUOUS
                    else "не удалось определить класс по названию"
                )
                unmatched_files.append(
                    {"band": label, "name": item["name"], "url": item.get("webViewLink", ""), "reason": reason}
                )
                continue

            group = by_code[result.class_code]
            if group.pk in matched:
                unmatched_files.append(
                    {
                        "band": label,
                        "name": item["name"],
                        "url": item.get("webViewLink", ""),
                        "reason": f"для класса {group.class_code} уже найден другой файл",
                    }
                )
                continue

            matched[group.pk] = {
                "class_group": group,
                "file_id": item["id"],
                "file_name": item["name"],
                "url": item.get("webViewLink", ""),
            }

        band_reports.append(
            {
                "band": label,
                "status": "ok",
                "classes_total": len(classes),
                "files_total": len(files),
                "message": "",
            }
        )

    all_classes = _band_classes(module, ClassGroup.GradeBand.PRIMARY_0_3) + _band_classes(
        module, ClassGroup.GradeBand.MIDDLE_HIGH_4_11
    )
    missing = [group for group in all_classes if group.pk not in matched]

    return {
        "module_id": module.pk,
        "module": str(module),
        "checked_at": timezone.now().isoformat(),
        "classes_total": len(all_classes),
        "matched_total": len(matched),
        "missing_total": len(missing),
        "unmatched_files_total": len(unmatched_files),
        "bands": band_reports,
        "matched": matched,
        "missing": missing,
        "unmatched_files": unmatched_files,
    }


def _sync_legacy_link(class_code: str, url: str) -> None:
    """Point the legacy `ClassSheetLink` at the same journal.

    Everything that already works reads journals through this model: the descriptor, criteria
    and grades check, the AI criteria review, the criteria table, DOCX generation. Writing the
    link here turns all of it on for the real journals instead of rebuilding it on top of
    `ClassRegister`. A bridge, not a permanent home - see docs/execution_plan.md.
    """

    ClassSheetLink.objects.update_or_create(
        class_code=class_code,
        subject_name="",
        defaults={"google_sheet_url": url, "is_active": True},
    )


@transaction.atomic
def attach_registers(module, scan: dict) -> dict:
    """Write a `ClassRegister` for every matched file, and bridge it to the legacy model."""

    created = updated = unchanged = 0

    for entry in scan["matched"].values():
        _sync_legacy_link(entry["class_group"].class_code, entry["url"])

        register, was_created = ClassRegister.objects.get_or_create(
            school_year=module.school_year,
            module=module,
            class_group=entry["class_group"],
            defaults={
                "google_sheet_url": entry["url"],
                "google_file_id": entry["file_id"],
                "status": ClassRegister.Status.CREATED,
            },
        )
        if was_created:
            created += 1
            continue

        changed = (
            register.google_file_id != entry["file_id"]
            or register.google_sheet_url != entry["url"]
            or register.status != ClassRegister.Status.CREATED
        )
        if not changed:
            unchanged += 1
            continue

        register.google_file_id = entry["file_id"]
        register.google_sheet_url = entry["url"]
        register.status = ClassRegister.Status.CREATED
        register.last_error_user_message = ""
        register.save()
        updated += 1

    return {"created": created, "updated": updated, "unchanged": unchanged}


def _result_json(scan: dict, attached: dict, *, dry_run: bool) -> dict:
    return {
        "module_id": scan["module_id"],
        "module": scan["module"],
        "checked_at": scan["checked_at"],
        "dry_run": dry_run,
        "classes_total": scan["classes_total"],
        "matched_total": scan["matched_total"],
        "missing_total": scan["missing_total"],
        "unmatched_files_total": scan["unmatched_files_total"],
        "bands": scan["bands"],
        "matched": [
            {
                "class_code": entry["class_group"].class_code,
                "file_name": entry["file_name"],
                "url": entry["url"],
            }
            for entry in scan["matched"].values()
        ],
        "missing": [
            {"class_code": group.class_code, "grade_number": group.grade_number}
            for group in scan["missing"]
        ],
        "unmatched_files": scan["unmatched_files"],
        "attached": attached,
    }


def _status(scan: dict) -> str:
    if scan["matched_total"] == 0:
        return JobRun.Status.FAILED
    if scan["missing_total"] or scan["unmatched_files_total"]:
        return JobRun.Status.PARTIAL
    return JobRun.Status.SUCCESS


def run_register_scan(module, *, drive, initiated_by=None, dry_run: bool = False) -> JobRun:
    """Scan the module folders and attach what was found, as a `JobRun`."""

    job_run = JobRun.objects.create(
        job_type=JOB_TYPE,
        status=JobRun.Status.RUNNING,
        started_at=timezone.now(),
        params_json={"module_id": module.pk, "dry_run": dry_run, "trigger": "manual"},
        initiated_by=initiated_by,
    )

    log_google_step(job_run, "Register scan started", context={"module": str(module), "dry_run": dry_run})

    try:
        scan = scan_module_folders(module, drive=drive, job_run=job_run)
    except GoogleIntegrationError as exc:
        log_google_error(job_run, exc, message="Register scan aborted")
        job_run.status = JobRun.Status.FAILED
        job_run.finished_at = timezone.now()
        job_run.result_json = {"error": exc.as_dict()}
        job_run.save(update_fields=["status", "finished_at", "result_json"])
        return job_run

    attached = {"created": 0, "updated": 0, "unchanged": 0}
    if not dry_run and scan["matched_total"]:
        attached = attach_registers(module, scan)

    job_run.status = _status(scan)
    job_run.finished_at = timezone.now()
    job_run.result_json = _result_json(scan, attached, dry_run=dry_run)
    job_run.save(update_fields=["status", "finished_at", "result_json"])

    log_google_step(
        job_run,
        "Register scan finished",
        context={
            "matched": scan["matched_total"],
            "missing": scan["missing_total"],
            "unmatched_files": scan["unmatched_files_total"],
            **attached,
        },
    )
    return job_run
