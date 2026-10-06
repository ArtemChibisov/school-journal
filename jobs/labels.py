"""Human names for job types.

`JobRun.job_type` is a technical string written by whoever starts the job. The event log and
the run list show it to the завуч, so it needs a Russian name. An unknown type falls back to
its raw value: a new job shows up in the log immediately, just without a nice name.
"""

from __future__ import annotations

JOB_TYPE_LABELS = {
    "ai_criteria_class_review": "AI-вычитка критериев",
    "build_criteria_table": "Сборка таблицы критериев",
    "check_missing_data": "Проверка незаполненных данных",
    "class_register_generation": "Создание журналов класса",
    "class_register_scan": "Поиск журналов модуля",
    "class_roster_import": "Импорт состава классов",
    "convert_docx_to_pdf": "Конвертация DOCX в PDF",
    "module_pdf_reports": "Отчёты модуля в PDF",
    "module_pdf_scan": "Проверка готовности к PDF",
    "parent_contact_import": "Импорт контактов родителей",
    "register_generation": "Создание журналов модуля",
    "parent_reports_check": "Проверка рассылки родителям",
    "parent_reports_send": "Рассылка отчётов родителям",
    "descriptor_criteria_fill_check": "Проверка заполненности журналов",
    "descriptor_fill_reminders": "Напоминания о заполнении",
    "download_descriptors": "Загрузка дескрипторов",
    "generate_docx_reports": "Генерация отчётов DOCX",
    "google_access_preflight": "Проверка доступов Google",
    "module_review_reports": "Отчёты модуля на вычитку",
    "parent_report_send": "Отправка отчёта родителю",
    "prepare_student_review_reports": "Отчёты учеников на вычитку",
    "run_full_pipeline": "Полный пайплайн",
    "run_validation": "Проверка значений",
    "send_parent_reports": "Отправка отчётов родителям",
    "send_validation_reminders": "Напоминания о проблемах",
    "student_report_generation": "Генерация отчётов учеников",
    "teacher_criteria_recheck": "Повторная проверка критериев",
    "test_job": "Тестовая задача",
    "validation": "Проверка значений",
}


def job_type_label(job_type: str | None) -> str:
    if not job_type:
        return "—"
    return JOB_TYPE_LABELS.get(job_type, job_type)


def labelled_job_types(job_types) -> list[tuple[str, str]]:
    """Pairs of (value, human name), sorted the way a person reads them."""

    pairs = [(value, job_type_label(value)) for value in job_types if value]
    return sorted(pairs, key=lambda pair: pair[1].lower())
