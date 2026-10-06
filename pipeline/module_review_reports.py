"""Building the review documents for a whole module in one go.

Thirty-two classes used to mean thirty-two trips through a form. This walks them in one run,
taking each class's folder, module number and dates from the module itself.

Classes are done one after another rather than at once: every class downloads a workbook from
Google and uploads a file per student, and thirty-two of those in parallel is a good way to be
rate-limited by Google and learn nothing about which class failed.

Each class still gets its own job run underneath, so its log, its file list and its errors stay
where they always were. This one records which classes were done, which were skipped and why.

The folders it makes are owned by the Google account the system signs in with, and a folder
nobody shared is a folder nobody can open: the завуч was left clicking «Request access» on the
school's own reports. So the module folder is opened to the address the year settings name,
once per run, and the class folders and the files inherit it.

A repeat run is the normal way to fix a class: a report is replaced by file name, keeping its
id, so the link the завуч already has now opens the corrected version. What a repeat run
cannot do is remove a report it no longer builds - a corrected surname makes a new file and
leaves the old one beside it, both looking equally real. Those are counted and named on the
screen, and removed only if the завуч asked for it.
"""

from __future__ import annotations

import threading

from django.db import close_old_connections
from django.utils import timezone

from jobs.models import JobLog, JobRun
from jobs.services import log_step
from journal_links.models import ClassSheetLink
from school_years.models import SchoolModule
from google_integration.drive import MIME_FOLDER, ensure_shared
from google_integration.errors import GoogleIntegrationError
from google_integration.ids import folder_url
from school_years.report_folders import resolve_report_folder
from school_years.report_targets import docx_target_for_class, module_number_for_reports

from .student_review_reports import run_prepare_student_review_reports_job

JOB_TYPE = "module_review_reports"


def _class_links(class_codes: list[str] | None) -> list[ClassSheetLink]:
    """The classes to build for. An empty choice means all of them."""

    queryset = ClassSheetLink.objects.filter(is_active=True)
    if class_codes:
        queryset = queryset.filter(class_code__in=class_codes)
    return list(queryset.order_by("class_code", "id"))


def _open_module_folder_to_the_school(drive, module: SchoolModule, folder_id: str, *, job_run) -> None:
    """Share the module folder, and do not let a failed share cost anyone their reports.

    Reports that exist but need one share are worth more than no reports at all, so this
    complains in the log and lets the run carry on.
    """

    email = (module.school_year.reports_access_group_email or "").strip()
    if not email:
        log_step(
            job_run=job_run,
            level=JobLog.Level.WARNING,
            message="No address to share the report folder with",
            context={"hint": "Заполните «Доступ к отчётам» в настройках учебного года"},
        )
        return

    try:
        granted = ensure_shared(drive, folder_id, email)
    except GoogleIntegrationError as exc:
        log_step(
            job_run=job_run,
            level=JobLog.Level.WARNING,
            message="Could not share the report folder",
            context={"email": email, "folder_id": folder_id, "reason": exc.technical_message},
        )
        return

    if granted:
        log_step(
            job_run=job_run,
            level=JobLog.Level.INFO,
            message="Report folder shared",
            context={"email": email, "folder_id": folder_id},
        )


def _files_not_from_this_run(drive, folder_id: str, built_names: set[str]) -> list[dict]:
    """What is in the class folder that this run did not put there."""

    return [
        item
        for item in drive.list_children(folder_id)
        if item.get("mimeType") != MIME_FOLDER and item.get("name") not in built_names
    ]


def _leftovers(drive, folder_id: str, built: set[str], *, class_code: str, job_run) -> list[dict]:
    """Name what the class folder holds besides this run's reports, and say so in the log."""

    try:
        found = _files_not_from_this_run(drive, folder_id, built)
    except GoogleIntegrationError as exc:
        log_step(
            job_run=job_run,
            level=JobLog.Level.WARNING,
            message="Could not check the class folder for old reports",
            context={"class_code": class_code, "reason": exc.technical_message},
        )
        return []

    if found:
        log_step(
            job_run=job_run,
            level=JobLog.Level.WARNING,
            message="Files in the class folder were not built by this run",
            context={"class_code": class_code, "files": [i.get("name") for i in found]},
        )
    return found


def _move_to_bin(drive, items: list[dict], *, job_run) -> list[str]:
    """Bin the leftovers, one by one, so one stubborn file does not keep the rest."""

    removed: list[str] = []
    for item in items:
        try:
            drive.trash_file(item["id"])
        except GoogleIntegrationError as exc:
            log_step(
                job_run=job_run,
                level=JobLog.Level.WARNING,
                message="Could not remove a leftover report",
                context={"name": item.get("name"), "reason": exc.technical_message},
            )
            continue
        removed.append(item.get("name", ""))
    return removed


def enqueue_module_review_reports_job(
    *,
    module_id: int,
    class_codes: list[str] | None = None,
    remove_extra_files: bool = False,
    initiated_by=None,
) -> JobRun:
    module = SchoolModule.objects.get(pk=module_id)
    job_run = JobRun.objects.create(
        job_type=JOB_TYPE,
        status=JobRun.Status.PENDING,
        started_at=timezone.now(),
        params_json={
            "module_id": module.pk,
            "module": str(module),
            "class_codes": class_codes or [],
            "remove_extra_files": remove_extra_files,
        },
        initiated_by=initiated_by,
    )
    log_step(
        job_run=job_run,
        level=JobLog.Level.INFO,
        message="Module review reports queued",
        context={"module": str(module), "classes": class_codes or "все классы"},
    )

    thread = threading.Thread(
        target=_run_in_thread,
        args=(str(job_run.id), module.pk, class_codes, remove_extra_files),
        daemon=True,
    )
    thread.start()
    return job_run


