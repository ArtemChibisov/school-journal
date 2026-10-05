"""Reading every grade in every journal and reporting the ones the report would lose.

Grade problems used to surface when the documents were already built: a value the builder does
not understand simply vanishes, so the gap is noticed in a finished report, fixed, and the
whole run repeated. This finds them first.

What counts as a problem is decided in `grade_values`, which carries the legacy rules
unchanged, so this predicts what the report will actually do rather than what it should do.
"""

from __future__ import annotations

from django.utils import timezone

from jobs.models import JobLog, JobRun
from jobs.services import log_step
from journal_links.models import ClassSheetLink
from school_years.models import ClassGroup, SchoolYear
from validation.job_runner import fetch_workbook_for_link

from .descriptor_criteria_fill import (
    DescriptorCriteriaFillReadError,
    _cell_value,
    _classify_sheet_type,
    _find_anchor,
    _find_comment_col,
    _find_student_name_header_row,
    _get_real_data_bounds,
    _is_filled_criteria_value,
    _student_rows,
    fetch_sheet_titles,
    restore_full_sheet_names,
)
from .grade_values import BAND_MIDDLE_HIGH, BAND_PRIMARY, classify_grade

JOB_TYPE = "grade_value_check"

MAX_REPORTED = 500


def grade_band_for_class(class_code: str) -> str:
    """Which scale this class is graded on, from the roster that knows its year group."""

    group = (
        ClassGroup.objects.filter(class_code=class_code, is_active=True)
        .order_by("-school_year__is_active", "-id")
        .first()
    )
    if group is None:
        return BAND_MIDDLE_HIGH
    return BAND_PRIMARY if group.grade_number <= 3 else BAND_MIDDLE_HIGH


def _student_name(ws, row_num: int) -> str:
    parts = [str(_cell_value(ws, row_num, col) or "").strip() for col in (1, 2)]
    return " ".join(part for part in parts if part)


def check_sheet_grades(ws, *, class_code: str, subject_name: str, grade_band: str) -> list[dict]:
    """Every value on this sheet that the report would not understand."""

    max_row, max_col = _get_real_data_bounds(ws)
    criteria_anchor = _find_anchor(ws, "критерии оценивания", "assessment criteria")
    if criteria_anchor is None:
        return []

    criteria_row, criteria_col = criteria_anchor
    comment_col = _find_comment_col(ws, criteria_row, criteria_col + 1, max_col)
    end_col = (comment_col - 1) if comment_col else max_col

    columns: list[tuple[int, str]] = []
    for col_num in range(criteria_col + 1, end_col + 1):
        title = _cell_value(ws, criteria_row, col_num)
        if _is_filled_criteria_value(title):
            columns.append((col_num, str(title).strip()))

    if not columns:
        return []

    name_header_row = _find_student_name_header_row(ws, max_row)
    problems: list[dict] = []

    for row_num in _student_rows(ws, name_header_row, max_row):
        for col_num, criterion in columns:
            value = _cell_value(ws, row_num, col_num)
            verdict = classify_grade(value, grade_band=grade_band)
            if not verdict.is_problem:
                continue
            problems.append(
                {
                    "class_code": class_code,
                    "subject_name": subject_name,
                    "student_name": _student_name(ws, row_num),
                    "criterion": criterion,
                    "value": str(value).strip(),
                    "verdict": verdict.verdict,
                    "reason": verdict.reason,
                    "row": row_num,
                }
            )

    return problems


def check_workbook_grades(path: str, *, class_code: str, sheet_titles=None) -> dict:
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException
    from zipfile import BadZipFile

    try:
        wb = load_workbook(path, data_only=True)
    except (FileNotFoundError, InvalidFileException, BadZipFile, OSError) as exc:
        raise DescriptorCriteriaFillReadError(f"Cannot read workbook: {path}") from exc

    grade_band = grade_band_for_class(class_code)
    problems: list[dict] = []
    sheets_checked = 0

    try:
        full_names = restore_full_sheet_names(wb.sheetnames, sheet_titles)
        for workbook_name in wb.sheetnames:
            sheet_name = full_names.get(workbook_name, workbook_name)
            if _classify_sheet_type(sheet_name) in {"tutor", "service"}:
                continue
            sheets_checked += 1
            problems.extend(
                check_sheet_grades(
                    wb[workbook_name],
                    class_code=class_code,
                    subject_name=sheet_name,
                    grade_band=grade_band,
                )
            )
    finally:
        wb.close()

    return {"problems": problems, "sheets_checked": sheets_checked, "grade_band": grade_band}


def run_grade_value_check(*, class_code: str | None = None, initiated_by=None) -> JobRun:
    """Read the journals and record every grade the report would lose."""

    job_run = JobRun.objects.create(
        job_type=JOB_TYPE,
        status=JobRun.Status.RUNNING,
        started_at=timezone.now(),
        params_json={"class_code": class_code},
        initiated_by=initiated_by,
    )

    links = ClassSheetLink.objects.filter(is_active=True)
    if class_code:
        links = links.filter(class_code=class_code)
    links = list(links.order_by("class_code", "id"))

    problems: list[dict] = []
    classes_checked = 0
    classes_failed = 0
    sheets_checked = 0

    for link in links:
        try:
            temp_file = fetch_workbook_for_link(link)
            result = check_workbook_grades(
                str(temp_file),
                class_code=link.class_code,
                sheet_titles=fetch_sheet_titles(link.google_sheet_url, job_run=job_run),
            )
        except Exception as exc:  # noqa: BLE001
            classes_failed += 1
            log_step(
                job_run=job_run,
                level=JobLog.Level.ERROR,
                message="Grade value check failed for a class",
                context={"class_code": link.class_code, "reason": str(exc)[:500]},
            )
            continue

        classes_checked += 1
        sheets_checked += result["sheets_checked"]
        for problem in result["problems"]:
            problem["sheet_url"] = link.google_sheet_url
        problems.extend(result["problems"])

    by_value: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for problem in problems:
        by_value[problem["value"]] = by_value.get(problem["value"], 0) + 1
        by_class[problem["class_code"]] = by_class.get(problem["class_code"], 0) + 1

    job_run.result_json = {
        "classes_checked": classes_checked,
        "classes_failed": classes_failed,
        "sheets_checked": sheets_checked,
        "problems_total": len(problems),
        # Capped so one journal full of the same typo cannot bloat the run record; the counts
        # above stay honest about how many there really were.
        "problems": problems[:MAX_REPORTED],
        "truncated": len(problems) > MAX_REPORTED,
        "by_value": sorted(by_value.items(), key=lambda item: -item[1]),
        "by_class": sorted(by_class.items()),
    }

    if classes_checked == 0 and links:
        job_run.status = JobRun.Status.FAILED
    elif classes_failed:
        job_run.status = JobRun.Status.PARTIAL
    else:
        job_run.status = JobRun.Status.SUCCESS
    job_run.finished_at = timezone.now()
    job_run.save()

    log_step(
        job_run=job_run,
        level=JobLog.Level.INFO,
        message="Grade value check finished",
        context={"classes": classes_checked, "problems": len(problems)},
    )
    return job_run
