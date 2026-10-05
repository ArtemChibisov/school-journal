"""The rules that decide whether a grade reaches the report.

Faithfully ported from the legacy report builder - `school_journal-main/generate_page.py` and
the two `marks_dict` files it loads. Ported, not improved: the point of checking values is to
predict what the report will do with them, so a check that applied better rules would pass a
value the report then drops, which is the failure it exists to prevent.

The known defects of those rules are therefore reproduced here, and named:

- a letter grade in classes 0-3 raises in the legacy builder, because the labels its letters
  map to are not in the primary scale. It is reported as an error, since it breaks generation.
- the 4-11 scale puts the boundary between `Meets expectations` and `Exceeds expectations` at
  95, while the labels on those columns read `(50-89%)` and `(90-100%)`. A child with 92 lands
  in a column that says 50-89. That is a bug in the rules, left alone here on purpose.

Both are for the school to decide on. When they are fixed, they are fixed here, and the check
and the report stay in step because they read the same table.
"""

from __future__ import annotations

from dataclasses import dataclass

BAND_PRIMARY = "primary_0_3"
BAND_MIDDLE_HIGH = "middle_high_4_11"

# marks_dict.py - the scale the legacy builder loads for classes 0-3.
PRIMARY_SCALE = (
    ("Пока не выполняет | Not Yet Able", -1, 40),
    ("Пока не может сам, но может с помощью учителя | Needs Assistance", 40, 49),
    ("Выполняет самостоятельно | Independent", 49, 89),
    ("Выполняет основное и дополнительное задание | Proficient", 89, 1000),
)

# marks_dict_upd.py - the scale for classes 4-11.
MIDDLE_HIGH_SCALE = (
    ("Не проявляется | Not evident (0-39%)", -1, 40),
    ("Начальный уровень | Beginner level (40-49%)", 40, 49),
    ("Соответствует ожиданиям по программе | Meets expectations (50-89%)", 49, 95),
    ("Превосходит ожидания по программе | Exceeds expectations (90-100%)", 95, 1000),
)

LETTER_TO_LABEL = {
    "A*": "Превосходит ожидания по программе | Exceeds expectations (90-100%)",
    "A+": "Превосходит ожидания по программе | Exceeds expectations (90-100%)",
    "A": "Соответствует ожиданиям по программе | Meets expectations (50-89%)",
    "B": "Соответствует ожиданиям по программе | Meets expectations (50-89%)",
    "C": "Соответствует ожиданиям по программе | Meets expectations (50-89%)",
    "D": "Соответствует ожиданиям по программе | Meets expectations (50-89%)",
    "E": "Начальный уровень | Beginner level (40-49%)",
    "F": "Не проявляется | Not evident (0-39%)",
    "G": "Не проявляется | Not evident (0-39%)",
    "U": "Не проявляется | Not evident (0-39%)",
}

# Exactly what the legacy builder treats as no grade. An em dash is not on the list, so a cell
# holding one is reported rather than passed over - which is right, because the report would
# drop it too.
EMPTY_VALUES = {"", "-"}

VERDICT_OK = "ok"
VERDICT_EMPTY = "empty"
VERDICT_UNKNOWN = "unknown"
VERDICT_LETTER_NOT_IN_SCALE = "letter_not_in_scale"

REASONS = {
    VERDICT_UNKNOWN: "Значение не распознаётся и в отчёт не попадёт",
    VERDICT_LETTER_NOT_IN_SCALE: "Буквенная оценка в классах 0–3: отчёт на ней ломается",
}


@dataclass(frozen=True)
class GradeVerdict:
    verdict: str
    label: str = ""
    reason: str = ""

    @property
    def is_problem(self) -> bool:
        return self.verdict in REASONS


def scale_for_band(grade_band: str):
    return PRIMARY_SCALE if grade_band == BAND_PRIMARY else MIDDLE_HIGH_SCALE


def _bucket_by_percent(value: float, scale) -> str | None:
    for label, low, high in scale:
        if low == -1:
            if value < high:
                return label
        elif low <= value < high:
            return label
    return None


def classify_grade(value: object, *, grade_band: str) -> GradeVerdict:
    """What the report would make of this cell."""

    text = "" if value is None else str(value).strip()
    if text in EMPTY_VALUES:
        return GradeVerdict(VERDICT_EMPTY)

    scale = scale_for_band(grade_band)
    label_by_name = {label for label, _, _ in scale}

    letter = text.upper()
    if letter in LETTER_TO_LABEL:
        label = LETTER_TO_LABEL[letter]
        if label not in label_by_name:
            return GradeVerdict(
                VERDICT_LETTER_NOT_IN_SCALE, reason=REASONS[VERDICT_LETTER_NOT_IN_SCALE]
            )
        return GradeVerdict(VERDICT_OK, label=label)

    try:
        # Plain float(), as the legacy builder does it: `7,5` with a comma is not a number to
        # it and never reaches the report, so it must not pass here either.
        number = float(text)
    except ValueError:
        return GradeVerdict(VERDICT_UNKNOWN, reason=REASONS[VERDICT_UNKNOWN])

    label = _bucket_by_percent(number, scale)
    if label is None:
        return GradeVerdict(VERDICT_UNKNOWN, reason=REASONS[VERDICT_UNKNOWN])
    return GradeVerdict(VERDICT_OK, label=label)
