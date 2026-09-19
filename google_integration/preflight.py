"""Access preflight for a school year.

DEV-0209. Before any mass operation, check that the Google account currently configured can
actually reach every folder, input table and template stored on the school year. Catching a
permission problem here is far cheaper than catching it halfway through creating 32 files.

This is also what makes the environment swap safe: after moving to another server with
another Google account, the preflight tells the administrator which links that account cannot
see, without touching the school year data.
"""

from __future__ import annotations

from django.utils import timezone

from jobs.models import JobRun

from .drive import MIME_FOLDER
from .errors import GoogleIntegrationError
from .ids import extract_folder_id, extract_spreadsheet_id
from .joblog import log_google_error, log_google_step

JOB_TYPE = "google_access_preflight"

KIND_FOLDER = "folder"
KIND_SPREADSHEET = "spreadsheet"

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_NOT_CONFIGURED = "not_configured"

# Every Google link a school year can carry, in the order the завуч fills them in.
CHECKS: tuple[tuple[str, str, str], ...] = (
    ("register_root_0_3_url", "Корневая папка журналов 0-3", KIND_FOLDER),
    ("register_root_4_11_url", "Корневая папка журналов 4-11", KIND_FOLDER),
    ("docx_root_0_3_url", "Папка DOCX 0-3", KIND_FOLDER),
    ("docx_root_4_11_url", "Папка DOCX 4-11", KIND_FOLDER),
    ("pdf_root_0_3_url", "Папка PDF 0-3", KIND_FOLDER),
    ("pdf_root_4_11_url", "Папка PDF 4-11", KIND_FOLDER),
    ("unsent_reports_folder_url", "Папка «Неотправленные отчеты»", KIND_FOLDER),
    ("class_register_template_url", "Шаблон class register", KIND_SPREADSHEET),
    ("class_roster_sheet_url", "Таблица состава классов", KIND_SPREADSHEET),
    ("subject_teacher_sheet_url", "Таблица предметов и преподавателей", KIND_SPREADSHEET),
    ("telegram_mapping_sheet_url", "Таблица Telegram-расшифровки", KIND_SPREADSHEET),
    ("parent_contacts_sheet_url", "Таблица контактов родителей", KIND_SPREADSHEET),
    ("monitoring_sheet_url", "Таблица мониторинга заполняемости", KIND_SPREADSHEET),
)


def _check_folder(drive, url: str) -> dict:
    folder_id = extract_folder_id(url)
    item = drive.get_file(folder_id)
    if item.get("mimeType") != MIME_FOLDER:
        raise GoogleIntegrationError(
            "not_found",
            f"Drive id {folder_id} is not a folder (mimeType={item.get('mimeType')!r})",
            user_message="Ссылка ведёт не на папку Google Drive.",
            context={"file_id": folder_id, "mime_type": item.get("mimeType")},
        )
    return {"resource_id": folder_id, "name": item.get("name", "")}


def _check_spreadsheet(sheets, url: str) -> dict:
    spreadsheet_id = extract_spreadsheet_id(url)
    titles = sheets.get_sheet_titles(spreadsheet_id)
    return {"resource_id": spreadsheet_id, "sheet_count": len(titles), "sheet_titles": titles[:20]}


def check_school_year_access(school_year, *, drive, sheets, job_run: JobRun | None = None) -> dict:
    """Check every configured link. A broken link never stops the remaining checks."""

    items: list[dict] = []
    ok = failed = not_configured = 0

    for field, label, kind in CHECKS:
        url = (getattr(school_year, field, "") or "").strip()
        item = {"field": field, "label": label, "kind": kind, "url": url}

        if not url:
            not_configured += 1
            items.append({**item, "status": STATUS_NOT_CONFIGURED})
            continue

        try:
            if kind == KIND_FOLDER:
                detail = _check_folder(drive, url)
            else:
                detail = _check_spreadsheet(sheets, url)
        except GoogleIntegrationError as exc:
            failed += 1
            items.append(
                {
                    **item,
                    "status": STATUS_FAILED,
                    "user_message": exc.user_message,
                    "error_category": exc.category,
                }
            )
            log_google_error(job_run, exc, message=f"Preflight failed: {label}", context={"field": field})
            continue

        ok += 1
        items.append({**item, "status": STATUS_OK, **detail})

    return {
        "school_year_id": school_year.pk,
        "school_year": str(school_year),
        "checked_at": timezone.now().isoformat(),
        "total": len(CHECKS),
        "ok": ok,
        "failed": failed,
        "not_configured": not_configured,
        "items": items,
    }


def _overall_status(result: dict) -> str:
    if result["failed"] == 0:
        return JobRun.Status.SUCCESS
    if result["ok"] > 0:
        return JobRun.Status.PARTIAL
    return JobRun.Status.FAILED


def run_google_access_preflight(school_year, *, drive, sheets, initiated_by=None) -> JobRun:
    """Run the preflight as a `JobRun` so the administrator gets a timeline and logs."""

    job_run = JobRun.objects.create(
        job_type=JOB_TYPE,
        status=JobRun.Status.RUNNING,
        started_at=timezone.now(),
        params_json={"school_year_id": school_year.pk, "trigger": "manual"},
        initiated_by=initiated_by,
    )

    log_google_step(job_run, "Google access preflight started", context={"school_year": str(school_year)})

    try:
        result = check_school_year_access(school_year, drive=drive, sheets=sheets, job_run=job_run)
    except GoogleIntegrationError as exc:
        log_google_error(job_run, exc, message="Google access preflight aborted")
        job_run.status = JobRun.Status.FAILED
        job_run.finished_at = timezone.now()
        job_run.result_json = {"error": exc.as_dict()}
        job_run.save(update_fields=["status", "finished_at", "result_json"])
        return job_run

    job_run.status = _overall_status(result)
    job_run.finished_at = timezone.now()
    job_run.result_json = result
    job_run.save(update_fields=["status", "finished_at", "result_json"])

    log_google_step(
        job_run,
        "Google access preflight finished",
        context={"ok": result["ok"], "failed": result["failed"], "not_configured": result["not_configured"]},
    )
    return job_run


def latest_preflight(school_year_id: int | None = None) -> JobRun | None:
    """Most recent preflight run, used by the status screen for the last-checked time."""

    queryset = JobRun.objects.filter(job_type=JOB_TYPE)
    if school_year_id is not None:
        queryset = queryset.filter(params_json__school_year_id=school_year_id)
    return queryset.order_by("-started_at").first()
