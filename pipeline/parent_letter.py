"""The letter a parent receives with their child's report.

The wording is the school's, carried over from the mailer it used last year. What the system
adds is the module: it knew which one the run was for all along, while the old script had the
module typed into a constant and renamed by hand every time - which is how a report for the
first module can go out announcing the third.

Nothing here touches SMTP. Building the letter and sending it are separate so the text can be
read, reviewed and corrected without anyone connecting to a mail server.
"""

from __future__ import annotations

from school_years.models import SchoolModule
from school_years.report_targets import format_module_dates, format_school_year

SIGNATURE = "ОЦ Школа Проектор"

# Dative, because the subject line reads «Отчёт по первому модулю».
ORDINAL_DATIVE = {
    1: "первому",
    2: "второму",
    3: "третьему",
    4: "четвёртому",
    5: "пятому",
    6: "шестому",
}

# Dative again, for «отчёт за первый учебный модуль».
ORDINAL_ACCUSATIVE = {
    1: "первый",
    2: "второй",
    3: "третий",
    4: "четвёртый",
    5: "пятый",
    6: "шестой",
}


def _number(module: SchoolModule) -> int | None:
    try:
        return int(module.number)
    except (TypeError, ValueError):
        return None


def subject_for(*, student_name: str, module: SchoolModule) -> str:
    """`Отчёт по первому модулю 2026-2027 — Мария Иванова`."""

    year = format_school_year(module.school_year)
    number = _number(module)
    if number is None:
        return f"Итоговый отчёт {year} — {student_name}"
    ordinal = ORDINAL_DATIVE.get(number, f"{number}")
    return f"Отчёт по {ordinal} модулю {year} — {student_name}"


def body_for(*, student_name: str, module: SchoolModule, class_code: str = "") -> str:
    """The letter itself.

    The student is named so a family with two children can tell the two letters apart. The
    name sits after a dash rather than inside a sentence: Russian would decline it there, and
    no amount of code declines surnames reliably. «Отчёт Мария Иванова» is how that goes
    wrong, and it goes wrong for four hundred families at once.
    """

    number = _number(module)
    dates = format_module_dates(module)
    if number is None:
        what = f"итоговый отчёт за {format_school_year(module.school_year)} учебный год"
    else:
        ordinal = ORDINAL_ACCUSATIVE.get(number, f"{number}-й")
        what = f"отчёт за {ordinal} учебный модуль"
    if dates:
        what = f"{what} ({dates})"

    who = f"{student_name}, {class_code}" if class_code else student_name

    return (
        "Дорогие родители!\n"
        "\n"
        f"Высылаем вам {what} — {who}.\n"
        "Отчёт во вложении.\n"
        "\n"
        "Если у вас появятся вопросы по отчёту, напишите тьютору класса.\n"
        "\n"
        "С уважением,\n"
        f"{SIGNATURE}"
    )


def letter_for(
    *, student_name: str, module: SchoolModule, class_code: str = ""
) -> tuple[str, str]:
    return (
        subject_for(student_name=student_name, module=module),
        body_for(student_name=student_name, module=module, class_code=class_code),
    )
