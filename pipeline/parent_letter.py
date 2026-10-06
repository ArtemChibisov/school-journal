"""The letter a parent receives with their child's report.

The wording is the school's and the school edits it, so it lives as a template with a handful
of placeholders rather than as a finished text. What the system fills in is everything that
differs letter to letter - the student, the class, the module and its dates. The old mailer
had the module typed into a constant and renamed by hand each time, which is how a report for
the first module goes out announcing the third.

A template is checked when it is saved, against the same placeholders a real letter uses. A
typo like `{studnet}` has to be caught by the person who made it, at the moment they made it,
and not halfway through four hundred letters.

Nothing here touches SMTP. Building the letter and sending it are separate so the text can be
read and corrected without anyone connecting to a mail server.
"""

from __future__ import annotations

import re
from datetime import date

from school_years.models import SchoolModule, SchoolYear
from school_years.report_targets import format_module_dates, format_school_year

SIGNATURE = "ОЦ Школа Проектор"

DEFAULT_SUBJECT = "Отчёт по {module_dative} {year} — {student}"

DEFAULT_BODY = (
    "Дорогие родители!\n"
    "\n"
    "Высылаем вам отчёт за {module_name} ({dates}) — {student}, {class}.\n"
    "Отчёт во вложении.\n"
    "\n"
    "Если у вас появятся вопросы по отчёту, напишите тьютору класса.\n"
    "\n"
    "С уважением,\n"
    f"{SIGNATURE}"
)

# Accusative for «отчёт за первый учебный модуль», dative for «отчёт по первому модулю».
ORDINAL = {
    1: ("первый", "первому"),
    2: ("второй", "второму"),
    3: ("третий", "третьему"),
    4: ("четвёртый", "четвёртому"),
    5: ("пятый", "пятому"),
    6: ("шестой", "шестому"),
}

FINAL = ("итоговый", "итоговому")

PLACEHOLDERS = {
    "student": "Имя ученика",
    "class": "Класс",
    "module_name": "«первый учебный модуль»",
    "module_dative": "«первому модулю» — для темы письма",
    "module_number": "Номер модуля",
    "dates": "Даты модуля",
    "year": "Учебный год",
}

FIELD = re.compile(r"\{([^{}]*)\}")


def _number(module: SchoolModule) -> int | None:
    try:
        return int(module.number)
    except (TypeError, ValueError):
        return None


def context_for(*, student_name: str, module: SchoolModule, class_code: str = "") -> dict:
    """Everything a template may refer to, and nothing else."""

    number = _number(module)
    accusative, dative = ORDINAL.get(number, FINAL) if number else FINAL
    return {
        "student": student_name,
        "class": class_code,
        "module_name": f"{accusative} учебный модуль",
        "module_dative": f"{dative} модулю",
        "module_number": str(number) if number else "",
        "dates": format_module_dates(module),
        "year": format_school_year(module.school_year) if module.school_year_id or module.school_year else "",
    }


def unknown_placeholders(template: str) -> list[str]:
    """Placeholders a template uses that nothing will fill in."""

    return sorted({name for name in FIELD.findall(template) if name not in PLACEHOLDERS})


def render(template: str, context: dict) -> str:
    return template.format_map(context)


def sample_module() -> SchoolModule:
    """A module to show the example with: the school's own when there is one.

    There is always an example on the screen, so a school that has not set up its year yet
    still sees what the letter looks like rather than an empty box.
    """

    year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
    module = (
        SchoolModule.objects.filter(school_year=year).order_by("number").first() if year else None
    )
    if module is not None:
        return module
    return SchoolModule(
        school_year=year or SchoolYear(name="26/27"),
        number=SchoolModule.Number.MODULE_1,
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 10, 2),
    )


def stored_template() -> tuple[str, str]:
    """The school's wording, or the built-in one when it has not changed anything."""

    from .models import ParentLetterTemplate

    saved = ParentLetterTemplate.objects.order_by("-updated_at").first()
    if saved is None:
        return DEFAULT_SUBJECT, DEFAULT_BODY
    return saved.subject or DEFAULT_SUBJECT, saved.body or DEFAULT_BODY


def letter_for(
    *, student_name: str, module: SchoolModule, class_code: str = "", template: tuple[str, str] | None = None
) -> tuple[str, str]:
    """Subject and body for one student.

    A template that fails to render falls back to the built-in one rather than costing the
    family their report: it was checked when it was saved, so getting here means something
    unforeseen, and a letter in the default wording beats no letter at all.
    """

    subject_template, body_template = template or stored_template()
    context = context_for(student_name=student_name, module=module, class_code=class_code)
    try:
        return render(subject_template, context), render(body_template, context)
    except (KeyError, IndexError, ValueError):
        return render(DEFAULT_SUBJECT, context), render(DEFAULT_BODY, context)


def sample_letter(template: tuple[str, str] | None = None) -> tuple[str, str]:
    """The example shown on the screen, built the way a real letter is built."""

    return letter_for(
        student_name="Мария Иванова",
        module=sample_module(),
        class_code="4А",
        template=template,
    )
