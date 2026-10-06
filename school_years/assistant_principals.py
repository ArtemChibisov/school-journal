"""Which завуч signs a given class's reports.

The report's last page names the завуч. That name used to live in a table inside the legacy
helper, keyed by the class codes of a year that had since ended: of this year's thirty-two
classes, nineteen matched nothing and their reports printed the label and then nothing at all.
Changing it meant changing code and deploying.

So it is a setting now, and it is kept the way the school assigns the job - by parallel, not by
class. The class's parallel comes from the roster, which is the only place that knows it
reliably; the class code is text, and `4К1` has been spelled four different ways in four
different tables.
"""

from __future__ import annotations

from .models import AssistantPrincipal, ClassGroup, SchoolYear


def parallel_of(school_year: SchoolYear, class_code: str) -> int | None:
    """The parallel a class belongs to, according to the roster."""

    group = ClassGroup.objects.filter(
        school_year=school_year, class_code__iexact=class_code, is_active=True
    ).first()
    return group.grade_number if group else None


def principal_for_parallel(parallel: int | None) -> AssistantPrincipal | None:
    if parallel is None:
        return None
    for principal in AssistantPrincipal.objects.filter(is_active=True):
        if parallel in principal.parallel_numbers:
            return principal
    return None


def principal_name_for_class(school_year: SchoolYear, class_code: str) -> str:
    """The name to print, or an empty string when nobody is assigned.

    Empty is returned rather than guessed: a report signed by the wrong завуч is worse than
    one signed by nobody, and the screen says which parallels are uncovered.
    """

    principal = principal_for_parallel(parallel_of(school_year, class_code))
    return principal.name if principal else ""


def coverage(school_year: SchoolYear) -> list[dict]:
    """Every parallel the school has, and who signs for it.

    Built for the screen, because the question people actually ask is not «кто такой завуч»
    but «у всех ли классов он есть».
    """

    parallels = sorted(
        {
            group.grade_number
            for group in ClassGroup.objects.filter(school_year=school_year, is_active=True)
        }
    )
    rows = []
    for parallel in parallels:
        principal = principal_for_parallel(parallel)
        classes = ClassGroup.objects.filter(
            school_year=school_year, grade_number=parallel, is_active=True
        ).order_by("class_code")
        rows.append(
            {
                "parallel": parallel,
                "principal": principal.name if principal else "",
                "classes": [group.class_code for group in classes],
            }
        )
    return rows
