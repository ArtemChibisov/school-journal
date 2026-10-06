"""Saving a module's DOCX reports as PDF, in the folders the school already keeps.

The reports are already on Drive. Nothing is rebuilt and nothing is downloaded: each DOCX in

    DOCX 0-3 / 1 модуль/module / 4А

is saved as a PDF in

    PDF 0-3 / 1 модуль/module / 4А

which is the same tree, one root over. The module folder and the class folder are made if they
are missing, and the module folder is opened to the school exactly as the DOCX one is -
otherwise the PDF a parent is meant to receive is a file nobody but the server can open.

This is deliberately a second step rather than part of building the reports. The завуч reads
the DOCX first, sends a teacher back to the журнал if something is wrong, and only then makes
the copies that go out. Running it again after a correction replaces the PDF in place.
"""

from __future__ import annotations

import threading

from django.db import close_old_connections
from django.utils import timezone

from google_integration.drive import MIME_DOCX
from google_integration.errors import GoogleIntegrationError
from google_integration.ids import folder_url
from google_integration.pdf import docx_to_pdf
from jobs.models import JobLog, JobRun
from jobs.services import log_step
from school_years.models import SchoolModule
from school_years.report_folders import find_report_folder, resolve_report_folder
from school_years.report_targets import docx_target_for_class

from .module_review_reports import _class_links, _open_module_folder_to_the_school

JOB_TYPE = "module_pdf_reports"


def enqueue_module_pdf_reports_job(
    *, module_id: int, class_codes: list[str] | None = None, initiated_by=None
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
        },
        initiated_by=initiated_by,
    )
    log_step(
        job_run=job_run,
        level=JobLog.Level.INFO,
        message="Module PDF reports queued",
        context={"module": str(module), "classes": class_codes or "все классы"},
    )
    threading.Thread(
        target=_run_in_thread, args=(str(job_run.id), module.pk, class_codes), daemon=True
    ).start()
    return job_run


def _run_in_thread(job_run_id: str, module_id: int, class_codes: list[str] | None) -> None:
    close_old_connections()
    try:
        job_run = JobRun.objects.get(id=job_run_id)
        module = SchoolModule.objects.get(pk=module_id)
        run_module_pdf_reports_job(module=module, class_codes=class_codes, job_run=job_run)
    except Exception as exc:  # noqa: BLE001
        try:
            job_run = JobRun.objects.get(id=job_run_id)
            job_run.status = JobRun.Status.FAILED
            job_run.finished_at = timezone.now()
            job_run.result_json = {
                "error": {"type": exc.__class__.__name__, "message": str(exc)[:500]}
            }
            job_run.save(update_fields=["status", "finished_at", "result_json"])
        except JobRun.DoesNotExist:
            pass
    finally:
        close_old_connections()


def _reports_in(drive, folder_id: str) -> list[dict]:
    """The DOCX in a class folder, by name, so two runs read them in the same order."""

    return sorted(
        (
            item
            for item in drive.list_children(folder_id, mime_type=MIME_DOCX)
            if item["name"].lower().endswith(".docx")
        ),
        key=lambda item: item["name"],
    )


def run_module_pdf_reports_job(
    *,
    module: SchoolModule,
    class_codes: list[str] | None = None,
    job_run: JobRun | None = None,
    initiated_by=None,
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
    pdf_created = 0
    classes_done = 0
    classes_failed = 0
    classes_skipped = 0

    for link in links:
        target = docx_target_for_class(module, link.class_code)
        blocked = target.missing or target.pdf_missing
        if blocked:
            classes_skipped += 1
            rows.append({"class_code": link.class_code, "status": "skipped", "reason": blocked})
            log_step(
                job_run=job_run,
                level=JobLog.Level.WARNING,
                message="Class skipped for PDF",
                context={"class_code": link.class_code, "reason": blocked},
            )
            continue

        # Look before building anything. Nothing is created until there is a report to
        # convert, so pressing this button before the reports exist costs nothing and leaves
        # no empty folders behind in either root.
        try:
            source = find_report_folder(
                drive,
                root_folder_url=target.docx_folder_url,
                module=module,
                class_code=link.class_code,
            )
            reports = _reports_in(drive, source.class_folder_id) if source else []
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

        if not reports:
            classes_skipped += 1
            rows.append(
                {
                    "class_code": link.class_code,
                    "status": "skipped",
                    "reason": "В папке класса нет DOCX-отчётов — сначала соберите их",
                }
            )
            continue

        try:
            destination = resolve_report_folder(
                drive,
                root_folder_url=target.pdf_folder_url,
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
                message="Could not open the PDF folder",
                context={"class_code": link.class_code, "reason": exc.technical_message},
            )
            continue

        if destination.module_folder_id not in shared_folders:
            shared_folders.add(destination.module_folder_id)
            _open_module_folder_to_the_school(
                drive, module, destination.module_folder_id, job_run=job_run
            )

        created = 0
        failures: list[str] = []
        for docx in reports:
            try:
                docx_to_pdf(drive, docx=docx, pdf_folder_id=destination.class_folder_id)
            except GoogleIntegrationError as exc:
                # One report Google refuses to render should not cost the class the rest.
                failures.append(docx["name"])
                log_step(
                    job_run=job_run,
                    level=JobLog.Level.ERROR,
                    message="Could not save a report as PDF",
                    context={
                        "class_code": link.class_code,
                        "file": docx["name"],
                        "reason": exc.technical_message,
                    },
                )
                continue
            created += 1

        pdf_created += created
        if failures:
            classes_failed += 1
        else:
            classes_done += 1

        row = {
            "class_code": link.class_code,
            "status": "success" if not failures else JobRun.Status.PARTIAL,
            "docx_created": created,
            "drive_folder_url": folder_url(destination.class_folder_id),
        }
        if failures:
            row["reason"] = "Не удалось сохранить: " + ", ".join(failures)
        rows.append(row)

    job_run.result_json = {
        "summary": {
            "classes_total": len(links),
            "classes_done": classes_done,
            "classes_failed": classes_failed,
            "classes_skipped": classes_skipped,
            "docx_created": pdf_created,
            "uploaded_success": pdf_created,
        },
        "module": str(module),
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
        message="Module PDF reports finished",
        context=job_run.result_json["summary"],
    )
    return job_run
