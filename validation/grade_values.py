"""The rules that decide whether a grade reaches the report.

Ported from the path the reports actually take: `enrich_mark` in `school_journal-main/main.py`,
which reads the cell, and the renderer in `generate_page.py`, which decides what to draw. The
two `marks_dict` files hold the scales.

(`make_mark_value` in generate_page.py looks like the parser but is never called - it is
imported in main.py and left unused. Reading it first is what made the first version of this
module reject nineteen thousand perfectly good grades.)

What the report does with a cell:

- the exact text of a scale level, which is what teachers pick from the dropdown, ticks that
  column. This is the ordinary case and by far the most common;
- a number falls into the band where `low < value <= high`, and ticks it;
- a dash means no grade was given, and the row is drawn as dashes;
- anything else is kept raw and handed to the renderer, which hunts through it for a letter
  grade and a number. `70 - B` and `86, A` survive this and are printed as `70% B`. Something
  with no letter in it - `Да`, `40,5`, a paragraph of feedback - has no column to go in and
  is written nowhere at all.

That last case is the one worth reporting: the teacher filled the cell, the check for
emptiness is satisfied, and the parent sees a blank.
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

# The letters the renderer recognises when it digs through an unrecognised cell.
LETTERS = {"A*", "A+", "A", "B", "C", "D", "E", "F", "G", "U"}

# A dash means the grade was not given. Teachers write it several ways, and the report
# draws exactly such a dash for an empty grade, so these are what it draws coming back.
NO_GRADE_VALUES = {"", "-", "--", "–", "—", "−"}

VERDICT_LEVEL = "level"
VERDICT_NUMBER = "number"
VERDICT_LETTER_AND_NUMBER = "letter_and_number"
VERDICT_EMPTY = "empty"
VERDICT_LOST = "lost"

REASONS = {VERDICT_LOST: "Отчёт не поймёт это значение — у родителя будет пусто"}


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


def _as_number(text: str) -> float | None:
    """A number the way a spreadsheet hands one over - never with a comma.

    `enrich_mark` only treats a cell as numeric when openpyxl already gave it as int or float,
    so `40,5` typed as text stays text and goes down the unrecognised path.
    """

    try:
        return float(text)
    except ValueError:
        return None


def _band_for_number(value: float, scale) -> str | None:
    # `low < value <= high`, exactly as enrich_mark compares.
    for label, low, high in scale:
        if low < value <= high:
            return label
    return None


def classify_grade(value: object, *, grade_band: str) -> GradeVerdict:
    """What the report would make of this cell."""

    if value is None:
        return GradeVerdict(VERDICT_EMPTY)

    scale = scale_for_band(grade_band)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        label = _band_for_number(float(value), scale)
        if label is None:
            return GradeVerdict(VERDICT_LOST, reason=REASONS[VERDICT_LOST])
        return GradeVerdict(VERDICT_NUMBER, label=label)

    text = str(value).strip()
    if text in NO_GRADE_VALUES:
        return GradeVerdict(VERDICT_EMPTY)

    for label, _, _ in scale:
        if text == label:
            return GradeVerdict(VERDICT_LEVEL, label=label)

    number = _as_number(text)
    if number is not None:
        label = _band_for_number(number, scale)
        if label is None:
            return GradeVerdict(VERDICT_LOST, reason=REASONS[VERDICT_LOST])
        return GradeVerdict(VERDICT_NUMBER, label=label)

    # The renderer's last resort is to hunt for a letter grade among the words, so `70 - B`
    # and `86, A` do come out right. But it hunts in anything: a sentence containing the word
    # `a` gives it a letter, and the parent is shown `A` where feedback was written.
    #
    # So a cell only passes when every word in it is part of a grade. Predicting that the
    # report prints something is not the same as predicting it prints the truth, and a grade
    # quietly replaced by the wrong one is worse than a grade that goes missing.
    letter = ""
    for token in text.replace("%", "").split():
        word = token.strip(",;:").upper()
        if not word or not any(char.isalnum() for char in word):
            continue
        if word in LETTERS:
            letter = word
            continue
        if _as_number(word) is not None:
            continue
        return GradeVerdict(VERDICT_LOST, reason=REASONS[VERDICT_LOST])

    if letter:
        return GradeVerdict(VERDICT_LETTER_AND_NUMBER, label=letter)

    return GradeVerdict(VERDICT_LOST, reason=REASONS[VERDICT_LOST])
