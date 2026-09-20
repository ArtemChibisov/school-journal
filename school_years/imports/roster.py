"""Reading the class roster table.

DEV-0401. Implements `docs/data_contracts/class_roster_contract.md`: two worksheets, `classes`
and `students`, joined by `class_code`.

The guiding rule from the contract is that the report is class-oriented. One malformed row must
never block the other classes: a class with a problem is reported and skipped, everything else
is imported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from google_integration.errors import GoogleIntegrationError

from .issues import SEVERITY_CRITICAL, SEVERITY_INFO, SEVERITY_WARNING, Issue, IssueList
from .text import comparison_key, normalize_text, parse_bool, parse_int

SHEET_CLASSES = "classes"
SHEET_STUDENTS = "students"

CLASSES_REQUIRED_HEADERS = ("class_code", "grade_number", "tutor_name", "is_active")
STUDENTS_REQUIRED_HEADERS = ("class_code", "student_order", "student_name", "is_active")
STUDENTS_OPTIONAL_HEADERS = ("notes",)

GRADE_MIN = 0
GRADE_MAX = 11

# Row 1 is the header, so a list index of 0 is spreadsheet row 2.
FIRST_DATA_ROW = 2


@dataclass(frozen=True)
class RosterStudent:
    class_code: str
    student_order: int
    student_name: str
    notes: str
    row: int


@dataclass(frozen=True)
class RosterClass:
    class_code: str
    grade_number: int
    tutor_name: str
    row: int
    students: tuple[RosterStudent, ...] = ()


@dataclass
class RosterParseResult:
    """Classes that can be generated, plus everything that went wrong on the way."""

    classes: list[RosterClass] = field(default_factory=list)
    issues: IssueList = field(default_factory=IssueList)
    # Every class_code the classes sheet mentions, whatever its state. A class missing from
    # this set is gone from the source; a class present but not in `classes` merely has a
    # problem. The two deserve different treatment on sync.
    seen_class_keys: set = field(default_factory=set)
    inactive_class_keys: set = field(default_factory=set)
    classes_total: int = 0
    classes_active: int = 0
    classes_valid: int = 0
    students_total: int = 0
    students_active: int = 0

    @property
    def is_usable(self) -> bool:
        """False when the workbook itself is wrong, not just some of its rows."""

        return not self.issues.has_workbook_level_critical()

    def as_dict(self) -> dict:
        return {
            "classes_total": self.classes_total,
            "classes_active": self.classes_active,
            "classes_valid": self.classes_valid,
            "students_total": self.students_total,
            "students_active": self.students_active,
            "issue_counts": self.issues.counts(),
            "issues": self.issues.as_dicts(),
            "classes": [
                {
                    "class_code": item.class_code,
                    "grade_number": item.grade_number,
                    "tutor_name": item.tutor_name,
                    "student_count": len(item.students),
                }
                for item in self.classes
            ],
        }


def _header_map(rows: list[list], sheet: str, required: tuple[str, ...], issues: IssueList) -> dict[str, int] | None:
    """Map required header names to column indexes, or report why that is impossible."""

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

    known = set(required) | set(STUDENTS_OPTIONAL_HEADERS)
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

    for name in STUDENTS_OPTIONAL_HEADERS:
        if name in header_row:
            mapping[name] = header_row.index(name)

    return mapping


def _cell(row: list, index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return normalize_text(row[index])


def _is_blank_row(row: list) -> bool:
    return not any(normalize_text(cell) for cell in row)


def _parse_classes(rows: list[list], issues: IssueList, result: RosterParseResult) -> tuple[dict[str, RosterClass], int, int]:
    """Return active, structurally valid classes keyed by comparison key."""

    mapping = _header_map(rows, SHEET_CLASSES, CLASSES_REQUIRED_HEADERS, issues)
    if mapping is None:
        return {}, 0, 0

    by_key: dict[str, RosterClass] = {}
    seen_rows: dict[str, int] = {}
    total = 0
    active = 0

    for offset, row in enumerate(rows[1:]):
        row_number = FIRST_DATA_ROW + offset
        if _is_blank_row(row):
            continue
        total += 1

        class_code = _cell(row, mapping["class_code"])
        if not class_code:
            issues.add(
                Issue(
                    code="EMPTY_CLASS_CODE",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_CLASSES,
                    row=row_number,
                    message=f"Строка {row_number} листа «{SHEET_CLASSES}»: не указан класс.",
                )
            )
            continue

        result.seen_class_keys.add(comparison_key(class_code))

        is_active = parse_bool(_cell(row, mapping["is_active"]))
        if is_active is None:
            issues.add(
                Issue(
                    code="INVALID_ACTIVE_FLAG",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    class_code=class_code,
                    sheet=SHEET_CLASSES,
                    row=row_number,
                    message=(
                        f"Класс {class_code}: непонятное значение в колонке «is_active». "
                        "Допустимо yes или no."
                    ),
                )
            )
            continue

        if not is_active:
            result.inactive_class_keys.add(comparison_key(class_code))
            issues.add(
                Issue(
                    code="INACTIVE_CLASS_IGNORED",
                    severity=SEVERITY_INFO,
                    scope="classes",
                    class_code=class_code,
                    sheet=SHEET_CLASSES,
                    row=row_number,
                    message=f"Класс {class_code} отмечен как неактивный и пропущен.",
                )
            )
            continue

        active += 1
        key = comparison_key(class_code)

        if key in seen_rows:
            issues.add(
                Issue(
                    code="DUPLICATE_CLASS_CODE",
                    severity=SEVERITY_CRITICAL,
                    scope="classes",
                    class_code=class_code,
                    sheet=SHEET_CLASSES,
                    row=row_number,
                    message=(
                        f"Класс {class_code} встречается дважды: строки {seen_rows[key]} и {row_number}. "
                        "Оставьте одну строку."
                    ),
                )
            )
            by_key.pop(key, None)
            continue
        seen_rows[key] = row_number

        grade_number = parse_int(_cell(row, mapping["grade_number"]))
        tutor_name = _cell(row, mapping["tutor_name"])
        broken = False

        if grade_number is None or not GRADE_MIN <= grade_number <= GRADE_MAX:
            issues.add(
                Issue(
                    code="INVALID_GRADE_NUMBER",
                    severity=SEVERITY_CRITICAL,
                    scope="classes",
                    class_code=class_code,
                    sheet=SHEET_CLASSES,
                    row=row_number,
                    message=(
                        f"Класс {class_code}: номер параллели должен быть целым числом "
                        f"от {GRADE_MIN} до {GRADE_MAX}."
                    ),
                )
            )
            broken = True

        if not tutor_name:
            issues.add(
                Issue(
                    code="MISSING_TUTOR",
                    severity=SEVERITY_CRITICAL,
                    scope="classes",
                    class_code=class_code,
                    sheet=SHEET_CLASSES,
                    row=row_number,
                    message=f"Класс {class_code}: не указан тьютор.",
                )
            )
            broken = True

        if broken:
            continue

        by_key[key] = RosterClass(
            class_code=class_code,
            grade_number=grade_number,
            tutor_name=tutor_name,
            row=row_number,
        )

    return by_key, total, active


def _parse_students(
    rows: list[list],
    classes_by_key: dict[str, RosterClass],
    issues: IssueList,
) -> tuple[dict[str, list[RosterStudent]], int, int]:
    mapping = _header_map(rows, SHEET_STUDENTS, STUDENTS_REQUIRED_HEADERS, issues)
    if mapping is None:
        return {}, 0, 0

    by_key: dict[str, list[RosterStudent]] = {key: [] for key in classes_by_key}
    orders: dict[str, dict[int, int]] = {key: {} for key in classes_by_key}
    names: dict[str, dict[str, int]] = {key: {} for key in classes_by_key}
    total = 0
    active = 0

    for offset, row in enumerate(rows[1:]):
        row_number = FIRST_DATA_ROW + offset
        if _is_blank_row(row):
            continue
        total += 1

        class_code = _cell(row, mapping["class_code"])
        if not class_code:
            issues.add(
                Issue(
                    code="EMPTY_CLASS_CODE",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_STUDENTS,
                    row=row_number,
                    message=f"Строка {row_number} листа «{SHEET_STUDENTS}»: не указан класс.",
                )
            )
            continue

        is_active = parse_bool(_cell(row, mapping["is_active"]))
        if is_active is None:
            issues.add(
                Issue(
                    code="INVALID_ACTIVE_FLAG",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    class_code=class_code,
                    sheet=SHEET_STUDENTS,
                    row=row_number,
                    message=(
                        f"Строка {row_number} листа «{SHEET_STUDENTS}»: непонятное значение "
                        "в колонке «is_active». Допустимо yes или no."
                    ),
                )
            )
            continue

        student_name = _cell(row, mapping["student_name"])

        if not is_active:
            issues.add(
                Issue(
                    code="INACTIVE_STUDENT_IGNORED",
                    severity=SEVERITY_INFO,
                    scope="students",
                    class_code=class_code,
                    sheet=SHEET_STUDENTS,
                    row=row_number,
                    message=f"Ученик {student_name or row_number} ({class_code}) неактивен и пропущен.",
                )
            )
            continue

        active += 1
        key = comparison_key(class_code)

        if key not in classes_by_key:
            issues.add(
                Issue(
                    code="STUDENT_CLASS_NOT_FOUND",
                    severity=SEVERITY_CRITICAL,
                    scope="students",
                    class_code=class_code,
                    sheet=SHEET_STUDENTS,
                    row=row_number,
                    message=(
                        f"Строка {row_number}: ученик отнесён к классу {class_code}, "
                        f"которого нет среди активных классов на листе «{SHEET_CLASSES}»."
                    ),
                )
            )
            continue

        if not student_name:
            issues.add(
                Issue(
                    code="MISSING_STUDENT_NAME",
                    severity=SEVERITY_CRITICAL,
                    scope="students",
                    class_code=class_code,
                    sheet=SHEET_STUDENTS,
                    row=row_number,
                    message=f"Класс {class_code}, строка {row_number}: не указано имя ученика.",
                )
            )
            continue

        student_order = parse_int(_cell(row, mapping["student_order"]))
        if student_order is None or student_order < 1:
            issues.add(
                Issue(
                    code="INVALID_STUDENT_ORDER",
                    severity=SEVERITY_CRITICAL,
                    scope="students",
                    class_code=class_code,
                    sheet=SHEET_STUDENTS,
                    row=row_number,
                    message=(
                        f"Класс {class_code}, ученик {student_name}: порядковый номер должен быть "
                        "целым числом от 1."
                    ),
                )
            )
            continue

        if student_order in orders[key]:
            issues.add(
                Issue(
                    code="DUPLICATE_STUDENT_ORDER",
                    severity=SEVERITY_CRITICAL,
                    scope="students",
                    class_code=class_code,
                    sheet=SHEET_STUDENTS,
                    row=row_number,
                    message=(
                        f"Класс {class_code}: порядковый номер {student_order} стоит у двух учеников, "
                        f"строки {orders[key][student_order]} и {row_number}."
                    ),
                )
            )
            continue

        name_key = comparison_key(student_name)
        if name_key in names[key]:
            issues.add(
                Issue(
                    code="DUPLICATE_STUDENT_NAME",
                    severity=SEVERITY_CRITICAL,
                    scope="students",
                    class_code=class_code,
                    sheet=SHEET_STUDENTS,
                    row=row_number,
                    message=(
                        f"Класс {class_code}: ученик {student_name} встречается дважды, "
                        f"строки {names[key][name_key]} и {row_number}."
                    ),
                )
            )
            continue

        orders[key][student_order] = row_number
        names[key][name_key] = row_number
        by_key[key].append(
            RosterStudent(
                class_code=classes_by_key[key].class_code,
                student_order=student_order,
                student_name=student_name,
                notes=_cell(row, mapping.get("notes")),
                row=row_number,
            )
        )

    return by_key, total, active


def _check_class_students(class_item: RosterClass, students: list[RosterStudent], issues: IssueList) -> None:
    if not students:
        issues.add(
            Issue(
                code="MISSING_STUDENTS",
                severity=SEVERITY_CRITICAL,
                scope="class",
                class_code=class_item.class_code,
                sheet=SHEET_STUDENTS,
                message=f"Класс {class_item.class_code}: нет ни одного активного ученика.",
            )
        )
        return

    expected = list(range(1, len(students) + 1))
    actual = sorted(student.student_order for student in students)
    if actual != expected:
        issues.add(
            Issue(
                code="STUDENT_ORDER_GAP",
                severity=SEVERITY_WARNING,
                scope="class",
                class_code=class_item.class_code,
                sheet=SHEET_STUDENTS,
                message=(
                    f"Класс {class_item.class_code}: в нумерации учеников есть пропуски. "
                    "Создание файлов это не остановит."
                ),
            )
        )


def parse_roster(sheets, spreadsheet_id: str) -> RosterParseResult:
    """Read and validate the roster workbook. Never raises for bad data, only for bad access."""

    result = RosterParseResult()
    issues = result.issues

    titles = {normalize_text(title).casefold(): title for title in sheets.get_sheet_titles(spreadsheet_id)}
    missing = [name for name in (SHEET_CLASSES, SHEET_STUDENTS) if name not in titles]
    if missing:
        issues.add(
            Issue(
                code="MISSING_REQUIRED_SHEET",
                severity=SEVERITY_CRITICAL,
                scope="workbook",
                message=(
                    "В таблице состава классов не найдены обязательные листы: "
                    f"{', '.join(missing)}."
                ),
            )
        )
        return result

    class_rows = sheets.read_values(spreadsheet_id, f"'{titles[SHEET_CLASSES]}'")
    student_rows = sheets.read_values(spreadsheet_id, f"'{titles[SHEET_STUDENTS]}'")

    classes_by_key, result.classes_total, result.classes_active = _parse_classes(class_rows, issues, result)
    students_by_key, result.students_total, result.students_active = _parse_students(
        student_rows, classes_by_key, issues
    )

    for key, class_item in classes_by_key.items():
        students = sorted(
            students_by_key.get(key, []),
            key=lambda student: (student.student_order, comparison_key(student.student_name)),
        )
        _check_class_students(class_item, students, issues)

        if issues.has_critical_for_class(class_item.class_code):
            continue

        result.classes.append(
            RosterClass(
                class_code=class_item.class_code,
                grade_number=class_item.grade_number,
                tutor_name=class_item.tutor_name,
                row=class_item.row,
                students=tuple(students),
            )
        )

    result.classes.sort(key=lambda item: (item.grade_number, comparison_key(item.class_code)))
    result.classes_valid = len(result.classes)
    return result


def read_roster_for_school_year(school_year, *, sheets) -> RosterParseResult:
    """Read the roster configured on a school year."""

    from google_integration.ids import extract_spreadsheet_id

    url = (school_year.class_roster_sheet_url or "").strip()
    if not url:
        raise GoogleIntegrationError(
            "invalid_config",
            f"School year {school_year.pk} has no class_roster_sheet_url",
            user_message="В настройках учебного года не указана таблица состава классов.",
        )

    return parse_roster(sheets, extract_spreadsheet_id(url))
