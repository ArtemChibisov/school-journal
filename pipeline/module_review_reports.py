"""Building the review documents for a whole module in one go.

Thirty-two classes used to mean thirty-two trips through a form. This walks them in one run,
taking each class's folder, module number and dates from the module itself.

Classes are done one after another rather than at once: every class downloads a workbook from
Google and uploads a file per student, and thirty-two of those in parallel is a good way to be
rate-limited by Google and learn nothing about which class failed.

Each class still gets its own job run underneath, so its log, its file list and its errors stay
where they always were. This one records which classes were done, which were skipped and why.
"""

from __future__ import annotations

import threading

from django.db import close_old_connections
from django.utils import timezone

from jobs.models import JobLog, JobRun
from jobs.services import log_step
from journal_links.models import ClassSheetLink
from school_years.models import SchoolModule
from school_years.report_targets import docx_target_for_class, module_number_for_reports

from .student_review_reports import run_prepare_student_review_reports_job

JOB_TYPE = "module_review_reports"


def _class_links(class_codes: list[str] | None) -> list[ClassSheetLink]:
    """The classes to build for. An empty choice means all of them."""

    queryset = ClassSheetLink.objects.filter(is_active=True)
    if class_codes:
        queryset = queryset.filter(class_code__in=class_codes)
    return list(queryset.order_by("class_code", "id"))


def enqueue_module_review_reports_job(
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
        message="Module review reports queued",
        context={"module": str(module), "classes": class_codes or "все классы"},
    )

    thread = threading.Thread(
        target=_run_in_thread, args=(str(job_run.id), module.pk, class_codes), daemon=True
    )
    thread.start()
    return job_run


def _run_in_thread(job_run_id: str, module_id: int, class_codes: list[str] | None) -> None:
    close_old_connections()
    try:
        job_run = JobRun.objects.get(id=job_run_id)
        module = SchoolModule.objects.get(pk=module_id)
        run_module_review_reports_job(module=module, class_codes=class_codes, job_run=job_run)
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
    job_run: JobRun | None = None,
    initiated_by=None,
    generator=None,
) -> JobRun:
    if job_run is None:
        job_run = JobRun.objects.create(
            job_type=JOB_TYPE,
            status=JobRun.Status.RUNNING,
            started_at=timezone.now(),
            params_json={"module_id": module.pk, "module": str(module), "class_codes": class_codes or []},
            initiated_by=initiated_by,
        )
    else:
        job_run.status = JobRun.Status.RUNNING
        job_run.started_at = job_run.started_at or timezone.now()
        job_run.save(update_fields=["status", "started_at"])

    links = _class_links(class_codes)
    rows: list[dict] = []
    docx_created = 0
    uploaded_success = 0
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

        child = run_prepare_student_review_reports_job(
            class_sheet_link_id=link.id,
            drive_folder_url=target.docx_folder_url,
            module_number=target.module_number,
            module_dates=target.module_dates,
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

        rows.append(
            {
                "class_code": link.class_code,
                "status": child.status,
                "docx_created": created,
                "uploaded_success": uploaded,
                "job_run_id": str(child.id),
            }
        )

    job_run.result_json = {
        "summary": {
            "classes_total": len(links),
            "classes_done": classes_done,
            "classes_failed": classes_failed,
            "classes_skipped": classes_skipped,
            "docx_created": docx_created,
            "uploaded_success": uploaded_success,
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
