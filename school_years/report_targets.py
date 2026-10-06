"""Where a class's reports go, and what goes in their header.

The module already holds all of it: its number, its dates and a Drive folder for each year
group. Asking the завуч to copy those into a form is asking them to retype what the system
knows, and to get it wrong on one of thirty-two runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ClassGroup, SchoolModule

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def format_module_dates(module: SchoolModule) -> str:
    """`1 сентября - 2 октября`, the shape the report header has always used."""

    if not module.starts_on or not module.ends_on:
        return ""
    start = f"{module.starts_on.day} {MONTHS_GENITIVE[module.starts_on.month - 1]}"
    end = f"{module.ends_on.day} {MONTHS_GENITIVE[module.ends_on.month - 1]}"
    return f"{start} - {end}"


def module_number_for_reports(module: SchoolModule) -> int | None:
    """The number the legacy header expects, or None for the final module, which has no digit."""

    try:
        return int(module.number)
    except (TypeError, ValueError):
        return None


def is_primary_grade(grade_number: int) -> bool:
    return 0 <= grade_number <= 3


@dataclass(frozen=True)
class ReportTarget:
    module: SchoolModule
    module_number: int | None
    module_dates: str
    docx_folder_url: str
    is_primary: bool
    missing: str = ""

    @property
    def is_ready(self) -> bool:
        return not self.missing


def docx_target_for_class(module: SchoolModule, class_code: str) -> ReportTarget:
    """Everything a DOCX run needs for one class, taken from the module it belongs to.

    Which folder depends on the year group, because the two halves of the school keep their
    reports apart. A class the roster does not know is treated as 4-11: that is where all but
    eleven of the classes are, and the run will say so rather than guess silently.
    """

    group = (
        ClassGroup.objects.filter(
            school_year=module.school_year, class_code=class_code, is_active=True
        ).first()
    )

    missing = ""
    if group is None:
        missing = f"Класс {class_code} не найден в составе классов"
        primary = False
    else:
        primary = is_primary_grade(group.grade_number)

    folder = module.docx_folder_0_3_url if primary else module.docx_folder_4_11_url
    folder = (folder or "").strip()
    if not missing and not folder:
        band = "0-3" if primary else "4-11"
        missing = f"У модуля не указана папка DOCX {band}"

    return ReportTarget(
        module=module,
        module_number=module_number_for_reports(module),
        module_dates=format_module_dates(module),
        docx_folder_url=folder,
        is_primary=primary,
        missing=missing,
    )