def _run_in_thread(
    job_run_id: str,
    module_id: int,
    class_codes: list[str] | None,
    remove_extra_files: bool = False,
) -> None:
    close_old_connections()
    try:
        job_run = JobRun.objects.get(id=job_run_id)
        module = SchoolModule.objects.get(pk=module_id)
        run_module_review_reports_job(
            module=module,
            class_codes=class_codes,
            remove_extra_files=remove_extra_files,
            job_run=job_run,
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


def run_module_review_reports_job(
    *,
    module: SchoolModule,
    class_codes: list[str] | None = None,
    remove_extra_files: bool = False,
    job_run: JobRun | None = None,
    initiated_by=None,
    generator=None,
    drive=None,
) -> JobRun:
    if job_run is None:
        job_run = JobRun.objects.create(
            job_type=JOB_TYPE,
            status=JobRun.Status.RUNNING,
            started_at=timezone.now(),
            params_json={
                "module_id": module.pk,
                "module": str(module),
                "class_codes": class_codes or [],
                "remove_extra_files": remove_extra_files,
            },
            initiated_by=initiated_by,
        )
    else:
        job_run.status = JobRun.Status.RUNNING
        job_run.started_at = job_run.started_at or timezone.now()
        job_run.save(update_fields=["status", "started_at"])

    if drive is None:
        from google_integration.status import build_clients

        drive, _ = build_clients()

    links = _class_links(class_codes)
    shared_folders: set[str] = set()
    rows: list[dict] = []
    docx_created = 0
    uploaded_success = 0
    extra_found = 0
    extra_removed = 0
    classes_done = 0
    classes_failed = 0
    classes_skipped = 0

    for link in links:
        target = docx_target_for_class(module, link.class_code)
        if not target.is_ready:
            classes_skipped += 1
            rows.append(
                {"class_code": link.class_code, "status": "skipped", "reason": target.missing}
            )
            log_step(
                job_run=job_run,
                level=JobLog.Level.WARNING,
                message="Class skipped for review reports",
                context={"class_code": link.class_code, "reason": target.missing},
            )
            continue

        # The configured folder holds a folder per module, and that one a folder per class.
        # Both are made here if they do not exist yet.
        try:
            folders = resolve_report_folder(
                drive,
                root_folder_url=target.docx_folder_url,
                module=module,
                class_code=link.class_code,
            )
        except GoogleIntegrationError as exc:
            classes_skipped += 1
            rows.append(
                {"class_code": link.class_code, "status": "skipped", "reason": exc.user_message}
            )
            log_step(
                job_run=job_run,
                level=JobLog.Level.ERROR,
                message="Could not open the report folder",
                context={"class_code": link.class_code, "reason": exc.technical_message},
            )
            continue

        # Once per module folder, however many classes land in it.
        if folders.module_folder_id not in shared_folders:
            shared_folders.add(folders.module_folder_id)
            _open_module_folder_to_the_school(
                drive, module, folders.module_folder_id, job_run=job_run
            )

        child = run_prepare_student_review_reports_job(
            class_sheet_link_id=link.id,
            drive_folder_url=folder_url(folders.class_folder_id),
            module_number=target.module_number,
            module_dates=target.module_dates,
            school_year=target.school_year,
            initiated_by=initiated_by,
            generator=generator,
        )
        summary = (child.result_json or {}).get("summary", {}) if isinstance(child.result_json, dict) else {}
        created = int(summary.get("docx_created", 0) or 0)
        uploaded = int(summary.get("uploaded_success", 0) or 0)
        docx_created += created
        uploaded_success += uploaded

        if child.status == JobRun.Status.SUCCESS:
            classes_done += 1
        else:
            classes_failed += 1

        row = {
            "class_code": link.class_code,
            "status": child.status,
            "docx_created": created,
            "uploaded_success": uploaded,
            "job_run_id": str(child.id),
        }

        # Only worth asking when this run actually built something: after a failure every
        # file in the folder looks like a leftover, and none of them is.
        built = {
            entry.get("name")
            for entry in ((child.result_json or {}).get("files") or [])
            if entry.get("name")
        }
        if built:
            leftovers = _leftovers(
                drive,
                folders.class_folder_id,
                built,
                class_code=link.class_code,
                job_run=job_run,
            )
            if leftovers:
                extra_found += len(leftovers)
                row["extra_files"] = [item.get("name", "") for item in leftovers]
                if remove_extra_files:
                    removed = _move_to_bin(drive, leftovers, job_run=job_run)
                    extra_removed += len(removed)
                    row["extra_removed"] = removed

        rows.append(row)

    job_run.result_json = {
        "summary": {
            "classes_total": len(links),
            "classes_done": classes_done,
            "classes_failed": classes_failed,
            "classes_skipped": classes_skipped,
            "docx_created": docx_created,
            "uploaded_success": uploaded_success,
            "extra_found": extra_found,
            "extra_removed": extra_removed,
        },
        "module": str(module),
        "module_number": module_number_for_reports(module),
        "classes": rows,
    }

    if not links:
        job_run.status = JobRun.Status.FAILED
    elif classes_failed or classes_skipped:
        job_run.status = JobRun.Status.PARTIAL
    else:
        job_run.status = JobRun.Status.SUCCESS
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["result_json", "status", "finished_at"])

    log_step(
        job_run=job_run,
        level=JobLog.Level.INFO,
        message="Module review reports finished",
        context=job_run.result_json["summary"],
    )
    return job_run
