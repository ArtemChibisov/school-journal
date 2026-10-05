"""Reading the subject-teachers workbook: who teaches what, and who they are.

Implements `docs/data_contracts/subject_teachers_contract.md` and
`docs/data_contracts/teachers_contract.md`. Two worksheets in one book:

- `teachers` - the directory. One row per person, with the Russian and the English spelling.
- `subject_teachers` - the assignments. One row per teacher per subject per class.

The directory exists because the school writes a teacher in the language the class is taught
in: `Юлия Галкина` in the А classes, `Yuliya Galkina` in the К ones. Nothing in a journal says
those are one person. Without the directory the monitoring table counts her twice and a
reminder goes to a name nobody answers to.

As in the roster importer, the report is class-oriented: a bad row never blocks the classes it
does not belong to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .issues import SEVERITY_CRITICAL, SEVERITY_INFO, Issue, IssueList
from .text import comparison_key, normalize_text, parse_bool

SHEET_TEACHERS = "teachers"
SHEET_ASSIGNMENTS = "subject_teachers"

TEACHERS_REQUIRED_HEADERS = ("teacher_name_ru", "teacher_name_en", "is_active")
TEACHERS_OPTIONAL_HEADERS = ("notes",)

ASSIGNMENTS_REQUIRED_HEADERS = ("class_code", "subject_name", "teacher_name", "is_active")
ASSIGNMENTS_OPTIONAL_HEADERS = ("group_name", "teacher_role", "source_sheet_name")

DEFAULT_TEACHER_ROLE = "subject_teacher"

# Row 1 holds the headers, so list index 0 is spreadsheet row 2.
FIRST_DATA_ROW = 2


def is_english_class(class_code: str) -> bool:
    """True for the classes taught in English, where the teacher is named in English.

    The school marks them with a К in the code - `4К1`, `11К`, `8К` - written in Cyrillic in
    the roster and sometimes in Latin elsewhere, so both letters count.
    """

    return "К" in class_code or "K" in class_code


@dataclass(frozen=True)
class Teacher:
    name_ru: str
    name_en: str
    is_active: bool
    notes: str
    row: int

    @property
    def display_name(self) -> str:
        return self.name_ru or self.name_en

    def spellings(self) -> tuple[str, ...]:
        return tuple(name for name in (self.name_ru, self.name_en) if name)


@dataclass(frozen=True)
class Assignment:
    class_code: str
    subject_name: str
    teacher_name: str
    group_name: str
    teacher_role: str
    source_sheet_name: str
    row: int


@dataclass
class SubjectTeacherParseResult:
    teachers: list[Teacher] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    issues: IssueList = field(default_factory=IssueList)
    teachers_total: int = 0
    teachers_active: int = 0
    assignments_total: int = 0
    assignments_active: int = 0

    @property
    def is_usable(self) -> bool:
        return not self.issues.has_workbook_level_critical()

    def as_dict(self) -> dict:
        return {
            "teachers_total": self.teachers_total,
            "teachers_active": self.teachers_active,
            "assignments_total": self.assignments_total,
            "assignments_active": self.assignments_active,
            "classes": sorted({item.class_code for item in self.assignments}),
            "subjects": sorted({item.subject_name for item in self.assignments}),
            "issue_counts": self.issues.counts(),
            "issues": self.issues.as_dicts(),
        }


def _header_map(
    rows: list[list],
    sheet: str,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    issues: IssueList,
) -> dict[str, int] | None:
    if not rows:
        issues.add(
            Issue(
                code="INVALID_HEADERS",
                severity=SEVERITY_CRITICAL,
                scope="sheet",
                sheet=sheet,
                message=f"Лист «{sheet}» пустой: не найдена строка заголовков.",
            )
        )
        return None

    header_row = [comparison_key(cell) for cell in rows[0]]
    mapping = {name: header_row.index(name) for name in required if name in header_row}

    missing = [name for name in required if name not in mapping]
    if missing:
        issues.add(
            Issue(
                code="INVALID_HEADERS",
                severity=SEVERITY_CRITICAL,
                scope="sheet",
                sheet=sheet,
                row=1,
                message=(
                    f"На листе «{sheet}» не хватает колонок: {', '.join(missing)}. "
                    "Проверьте, что заголовки в первой строке не переименованы."
                ),
            )
        )
        return None

    for name in optional:
        if name in header_row:
            mapping[name] = header_row.index(name)

    known = set(required) | set(optional)
    extra = [name for name in header_row if name and name not in known]
    if extra:
        issues.add(
            Issue(
                code="EXTRA_COLUMNS_IGNORED",
                severity=SEVERITY_INFO,
                scope="sheet",
                sheet=sheet,
                row=1,
                message=f"На листе «{sheet}» есть лишние колонки, они не читаются: {', '.join(extra)}.",
            )
        )

    return mapping


def _cell(row: list, mapping: dict[str, int], name: str) -> str:
    index = mapping.get(name)
    if index is None or index >= len(row):
        return ""
    return normalize_text(row[index])


def _is_blank(row: list) -> bool:
    return not any(normalize_text(cell) for cell in row)


def _parse_teachers(rows: list[list], issues: IssueList) -> list[Teacher]:
    mapping = _header_map(
        rows, SHEET_TEACHERS, TEACHERS_REQUIRED_HEADERS, TEACHERS_OPTIONAL_HEADERS, issues
    )
    if mapping is None:
        return []

    teachers: list[Teacher] = []
    seen: dict[str, int] = {}

    for offset, row in enumerate(rows[1:]):
        number = FIRST_DATA_ROW + offset
        if _is_blank(row):
            continue

        name_ru = _cell(row, mapping, "teacher_name_ru")
        name_en = _cell(row, mapping, "teacher_name_en")
        if not name_ru and not name_en:
            issues.add(
                Issue(
                    code="EMPTY_TEACHER_ROW",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_TEACHERS,
                    row=number,
                    message=f"Строка {number}: не заполнено ни русское, ни английское имя.",
                )
            )
            continue

        is_active = parse_bool(_cell(row, mapping, "is_active"))
        if is_active is None:
            issues.add(
                Issue(
                    code="INVALID_ACTIVE_FLAG",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_TEACHERS,
                    row=number,
                    message=(
                        f"Строка {number}: в колонке is_active непонятное значение "
                        f"у преподавателя «{name_ru or name_en}». Ожидается yes или no."
                    ),
                )
            )
            continue

        duplicate = False
        for spelling in (name_ru, name_en):
            if not spelling:
                continue
            key = comparison_key(spelling)
            if key in seen:
                issues.add(
                    Issue(
                        code="DUPLICATE_TEACHER_NAME",
                        severity=SEVERITY_CRITICAL,
                        scope="row",
                        sheet=SHEET_TEACHERS,
                        row=number,
                        message=(
                            f"Строка {number}: написание «{spelling}» уже встречается "
                            f"в строке {seen[key]}. По имени из журнала нельзя будет понять, кто это."
                        ),
                    )
                )
                duplicate = True
            else:
                seen[key] = number
        if duplicate:
            continue

        teachers.append(
            Teacher(
                name_ru=name_ru,
                name_en=name_en,
                is_active=is_active,
                notes=_cell(row, mapping, "notes"),
                row=number,
            )
        )

    return teachers


def _parse_assignments(rows: list[list], issues: IssueList) -> list[Assignment]:
    mapping = _header_map(
        rows,
        SHEET_ASSIGNMENTS,
        ASSIGNMENTS_REQUIRED_HEADERS,
        ASSIGNMENTS_OPTIONAL_HEADERS,
        issues,
    )
    if mapping is None:
        return []

    assignments: list[Assignment] = []
    seen: dict[tuple[str, str, str, str], int] = {}

    for offset, row in enumerate(rows[1:]):
        number = FIRST_DATA_ROW + offset
        if _is_blank(row):
            continue

        class_code = _cell(row, mapping, "class_code")
        subject_name = _cell(row, mapping, "subject_name")
        teacher_name = _cell(row, mapping, "teacher_name")

        if not class_code:
            issues.add(
                Issue(
                    code="EMPTY_CLASS_CODE",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_ASSIGNMENTS,
                    row=number,
                    message=f"Строка {number}: не указан класс.",
                )
            )
            continue

        is_active = parse_bool(_cell(row, mapping, "is_active"))
        if is_active is None:
            issues.add(
                Issue(
                    code="INVALID_ACTIVE_FLAG",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_ASSIGNMENTS,
                    row=number,
                    class_code=class_code,
                    message=(
                        f"Строка {number}: в колонке is_active непонятное значение. "
                        "Ожидается yes или no."
                    ),
                )
            )
            continue

        if not is_active:
            issues.add(
                Issue(
                    code="INACTIVE_ASSIGNMENT_IGNORED",
                    severity=SEVERITY_INFO,
                    scope="row",
                    sheet=SHEET_ASSIGNMENTS,
                    row=number,
                    class_code=class_code,
                    message=f"Строка {number}: предмет «{subject_name}» помечен неактивным и пропущен.",
                )
            )
            continue

        if not subject_name:
            issues.add(
                Issue(
                    code="MISSING_SUBJECT_NAME",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_ASSIGNMENTS,
                    row=number,
                    class_code=class_code,
                    message=f"Строка {number}: у класса {class_code} не указан предмет.",
                )
            )
            continue

        if not teacher_name:
            issues.add(
                Issue(
                    code="MISSING_TEACHER_NAME",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_ASSIGNMENTS,
                    row=number,
                    class_code=class_code,
                    message=f"Строка {number}: у предмета «{subject_name}» в классе {class_code} нет преподавателя.",
                )
            )
            continue

        group_name = _cell(row, mapping, "group_name")
        key = (
            comparison_key(class_code),
            comparison_key(subject_name),
            comparison_key(group_name),
            comparison_key(teacher_name),
        )
        if key in seen:
            issues.add(
                Issue(
                    code="DUPLICATE_ASSIGNMENT",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_ASSIGNMENTS,
                    row=number,
                    class_code=class_code,
                    message=(
                        f"Строка {number}: «{subject_name}» у «{teacher_name}» в классе {class_code} "
                        f"уже есть в строке {seen[key]}."
                    ),
                )
            )
            continue
        seen[key] = number

        assignments.append(
            Assignment(
                class_code=class_code,
                subject_name=subject_name,
                teacher_name=teacher_name,
                group_name=group_name,
                teacher_role=_cell(row, mapping, "teacher_role") or DEFAULT_TEACHER_ROLE,
                source_sheet_name=_cell(row, mapping, "source_sheet_name") or subject_name,
                row=number,
            )
        )

    return assignments


def _check_group_consistency(assignments: list[Assignment], issues: IssueList) -> None:
    """A class subject is either whole-class or split into groups, never both.

    Mixed rows leave no way to tell whether the group row is a subset of the whole-class one
    or a separate lesson, and generation would have to guess.
    """

    by_subject: dict[tuple[str, str], list[Assignment]] = {}
    for item in assignments:
        by_subject.setdefault((comparison_key(item.class_code), comparison_key(item.subject_name)), []).append(item)

    for group in by_subject.values():
        if len(group) < 2:
            continue
        with_group = [item for item in group if item.group_name]
        without_group = [item for item in group if not item.group_name]
        if with_group and without_group:
            first = group[0]
            issues.add(
                Issue(
                    code="MIXED_GROUP_AND_WHOLE_CLASS_ASSIGNMENT",
                    severity=SEVERITY_CRITICAL,
                    scope="class",
                    sheet=SHEET_ASSIGNMENTS,
                    row=first.row,
                    class_code=first.class_code,
                    message=(
                        f"Предмет «{first.subject_name}» в классе {first.class_code} записан "
                        "и на весь класс, и по группам. Нужно выбрать что-то одно."
                    ),
                )
            )


def _check_teachers_against_assignments(
    teachers: list[Teacher], assignments: list[Assignment], issues: IssueList
) -> None:
    """Join the two sheets: every name used must be known, every known name should be used."""

    by_spelling: dict[str, Teacher] = {}
    for teacher in teachers:
        for spelling in teacher.spellings():
            by_spelling[comparison_key(spelling)] = teacher

    used: dict[int, set[str]] = {}
    for item in assignments:
        teacher = by_spelling.get(comparison_key(item.teacher_name))
        if teacher is None:
            issues.add(
                Issue(
                    code="UNKNOWN_TEACHER",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_ASSIGNMENTS,
                    row=item.row,
                    class_code=item.class_code,
                    message=(
                        f"Строка {item.row}: преподавателя «{item.teacher_name}» нет в справочнике. "
                        "Добавьте его на лист teachers или проверьте написание."
                    ),
                )
            )
            continue
        used.setdefault(teacher.row, set()).add(item.class_code)

    for teacher in teachers:
        classes = used.get(teacher.row)
        if not classes:
            if teacher.is_active:
                issues.add(
                    Issue(
                        code="UNUSED_TEACHER",
                        severity=SEVERITY_INFO,
                        scope="row",
                        sheet=SHEET_TEACHERS,
                        row=teacher.row,
                        message=(
                            f"Преподаватель «{teacher.display_name}» есть в справочнике, "
                            "но не ведёт ни одного предмета."
                        ),
                    )
                )
            continue

        if not teacher.name_en and any(is_english_class(code) for code in classes):
            issues.add(
                Issue(
                    code="MISSING_ENGLISH_NAME",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_TEACHERS,
                    row=teacher.row,
                    message=(
                        f"У «{teacher.name_ru}» не заполнено английское написание, "
                        f"а он ведёт в классах: {', '.join(sorted(c for c in classes if is_english_class(c)))}."
                    ),
                )
            )

        if not teacher.name_ru and any(not is_english_class(code) for code in classes):
            issues.add(
                Issue(
                    code="MISSING_RUSSIAN_NAME",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_TEACHERS,
                    row=teacher.row,
                    message=(
                        f"У «{teacher.name_en}» не заполнено русское написание, "
                        f"а он ведёт в классах: {', '.join(sorted(c for c in classes if not is_english_class(c)))}."
                    ),
                )
            )


def parse_subject_teachers(sheets, spreadsheet_id: str) -> SubjectTeacherParseResult:
    """Read and validate the workbook. Never raises for bad data, only for bad access."""

    result = SubjectTeacherParseResult()
    issues = result.issues

    titles = {normalize_text(title).casefold(): title for title in sheets.get_sheet_titles(spreadsheet_id)}
    missing = [name for name in (SHEET_TEACHERS, SHEET_ASSIGNMENTS) if name not in titles]
    if missing:
        issues.add(
            Issue(
                code="MISSING_REQUIRED_SHEET",
                severity=SEVERITY_CRITICAL,
                scope="workbook",
                message=(
                    f"В таблице не хватает листов: {', '.join(missing)}. "
                    "Имена листов должны быть написаны ровно так, латинскими буквами."
                ),
            )
        )
        return result

    result.teachers = _parse_teachers(
        sheets.read_values(spreadsheet_id, f"'{titles[SHEET_TEACHERS]}'"), issues
    )
    result.assignments = _parse_assignments(
        sheets.read_values(spreadsheet_id, f"'{titles[SHEET_ASSIGNMENTS]}'"), issues
    )

    _check_group_consistency(result.assignments, issues)
    _check_teachers_against_assignments(result.teachers, result.assignments, issues)

    result.teachers_total = len(result.teachers)
    result.teachers_active = sum(1 for item in result.teachers if item.is_active)
    result.assignments_total = len(result.assignments)
    result.assignments_active = len(result.assignments)
    return result
