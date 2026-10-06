"""Where a class's reports go, and what goes in their header.

The module already holds all of it: its number, its dates and a Drive folder for each year
group. Asking the завуч to copy those into a form is asking them to retype what the system
knows, and to get it wrong on one of thirty-two runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ClassGroup, SchoolModule, SchoolYear


def format_module_dates(module: SchoolModule) -> str:
    """`12.01 – 13.02.2026`, the shape printed on the report's first page.

    The year is given once, at the end, the way the school writes it. A module that runs
    across New Year gets it twice, because `29.12 – 13.02.2026` would read as one winter.
    """

    if not module.starts_on or not module.ends_on:
        return ""
    start, end = module.starts_on, module.ends_on
    head = f"{start.day:02d}.{start.month:02d}"
    if start.year != end.year:
        head = f"{head}.{start.year}"
    return f"{head} – {end.day:02d}.{end.month:02d}.{end.year}"


YEAR_SHORTHAND = re.compile(r"^(\d{2}|\d{4})\s*[/–—-]\s*(\d{2}|\d{4})$")


def format_school_year(school_year: SchoolYear) -> str:
    """`2025-2026` for the header, whichever shorthand the year was named with.

    `25/26`, `2025/2026` and `2025-2026` all name the same year, and which one someone typed
    into the settings should not decide what parents read. Anything else is printed as given:
    a school that names its year something we did not foresee means it that way.
    """

    name = (school_year.name or "").strip()
    match = YEAR_SHORTHAND.match(name)
    if not match:
        return name
    first, second = (int(part) for part in match.groups())
    return f"{first + 2000 if first < 100 else first}-{second + 2000 if second < 100 else second}"


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
    school_year: str
    docx_folder_url: str
    pdf_folder_url: str
    is_primary: bool
    missing: str = ""
    pdf_missing: str = ""

    @property
    def is_ready(self) -> bool:
        """Ready to build DOCX. PDF has its own folder and its own answer."""

        return not self.missing

    @property
    def is_pdf_ready(self) -> bool:
        return not self.pdf_missing


def _folder_for(module: SchoolModule, kind: str, *, primary: bool) -> str:
    """The module's own folder when it has one, otherwise the year's.

    Setting the year's once is enough for a school that keeps all its reports together; a
    module that wants its own folder overrides it without anyone filling six fields per module.
    """

    band = "0_3" if primary else "4_11"
    own = getattr(module, f"{kind}_folder_{band}_url", "")
    fallback = getattr(module.school_year, f"{kind}_root_{band}_url", "")
    return (own or fallback or "").strip()


def register_template_url(school_year: SchoolYear, *, primary: bool) -> str:
    """Бланк журнала для этой половины школы.

    0-3 и 4-11 оценивают по разным шкалам, поэтому и бланк разный. Выбирается по параллели
    класса из состава классов — по коду класса такое не определить, он просто текст.
    """

    field = "class_register_template_0_3_url" if primary else "class_register_template_4_11_url"
    return (getattr(school_year, field, "") or "").strip()


def docx_target_for_class(module: SchoolModule, class_code: str) -> ReportTarget:
    """Everything a run needs for one class, taken from the module it belongs to.

    Which folder depends on the year group, because the two halves of the school keep their
    reports apart. A class the roster does not know is treated as 4-11: that is where all but
    eleven of the classes are, and the run will say so rather than guess silently.

    Both folders are resolved, and each refuses separately: a missing PDF folder is no reason
    to withhold the DOCX a завуч is waiting to read.
    """

    group = (
        ClassGroup.objects.filter(
            school_year=module.school_year, class_code=class_code, is_active=True
        ).first()
    )

    unknown_class = ""
    if group is None:
        unknown_class = f"Класс {class_code} не найден в составе классов"
        primary = False
    else:
        primary = is_primary_grade(group.grade_number)

    band = "0-3" if primary else "4-11"
    docx_folder = _folder_for(module, "docx", primary=primary)
    pdf_folder = _folder_for(module, "pdf", primary=primary)

    missing = unknown_class
    if not missing and not docx_folder:
        missing = f"Не указана папка DOCX {band}: ни у модуля, ни в настройках года"

    pdf_missing = unknown_class
    if not pdf_missing and not pdf_folder:
        pdf_missing = f"Не указана папка PDF {band}: ни у модуля, ни в настройках года"

    return ReportTarget(
        module=module,
        module_number=module_number_for_reports(module),
        module_dates=format_module_dates(module),
        school_year=format_school_year(module.school_year),
        docx_folder_url=docx_folder,
        pdf_folder_url=pdf_folder,
        is_primary=primary,
        missing=missing,
        pdf_missing=pdf_missing,
    )
