"""Deciding whom not to remind in the current module.

A gap in a journal is not always something to chase. The Drama teacher wrote in his own
descriptor that he ran module 1 without criteria on purpose and would introduce them in module
2. The check is right that criteria are missing; reminding him about it every half hour is not.

So the завуч can clear the tick next to a teacher, and the reminders stop - for that module.
His note says the next module will be different, so the silence ends when the module does and
nobody has to remember to switch him back on.
"""

from __future__ import annotations

from school_years.imports.text import comparison_key
from school_years.models import SchoolModule, Teacher

from .models import ReminderMute


def current_module() -> SchoolModule | None:
    """The module reminders are about: the one running today, else the latest one set up.

    Falling back to the latest matters in the days right after a module closes, when teachers
    are still finishing it - which is exactly when reminders are worth sending.
    """

    from django.utils import timezone

    from school_years.models import SchoolYear

    school_year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
    if school_year is None:
        return None

    modules = SchoolModule.objects.filter(school_year=school_year)
    today = timezone.localdate()
    running = modules.filter(starts_on__lte=today, ends_on__gte=today).first()
    return running or modules.order_by("-number").first()


def resolve_teacher(name: str, *, school_year) -> Teacher | None:
    """Find the person a name in the fill report refers to.

    The report quotes the journal, and a journal names a teacher in the language its class is
    taught in. `Юлия Галкина` and `Yuliya Galkina` are one person, and one tick must silence
    both - which is exactly what the directory is for.
    """

    key = comparison_key(name)
    if not key:
        return None

    for teacher in Teacher.objects.filter(school_year=school_year):
        if key in {comparison_key(teacher.name_ru), comparison_key(teacher.name_en)} - {""}:
            return teacher
    return None


def muted_teacher_ids(module: SchoolModule) -> set[int]:
    return set(
        ReminderMute.objects.filter(module=module).values_list("teacher_id", flat=True)
    )


def is_muted(name: str, *, module: SchoolModule) -> bool:
    """True when this name belongs to a teacher the завуч silenced for this module.

    A name the directory does not know is never muted: silence has to be a decision somebody
    made, and here nobody could have made it.
    """

    if module is None:
        return False
    teacher = resolve_teacher(name, school_year=module.school_year)
    if teacher is None:
        return False
    return ReminderMute.objects.filter(module=module, teacher=teacher).exists()


def set_muted(
    *, module: SchoolModule, teacher: Teacher, muted: bool, user=None, reason: str = ""
) -> bool:
    """Turn reminders for one teacher off or on for this module. Returns the new state."""

    if muted:
        ReminderMute.objects.get_or_create(
            module=module,
            teacher=teacher,
            defaults={"created_by": user, "reason": reason},
        )
        return True

    ReminderMute.objects.filter(module=module, teacher=teacher).delete()
    return False
