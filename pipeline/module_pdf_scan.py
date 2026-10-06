"""Looking at what is actually in Drive before anyone presses the PDF button.

The PDF step reads folders, not the database: it converts whatever DOCX a class folder holds.
That makes «какие классы вообще готовы?» a question only Drive can answer, and answering it by
pressing the button and reading the skips afterwards is the wrong way round.

So this walks the same folders the PDF run would and reports what it found: how many reports a
class has, how many PDF it already has, and - when the answer is none - which of the several
reasons applies. It creates nothing: a class whose folder does not exist yet is a class with
no reports, not a class that needs a folder.
"""

from __future__ import annotations

import threading

from django.db import close_old_connections
from django.utils import timezone

from google_integration.drive import MIME_DOCX, MIME_PDF
from google_integration.errors import GoogleIntegrationError
from google_integration.ids import folder_url
from jobs.models import JobLog, JobRun
from jobs.services import log_step
from school_years.models import SchoolModule
from school_years.report_folders import find_report_folder
from school_years.report_targets import docx_target_for_class

from .module_review_reports import _class_links

JOB_TYPE = "module_pdf_scan"


def enqueue_module_pdf_scan_job(
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
        message="PDF readiness scan queued",
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
        run_module_pdf_scan_job(module=module, class_codes=class_codes, job_run=job_run)
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


def _count(drive, folder_id: str, mime_type: str, suffix: str) -> int:
    return sum(
        1
        for item in drive.list_children(folder_id, mime_type=mime_type)
        if item["name"].lower().endswith(suffix)
    )


def scan_class(drive, module: SchoolModule, class_code: str) -> dict:
    """What the PDF run would find for one class, without touching anything."""

    row: dict = {"class_code": class_code, "docx": 0, "pdf": 0, "ready": False, "reason": ""}

    target = docx_target_for_class(module, class_code)
    blocked = target.missing or target.pdf_missing
    if blocked:
        row["reason"] = blocked
        return row

    source = find_report_folder(
        drive, root_folder_url=target.docx_folder_url, module=module, class_code=class_code
    )
    if source is None:
        row["reason"] = "Папки класса ещё нет — отчёты не собирали"
        return row

    row["docx"] = _count(drive, source.class_folder_id, MIME_DOCX, ".docx")
    row["docx_folder_url"] = folder_url(source.class_folder_id)
    if not row["docx"]:
        row["reason"] = "Папка есть, но DOCX-отчётов в ней нет"
        return row

    destination = find_report_folder(
        drive, root_folder_url=target.pdf_folder_url, module=module, class_code=class_code
    )
    if destination is not None:
        row["pdf"] = _count(drive, destination.class_folder_id, MIME_PDF, ".pdf")
        row["pdf_folder_url"] = folder_url(destination.class_folder_id)

    row["ready"] = True
    # Said plainly, because «15 и 15» and «15 и 3» mean quite different things to someone
    # deciding whether to press the button.
    if not row["pdf"]:
        row["reason"] = "Готов к сохранению в PDF"
    elif row["pdf"] < row["docx"]:
        row["reason"] = f"PDF есть не для всех: {row['pdf']} из {row['docx']}"
    else:
        row["reason"] = "PDF уже сделаны — повторный запуск их заменит"
    return row


def run_module_pdf_scan_job(
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

    rows: list[dict] = []
    for link in _class_links(class_codes):
        try:
            rows.append(scan_class(drive, module, link.class_code))
        except GoogleIntegrationError as exc:
            rows.append(
                {
                    "class_code": link.class_code,
                    "docx": 0,
                    "pdf": 0,
                    "ready": False,
                    "reason": exc.user_message,
                }
            )
            log_step(
                job_run=job_run,
                level=JobLog.Level.ERROR,
                message="Could not read a class folder",
                context={"class_code": link.class_code, "reason": exc.technical_message},
            )

    ready = [row for row in rows if row["ready"]]
    job_run.result_json = {
        "summary": {
            "classes_total": len(rows),
            "classes_ready": len(ready),
            "classes_not_ready": len(rows) - len(ready),
            "docx_found": sum(row["docx"] for row in rows),
            "pdf_found": sum(row["pdf"] for row in rows),
        },
        "module": str(module),
        "module_id": module.pk,
        "classes": rows,
    }
    job_run.status = JobRun.Status.SUCCESS if ready else JobRun.Status.PARTIAL
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["result_json", "status", "finished_at"])

    log_step(
        job_run=job_run,
        level=JobLog.Level.INFO,
        message="PDF readiness scan finished",
        context=job_run.result_json["summary"],
    )
    return job_run
